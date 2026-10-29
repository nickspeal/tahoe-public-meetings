// Usage analytics with hosted GoatCounter (decision 0029). The build emits the count script only when
// PUBLIC_GOATCOUNTER_URL is set, which is the Vercel Production environment alone; local, preview, and test
// builds count nothing. The click attributes below are always emitted and stay inert without the script.

/**
 * count.js is served from this site (`public/goatcounter.js`, ISC licensed) rather than from gc.zgo.at, which
 * list-based blockers stop. It is a verbatim copy of the pinned upstream release; `integrity` is upstream's SRI
 * hash from https://www.goatcounter.com/help/countjs-versions, and a test checks the copy still matches it, so
 * an update means replacing the file and this hash together.
 */
export const GOATCOUNTER_SCRIPT = {
  src: '/goatcounter.js',
  upstream: 'https://gc.zgo.at/count.v5.js',
  integrity: 'sha384-atnOLvQb9t+jTSipvd75X2yginT4PjVbqDdlJAmxMm+wYElFmeR6EmLP5bYeoRVQ',
} as const;

/**
 * The count endpoint when analytics is configured; only an https:// URL counts as configured.
 * Typed loosely so `import.meta.env`, whose DEV/PROD/SSR flags are booleans, can be passed as is.
 */
export function analyticsConfig(env: Record<string, unknown>): { endpoint: string } | null {
  const raw = env.PUBLIC_GOATCOUNTER_URL;
  const value = typeof raw === 'string' ? raw.trim() : '';
  if (!value) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  return url.protocol === 'https:' && url.hostname ? { endpoint: url.href } : null;
}

export type OutboundEvent =
  | 'outbound-agenda'
  | 'outbound-staff-report'
  | 'outbound-attachment'
  | 'outbound-minutes'
  | 'outbound-recording'
  | 'outbound-agency'
  | 'outbound-comment-period'
  | 'calendar-ics'
  | 'calendar-google'
  | 'calendar-outlook';

/**
 * Attributes count.js reads from a clicked link: the event name, the page it was clicked on, and the agency.
 * Spread onto an anchor: `<a href={url} {...clickEvent('outbound-agenda', Astro.url.pathname, meeting.agencyId)}>`.
 */
export function clickEvent(event: OutboundEvent, referrer: string, agencyId?: string | null) {
  return {
    'data-goatcounter-click': event,
    'data-goatcounter-referrer': referrer,
    ...(agencyId ? { 'data-goatcounter-title': agencyId } : {}),
  };
}

/** A meeting's official document by what its title says it is: minutes or a synopsis, an agenda or packet, or other material. */
export function documentEvent(title: string): OutboundEvent {
  if (/minutes|synopsis/i.test(title)) return 'outbound-minutes';
  if (/agenda|packet/i.test(title) && !/supplement/i.test(title)) return 'outbound-agenda';
  return 'outbound-attachment';
}
