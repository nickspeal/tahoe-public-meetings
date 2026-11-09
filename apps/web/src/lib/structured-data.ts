import type { Meeting } from '../../../../packages/domain/public';
import { isPlaceholderItem, isRoutineItem } from './routine-items';

/**
 * schema.org JSON-LD for meeting and body pages, following Google's Event structured-data guidelines:
 * only facts the export states (no invented end times, places, or images beyond the site's preview image).
 * SiteLayout wraps the returned nodes in one `@graph`.
 */
export interface BodyRef { name: string; url: string; officialUrl?: string }

type Node = Record<string, unknown>;

const SCHEMA = 'https://schema.org/';
const DESCRIPTION_ITEMS = 3;

function breadcrumbs(homeUrl: string, trail: { name: string; url: string }[]): Node {
  const crumbs = [{ name: 'Home', url: homeUrl }, ...trail];
  return {
    '@type': 'BreadcrumbList',
    itemListElement: crumbs.map((crumb, index) => ({ '@type': 'ListItem', position: index + 1, name: crumb.name, item: crumb.url })),
  };
}

function organization(name: string, officialUrl?: string): Node {
  return { '@type': 'GovernmentOrganization', name, ...(officialUrl ? { url: officialUrl } : {}) };
}

/** "2026-11-03T17:30:00-08:00": the wall-clock time in the meeting's timezone, as Google asks for. */
export function localIso(instant: string, timeZone: string): string | null {
  const date = new Date(instant);
  if (Number.isNaN(date.valueOf())) return null;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'longOffset',
  }).formatToParts(date).map((part) => [part.type, part.value]));
  // longOffset reads "GMT-08:00", or plain "GMT" at UTC.
  const offset = parts.timeZoneName === 'GMT' ? '+00:00' : parts.timeZoneName.replace('GMT', '');
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}${offset}`;
}

/** The local meeting date (YYYY-MM-DD), from the verified date or the start time. */
function localDate(meeting: Meeting): string | null {
  if (meeting.date && /^\d{4}-\d{2}-\d{2}$/.test(meeting.date)) return meeting.date;
  return meeting.startsAt ? localIso(meeting.startsAt, meeting.timezone)?.slice(0, 10) ?? null : null;
}

function dateLabel(day: string) {
  // Noon UTC stays on the same calendar day in every US timezone.
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', month: 'long', day: 'numeric', year: 'numeric' }).format(new Date(`${day}T12:00:00Z`));
}

/**
 * Agency titles ("10 26 2026 Special Closed Session City Council") make poor event names; the body name plus the
 * kind of meeting the title states reads as a resident would search: "South Lake Tahoe City Council Special Closed Session".
 */
export function meetingKind(title: string): string {
  const words = [
    /\bspecial\b/i.test(title) && 'Special',
    /\bjoint\b/i.test(title) && 'Joint',
    /\bclosed session\b/i.test(title) ? 'Closed Session' : /\bworkshop\b/i.test(title) ? 'Workshop' : /\bpublic hearing\b/i.test(title) ? 'Public Hearing' : 'Meeting',
  ];
  return words.filter(Boolean).join(' ');
}

function eventStatus(meeting: Meeting) {
  if (meeting.status === 'canceled' || meeting.change === 'canceled') return `${SCHEMA}EventCancelled`;
  if (meeting.status === 'rescheduled' || meeting.change === 'rescheduled') return `${SCHEMA}EventRescheduled`;
  // Planned meetings come from an official notice, so they are scheduled as far as any source says.
  return `${SCHEMA}EventScheduled`;
}

/** "City Hall, Council Chambers, 1901 Lisa Maloff Way" names the venue first; the whole string is the address. */
function place(location: string): Node {
  const first = location.split(/\s*(?:,|\||\s[–—-]\s)\s*/)[0].trim();
  // "1001 E. 9th Street, Reno" has no venue name to pull out.
  const name = !first || /^\d/.test(first) ? location : first;
  return { '@type': 'Place', name, address: location };
}

/** Agenda text can run to a paragraph; a description lists subjects, so each is cut at a word. */
function clip(text: string, max = 120) {
  if (text.length <= max) return text;
  return `${text.slice(0, max).replace(/\s+\S*$/, '')}…`;
}

function description(meeting: Meeting, fallback: string): string {
  const shown = meeting.items
    .filter((item) => !item.suppressed && !isRoutineItem(item) && !isPlaceholderItem(item))
    .sort((a, b) => a.order - b.order);
  // Items with a topic are the substance; untagged ones are often headings ("New Business") or procedure.
  const tagged = shown.filter((item) => item.topics.length > 0);
  const titles = (tagged.length ? tagged : shown)
    .map((item) => clip((item.shortTitle ?? item.title).replace(/\s+/g, ' ').trim()))
    .filter(Boolean);
  if (titles.length) {
    const more = titles.length > DESCRIPTION_ITEMS ? `, and ${titles.length - DESCRIPTION_ITEMS} more` : '';
    return `Agenda: ${titles.slice(0, DESCRIPTION_ITEMS).join('; ')}${more}.`;
  }
  const previews = (meeting.previews ?? []).map((preview) => preview.subject.trim()).filter(Boolean);
  if (previews.length) return `Planned subjects: ${previews.slice(0, DESCRIPTION_ITEMS).join('; ')}.`;
  return fallback;
}

/**
 * The meeting's name as residents search for it, also its page title and search-result headline:
 * "South Lake Tahoe City Council Special Closed Session – October 26, 2026".
 */
export function meetingSearchName(meeting: Meeting, bodyName: string): string {
  const day = localDate(meeting);
  // A per-record host (agency "Other") is a one-off meeting: its own title and host say more than the catch-all body.
  const baseName = meeting.host ? meeting.title.replace(/\s+/g, ' ').trim() : `${bodyName} ${meetingKind(meeting.title)}`;
  return day ? `${baseName} – ${dateLabel(day)}` : baseName;
}

/**
 * The search-result snippet: when the meeting is, then what is on it.
 * "Tuesday, November 17, 2026 at 5:30 PM. Planned subjects: Ordinance Increasing City Council Compensation; …"
 */
export function meetingSearchDescription(meeting: Meeting, bodyName: string): string {
  const day = localDate(meeting);
  const when = meeting.startsAt
    ? new Intl.DateTimeFormat('en-US', { timeZone: meeting.timezone, weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(meeting.startsAt))
    : day ? new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }).format(new Date(`${day}T12:00:00Z`)) : null;
  const canceled = eventStatus(meeting) === `${SCHEMA}EventCancelled`;
  const lead = [canceled ? 'Canceled' : null, when, meeting.location && !canceled ? meeting.location.replace(/\s+/g, ' ').trim() : null].filter(Boolean).join(' · ');
  const body = description(meeting, `Agenda, official documents, and how to attend or comment on this ${meeting.host ?? bodyName} meeting.`);
  return lead ? `${lead}. ${body}` : body;
}

export function meetingJsonLd(meeting: Meeting, ctx: { url: string; body: BodyRef; homeUrl: string }): Node[] {
  if (meeting.kind === 'forecast') return [];
  const day = localDate(meeting);
  const startDate = meeting.startsAt ? localIso(meeting.startsAt, meeting.timezone) : day;
  const name = meetingSearchName(meeting, ctx.body.name);
  const trail = breadcrumbs(ctx.homeUrl, [{ name: ctx.body.name, url: ctx.body.url }, { name, url: ctx.url }]);
  if (!startDate) return [trail];

  const p = meeting.participation;
  // A join link, or failing that a livestream: either lets a resident attend without being in the room.
  const onlineUrl = p.remoteUrl ?? p.watchUrl ?? null;
  const locations: Node[] = [
    ...(meeting.location ? [place(meeting.location)] : []),
    ...(onlineUrl ? [{ '@type': 'VirtualLocation', url: onlineUrl }] : []),
  ];
  const attendance = meeting.location && onlineUrl ? 'MixedEventAttendanceMode' : onlineUrl ? 'OnlineEventAttendanceMode' : 'OfflineEventAttendanceMode';
  const organizer = meeting.host ? organization(meeting.host) : organization(ctx.body.name, ctx.body.officialUrl);

  const event: Node = {
    '@type': 'Event',
    '@id': `${ctx.url}#event`,
    name,
    url: ctx.url,
    startDate,
    eventStatus: eventStatus(meeting),
    eventAttendanceMode: `${SCHEMA}${attendance}`,
    ...(locations.length ? { location: locations.length === 1 ? locations[0] : locations } : {}),
    organizer,
    description: description(meeting, `Public meeting of the ${meeting.host ?? ctx.body.name}.`),
    isAccessibleForFree: true,
    image: [new URL('/og-image.png', ctx.homeUrl).href],
  };
  return [event, trail];
}

export function bodyJsonLd(ctx: BodyRef & { homeUrl: string }): Node[] {
  const org: Node = {
    '@type': 'GovernmentOrganization',
    '@id': `${ctx.url}#body`,
    name: ctx.name,
    ...(ctx.officialUrl ? { url: ctx.officialUrl } : {}),
    // The hub page on this site is about the body; the body's own site is its url.
    mainEntityOfPage: ctx.url,
  };
  return [org, breadcrumbs(ctx.homeUrl, [{ name: ctx.name, url: ctx.url }])];
}
