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

/** A date-only listing has no confirmed event start and is not exported as all-day. */
export function canExportMeeting(meeting: Meeting): boolean {
  return Boolean(instant(meeting.startsAt) && instant(meeting.updatedAt));
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

export function buildCalendarEvent(meeting: Meeting): string {
  if (!canExportMeeting(meeting)) throw new Error('A confirmed start time and source update timestamp are required.');
  const start = utcTimestamp(meeting.startsAt!);
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
    `DTSTART:${start}`,
    `DTEND:${utcTimestamp(endFor(meeting, instant(meeting.startsAt)!))}`,
    `SUMMARY:${escapeText(meeting.title)}`,
  ];

  const officialUrl = safeHttpUrl(meeting.officialUrl);
  if (officialUrl) lines.push(`URL:${officialUrl}`);
  if (meeting.location) lines.push(`LOCATION:${escapeText(meeting.location)}`);

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
  if (details.length) lines.push(`DESCRIPTION:${escapeText(details.join('\n'))}`);

  if (meeting.status === 'canceled' || meeting.change === 'canceled') lines.push('STATUS:CANCELLED');
  else if (meeting.status === 'scheduled' || meeting.status === 'rescheduled') lines.push('STATUS:CONFIRMED');
  lines.push('END:VEVENT', 'END:VCALENDAR');
  return `${lines.map(foldLine).join('\r\n')}\r\n`;
}
