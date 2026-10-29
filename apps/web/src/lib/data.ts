import type { AgendaItem, Meeting, MeetingPreview, PublicCommentPeriod, PublicData } from '../../../../packages/domain/public';
import topicCatalog from '../../../../packages/domain/topics.json';
import { isRoutineItem } from './routine-items';

const publicDataModules = import.meta.glob('../data/public.json', { eager: true, import: 'default' }) as Record<string, PublicData>;
const loadedData = publicDataModules['../data/public.json'];

const emptyData: PublicData = {
  schemaVersion: 2,
  generatedAt: new Date().toISOString(),
  mode: 'fixture',
  coverageNote: 'No published meeting records are available yet.',
  agencies: [],
  topics: topicCatalog.map(({ id, name, description }) => ({ id, name, description })),
  meetings: [],
};

export const data: PublicData = loadedData ?? emptyData;

export const commentPeriods = data.commentPeriods ?? [];
export const topics = data.topics;
export const agencies = data.agencies;
/** Stored agency records. Only these get meeting pages, calendar files, and coverage counts. */
export const meetings = data.meetings.filter((meeting) => !isForecast(meeting));
/** Expected dates derived from agency schedules at export time; shown on the timeline only when opted in. */
export const forecastMeetings = data.meetings.filter(isForecast);

export function isForecast(meeting: Meeting) {
  return meeting.kind === 'forecast';
}

export function agencyFor(meeting: Meeting) {
  return agencies.find((agency) => agency.id === meeting.agencyId);
}

/** Who holds a meeting, as shown to readers: its host when the record names one (Other, co-hosted meetings), else the agency. */
export function meetingAgencyLabel(meeting: Meeting) {
  return meeting.host || agencyFor(meeting)?.name;
}

/** Who runs a comment period, as shown to readers: its host when the record names one, else the agency. */
export function periodAgencyLabel(period: PublicCommentPeriod) {
  return period.host || agencies.find((agency) => agency.id === period.agencyId)?.name;
}

/** Agenda items shown to readers, in agenda order: not suppressed, and not routine meeting mechanics. */
export function allItems(meeting: Meeting): AgendaItem[] {
  return meeting.items.filter((item) => !item.suppressed && !isRoutineItem(item)).sort((a, b) => a.order - b.order);
}

/** "Not before 11:00 AM" when the agenda says an item will not be heard before a stated time. */
export function timeCertainLabel(item: AgendaItem): string | null {
  return item.timeCertain ? `Not before ${item.timeCertain.time}` : null;
}

export function meetingDate(meeting: Meeting) {
  return meeting.startsAt ? new Date(meeting.startsAt) : null;
}

export function formatDate(value: string | Date | null, options: Intl.DateTimeFormatOptions = {}) {
  if (!value) return 'Date not yet published';
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.valueOf())) return 'Date not yet published';
  const fields = ['weekday', 'era', 'year', 'month', 'day', 'hour', 'minute', 'second', 'timeZoneName', 'dateStyle', 'timeStyle'];
  const hasRequestedFields = fields.some((field) => field in options);
  return new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Los_Angeles',
    ...(hasRequestedFields ? {} : { month: 'long', day: 'numeric', year: 'numeric' }),
    ...options,
  }).format(date);
}

export function meetingDateValue(meeting: Meeting) {
  return meeting.startsAt ?? meeting.date ?? null;
}

export function meetingDateLabel(meeting: Meeting, options: Intl.DateTimeFormatOptions = {}) {
  const value = meetingDateValue(meeting);
  if (!value) return 'Date not yet published';
  const date = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00-08:00`) : value;
  return formatDate(date, options);
}

export function meetingHref(meeting: Meeting) {
  // Astro's static route params use the literal colon. Encoding it into %3A
  // creates a path that the dev/preview server does not resolve consistently.
  const encodedId = encodeURIComponent(meeting.id).replaceAll('%3A', ':');
  return `/meetings/${encodedId}/`;
}

export function commentPeriodHref(period: PublicCommentPeriod) {
  // Stored IDs are `${agencyId}:${notice slug}`; the page path splits them like calendar files do.
  const prefix = `${period.agencyId}:`;
  const slug = period.id.startsWith(prefix) ? period.id.slice(prefix.length) : period.id;
  return `/comment-periods/${encodeURIComponent(period.agencyId)}/${encodeURIComponent(slug)}/`;
}

export type CommentPeriodStatus = 'upcoming' | 'open' | 'closed';

const pacificDay = (date: Date) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);

/** Evaluated at build time: the static site changes a period's status on the next rebuild after its deadline. */
export function commentPeriodStatus(period: PublicCommentPeriod, now = new Date()): CommentPeriodStatus {
  const today = pacificDay(now);
  const closed = period.closesAt ? new Date(period.closesAt).valueOf() < now.valueOf() : period.closesOn < today;
  if (closed) return 'closed';
  return period.opensOn && period.opensOn > today ? 'upcoming' : 'open';
}

const commentPeriodDeadline = (period: PublicCommentPeriod) => period.closesAt ? new Date(period.closesAt).valueOf() : new Date(`${period.closesOn}T23:59:59-08:00`).valueOf();

/** Open and upcoming periods, soonest deadline first. */
export function currentCommentPeriods(now = new Date()) {
  return commentPeriods.filter((period) => commentPeriodStatus(period, now) !== 'closed').sort((a, b) => commentPeriodDeadline(a) - commentPeriodDeadline(b) || a.id.localeCompare(b.id));
}

/** Closed periods, most recently closed first. */
export function pastCommentPeriods(now = new Date()) {
  return commentPeriods.filter((period) => commentPeriodStatus(period, now) === 'closed').sort((a, b) => commentPeriodDeadline(b) - commentPeriodDeadline(a) || a.id.localeCompare(b.id));
}

/** Meetings linked to a comment period, in date order; undated ones last. IDs not in the export are skipped. */
export function commentPeriodMeetings(period: PublicCommentPeriod) {
  const ids = new Set(period.meetingIds ?? []);
  const when = (meeting: Meeting) => { const value = meetingDateValue(meeting); return value ? new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T12:00:00-08:00` : value).valueOf() : Number.MAX_SAFE_INTEGER; };
  return data.meetings.filter((meeting) => ids.has(meeting.id)).sort((a, b) => when(a) - when(b) || a.id.localeCompare(b.id));
}

/** A calendar date from a notice (YYYY-MM-DD), read as a Pacific day. */
export function noticeDayLabel(value: string, options: Intl.DateTimeFormatOptions = { month: 'long', day: 'numeric', year: 'numeric' }) {
  return formatDate(`${value}T12:00:00-08:00`, options);
}

export function commentDeadlineLabel(period: PublicCommentPeriod, options: Intl.DateTimeFormatOptions = { month: 'long', day: 'numeric', year: 'numeric' }) {
  return period.closesAt ? formatDate(period.closesAt, { ...options, hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }) : noticeDayLabel(period.closesOn, options);
}

export function topicHref(topicId: string) {
  return `/topics/${encodeURIComponent(topicId)}/`;
}

export function topicName(topicId: string) {
  return topics.find((topic) => topic.id === topicId)?.name ?? topicId.replaceAll('-', ' ');
}

export function sourceDoc(meeting: Meeting, documentId: string) {
  return meeting.documents.find((document) => document.id === documentId);
}

export { citationHref, isPdfDocument, itemSourceLink, staffReportLink } from './source-links';

/** Parser outcomes that a reader may want explained next to the agenda history, in brief. */
const AGENDA_SOURCE_NOTES: [RegExp, string][] = [
  [/unvalidated structure/i, 'One agenda document could not be read reliably, so items are not listed here.'],
  [/differing item sequences/i, 'The agenda documents list different items, so items are not listed here.'],
];

export function agendaSourceNote(meeting: Pick<Meeting, 'warnings'>) {
  for (const warning of meeting.warnings) {
    const note = AGENDA_SOURCE_NOTES.find(([pattern]) => pattern.test(warning));
    if (note) return note[1];
  }
  return null;
}

export function residentWarning(value: string) {
  if (/playback timestamps/i.test(value)) return 'A recording start time may differ from the scheduled meeting time; check the official agenda.';
  if (/CivicClerk.*wall.clock/i.test(value)) return 'The scheduled start time has not been verified; check the official agenda.';
  if (/HTML\/text source retained/i.test(value)) return 'This source is a webpage or text document; citations refer to source sections rather than PDF pages.';
  // Parser and discovery outcomes, and a missing agenda, are explained by the agenda section itself (see agendaAvailability).
  if (/No parser exists|review required|agenda items require (?:source )?review|No complete .* agenda recognized/i.test(value)) return '';
  if (/No agenda document is linked|did not link an agenda/i.test(value)) return '';
  // Shown briefly in the agenda history card instead (see agendaSourceNote).
  if (AGENDA_SOURCE_NOTES.some(([pattern]) => pattern.test(value))) return '';
  if (/A source PDF changed/i.test(value)) return 'Source documents changed; some agenda details still need to be refreshed.';

  const retainedPages = value.match(/Text retained for (\d+) of (\d+) source pages/i);
  if (retainedPages) return `Agenda text covers ${retainedPages[1]} of ${retainedPages[2]} source pages; open the official document for full context.`;
  if (/only selected source pages are available as text/i.test(value)) return 'Agenda text only; open the packet for supporting reports.';
  if (/original fetch time is unknown/i.test(value)) return 'Original source capture time is unknown.';
  if (/links a Municode HTML agenda/i.test(value)) return 'Agenda is available on the official meeting page; agenda items are not listed here yet.';
  if (/marks this regular meeting canceled/i.test(value)) return 'Canceled; the official event page lists replacement meetings at 4:00 p.m. and 5:30 p.m.';
  if (/extract|scanned page|ocr|text.*(?:failed|unavailable)/i.test(value)) return 'Some pages could not be read; open the official document.';
  return value.replace(/\bfixture\b/gi, 'source record').replace(/\bsample\b/gi, 'source record');
}

export type AgendaAvailability = {
  /** items: listed here; posted: official agenda captured but items not listed; not-posted: no agenda yet;
   *  elsewhere: past meeting whose agenda lives only on the agency site; canceled;
   *  notice: a one-off meeting (open house, workshop) whose notice is all it will ever publish. */
  kind: 'items' | 'posted' | 'not-posted' | 'elsewhere' | 'canceled' | 'notice';
  /** Short meeting-card text; empty when the missing item count (or the card's status pill) already says enough. */
  label: string;
  heading: string;
  body: string;
  href?: string;
  linkText?: string;
};

export function agendaAvailability(meeting: Meeting): AgendaAvailability {
  const count = allItems(meeting).length;
  if (count) {
    const label = `${count} agenda ${count === 1 ? 'item' : 'items'}`;
    return { kind: 'items', label, heading: label, body: '' };
  }
  if (meeting.status === 'canceled') {
    return { kind: 'canceled', label: 'Meeting canceled', heading: 'This meeting was canceled',
      body: 'No agenda items are listed for a canceled meeting. The official meeting page lists any replacement meeting.',
      href: meeting.officialUrl, linkText: 'Official meeting page' };
  }
  // One-off meetings (configured or read from a notice) never get an agenda: their official URL is the notice
  // itself, so the notice counts as the posted document (IDEA-018 R3). Every one-off ID starts with "one-off-",
  // which one_offs.py enforces; testing the ID rather than a warning's wording keeps this stable if the note changes.
  if (isOneOff(meeting) && !meeting.documents.length) {
    return { kind: 'notice', label: 'Meeting notice posted', heading: 'Meeting notice',
      body: 'The notice has the details for this meeting.', href: meeting.officialUrl, linkText: 'Open the notice' };
  }
  const agenda = meeting.documents.find((document) => /agenda|packet/i.test(document.title) && !/supplement/i.test(document.title));
  if (agenda) {
    return { kind: 'posted', label: '', heading: 'Agenda posted; items aren’t listed here yet',
      body: 'The agency has published an agenda, but its items haven’t been extracted for this page. Open the official agenda to see what’s on it.',
      href: agenda.url, linkText: 'Open the official agenda' };
  }
  const other = meeting.documents[0];
  if (other) {
    // Only minutes, a synopsis, or supplemental material were captured.
    return { kind: 'elsewhere', label: 'Agenda on agency website', heading: 'Agenda not available here',
      body: 'This meeting’s agenda wasn’t captured for this page. The official document below and the meeting page have more detail.',
      href: other.url, linkText: other.title.replace(/^Official\s+/i, 'Open the official ') };
  }
  const value = meetingDateValue(meeting);
  const upcoming = !value || localDateKey(value) >= localDateKey(new Date().toISOString());
  // Source notes record when the agency page has an agenda that could not be captured (blocked or not yet parsed).
  const agendaElsewhere = meeting.warnings.some((warning) => !/\bno agenda|not posted|did not link/i.test(warning) &&
    /\b(?:includes|links|lists|verified)\b[^.]*\bagenda\b|\bagenda\b[^.]*\b(?:is linked|was verified)\b/i.test(warning));
  if (agendaElsewhere) {
    return { kind: 'elsewhere', label: 'Agenda on agency website', heading: 'Agenda available on the agency website',
      body: 'Open the official meeting page for the agenda.', href: meeting.officialUrl, linkText: 'Official meeting page' };
  }
  if (upcoming) {
    return { kind: 'not-posted', label: '', heading: 'Agenda not posted yet',
      body: 'Agencies usually publish agendas a few days before a meeting. Check back closer to the meeting date, or follow the official meeting page.',
      href: meeting.officialUrl, linkText: 'Official meeting page' };
  }
  return { kind: 'elsewhere', label: 'Agenda on agency website', heading: 'Agenda not available here',
    body: 'This meeting’s agenda couldn’t be retrieved for this page. The official meeting page has the agenda and any minutes.',
    href: meeting.officialUrl, linkText: 'Official meeting page' };
}

/** A meeting the timeline lists apart from the regular bodies' meetings: an open house, workshop, or similar event. */
export function isOneOff(meeting: Meeting) {
  return meeting.sourceId.startsWith('one-off-');
}

/** Subjects an official notice or planning calendar says are planned for this meeting; the export sends them only before the agenda is posted. */
export function meetingPreviews(meeting: Meeting): MeetingPreview[] {
  return meeting.previews ?? [];
}

export function hasPreviews(meeting: Meeting) {
  return meetingPreviews(meeting).length > 0;
}

/** Expected meetings and upcoming ones whose agency has not posted an agenda yet: they carry the "Expected · no agenda yet" pill. */
export function agendaNotPosted(meeting: Meeting) {
  return isForecast(meeting) || agendaAvailability(meeting).kind === 'not-posted';
}

/**
 * Meetings the timeline keeps behind the "upcoming meetings without a posted agenda" reveal (decision 0022).
 * A meeting with previews is shown by default and keeps its pill (IDEA-018 decision 1), so it is not awaiting here.
 */
export function awaitingAgenda(meeting: Meeting) {
  return agendaNotPosted(meeting) && !hasPreviews(meeting);
}

/** Previews whose topics include the selected topic, or all of them with no topic. */
export function previewsForTopic(meeting: Meeting, topicId = '') {
  return meetingPreviews(meeting).filter((preview) => !topicId || preview.topics.includes(topicId));
}

/** "From the agenda planning calendar updated September 30, 2026" or "From the West Shore Trail Notice of Preparation". */
export function previewSourceLabel(source: MeetingPreview['source']) {
  return source.kind === 'planning-calendar' ? `From the agenda planning calendar updated ${noticeDayLabel(source.publishedOn)}` : `From the ${source.title}`;
}

export function meetingAgendaLabel(meeting: Meeting) {
  return agendaAvailability(meeting).label;
}

function localDateKey(value: string) {
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value));
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? '';
  return `${part('year')}-${part('month')}-${part('day')}`;
}

/** A label only for a meeting that is not going ahead as listed; ordinary meetings carry none. */
/** Status pill shared by expected meetings and upcoming ones still waiting on their agenda. */
export const awaitingAgendaLabel = 'Expected · no agenda yet';

export function meetingStateLabel(meeting: Meeting) {
  if (meeting.status === 'canceled') return 'Canceled';
  if (meeting.status === 'rescheduled') return 'Rescheduled';
  if (agendaNotPosted(meeting)) return awaitingAgendaLabel;
  return null;
}

export function sourceCheckLabel(value: string | null) {
  if (!value) return 'Not checked live';
  return data.mode === 'fixture' ? `Source capture: ${formatDate(value)}` : `Last checked ${formatDate(value)}`;
}

export type AgendaHistoryEntry = { at: string; text: string; change: 'added' | 'revised' };

/**
 * A meeting's document history as one line per day and kind of change, oldest first,
 * or null for an export written before history was recorded.
 */
export function agendaHistory(meeting: Meeting): AgendaHistoryEntry[] | null {
  if (!meeting.history) return null;
  const groups = new Map<string, { at: string; change: 'added' | 'revised'; documents: string[] }>();
  for (const event of meeting.history) {
    const key = `${localDateKey(event.at)}:${event.change}`;
    const group = groups.get(key) ?? { at: event.at, change: event.change, documents: [] };
    if (!group.documents.includes(event.document)) group.documents.push(event.document);
    groups.set(key, group);
  }
  return [...groups.values()].map(({ at, change, documents }) => ({
    at, change, text: `${listTitles(documents)} ${change === 'added' ? 'found' : 'changed'}`,
  }));
}

function listTitles(titles: string[]) {
  if (titles.length < 3) return titles.join(' and ');
  return `${titles.slice(0, -1).join(', ')}, and ${titles.at(-1)}`;
}

export function agendaHistoryHeading(entries: AgendaHistoryEntry[]) {
  if (!entries.length) return 'No agenda found yet';
  const latest = formatDate(entries.at(-1)!.at);
  return entries.length === 1 ? `No changes since ${latest}` : `Last changed ${latest}`;
}

export function documentFetchLabel(value: string) {
  if (!value) return 'Capture date unknown';
  return data.mode === 'fixture' ? `source captured ${formatDate(value)}` : `checked ${formatDate(value)}`;
}

export function documentStatusLabel(status: string) {
  const labels: Record<string, string> = {
    extracted: 'Text available',
    complete: 'Text available',
    fetched: 'Source available',
    pending: 'Agenda text not yet available',
    failed: 'Document text unavailable',
    extraction_failed: 'Document text unavailable',
    error: 'Source could not be read',
    unavailable: 'Source unavailable',
  };
  return labels[status.toLowerCase()] ?? 'Source status unavailable';
}

export function agencySourceLabel(agency: PublicData['agencies'][number]) {
  if (agency.status === 'error') return 'Could not verify latest source';
  if (agency.status === 'never_checked') return data.mode === 'fixture' ? 'Not checked live' : 'Not checked yet';
  if (agency.status === 'ok' && agency.lastCheckedAt) {
    return data.mode === 'fixture'
      ? `Source capture: ${formatDate(agency.lastCheckedAt)}`
      : `Checked ${formatDate(agency.lastCheckedAt)}`;
  }
  return agency.status === 'ok' ? 'Source check complete' : 'Source status unavailable';
}


/** Meeting notice tags and preview topics also apply before an agenda has been posted. */
export function meetingTopicIds(meeting: Meeting): string[] {
  return [...new Set([...(meeting.topics ?? []), ...allItems(meeting).flatMap((item) => item.topics), ...meetingPreviews(meeting).flatMap((preview) => preview.topics)])];
}
