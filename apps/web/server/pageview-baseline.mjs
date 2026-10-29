/**
 * Baseline pageview counter: measures how many readers the browser-side GoatCounter script misses.
 *
 * Pages send their path to POST /api/seen (api/seen.js), a first-party request that list-based
 * blockers do not match, and this module forwards it to a second GoatCounter site through the
 * server-side count API (https://www.goatcounter.com/help/api). Nothing identifying is sent:
 * no address, no session, no cookie, no query string. Comparing the two dashboards' pageviews
 * per day gives the fraction of readers the main dashboard sees (docs/operations/analytics.md).
 */

export const URL_VARIABLE = 'GOATCOUNTER_BASELINE_URL';
export const TOKEN_VARIABLE = 'GOATCOUNTER_BASELINE_TOKEN';
export const MAX_PATH_LENGTH = 200;
export const MAX_BODY_LENGTH = 1000;

/** The second GoatCounter site's origin and API token, or null when the baseline is off. */
export function baselineTarget(env = process.env) {
  const raw = env[URL_VARIABLE]?.trim();
  const token = env[TOKEN_VARIABLE]?.trim();
  if (!raw || !token) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== 'https:' || !url.hostname) return null;
    return { endpoint: new URL('/api/v0/count', url.origin).href, token };
  } catch {
    return null;
  }
}

/** A site path as sent by the page script: absolute, no query or fragment, printable ASCII. */
export function parsePath(body) {
  if (typeof body !== 'string') return null;
  const path = body.trim();
  if (!path.startsWith('/') || path.startsWith('//') || path.length > MAX_PATH_LENGTH) return null;
  if (!/^[\x21-\x7e]+$/.test(path) || /[?#"'<>\\]/.test(path)) return null;
  return path;
}

/** Same-origin only: the beacon is sent by the site's own pages. */
export function sameOrigin(request) {
  const origin = request.headers.get('origin');
  if (origin) return origin === new URL(request.url).origin;
  return request.headers.get('sec-fetch-site') === 'same-origin';
}

export function createBaselineHandler({ env = process.env, fetchImpl = fetch, log = console } = {}) {
  let warned = false;
  return async function handle(request) {
    if (request.method !== 'POST') return new Response(null, { status: 405, headers: { Allow: 'POST' } });
    if (!sameOrigin(request)) return new Response(null, { status: 403 });
    const text = await request.text();
    if (text.length > MAX_BODY_LENGTH) return new Response(null, { status: 413 });
    const path = parsePath(text);
    if (!path) return new Response(null, { status: 400 });

    const target = baselineTarget(env);
    if (!target) {
      if (!warned) log.error(`baseline: ${URL_VARIABLE} or ${TOKEN_VARIABLE} is not configured; pageviews are dropped.`);
      warned = true;
      return new Response(null, { status: 204 });
    }
    // The User-Agent lets GoatCounter fill browser and system stats and apply its bot detection;
    // it stores only the derived names, never the header. No address means no session or location.
    const hit = { path };
    const userAgent = request.headers.get('user-agent');
    if (userAgent) hit.user_agent = userAgent.slice(0, 500);
    try {
      const response = await fetchImpl(target.endpoint, {
        method: 'POST',
        headers: { Authorization: `Bearer ${target.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ no_sessions: true, hits: [hit] }),
      });
      // 202 is success. 429 (4 requests/second) and other failures are logged and dropped; the baseline is a floor too.
      if (!response.ok) log.error(`baseline: GoatCounter answered ${response.status} for ${path}`);
    } catch (error) {
      log.error(`baseline: could not reach GoatCounter: ${error?.message ?? error}`);
    }
    return new Response(null, { status: 204 });
  };
}
