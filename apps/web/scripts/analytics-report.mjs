#!/usr/bin/env node
/**
 * Compare the main GoatCounter site with the baseline site (operator or coding agent).
 *
 *   npm run analytics                 # last 7 days
 *   npm run analytics -- --days 30
 *   npm run analytics -- --json
 *
 * Reads GOATCOUNTER_API_TOKEN or GOATCOUNTER_API_KEY (an API key with permission to read statistics), GOATCOUNTER_SITE_URL
 * (the main site, e.g. https://code.goatcounter.com; derived from PUBLIC_GOATCOUNTER_URL when unset),
 * and GOATCOUNTER_BASELINE_URL (optional; without it only the main site is reported).
 * GoatCounter's API reports visits, not pageviews; see docs/operations/analytics.md.
 */
import { parseArgs } from 'node:util';

const USAGE = 'usage: analytics-report.mjs [--days N] [--json]';
/** The API allows 4 requests a second; stay under it. */
const REQUEST_GAP_MS = 300;
const PAGE_LIMIT = 100;
const MAX_PAGES = 5;

export function siteUrls(env) {
  const token = (env.GOATCOUNTER_API_TOKEN ?? env.GOATCOUNTER_API_KEY)?.trim();
  let main = env.GOATCOUNTER_SITE_URL?.trim();
  if (!main && env.PUBLIC_GOATCOUNTER_URL?.trim()) {
    try { main = new URL(env.PUBLIC_GOATCOUNTER_URL.trim()).origin; } catch { main = undefined; }
  }
  const baseline = env.GOATCOUNTER_BASELINE_URL?.trim();
  const origin = (value) => { try { return new URL(value).origin; } catch { return null; } };
  return { token: token || null, main: main ? origin(main) : null, baseline: baseline ? origin(baseline) : null };
}

/** `start` and `end` for the API: whole UTC days, `days` of them, ending at the current hour. */
export function dateRange(days, now = new Date()) {
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), now.getUTCHours()));
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - (days - 1)));
  return { start: start.toISOString().replace(/\.\d{3}Z$/, 'Z'), end: end.toISOString().replace(/\.\d{3}Z$/, 'Z') };
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

export function createClient({ token, fetchImpl = fetch, gap = REQUEST_GAP_MS }) {
  let last = 0;
  return async function get(site, path, params = {}) {
    const url = new URL(`/api/v0/${path}`, site);
    for (const [key, value] of Object.entries(params)) {
      if (Array.isArray(value)) value.forEach((item) => url.searchParams.append(key, String(item)));
      else if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
    const wait = last + gap - Date.now();
    if (wait > 0) await sleep(wait);
    last = Date.now();
    const response = await fetchImpl(url.href, { headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } });
    const text = await response.text();
    if (!response.ok) throw new Error(`${url.pathname} on ${url.host}: HTTP ${response.status} ${text.slice(0, 200)}`);
    return JSON.parse(text);
  };
}

/** Visits per day, total visits, and every path and event with its visits, for one site. */
export async function siteStats(get, site, range) {
  const total = await get(site, 'stats/total', range);
  const byDay = Object.fromEntries((total.stats ?? []).map((day) => [day.day, day.daily ?? 0]));
  const hits = [];
  const exclude = [];
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const result = await get(site, 'stats/hits', { ...range, limit: PAGE_LIMIT, group: 'day', exclude_paths: exclude });
    for (const hit of result.hits ?? []) {
      hits.push({ path: hit.path, event: Boolean(hit.event), count: hit.count ?? 0 });
      exclude.push(hit.path_id);
    }
    if (!result.more) break;
  }
  return {
    total: total.total ?? 0,
    totalEvents: total.total_events ?? 0,
    pageVisits: (total.total ?? 0) - (total.total_events ?? 0),
    byDay,
    pages: hits.filter((hit) => !hit.event).sort((a, b) => b.count - a.count),
    events: hits.filter((hit) => hit.event).sort((a, b) => b.count - a.count),
  };
}

const percent = (part, whole) => (whole > 0 ? `${Math.round((part / whole) * 100)}%` : 'n/a');

/** Combine both sites into one report: per-day and per-path visits with the tracked share. */
export function buildReport({ main, baseline, range }) {
  const days = [...new Set([...Object.keys(main.byDay), ...Object.keys(baseline?.byDay ?? {})])].sort();
  const paths = [...new Set([...main.pages.map((p) => p.path), ...(baseline?.pages ?? []).map((p) => p.path)])];
  const mainByPath = Object.fromEntries(main.pages.map((p) => [p.path, p.count]));
  const baselineByPath = Object.fromEntries((baseline?.pages ?? []).map((p) => [p.path, p.count]));
  return {
    range,
    totals: { main: main.pageVisits, baseline: baseline?.pageVisits ?? null, tracked: baseline ? percent(main.pageVisits, baseline.pageVisits) : null },
    days: days.map((day) => ({ day, main: main.byDay[day] ?? 0, baseline: baseline ? baseline.byDay[day] ?? 0 : null, tracked: baseline ? percent(main.byDay[day] ?? 0, baseline.byDay[day] ?? 0) : null })),
    pages: paths
      .map((path) => ({ path, main: mainByPath[path] ?? 0, baseline: baseline ? baselineByPath[path] ?? 0 : null, tracked: baseline ? percent(mainByPath[path] ?? 0, baselineByPath[path] ?? 0) : null }))
      .sort((a, b) => (b.baseline ?? b.main) - (a.baseline ?? a.main)),
    events: main.events,
  };
}

function table(rows, columns, log) {
  const cells = rows.map((row) => columns.map(([, pick]) => String(pick(row) ?? '')));
  const widths = columns.map(([name], i) => Math.max(name.length, ...cells.map((row) => row[i].length)));
  const line = (values) => values.map((value, i) => (i === 0 ? value.padEnd(widths[i]) : value.padStart(widths[i]))).join('  ');
  log(line(columns.map(([name]) => name)));
  for (const row of cells) log(line(row));
}

export function printReport(report, log = console.log) {
  const hasBaseline = report.totals.baseline !== null;
  log(`GoatCounter visits, ${report.range.start} to ${report.range.end} (UTC). The API counts visits, not pageviews; the "tracked" share is main divided by baseline.`);
  log('');
  log(`Total page visits: main ${report.totals.main}${hasBaseline ? `, baseline ${report.totals.baseline}, tracked ${report.totals.tracked}` : ''}`);
  log('');
  table(report.days, hasBaseline
    ? [['day', (r) => r.day], ['main', (r) => r.main], ['baseline', (r) => r.baseline], ['tracked', (r) => r.tracked]]
    : [['day', (r) => r.day], ['main', (r) => r.main]], log);
  log('');
  log(hasBaseline ? 'Pages (sorted by baseline visits):' : 'Pages:');
  table(report.pages.slice(0, 25), hasBaseline
    ? [['path', (r) => r.path], ['main', (r) => r.main], ['baseline', (r) => r.baseline], ['tracked', (r) => r.tracked]]
    : [['path', (r) => r.path], ['main', (r) => r.main]], log);
  log('');
  log('Events (main site):');
  if (report.events.length === 0) log('  none yet');
  else table(report.events, [['event', (r) => r.path], ['visits', (r) => r.count]], log);
}

export async function main(argv = process.argv.slice(2), { env = process.env, fetchImpl = fetch, log = console.log, now = new Date() } = {}) {
  const { values } = parseArgs({ args: argv, options: { days: { type: 'string', default: '7' }, json: { type: 'boolean', default: false } } });
  const days = Number(values.days);
  if (!Number.isInteger(days) || days < 1 || days > 365) throw new Error(USAGE);
  const sites = siteUrls(env);
  if (!sites.token) throw new Error('Set GOATCOUNTER_API_TOKEN to an API key that can read statistics.');
  if (!sites.main) throw new Error('Set GOATCOUNTER_SITE_URL (or PUBLIC_GOATCOUNTER_URL) to the main GoatCounter site.');
  const get = createClient({ token: sites.token, fetchImpl });
  const range = dateRange(days, now);
  const mainStats = await siteStats(get, sites.main, range);
  const baselineStats = sites.baseline ? await siteStats(get, sites.baseline, range) : null;
  const report = buildReport({ main: mainStats, baseline: baselineStats, range });
  if (values.json) log(JSON.stringify(report, null, 2));
  else printReport(report, log);
  return report;
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
  });
}
