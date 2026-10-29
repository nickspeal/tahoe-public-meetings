import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const source = await readFile(resolve(root, 'src/lib/calendar.ts'), 'utf8');
const javascript = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const calendar = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
const unfold = (content) => content.replace(/\r\n[ \t]/g, '');

function meeting(overrides = {}) {
  return {
    id: 'truckee:1795', agencyId: 'truckee', sourceId: '1795',
    title: 'Town Council Meeting', startsAt: '2026-09-23T00:00:00Z', date: '2026-09-22',
    timezone: 'America/Los_Angeles', status: 'scheduled', location: 'Town Hall',
    officialUrl: 'https://town.example/meeting',
    participation: { instructions: 'Comments by email', url: 'https://town.example/comment', remoteUrl: null, deadline: null },
    updatedAt: '2026-09-20T12:34:56Z', change: null,
    duration: { minutes: 150, source: 'estimated', basis: 'Estimated from 12 agenda items.' },
    ...overrides,
  };
}

test('uses explicit UTC instants across both daylight-saving offsets', () => {
  // The same 9 a.m. local meeting time has a different UTC offset on each side of DST.
  const before = calendar.buildCalendarEvent(meeting({ startsAt: '2026-10-31T09:00:00-07:00' }));
  const after = calendar.buildCalendarEvent(meeting({ startsAt: '2026-11-03T09:00:00-08:00' }));
  assert.match(before, /DTSTART:20261031T160000Z\r\n/);
  assert.match(after, /DTSTART:20261103T170000Z\r\n/);
  // 150 estimated minutes; DTEND is computed in UTC so it is correct across the DST change.
  assert.match(before, /DTEND:20261031T183000Z\r\n/);
  assert.match(after, /DTEND:20261103T193000Z\r\n/);
});

test('every event has an end: duration minutes, with a long fallback when absent', () => {
  const published = calendar.buildCalendarEvent(meeting({ duration: { minutes: 90, source: 'observed', basis: 'x' } }));
  assert.match(published, /DTSTART:20260923T000000Z\r\nDTEND:20260923T013000Z\r\n/);
  const missing = calendar.buildCalendarEvent(meeting({ duration: undefined }));
  assert.match(missing, /DTEND:20260923T030000Z\r\n/);
});

test('labels estimated lengths in the description but not observed ones', () => {
  const estimated = calendar.buildCalendarEvent(meeting());
  assert.match(unfold(estimated), /Estimated length\\; agencies do not publish an end time\.\r\n/);
  const observed = calendar.buildCalendarEvent(meeting({ duration: { minutes: 90, source: 'observed', basis: 'x' } }));
  assert.doesNotMatch(observed, /Estimated length/);
  const bare = calendar.buildCalendarEvent(meeting({ participation: { instructions: null, url: null, remoteUrl: null, deadline: null }, officialUrl: '' }));
  assert.match(unfold(bare), /DESCRIPTION:Estimated length/);
});

test('uses a stable agency-native UID and source update time for LAST-MODIFIED', () => {
  const first = calendar.buildCalendarEvent(meeting());
  const updated = calendar.buildCalendarEvent(meeting({ title: 'Revised Council Meeting', updatedAt: '2026-09-21T04:00:00Z' }));
  assert.equal(first.match(/^UID:(.+)$/m)[1], updated.match(/^UID:(.+)$/m)[1]);
  assert.match(first, /LAST-MODIFIED:20260920T123456Z\r\n/);
  assert.match(updated, /LAST-MODIFIED:20260921T040000Z\r\n/);
  assert.equal(first.match(/^DTSTAMP:(.+)$/m)[1], first.match(/^LAST-MODIFIED:(.+)$/m)[1]);
  assert.equal(updated.match(/^DTSTAMP:(.+)$/m)[1], updated.match(/^LAST-MODIFIED:(.+)$/m)[1]);
  assert.doesNotMatch(first, /^METHOD:/m);
});

test('escapes text controls without injecting new properties', () => {
  const event = calendar.buildCalendarEvent(meeting({
    title: 'Budget, trails; \u00e9\nBEGIN:VEVENT\nSTATUS:CONFIRMED',
    location: 'Room 1, Town Hall; East',
    participation: { instructions: 'Line one\r\nLine two, safely; yes \\ end', url: null, remoteUrl: null, deadline: null },
  }));
  assert.match(unfold(event), /SUMMARY:Budget\\, trails\\; \u00e9\\nBEGIN:VEVENT\\nSTATUS:CONFIRMED\r\n/);
  assert.match(event, /LOCATION:Room 1\\, Town Hall\\; East\r\n/);
  assert.match(unfold(event), /DESCRIPTION:Official meeting page: https:\/\/town\.example\/meeting\\nPublic comment: Line one\\nLine two\\, safely\\; yes \\\\ end\\nEstimated length\\; agencies do not publish an end time\.\r\n/);
  assert.match(event, /STATUS:CONFIRMED\r\n/);
  assert.equal((event.match(/^BEGIN:VEVENT$/gm) ?? []).length, 1);
  assert.equal((event.match(/^STATUS:/gm) ?? []).length, 1);
});

test('folds long UTF-8 content lines at no more than 75 octets', () => {
  const event = calendar.buildCalendarEvent(meeting({ title: `Tahoe Council ${'🌲é'.repeat(60)}` }));
  for (const line of event.split('\r\n').filter(Boolean)) {
    assert.ok(Buffer.byteLength(line, 'utf8') <= 75, `${Buffer.byteLength(line, 'utf8')} octets: ${line}`);
  }
  assert.match(event, /\r\n /);
});

test('omits unconfirmed date-only meetings and rejects a floating start timestamp', () => {
  assert.equal(calendar.canExportMeeting(meeting({ startsAt: null })), false);
  assert.equal(calendar.canExportMeeting(meeting({ startsAt: '2026-11-04T15:00:00' })), false);
  assert.throws(() => calendar.buildCalendarEvent(meeting({ startsAt: null })), /confirmed start time/);
  assert.equal(calendar.calendarHref(meeting()), '/calendar/truckee/1795.ics');
  const noParticipation = calendar.buildCalendarEvent(meeting({
    participation: { instructions: null, url: null, remoteUrl: null, deadline: null },
  }));
  assert.match(unfold(noParticipation), /DESCRIPTION:Official meeting page: https:\/\/town\.example\/meeting\\nEstimated length/);
});

test('Astro static output includes a timed event and omits a date-only listing', async () => {
  // Picked from the current export, so the test follows whichever meetings it holds.
  const data = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));
  const stored = data.meetings.filter((candidate) => candidate.kind !== 'expected');
  const timed = stored.find((candidate) => calendar.canExportMeeting(candidate));
  const dateOnly = stored.find((candidate) => !candidate.startsAt);
  assert.ok(timed && dateOnly, 'export should include a timed meeting and a date-only one');
  const event = await readFile(resolve(root, `dist${calendar.calendarHref(timed)}`), 'utf8');
  assert.match(event, new RegExp(`UID:${timed.agencyId}\\.${timed.sourceId}@tahoe-public-meetings\\.local\r\n`));
  assert.match(event, /DTSTART:\d{8}T\d{6}Z\r\nDTEND:\d{8}T\d{6}Z\r\n/);
  await assert.rejects(readFile(resolve(root, `dist${calendar.calendarHref(dateOnly)}`), 'utf8'), { code: 'ENOENT' });
});

test('a meeting with a named host says so in the description', () => {
  assert.match(unfold(calendar.buildCalendarEvent(meeting({ host: 'TRPA and El Dorado County' }))), /DESCRIPTION:[^\r]*Hosted by: TRPA and El Dorado County/);
  assert.doesNotMatch(calendar.buildCalendarEvent(meeting()), /Hosted by/);
});

test('canceled meetings have no calendar event', () => {
  assert.equal(calendar.canExportMeeting(meeting({ status: 'canceled' })), false);
  assert.equal(calendar.canExportMeeting(meeting({ change: 'canceled' })), false);
  assert.throws(() => calendar.buildCalendarEvent(meeting({ status: 'canceled' })), /confirmed start time/);
  assert.throws(() => calendar.googleCalendarHref(meeting({ status: 'canceled' })), /confirmed start time/);
});

test('Google Calendar link prefills the same event as the calendar file', () => {
  const url = new URL(calendar.googleCalendarHref(meeting({ planned: true })));
  assert.equal(url.origin + url.pathname, 'https://calendar.google.com/calendar/render');
  assert.equal(url.searchParams.get('action'), 'TEMPLATE');
  assert.equal(url.searchParams.get('text'), 'Town Council Meeting');
  assert.equal(url.searchParams.get('dates'), '20260923T000000Z/20260923T023000Z');
  assert.equal(url.searchParams.get('location'), 'Town Hall');
  assert.match(url.searchParams.get('details'), /^Subject to change\.\nOfficial meeting page: https:\/\/town\.example\/meeting\nPublic comment: Comments by email\n/);
});

test('Outlook.com link prefills the event with ISO UTC times', () => {
  const url = new URL(calendar.outlookCalendarHref(meeting({ title: 'Budget & trails #2', location: null })));
  assert.equal(url.origin + url.pathname, 'https://outlook.live.com/calendar/0/action/compose');
  assert.equal(url.searchParams.get('rru'), 'addevent');
  assert.equal(url.searchParams.get('subject'), 'Budget & trails #2');
  assert.equal(url.searchParams.get('startdt'), '2026-09-23T00:00:00Z');
  assert.equal(url.searchParams.get('enddt'), '2026-09-23T02:30:00Z');
  assert.equal(url.searchParams.has('location'), false);
});

test('web calendar links cut a long description and point to the official page', () => {
  const instructions = 'x'.repeat(5000);
  const url = new URL(calendar.googleCalendarHref(meeting({ participation: { instructions, url: null, remoteUrl: null, deadline: null } })));
  const details = url.searchParams.get('details');
  assert.ok(details.length < 1600, `${details.length} characters`);
  assert.match(details, /…\nFull details: https:\/\/town\.example\/meeting$/);
});
