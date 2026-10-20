#!/usr/bin/env node
/**
 * Prebuild step: load the newest public export from Postgres into src/data/public.json.
 *
 * - On Vercel (VERCEL=1) or with TAHOE_REQUIRE_EXPORT=1 the export is required. Any
 *   missing URL, missing row, or malformed payload fails the build so Vercel keeps the
 *   previous production deployment live instead of publishing an empty site.
 * - Elsewhere it runs only when TAHOE_EXPORT_DATABASE_URL is set. Local builds
 *   (scripts/project.py sets DATABASE_URL for the worker) keep the file written by
 *   `tahoe-ingest export`.
 *
 * The connection string is never printed; logs name only the environment variable.
 */
import { mkdir, rename, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const SCHEMA_VERSION = 2;
export const OUTPUT = resolve(dirname(fileURLToPath(import.meta.url)), '../src/data/public.json');
/** Checked in order. PRISMA_DATABASE_URL is often an Accelerate prisma+postgres:// URL, which pg cannot use. */
export const URL_VARIABLES = ['TAHOE_EXPORT_DATABASE_URL', 'DATABASE_URL', 'POSTGRES_URL', 'PRISMA_DATABASE_URL'];
const POSTGRES_SCHEMES = new Set(['postgres:', 'postgresql:']);
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);
const SSL_PARAMS = ['sslmode', 'ssl', 'sslcert', 'sslkey', 'sslrootcert', 'uselibpqcompat'];

export class ExportError extends Error {}

export function isRequired(env) {
  return env.VERCEL === '1' || env.TAHOE_REQUIRE_EXPORT === '1';
}

/**
 * Pick the connection string to use. Returns { name, url } or null with the reasons
 * candidates were skipped. Values are never included in `skipped`.
 */
export function selectDatabaseUrl(env, { required = isRequired(env) } = {}) {
  const names = required ? URL_VARIABLES : URL_VARIABLES.slice(0, 1);
  const skipped = [];
  for (const name of names) {
    const value = env[name]?.trim();
    if (!value) continue;
    let parsed;
    try {
      parsed = new URL(value);
    } catch {
      skipped.push(`${name} is not a valid URL`);
      continue;
    }
    if (!POSTGRES_SCHEMES.has(parsed.protocol)) {
      skipped.push(`${name} uses the unsupported scheme ${parsed.protocol.replace(/:$/, '')}://`);
      continue;
    }
    return { name, url: value, skipped };
  }
  return { name: null, url: null, skipped };
}

/** SSL is always on and verified, except for a loopback database used in local tests. */
export function connectionConfig(url) {
  const parsed = new URL(url);
  for (const param of SSL_PARAMS) parsed.searchParams.delete(param);
  const loopback = LOOPBACK_HOSTS.has(parsed.hostname);
  return {
    connectionString: parsed.toString(),
    ssl: loopback ? false : { rejectUnauthorized: true },
    connectionTimeoutMillis: 20_000,
    statement_timeout: 30_000,
    query_timeout: 35_000,
    application_name: 'tahoe-web-build',
  };
}

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

/** A basic shape check; the worker validates the full schema before inserting the row. */
export function validatePayload(payload, rowSchemaVersion = SCHEMA_VERSION) {
  const problems = [];
  if (typeof payload === 'string') {
    try {
      payload = JSON.parse(payload);
    } catch {
      throw new ExportError('Export payload is not valid JSON.');
    }
  }
  if (!isObject(payload)) throw new ExportError('Export payload is not a JSON object.');
  if (payload.schemaVersion !== SCHEMA_VERSION) problems.push(`schemaVersion is ${JSON.stringify(payload.schemaVersion)}, expected ${SCHEMA_VERSION}`);
  if (rowSchemaVersion !== payload.schemaVersion) problems.push(`row schema_version ${rowSchemaVersion} does not match payload schemaVersion`);
  if (typeof payload.generatedAt !== 'string' || Number.isNaN(Date.parse(payload.generatedAt))) problems.push('generatedAt is not a timestamp');
  if (!['fixture', 'live'].includes(payload.mode)) problems.push('mode is not fixture or live');
  if (typeof payload.coverageNote !== 'string') problems.push('coverageNote is missing');
  for (const key of ['agencies', 'topics', 'meetings']) {
    if (!Array.isArray(payload[key])) problems.push(`${key} is not an array`);
    else if (payload[key].length === 0) problems.push(`${key} is empty`);
  }
  if (payload.commentPeriods !== undefined && !Array.isArray(payload.commentPeriods)) problems.push('commentPeriods is not an array');
  if (Array.isArray(payload.meetings)) {
    const bad = payload.meetings.findIndex((meeting) => !isObject(meeting) || typeof meeting.id !== 'string' || typeof meeting.agencyId !== 'string' || !Array.isArray(meeting.items));
    if (bad >= 0) problems.push(`meetings[${bad}] lacks id, agencyId, or items`);
  }
  if (problems.length) throw new ExportError(`Export payload failed the shape check: ${problems.join('; ')}.`);
  return payload;
}

export async function fetchLatestExport(url) {
  const { default: pg } = await import('pg');
  const client = new pg.Client(connectionConfig(url));
  await client.connect();
  try {
    const result = await client.query(
      'select id, schema_version, generated_at, payload from public_exports order by id desc limit 1',
    );
    return result.rows[0] ?? null;
  } finally {
    await client.end();
  }
}

export async function writeExport(payload, output = OUTPUT) {
  await mkdir(dirname(output), { recursive: true });
  const temporary = `${output}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(payload)}\n`);
  await rename(temporary, output);
}

export async function main(env = process.env, { fetchRow = fetchLatestExport, output = OUTPUT, log = console.log } = {}) {
  const required = isRequired(env);
  const { name, url, skipped } = selectDatabaseUrl(env, { required });
  for (const reason of skipped) log(`fetch-export: skipped ${reason}.`);
  if (!url) {
    if (required) {
      throw new ExportError(`No usable Postgres URL. Set one of ${URL_VARIABLES.slice(0, 3).join(', ')} for this build.`);
    }
    log('fetch-export: no TAHOE_EXPORT_DATABASE_URL; using the local src/data/public.json.');
    return { fetched: false };
  }
  log(`fetch-export: reading the newest public_exports row using ${name}.`);
  let row;
  try {
    row = await fetchRow(url);
  } catch (error) {
    // Driver messages describe the failure (host lookup, auth, missing table) without the password.
    throw new ExportError(`Could not read public_exports: ${error?.message ?? error}`);
  }
  if (!row) throw new ExportError('public_exports has no rows; run the ingestion export before building.');
  const payload = validatePayload(row.payload, Number(row.schema_version));
  await writeExport(payload, output);
  const ageHours = (Date.now() - Date.parse(payload.generatedAt)) / 3_600_000;
  log(`fetch-export: wrote export #${row.id} generated ${payload.generatedAt} (${ageHours.toFixed(1)} h old): ${payload.meetings.length} meetings, ${payload.agencies.length} agencies.`);
  if (ageHours > 48) log('fetch-export: warning: the newest export is more than 48 hours old; check the scheduled ingestion job.');
  return { fetched: true, id: row.id };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    console.error(`fetch-export: ${error instanceof ExportError ? error.message : `unexpected failure: ${error?.message ?? error}`}`);
    process.exit(1);
  });
}
