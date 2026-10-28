import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import {
  ExportError, SCHEMA_VERSION, connectionConfig, isRequired, main, selectDatabaseUrl, validatePayload,
} from '../scripts/fetch-export.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const REMOTE = 'postgres://user:s3cret@db.prisma.io:5432/postgres?sslmode=require';
const ACCELERATE = 'prisma+postgres://accelerate.prisma-data.net/?api_key=s3cret';

function payload(overrides = {}) {
  return {
    schemaVersion: 2, generatedAt: '2026-10-20T12:00:00Z', mode: 'live', coverageNote: 'Coverage.',
    agencies: [{ id: 'truckee' }], topics: [{ id: 'housing' }],
    meetings: [{ id: 'truckee:1797', agencyId: 'truckee', items: [] }],
    ...overrides,
  };
}

async function run(env, fetchRow, fetchPublished = async () => { throw new Error('HTTP 404'); }) {
  const dir = await mkdtemp(join(tmpdir(), 'fetch-export-'));
  const output = join(dir, 'public.json');
  const logs = [];
  try {
    const result = await main(env, { fetchRow, fetchPublished, output, log: (line) => logs.push(line) });
    const written = result.fetched ? JSON.parse(await readFile(output, 'utf8')) : null;
    return { result, written, logs };
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

test('schema version matches the domain PublicData type', async () => {
  const source = await readFile(resolve(root, '../../packages/domain/public.ts'), 'utf8');
  assert.match(source, new RegExp(`schemaVersion: ${SCHEMA_VERSION};`));
});

test('export is required only on Vercel or when forced', () => {
  assert.equal(isRequired({}), false);
  assert.equal(isRequired({ VERCEL: '1' }), true);
  assert.equal(isRequired({ TAHOE_REQUIRE_EXPORT: '1' }), true);
});

test('local builds ignore DATABASE_URL and use only the explicit override', () => {
  assert.equal(selectDatabaseUrl({ DATABASE_URL: REMOTE }).url, null);
  assert.equal(selectDatabaseUrl({ DATABASE_URL: REMOTE, TAHOE_EXPORT_DATABASE_URL: 'postgresql://a@h/db' }).name, 'TAHOE_EXPORT_DATABASE_URL');
});

test('Vercel builds prefer the override, then DATABASE_URL, then POSTGRES_URL', () => {
  const env = { VERCEL: '1', DATABASE_URL: REMOTE, POSTGRES_URL: 'postgres://p@h/db' };
  assert.equal(selectDatabaseUrl(env).name, 'DATABASE_URL');
  assert.equal(selectDatabaseUrl({ ...env, TAHOE_EXPORT_DATABASE_URL: 'postgres://o@h/db' }).name, 'TAHOE_EXPORT_DATABASE_URL');
  assert.equal(selectDatabaseUrl({ VERCEL: '1', POSTGRES_URL: REMOTE }).name, 'POSTGRES_URL');
});

test('non-Postgres schemes such as Accelerate URLs are skipped without printing values', () => {
  const selected = selectDatabaseUrl({ VERCEL: '1', DATABASE_URL: ACCELERATE, PRISMA_DATABASE_URL: ACCELERATE, POSTGRES_URL: 'not a url' });
  assert.equal(selected.url, null);
  assert.equal(selected.skipped.length, 3);
  assert.ok(selected.skipped.every((reason) => !reason.includes('s3cret') && !reason.includes('accelerate')));
  assert.equal(selectDatabaseUrl({ VERCEL: '1', DATABASE_URL: ACCELERATE, PRISMA_DATABASE_URL: REMOTE }).name, 'PRISMA_DATABASE_URL');
});

test('remote connections verify TLS; URL ssl parameters do not override it', () => {
  const remote = connectionConfig(REMOTE);
  assert.deepEqual(remote.ssl, { rejectUnauthorized: true });
  assert.ok(!remote.connectionString.includes('sslmode'));
  assert.equal(connectionConfig('postgres://u:p@db.example/x?sslmode=disable').ssl.rejectUnauthorized, true);
  assert.equal(connectionConfig('postgresql://u:p@127.0.0.1:55432/x').ssl, false);
});

test('shape check accepts a valid export and rejects empty or mismatched ones', () => {
  assert.equal(validatePayload(payload()).meetings.length, 1);
  assert.equal(validatePayload(JSON.stringify(payload())).schemaVersion, 2);
  assert.throws(() => validatePayload(null), ExportError);
  assert.throws(() => validatePayload('{'), /not valid JSON/);
  assert.throws(() => validatePayload(payload({ meetings: [] })), /meetings is empty/);
  assert.throws(() => validatePayload(payload({ schemaVersion: 3 }), 3), /expected 2/);
  assert.throws(() => validatePayload(payload(), 1), /row schema_version 1/);
  assert.throws(() => validatePayload(payload({ generatedAt: 'soon' })), /generatedAt/);
  assert.throws(() => validatePayload(payload({ meetings: [{ id: 'x' }] })), /meetings\[0\]/);
});

test('local build without an override skips fetching', async () => {
  const { result, logs } = await run({ DATABASE_URL: REMOTE }, () => assert.fail('must not connect'));
  assert.equal(result.fetched, false);
  assert.match(logs.join('\n'), /using the local/);
});

test('Vercel build fails without a usable URL', async () => {
  await assert.rejects(run({ VERCEL: '1', PRISMA_DATABASE_URL: ACCELERATE }, () => assert.fail('must not connect')), /No usable Postgres URL/);
});

test('Vercel build fails when the database and the live site export both fail', async () => {
  await assert.rejects(run({ VERCEL: '1', DATABASE_URL: REMOTE }, async () => null), /no rows.*could not be read either: HTTP 404/);
  await assert.rejects(run({ VERCEL: '1', DATABASE_URL: REMOTE }, async () => { throw new Error('relation "public_exports" does not exist'); }), /Could not read public_exports/);
  await assert.rejects(run({ VERCEL: '1', DATABASE_URL: REMOTE }, async () => null, async () => payload({ meetings: [] })), /meetings is empty/);
});

test('when the database cannot be read, the build reuses the export the live site serves', async () => {
  const quota = async () => { throw new Error('Operations limit exceeded for this billing period'); };
  let requested;
  const { result, written, logs } = await run({ VERCEL: '1', DATABASE_URL: REMOTE }, quota, async (url) => { requested = url; return payload({ generatedAt: '2026-10-19T12:00:00Z' }); });
  assert.equal(requested, 'https://tahoepublicmeetings.org/data/public-export.json');
  assert.deepEqual({ fetched: result.fetched, fallback: result.fallback }, { fetched: true, fallback: true });
  assert.equal(written.generatedAt, '2026-10-19T12:00:00Z', 'the published export keeps its own timestamp, so the freshness check still sees stale data');
  assert.match(logs.join('\n'), /Operations limit exceeded[\s\S]*live site/);
  assert.ok(logs.every((line) => !line.includes('s3cret') && !line.includes('db.prisma.io')));
  await run({ VERCEL: '1', DATABASE_URL: REMOTE, TAHOE_FALLBACK_EXPORT_URL: 'https://preview.example/data/public-export.json' }, quota, async (url) => { requested = url; return payload(); });
  assert.equal(requested, 'https://preview.example/data/public-export.json');
});

test('Vercel build writes the newest payload and never logs the connection string', async () => {
  const { result, written, logs } = await run({ VERCEL: '1', DATABASE_URL: REMOTE }, async (url) => {
    assert.equal(url, REMOTE);
    return { id: '7', schema_version: 2, generated_at: new Date(), payload: payload() };
  });
  assert.equal(result.fetched, true);
  assert.deepEqual(written, payload());
  assert.ok(logs.every((line) => !line.includes('s3cret') && !line.includes('db.prisma.io')));
});
