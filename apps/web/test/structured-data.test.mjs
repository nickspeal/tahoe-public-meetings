import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const transpile = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const dataUrl = (javascript) => `data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`;
const routine = dataUrl(transpile(await readFile(resolve(root, 'src/lib/routine-items.ts'), 'utf8')));
const source = (await readFile(resolve(root, 'src/lib/structured-data.ts'), 'utf8')).replace("'./routine-items'", `'${routine}'`);
const { meetingJsonLd, bodyJsonLd, localIso, meetingKind } = await import(dataUrl(transpile(source)));

const HOME = 'https://tahoepublicmeetings.org/';
const body = { name: 'South Lake Tahoe City Council', url: `${HOME}meetings/south-lake-tahoe-city-council/`, officialUrl: 'https://www.cityofslt.us/' };
const ctx = { url: `${body.url}2026-11-03/`, body, homeUrl: HOME };
const item = (order, title, extra = {}) => ({ id: `i${order}`, number: String(order), order, title, section: null, topics: [], summary: null, citations: [], change: null, updatedAt: '', ...extra });
const meeting = (overrides = {}) => ({
  id: 'slt:1', agencyId: 'slt', sourceId: '1', kind: 'confirmed', title: '11 03 2026 Special Closed Session City Council',
  startsAt: '2026-11-04T01:30:00Z', date: '2026-11-03', timezone: 'America/Los_Angeles', status: 'unknown',
  location: 'City Hall, Council Chambers, 1901 Lisa Maloff Way, South Lake Tahoe, CA',
  officialUrl: 'https://example.gov/agenda', minutesUrl: null, recordingUrl: null, lastCheckedAt: null, updatedAt: '', change: null, warnings: [], documents: [],
  participation: { instructions: null, url: null, remoteUrl: 'https://us02web.zoom.us/j/1', deadline: null, watchUrl: null, phone: null },
  items: [item(1, 'Call to Order'), item(2, 'Approve a contract for snow removal'), item(3, 'Long agenda text', { shortTitle: 'Adopt the housing element' }),
    item(4, 'Hidden', { suppressed: true }), item(5, 'Budget hearing'), item(6, 'Parking ordinance')],
  duration: { minutes: 120, source: 'estimated', basis: '' },
  ...overrides,
});

test('startDate is wall-clock time with the offset of the meeting timezone', () => {
  assert.equal(localIso('2026-11-04T01:30:00Z', 'America/Los_Angeles'), '2026-11-03T17:30:00-08:00');
  assert.equal(localIso('2026-07-08T16:30:00Z', 'America/Los_Angeles'), '2026-07-08T09:30:00-07:00');
  assert.equal(localIso('2026-07-08T16:30:00Z', 'UTC'), '2026-07-08T16:30:00+00:00');
});

test('the event name is the body and the kind of meeting the title states, with the date', () => {
  assert.equal(meetingKind('10 26 2026 Special Closed Session City Council'), 'Special Closed Session');
  assert.equal(meetingKind('City Council Budget Workshop Agenda — August 25, 2026'), 'Workshop');
  assert.equal(meetingKind('Douglas County Board of County Commissioners and Regional Transportation Commission Joint Meeting'), 'Joint Meeting');
  assert.equal(meetingKind('Town Council Meeting'), 'Meeting');
  const [event] = meetingJsonLd(meeting(), ctx);
  assert.equal(event.name, 'South Lake Tahoe City Council Special Closed Session – November 3, 2026');
});

test('a hybrid meeting is a mixed-attendance Event with a place, a virtual location, and real agenda content', () => {
  const [event, crumbs] = meetingJsonLd(meeting(), ctx);
  assert.equal(event['@type'], 'Event');
  assert.equal(event.url, ctx.url);
  assert.equal(event.startDate, '2026-11-03T17:30:00-08:00');
  assert.equal(event.endDate, undefined);
  assert.equal(event.eventStatus, 'https://schema.org/EventScheduled');
  assert.equal(event.eventAttendanceMode, 'https://schema.org/MixedEventAttendanceMode');
  assert.deepEqual(event.location, [
    { '@type': 'Place', name: 'City Hall', address: 'City Hall, Council Chambers, 1901 Lisa Maloff Way, South Lake Tahoe, CA' },
    { '@type': 'VirtualLocation', url: 'https://us02web.zoom.us/j/1' },
  ]);
  assert.deepEqual(event.organizer, { '@type': 'GovernmentOrganization', name: body.name, url: body.officialUrl });
  assert.equal(event.isAccessibleForFree, true);
  // Routine and suppressed items are left out, short titles are used, and the rest are counted.
  assert.equal(event.description, 'Agenda: Approve a contract for snow removal; Adopt the housing element; Budget hearing, and 1 more.');
  assert.deepEqual(crumbs.itemListElement.map((crumb) => [crumb.position, crumb.name, crumb.item]),
    [[1, 'Home', HOME], [2, body.name, body.url], [3, event.name, ctx.url]]);
});

test('attendance mode and location follow what the meeting states', () => {
  const remoteOnly = meetingJsonLd(meeting({ location: null }), ctx)[0];
  assert.equal(remoteOnly.eventAttendanceMode, 'https://schema.org/OnlineEventAttendanceMode');
  assert.deepEqual(remoteOnly.location, { '@type': 'VirtualLocation', url: 'https://us02web.zoom.us/j/1' });
  const inPerson = meetingJsonLd(meeting({ participation: { instructions: null, url: null, remoteUrl: null, deadline: null } }), ctx)[0];
  assert.equal(inPerson.eventAttendanceMode, 'https://schema.org/OfflineEventAttendanceMode');
  assert.equal(inPerson.location['@type'], 'Place');
  const neither = meetingJsonLd(meeting({ location: null, participation: { instructions: null, url: null, remoteUrl: null, deadline: null } }), ctx)[0];
  assert.equal(neither.location, undefined);
});

test('status maps to schema.org event status; planned meetings stay scheduled', () => {
  const status = (overrides) => meetingJsonLd(meeting(overrides), ctx)[0].eventStatus;
  assert.equal(status({ status: 'canceled' }), 'https://schema.org/EventCancelled');
  assert.equal(status({ change: 'canceled' }), 'https://schema.org/EventCancelled');
  assert.equal(status({ status: 'rescheduled' }), 'https://schema.org/EventRescheduled');
  assert.equal(status({ planned: true, items: [] }), 'https://schema.org/EventScheduled');
});

test('a date-only meeting gets a date, a dateless one no Event, and a forecast nothing', () => {
  const dateOnly = meetingJsonLd(meeting({ startsAt: null }), ctx);
  assert.equal(dateOnly[0].startDate, '2026-11-03');
  const dateless = meetingJsonLd(meeting({ startsAt: null, date: null }), ctx);
  assert.deepEqual(dateless.map((node) => node['@type']), ['BreadcrumbList']);
  assert.deepEqual(meetingJsonLd(meeting({ kind: 'forecast' }), ctx), []);
});

test('descriptions prefer topic-tagged items and clip long agenda text at a word', () => {
  const long = `Conference with labor negotiator ${'regarding the employee organizations '.repeat(5)}`;
  const items = [item(1, 'New Business'), item(2, long, { topics: ['budget'] }), item(3, 'Parking ordinance', { topics: ['parking'] })];
  const text = meetingJsonLd(meeting({ items }), ctx)[0].description;
  assert.match(text, /^Agenda: Conference with labor negotiator regarding .*\S…; Parking ordinance\.$/);
  assert.ok(text.length < 160);
});

test('a bare street address is the place name too', () => {
  const [event] = meetingJsonLd(meeting({ location: '1001 E. 9th Street, Reno, Nevada 89512', participation: { remoteUrl: null } }), ctx);
  assert.deepEqual(event.location, { '@type': 'Place', name: '1001 E. 9th Street, Reno, Nevada 89512', address: '1001 E. 9th Street, Reno, Nevada 89512' });
});

test('descriptions fall back to planned subjects, then to the body', () => {
  const previews = [{ subject: 'Short-term rental ordinance', section: null, topics: [], source: {} }];
  assert.equal(meetingJsonLd(meeting({ items: [], previews }), ctx)[0].description, 'Planned subjects: Short-term rental ordinance.');
  assert.equal(meetingJsonLd(meeting({ items: [item(1, 'Adjournment')] }), ctx)[0].description, `Public meeting of the ${body.name}.`);
});

test('a hosted one-off meeting is named by its own title and host', () => {
  const [event] = meetingJsonLd(meeting({ host: 'TRPA and El Dorado County', title: 'West Shore Trail Scoping Meeting' }), ctx);
  assert.equal(event.name, 'West Shore Trail Scoping Meeting – November 3, 2026');
  assert.deepEqual(event.organizer, { '@type': 'GovernmentOrganization', name: 'TRPA and El Dorado County' });
});

test('a body hub is a GovernmentOrganization with a breadcrumb', () => {
  const [org, crumbs] = bodyJsonLd({ ...body, homeUrl: HOME });
  assert.deepEqual(org, { '@type': 'GovernmentOrganization', '@id': `${body.url}#body`, name: body.name, url: body.officialUrl, mainEntityOfPage: body.url });
  assert.deepEqual(crumbs.itemListElement.map((crumb) => crumb.item), [HOME, body.url]);
});
