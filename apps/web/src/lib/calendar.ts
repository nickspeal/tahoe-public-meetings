import type { Meeting } from '../../../../packages/domain/public';

const DATE_TIME_WITH_ZONE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/i;

function instant(value: string | null | undefined): Date | null {
  if (!value || !DATE_TIME_WITH_ZONE.test(value)) return null;
  const parsed = new Date(value);
  return Number.isFinite(parsed.valueOf()) ? parsed : null;
}

function utcTimestamp(value: string | Date): string {
  const date = value instanceof Date ? value : instant(value);
  if (!date || !Number.isFinite(date.valueOf())) throw new Error('Calendar timestamps must include a valid time zone.');
  return date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

export function isCanceled(meeting: Meeting): boolean {
  return meeting.status === 'canceled' || meeting.change === 'canceled';
}

/** A date-only listing has no confirmed event start and is not exported as all-day; a canceled meeting is not exported at all. */
export function canExportMeeting(meeting: Meeting): boolean {
  return !isCanceled(meeting) && Boolean(instant(meeting.startsAt) && instant(meeting.updatedAt));
}

export function calendarHref(meeting: Meeting): string {
  return `/calendar/${encodeURIComponent(meeting.agencyId)}/${encodeURIComponent(meeting.sourceId)}.ics`;
}

function escapeText(value: string): string {
  return value
    .replace(/\r\n|\r|\n/g, '\n')
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, '')
    .replace(/\\/g, '\\\\')
    .replace(/\n/g, '\\n')
    .replace(/,/g, '\\,')
    .replace(/;/g, '\\;');
}

function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return null;
    return url.toString().replace(/[\r\n\u0000-\u001F\u007F]/g, '');
  } catch {
    return null;
  }
}

/** Fold at Unicode code-point boundaries, counting UTF-8 octets as RFC 5545 requires. */
function foldLine(line: string): string {
  const output: string[] = [];
  const encoder = new TextEncoder();
  let chunk = '';
  let octets = 0;
  let limit = 75;
  for (const character of line) {
    const size = encoder.encode(character).length;
    if (octets + size > limit && chunk) {
      output.push(chunk);
      chunk = ' ';
      octets = 1;
      limit = 75;
    }
    chunk += character;
    octets += size;
  }
  output.push(chunk);
  return output.join('\r\n');
}

function uidFor(meeting: Meeting): string {
  const agency = encodeURIComponent(meeting.agencyId);
  const source = encodeURIComponent(meeting.sourceId);
  return `${agency}.${source}@tahoe-public-meetings.local`;
}

const FALLBACK_MINUTES = 180;

/** Every event has an end. Estimates are labelled in the description; a missing duration falls back long. */
function endFor(meeting: Meeting, start: Date): Date {
  const minutes = meeting.duration?.minutes;
  const valid = typeof minutes === 'number' && Number.isFinite(minutes) && minutes > 0;
  return new Date(start.valueOf() + (valid ? minutes : FALLBACK_MINUTES) * 60_000);
}

interface EventFields {
  start: Date;
  end: Date;
  title: string;
  location: string | null;
  officialUrl: string | null;
  description: string;
}

function eventFields(meeting: Meeting): EventFields {
  if (!canExportMeeting(meeting)) throw new Error('A confirmed start time and source update timestamp are required.');
  const start = instant(meeting.startsAt)!;
  const officialUrl = safeHttpUrl(meeting.officialUrl);
  const details: string[] = [];
  // A planned meeting comes from a notice or planning calendar, not an agenda (IDEA-018 R6, decision 9).
  if (meeting.planned) details.push('Subject to change.');
  if (meeting.host) details.push(`Hosted by: ${meeting.host}`);
  if (officialUrl) details.push(`Official meeting page: ${officialUrl}`);
  if (meeting.participation.instructions) details.push(`Public comment: ${meeting.participation.instructions}`);
  const participationUrl = safeHttpUrl(meeting.participation.url);
  if (participationUrl) details.push(`Participation details: ${participationUrl}`);
  const remoteUrl = safeHttpUrl(meeting.participation.remoteUrl);
  if (remoteUrl) details.push(`Remote access: ${remoteUrl}`);
  if (meeting.participation.phone) details.push(`By phone: ${meeting.participation.phone}`);
  const watchUrl = safeHttpUrl(meeting.participation.watchUrl ?? null);
  if (watchUrl) details.push(`Watch live: ${watchUrl}`);
  if (meeting.participation.email) details.push(`Written comment: ${meeting.participation.email}`);
  if (meeting.participation.deadline) details.push(`Public comment deadline: ${meeting.participation.deadline}`);
  if (!meeting.duration || meeting.duration.source === 'estimated') {
    details.push('Estimated length; agencies do not publish an end time.');
  }
  return {
    start,
    end: endFor(meeting, start),
    title: meeting.title,
    location: meeting.location || null,
    officialUrl,
    description: details.join('\n'),
  };
}

export function buildCalendarEvent(meeting: Meeting): string {
  const event = eventFields(meeting);
  const modified = utcTimestamp(meeting.updatedAt);
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Tahoe Public Meetings//Single Meeting Export//EN',
    'CALSCALE:GREGORIAN',
    'BEGIN:VEVENT',
    `UID:${escapeText(uidFor(meeting))}`,
    `DTSTAMP:${modified}`,
    `LAST-MODIFIED:${modified}`,
    `DTSTART:${utcTimestamp(event.start)}`,
    `DTEND:${utcTimestamp(event.end)}`,
    `SUMMARY:${escapeText(event.title)}`,
  ];
  if (event.officialUrl) lines.push(`URL:${event.officialUrl}`);
  if (event.location) lines.push(`LOCATION:${escapeText(event.location)}`);
  if (event.description) lines.push(`DESCRIPTION:${escapeText(event.description)}`);
  if (meeting.status === 'scheduled' || meeting.status === 'rescheduled') lines.push('STATUS:CONFIRMED');
  lines.push('END:VEVENT', 'END:VCALENDAR');
  return `${lines.map(foldLine).join('\r\n')}\r\n`;
}

/** Web calendars take the event in the URL; a long description is cut so the link stays a safe length. */
const MAX_LINK_DESCRIPTION = 1500;

function linkDescription(event: EventFields): string {
  if (event.description.length <= MAX_LINK_DESCRIPTION) return event.description;
  const cut = `${event.description.slice(0, MAX_LINK_DESCRIPTION).trimEnd()}…`;
  return event.officialUrl ? `${cut}\nFull details: ${event.officialUrl}` : cut;
}

/** Google Calendar's prefilled "new event" link; the person still saves it themselves. */
export function googleCalendarHref(meeting: Meeting): string {
  const event = eventFields(meeting);
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: event.title,
    dates: `${utcTimestamp(event.start)}/${utcTimestamp(event.end)}`,
    details: linkDescription(event),
  });
  if (event.location) params.set('location', event.location);
  return `https://calendar.google.com/calendar/render?${params}`;
}

/** Outlook.com's prefilled compose link for personal Microsoft accounts. */
export function outlookCalendarHref(meeting: Meeting): string {
  const event = eventFields(meeting);
  const params = new URLSearchParams({
    path: '/calendar/action/compose',
    rru: 'addevent',
    subject: event.title,
    startdt: event.start.toISOString().replace(/\.\d{3}Z$/, 'Z'),
    enddt: event.end.toISOString().replace(/\.\d{3}Z$/, 'Z'),
    body: linkDescription(event),
  });
  if (event.location) params.set('location', event.location);
  return `https://outlook.live.com/calendar/0/action/compose?${params}`;
}
