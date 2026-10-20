import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';
import { createFeedbackHandler, notify, parseSubmission, withClient, PER_CLIENT_HOURLY_LIMIT } from '../server/feedback.mjs';
import { main as feedbackCli } from '../scripts/feedback.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const transpile = async (file) => ts.transpileModule(await readFile(resolve(root, file), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const silent = { error() {} };

function post(body, headers = {}) {
  return new Request('https://example.test/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.9, 10.0.0.1', ...headers },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  });
}

/** In-memory stand-in for the two queries saveFeedback runs. */
function fakeDatabase() {
  const rows = [];
  const client = {
    async query(sql, params = []) {
      if (/^\s*CREATE/.test(sql)) return { rows: [] };
      if (sql.startsWith('select count')) return { rows: [{ site: rows.length, client: rows.filter((row) => row.client_hash === params[0]).length }] };
      if (sql.startsWith('insert')) {
        const [kind, message, page, client_hash] = params;
        rows.push({ id: rows.length + 1, kind, message, page, client_hash });
        return { rows: [{ id: String(rows.length) }] };
      }
      throw new Error(`unexpected query: ${sql}`);
    },
  };
  return { rows, connect: async (_url, work) => work(client) };
}

test('validates submissions and quietly drops the honeypot', () => {
  assert.deepEqual(parseSubmission({ message: '  Add a wildfire topic \r\n', kind: 'topic', page: '/?topic=housing' }), { spam: false, kind: 'topic', message: 'Add a wildfire topic', page: '/?topic=housing' });
  assert.equal(parseSubmission({ message: 'Hello there', kind: 'admin', page: 'https://evil.test/' }).kind, 'general');
  assert.equal(parseSubmission({ message: 'Hello there', page: 'https://evil.test/' }).page, null);
  assert.deepEqual(parseSubmission({ message: 'Buy now', website: 'spam.test' }), { spam: true });
  assert.throws(() => parseSubmission({ message: 'x' }), /a little more/);
  assert.throws(() => parseSubmission({ message: 'x'.repeat(4001) }), /4000/);
  assert.throws(() => parseSubmission([]), /JSON object/);
});

test('stores feedback, hashes the client address, and alerts without the text', async () => {
  const database = fakeDatabase();
  const alerts = [];
  const handle = createFeedbackHandler({
    env: { DATABASE_URL: 'postgres://user:pw@db.example.test/app', FEEDBACK_HASH_SALT: 'salt' },
    connect: database.connect,
    sendAlert: async (alert) => alerts.push(alert),
    log: silent,
  });
  const response = await handle(post({ message: "I'm interested in adding a new topic: wildfire", kind: 'topic', page: '/' }));
  assert.equal(response.status, 201);
  assert.equal(database.rows.length, 1);
  assert.equal(database.rows[0].kind, 'topic');
  assert.match(database.rows[0].client_hash, /^[0-9a-f]{32}$/);
  assert.ok(!database.rows[0].client_hash.includes('203.0.113.9'));
  assert.deepEqual(alerts, [{ id: 1, kind: 'topic' }]);
});

test('rejects non-JSON requests, rate limits a client, and survives a failed alert', async () => {
  const database = fakeDatabase();
  const handle = createFeedbackHandler({
    env: { DATABASE_URL: 'postgres://db.example.test/app' },
    connect: database.connect,
    sendAlert: async () => { throw new Error('ntfy down'); },
    log: silent,
  });
  assert.equal((await handle(post('message=hi', { 'Content-Type': 'application/x-www-form-urlencoded' }))).status, 415);
  assert.equal((await handle(post('{not json'))).status, 400);
  for (let index = 0; index < PER_CLIENT_HOURLY_LIMIT; index += 1) assert.equal((await handle(post({ message: `Note ${index}` }))).status, 201);
  const limited = await handle(post({ message: 'One more' }));
  assert.equal(limited.status, 429);
  assert.equal((await handle(post({ message: 'Different reader' }, { 'X-Forwarded-For': '198.51.100.4' }))).status, 201);
  assert.equal(database.rows.length, PER_CLIENT_HOURLY_LIMIT + 1);
});

test('reports an unavailable service when no database is configured', async () => {
  const handle = createFeedbackHandler({ env: {}, log: silent });
  assert.equal((await handle(post({ message: 'Hello there' }))).status, 503);
});

test('posts the ntfy alert with a title and optional token', async () => {
  const calls = [];
  const fetchImpl = async (url, init) => { calls.push({ url, init }); return { ok: true }; };
  assert.equal(await notify({ id: 7, kind: 'general' }, {}, fetchImpl), false);
  assert.equal(await notify({ id: 7, kind: 'topic' }, { NTFY_URL: 'https://ntfy.example.test/topic', NTFY_TOKEN: 'tk_abc' }, fetchImpl), true);
  assert.equal(calls[0].url, 'https://ntfy.example.test/topic');
  assert.equal(calls[0].init.headers.Title, 'New topic suggestion');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer tk_abc');
  assert.match(calls[0].init.body, /#7/);
});

test('the Suggest a topic option opens the feedback dialog with the starter prompt', async () => {
  const html = await readFile(resolve(root, 'dist/index.html'), 'utf8');
  const dom = new JSDOM(html, { url: 'http://localhost/?topic=transit', runScripts: 'outside-only' });
  const { window } = dom;
  window.HTMLDialogElement.prototype.showModal = function showModal() { this.open = true; };
  window.HTMLDialogElement.prototype.close = function close() { this.open = false; this.dispatchEvent(new window.Event('close')); };
  window.eval(await transpile('src/scripts/feedback-dialog.ts'));
  window.eval(await transpile('src/scripts/timeline-filters.ts'));
  const { document } = window;
  const select = document.querySelector('#topic-filter');
  const suggest = [...select.options].at(-1);
  assert.equal(suggest.textContent, 'Suggest a topic…');

  select.value = suggest.value;
  select.dispatchEvent(new window.Event('change', { bubbles: true }));
  const dialog = document.querySelector('#feedback-dialog');
  assert.equal(dialog.open, true);
  assert.equal(select.value, 'transit');
  assert.equal(window.location.search, '?topic=transit');
  assert.equal(document.querySelector('#feedback-message').value, "I'm interested in adding a new topic: ");
  assert.equal(document.querySelector('#feedback-form [name=kind]').value, 'topic');
  assert.equal(document.querySelector('#feedback-title').textContent, 'Suggest a topic');

  dialog.close();
  document.querySelector('.nav-feedback').click();
  assert.equal(dialog.open, true);
  assert.equal(document.querySelector('#feedback-title').textContent, 'Send feedback');
  assert.equal(document.querySelector('#feedback-form [name=kind]').value, 'general');
  window.close();
});

const localUrl = process.env.FEEDBACK_TEST_DATABASE_URL;
test('round-trips through Postgres and the triage CLI', { skip: !localUrl && 'set FEEDBACK_TEST_DATABASE_URL to a disposable database' }, async () => {
  const handle = createFeedbackHandler({ env: { FEEDBACK_DATABASE_URL: localUrl }, sendAlert: async () => false, log: silent });
  const marker = `integration ${Date.now()}`;
  assert.equal((await handle(post({ message: marker, page: '/about/' }, { 'X-Forwarded-For': `192.0.2.${Date.now() % 250}` }))).status, 201);
  const output = [];
  const rows = await feedbackCli(['list', '--json'], { env: { FEEDBACK_DATABASE_URL: localUrl }, log: (line) => output.push(line) });
  const row = rows.find((entry) => entry.message === marker);
  assert.ok(row);
  const marked = await feedbackCli(['mark', String(row.id), 'done', '--note', 'test'], { env: { FEEDBACK_DATABASE_URL: localUrl }, log: () => {} });
  assert.equal(marked.status, 'done');
  await withClient(localUrl, (client) => client.query('delete from feedback where id = $1', [row.id]));
});
