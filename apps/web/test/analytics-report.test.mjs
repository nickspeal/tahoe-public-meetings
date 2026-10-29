import assert from 'node:assert/strict';
import test from 'node:test';
import { buildReport, dateRange, main, siteUrls } from '../scripts/analytics-report.mjs';

const env = {
  GOATCOUNTER_API_TOKEN: 'read-token',
  PUBLIC_GOATCOUNTER_URL: 'https://example.goatcounter.com/count',
  GOATCOUNTER_BASELINE_URL: 'https://example-baseline.goatcounter.com/',
};

/** Answers the two stats endpoints for both sites with fixed numbers. */
function fakeApi() {
  const requests = [];
  const fetchImpl = async (href, init) => {
    const url = new URL(href);
    requests.push({ url, auth: init.headers.Authorization });
    const baseline = url.host.startsWith('example-baseline');
    let body;
    if (url.pathname === '/api/v0/stats/total') {
      body = baseline
        ? { total: 40, total_events: 0, stats: [{ day: '2026-10-28', daily: 15 }, { day: '2026-10-29', daily: 25 }] }
        : { total: 36, total_events: 6, stats: [{ day: '2026-10-28', daily: 12 }, { day: '2026-10-29', daily: 24 }] };
    } else if (url.pathname === '/api/v0/stats/hits') {
      body = baseline
        ? { hits: [{ path_id: 1, path: '/', event: false, count: 30 }, { path_id: 2, path: '/meetings/x/', event: false, count: 10 }], more: false }
        : { hits: [{ path_id: 1, path: '/', event: false, count: 24 }, { path_id: 2, path: '/meetings/x/', event: false, count: 6 }, { path_id: 3, path: 'outbound-agenda', event: true, count: 6 }], more: false };
    } else {
      return new Response('not found', { status: 404 });
    }
    return new Response(JSON.stringify(body), { status: 200 });
  };
  return { requests, fetchImpl };
}

test('site URLs come from the token, the public count URL, and the baseline variable', () => {
  assert.deepEqual(siteUrls(env), { token: 'read-token', main: 'https://example.goatcounter.com', baseline: 'https://example-baseline.goatcounter.com' });
  assert.equal(siteUrls({ ...env, GOATCOUNTER_SITE_URL: 'https://other.goatcounter.com/' }).main, 'https://other.goatcounter.com');
  assert.deepEqual(siteUrls({}), { token: null, main: null, baseline: null });
});

test('the date range covers whole UTC days up to the current hour', () => {
  assert.deepEqual(dateRange(7, new Date('2026-10-29T18:42:11Z')), { start: '2026-10-23T00:00:00Z', end: '2026-10-29T18:00:00Z' });
  assert.deepEqual(dateRange(1, new Date('2026-11-01T00:10:00Z')), { start: '2026-11-01T00:00:00Z', end: '2026-11-01T00:00:00Z' });
});

test('the report compares both sites per day and per path and lists events', async () => {
  const { requests, fetchImpl } = fakeApi();
  const lines = [];
  const report = await main(['--days', '2'], { env, fetchImpl, log: (line) => lines.push(line), now: new Date('2026-10-29T20:00:00Z') });
  assert.equal(requests.length, 4);
  assert.ok(requests.every((r) => r.auth === 'Bearer read-token'));
  assert.equal(requests[0].url.searchParams.get('start'), '2026-10-28T00:00:00Z');
  assert.equal(requests[1].url.searchParams.get('group'), 'day');
  assert.deepEqual(report.totals, { main: 30, baseline: 40, tracked: '75%' });
  assert.deepEqual(report.days, [
    { day: '2026-10-28', main: 12, baseline: 15, tracked: '80%' },
    { day: '2026-10-29', main: 24, baseline: 25, tracked: '96%' },
  ]);
  assert.deepEqual(report.pages[0], { path: '/', main: 24, baseline: 30, tracked: '80%' });
  assert.deepEqual(report.events, [{ path: 'outbound-agenda', event: true, count: 6 }]);
  assert.ok(lines.some((line) => line.includes('tracked 75%')));
  assert.ok(lines.some((line) => line.includes('outbound-agenda')));
});

test('without a baseline site only the main site is reported', () => {
  const main = { pageVisits: 10, byDay: { '2026-10-29': 10 }, pages: [{ path: '/', event: false, count: 10 }], events: [] };
  const report = buildReport({ main, baseline: null, range: { start: 'a', end: 'b' } });
  assert.deepEqual(report.totals, { main: 10, baseline: null, tracked: null });
  assert.deepEqual(report.pages, [{ path: '/', main: 10, baseline: null, tracked: null }]);
});
