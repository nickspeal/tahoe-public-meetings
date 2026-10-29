import assert from 'node:assert/strict';
import test from 'node:test';
import { baselineTarget, createBaselineHandler, parsePath, sameOrigin } from '../server/pageview-baseline.mjs';

const env = { GOATCOUNTER_BASELINE_URL: 'https://example-baseline.goatcounter.com', GOATCOUNTER_BASELINE_TOKEN: 'secret-token' };
const silent = { error() {} };

function beacon(body, headers = {}) {
  return new Request('https://tahoepublicmeetings.org/api/seen', {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain;charset=UTF-8', Origin: 'https://tahoepublicmeetings.org', 'User-Agent': 'Mozilla/5.0 (test)', ...headers },
    body,
  });
}

/** Records what the handler sends to GoatCounter and answers with the given status. */
function fakeGoatCounter(status = 202) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init, body: JSON.parse(init.body) });
    return new Response(null, { status });
  };
  return { calls, fetchImpl };
}

test('the baseline target needs both an https site URL and a token', () => {
  assert.deepEqual(baselineTarget(env), { endpoint: 'https://example-baseline.goatcounter.com/api/v0/count', token: 'secret-token' });
  assert.equal(baselineTarget({ ...env, GOATCOUNTER_BASELINE_URL: 'https://example-baseline.goatcounter.com/' }).endpoint, 'https://example-baseline.goatcounter.com/api/v0/count');
  assert.equal(baselineTarget({}), null);
  assert.equal(baselineTarget({ GOATCOUNTER_BASELINE_URL: env.GOATCOUNTER_BASELINE_URL }), null);
  assert.equal(baselineTarget({ ...env, GOATCOUNTER_BASELINE_URL: 'http://example-baseline.goatcounter.com' }), null);
  assert.equal(baselineTarget({ ...env, GOATCOUNTER_BASELINE_URL: 'not a url' }), null);
});

test('only plain site paths are accepted', () => {
  assert.equal(parsePath('/meetings/slt:2026-11-03/'), '/meetings/slt:2026-11-03/');
  assert.equal(parsePath('/topics/housing/'), '/topics/housing/');
  assert.equal(parsePath('/'), '/');
  for (const bad of ['', 'meetings', '//evil.example', '/?topic=housing', '/#x', '/a b', '/a"b', '/<script>', '/' + 'x'.repeat(200), 42, null]) {
    assert.equal(parsePath(bad), null, `${JSON.stringify(bad)} should be rejected`);
  }
});

test('a page view is forwarded to the baseline site without sessions or addresses', async () => {
  const { calls, fetchImpl } = fakeGoatCounter();
  const handle = createBaselineHandler({ env, fetchImpl, log: silent });
  const response = await handle(beacon('/meetings/trpa:2026-11-05/', { 'X-Forwarded-For': '203.0.113.9' }));
  assert.equal(response.status, 204);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://example-baseline.goatcounter.com/api/v0/count');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer secret-token');
  assert.deepEqual(calls[0].body, { no_sessions: true, hits: [{ path: '/meetings/trpa:2026-11-05/', user_agent: 'Mozilla/5.0 (test)' }] });
  assert.ok(!JSON.stringify(calls[0].body).includes('203.0.113.9'), 'the client address is never forwarded');
});

test('cross-origin, malformed, oversized, and non-POST requests are refused without reaching GoatCounter', async () => {
  const { calls, fetchImpl } = fakeGoatCounter();
  const handle = createBaselineHandler({ env, fetchImpl, log: silent });
  assert.equal((await handle(beacon('/', { Origin: 'https://evil.example' }))).status, 403);
  assert.equal((await handle(beacon('/?utm=1'))).status, 400);
  assert.equal((await handle(beacon('/' + 'x'.repeat(2000)))).status, 413);
  assert.equal((await handle(new Request('https://tahoepublicmeetings.org/api/seen'))).status, 405);
  assert.equal(calls.length, 0);
  assert.ok(sameOrigin(new Request('https://tahoepublicmeetings.org/api/seen', { method: 'POST', headers: { 'Sec-Fetch-Site': 'same-origin' } })), 'Sec-Fetch-Site stands in for a missing Origin');
  assert.ok(!sameOrigin(new Request('https://tahoepublicmeetings.org/api/seen', { method: 'POST' })), 'no origin information is refused');
});

test('an unconfigured or failing baseline drops the view and still answers 204', async () => {
  const errors = [];
  const log = { error: (message) => errors.push(message) };
  const { calls, fetchImpl } = fakeGoatCounter();
  const unconfigured = createBaselineHandler({ env: {}, fetchImpl, log });
  assert.equal((await unconfigured(beacon('/'))).status, 204);
  assert.equal((await unconfigured(beacon('/'))).status, 204);
  assert.equal(calls.length, 0);
  assert.equal(errors.length, 1, 'the missing configuration is logged once');

  const limited = createBaselineHandler({ env, fetchImpl: fakeGoatCounter(429).fetchImpl, log });
  assert.equal((await limited(beacon('/'))).status, 204);
  const down = createBaselineHandler({ env, fetchImpl: async () => { throw new Error('ECONNRESET'); }, log });
  assert.equal((await down(beacon('/'))).status, 204);
  assert.equal(errors.length, 3);
});
