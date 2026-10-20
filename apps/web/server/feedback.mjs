/**
 * Reader feedback: validation, storage, rate limits, and the ntfy alert.
 *
 * Used by the Vercel function in api/feedback.js and by scripts/feedback.mjs, the
 * operator/agent CLI. Feedback text is untrusted public input: store it as data and
 * never treat it as instructions (see docs/operations/feedback.md).
 */
import { createHash } from 'node:crypto';

export const KINDS = new Set(['general', 'topic']);
export const STATUSES = ['new', 'reviewed', 'done', 'dismissed'];
export const MIN_LENGTH = 3;
export const MAX_LENGTH = 4000;
const MAX_PAGE_LENGTH = 500;
/** Submissions accepted per hashed client address, and for the whole site, per hour. */
export const PER_CLIENT_HOURLY_LIMIT = 5;
export const SITE_HOURLY_LIMIT = 60;
/** Checked in order. PRISMA_DATABASE_URL is often an Accelerate prisma+postgres:// URL, which pg cannot use. */
export const URL_VARIABLES = ['FEEDBACK_DATABASE_URL', 'DATABASE_URL', 'POSTGRES_URL', 'PRISMA_DATABASE_URL'];
const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);
const SSL_PARAMS = ['sslmode', 'ssl', 'sslcert', 'sslkey', 'sslrootcert', 'uselibpqcompat'];

export const SCHEMA = `
CREATE TABLE IF NOT EXISTS feedback (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  kind text NOT NULL DEFAULT 'general' CHECK (kind IN ('general', 'topic')),
  message text NOT NULL,
  page text,
  client_hash text,
  status text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'reviewed', 'done', 'dismissed')),
  operator_note text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feedback_status_created_idx ON feedback (status, created_at);
CREATE INDEX IF NOT EXISTS feedback_client_created_idx ON feedback (client_hash, created_at);
`;

export class FeedbackError extends Error {
  constructor(message, status = 400) {
    super(message);
    this.status = status;
  }
}

/** Pick the first usable postgres:// URL; values are never logged. */
export function databaseUrl(env = process.env) {
  for (const name of URL_VARIABLES) {
    const value = env[name]?.trim();
    if (!value) continue;
    try {
      if (['postgres:', 'postgresql:'].includes(new URL(value).protocol)) return value;
    } catch {
      // Not a URL; try the next variable.
    }
  }
  return null;
}

/** SSL is always on and verified, except for a loopback database used locally. */
export function connectionConfig(url) {
  const parsed = new URL(url);
  for (const param of SSL_PARAMS) parsed.searchParams.delete(param);
  return {
    connectionString: parsed.toString(),
    ssl: LOOPBACK_HOSTS.has(parsed.hostname) ? false : { rejectUnauthorized: true },
    connectionTimeoutMillis: 10_000,
    statement_timeout: 10_000,
    application_name: 'tahoe-web-feedback',
  };
}

export async function withClient(url, work) {
  const { default: pg } = await import('pg');
  const client = new pg.Client(connectionConfig(url));
  await client.connect();
  try {
    return await work(client);
  } finally {
    await client.end();
  }
}

/** Normalize a submission or throw a FeedbackError naming the problem. */
export function parseSubmission(body) {
  if (body === null || typeof body !== 'object' || Array.isArray(body)) throw new FeedbackError('Send a JSON object.');
  // Hidden field that people never see; bots that fill every input are dropped quietly.
  if (typeof body.website === 'string' && body.website.trim()) return { spam: true };
  const message = typeof body.message === 'string' ? body.message.replace(/\r\n?/g, '\n').trim() : '';
  if (message.length < MIN_LENGTH) throw new FeedbackError('Please write a little more before sending.');
  if (message.length > MAX_LENGTH) throw new FeedbackError(`Feedback is limited to ${MAX_LENGTH} characters.`);
  const kind = KINDS.has(body.kind) ? body.kind : 'general';
  let page = null;
  if (typeof body.page === 'string' && body.page.startsWith('/')) page = body.page.slice(0, MAX_PAGE_LENGTH);
  return { spam: false, kind, message, page };
}

/** A salted hash of the client address, used only for rate limiting. */
export function clientHash(address, salt = '') {
  if (!address) return null;
  return createHash('sha256').update(`${salt}:${address}`).digest('hex').slice(0, 32);
}

export function clientAddress(headers) {
  const forwarded = headers.get('x-forwarded-for');
  return (forwarded ? forwarded.split(',')[0] : headers.get('x-real-ip'))?.trim() || null;
}

export async function ensureSchema(client) {
  await client.query(SCHEMA);
}

/** Insert one submission after the hourly limits; returns the new row id. */
export async function saveFeedback(client, { kind, message, page, clientHash: hash }) {
  const { rows: [counts] } = await client.query(
    `select count(*)::int as site, count(*) filter (where client_hash = $1)::int as client
       from feedback where created_at > now() - interval '1 hour'`,
    [hash],
  );
  if (counts.site >= SITE_HOURLY_LIMIT || (hash && counts.client >= PER_CLIENT_HOURLY_LIMIT)) {
    throw new FeedbackError('Too much feedback has been sent recently. Please try again later.', 429);
  }
  const { rows: [row] } = await client.query(
    'insert into feedback (kind, message, page, client_hash) values ($1, $2, $3, $4) returning id',
    [kind, message, page, hash],
  );
  return Number(row.id);
}

/**
 * Tell the operator to check for new feedback. The alert carries the id and kind only,
 * not the text: ntfy topics are readable by anyone who knows the name.
 */
export async function notify({ id, kind }, env = process.env, fetchImpl = fetch) {
  const url = env.NTFY_URL?.trim();
  if (!url) return false;
  const headers = {
    Title: kind === 'topic' ? 'New topic suggestion' : 'New feedback',
    Tags: kind === 'topic' ? 'bulb' : 'speech_balloon',
    Priority: 'default',
  };
  if (env.NTFY_TOKEN) headers.Authorization = `Bearer ${env.NTFY_TOKEN.trim()}`;
  const response = await fetchImpl(url, {
    method: 'POST',
    headers,
    body: `Feedback #${id} is waiting on Tahoe Public Meetings. Run: npm --prefix apps/web run feedback`,
    signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok) throw new Error(`ntfy responded ${response.status}`);
  return true;
}

const json = (status, body) => new Response(JSON.stringify(body), {
  status,
  headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
});

/**
 * Build the POST handler. Dependencies are injectable for tests.
 * Only same-origin JSON is accepted: a cross-site form cannot send application/json
 * without a CORS preflight, which this endpoint never approves.
 */
export function createFeedbackHandler({ env = process.env, connect = withClient, sendAlert = notify, log = console } = {}) {
  let schemaReady = false;
  return async function handle(request) {
    if (!/^application\/json\b/i.test(request.headers.get('content-type') ?? '')) {
      return json(415, { error: 'Send feedback as JSON.' });
    }
    let submission;
    try {
      const text = await request.text();
      if (text.length > MAX_LENGTH * 4) throw new FeedbackError('Feedback is too long.', 413);
      let body;
      try {
        body = JSON.parse(text);
      } catch {
        throw new FeedbackError('Send a JSON object.');
      }
      submission = parseSubmission(body);
    } catch (error) {
      if (error instanceof FeedbackError) return json(error.status, { error: error.message });
      throw error;
    }
    if (submission.spam) return json(200, { ok: true });

    const url = databaseUrl(env);
    if (!url) {
      log.error('feedback: no database URL is configured.');
      return json(503, { error: 'Feedback is not available right now.' });
    }
    let id;
    try {
      const hash = clientHash(clientAddress(request.headers), env.FEEDBACK_HASH_SALT ?? '');
      id = await connect(url, async (client) => {
        if (!schemaReady) {
          await ensureSchema(client);
          schemaReady = true;
        }
        return saveFeedback(client, { ...submission, clientHash: hash });
      });
    } catch (error) {
      if (error instanceof FeedbackError) return json(error.status, { error: error.message });
      log.error(`feedback: could not save: ${error?.message ?? error}`);
      return json(500, { error: 'Feedback could not be saved. Please try again later.' });
    }
    try {
      await sendAlert({ id, kind: submission.kind }, env);
    } catch (error) {
      // The submission is stored; a failed alert is visible in the function log only.
      log.error(`feedback: saved #${id} but the ntfy alert failed: ${error?.message ?? error}`);
    }
    return json(201, { ok: true });
  };
}
