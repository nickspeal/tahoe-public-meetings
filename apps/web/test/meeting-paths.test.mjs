import assert from 'node:assert/strict';
import { access, readdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { agencyCatalog, meetingFile, meetingPath, paths } from './helpers/meeting-paths.mjs';
import { data, dist, record as fixture } from './helpers/site.mjs';

const pages = paths.agencyPages(agencyCatalog);
const record = (fields) => ({ agencyId: 'slt', sourceId: fields.id.split(':')[1], date: null, startsAt: null, timezone: 'America/Los_Angeles', ...fields });
const pathsFor = (meetings) => Object.fromEntries(paths.meetingPaths(meetings, pages));

test('titles name the kind of meeting from a fixed keyword list; regular and canceled meetings have none', () => {
  assert.equal(paths.meetingQualifier('11 03 2026 Special Closed Session City Council'), 'special-closed-session');
  assert.equal(paths.meetingQualifier('City Council Budget Workshop Agenda — August 25, 2026'), 'budget-workshop');
  assert.equal(paths.meetingQualifier('Joint Town Council and Tahoe Forest Hospital District Meeting'), 'joint');
  assert.equal(paths.meetingQualifier('Nevada Division of State Parks Public Hearing on Regulations'), 'hearing');
  assert.equal(paths.meetingQualifier('10 29 2026 South Tahoe Transit Joint Powers Authority'), '');
  assert.equal(paths.meetingQualifier('Truckee/Lake Tahoe Unmet Transit Needs Public Workshop (jointly hosted)'), 'workshop');
  assert.equal(paths.meetingQualifier('North Tahoe Public Utility District Board of Directors’ Regular Meeting'), '');
  assert.equal(paths.meetingQualifier('Notice of Cancelation of Town Council Meeting'), '');
  assert.equal(paths.meetingQualifier('Washoe County Board of County Commissioners Regular Meeting (2026-09-22; canceled)'), '');
});

test('the date is the local day of the start time, else the date-only value', () => {
  assert.equal(paths.meetingLocalDate({ startsAt: '2026-11-18T01:30:00Z', date: '2026-11-17', timezone: 'America/Los_Angeles' }), '2026-11-17');
  assert.equal(paths.meetingLocalDate({ startsAt: null, date: '2026-12-02', timezone: 'America/Los_Angeles' }), '2026-12-02');
  assert.equal(paths.meetingLocalDate({ startsAt: null, date: null, timezone: 'America/Los_Angeles' }), null);
});

test('a planned placeholder and the confirmed record that replaces it share a path', () => {
  const planned = record({ id: 'slt:planned-2026-11-17', title: 'City Council Meeting', startsAt: '2026-11-18T01:30:00Z', date: '2026-11-17' });
  const confirmed = record({ id: 'slt:2401', title: '11 17 2026 City Council', startsAt: '2026-11-18T01:30:00Z', date: '2026-11-17' });
  assert.equal(pathsFor([planned])[planned.id], '/meetings/south-lake-tahoe-city-council/2026-11-17/');
  assert.equal(pathsFor([confirmed])[confirmed.id], '/meetings/south-lake-tahoe-city-council/2026-11-17/');
});

test('another meeting on the same day leaves an existing path alone', () => {
  const regular = record({ id: 'slt:planned-2026-11-03', title: '11 03 2026 City Council', startsAt: '2026-11-03T17:30:00Z' });
  const closed = record({ id: 'slt:event-1540', title: '11 03 2026 Special Closed Session City Council', startsAt: '2026-11-03T16:30:00Z' });
  const before = pathsFor([regular]);
  const after = pathsFor([regular, closed]);
  assert.equal(after[regular.id], before[regular.id]);
  assert.equal(after[closed.id], '/meetings/south-lake-tahoe-city-council/2026-11-03-special-closed-session/');
});

test('two meetings of the same kind on one day are numbered by start time as a last resort', () => {
  const late = record({ id: 'slt:b', title: 'Special City Council', startsAt: '2026-10-13T20:00:00Z' });
  const early = record({ id: 'slt:a', title: 'Special City Council', startsAt: '2026-10-13T17:00:00Z' });
  assert.deepEqual(pathsFor([late, early]), {
    'slt:a': '/meetings/south-lake-tahoe-city-council/2026-10-13-special/',
    'slt:b': '/meetings/south-lake-tahoe-city-council/2026-10-13-special-2/',
  });
});

test('undated meetings and host-per-record meetings are named by their source ID', () => {
  const undated = record({ id: 'slt:2500', title: 'City Council' });
  const other = record({ id: 'other:one-off-west-shore-trail-nop-2026-11-02', agencyId: 'other', title: 'Scoping Meeting', startsAt: '2026-11-03T01:30:00Z' });
  assert.deepEqual(pathsFor([undated, other]), {
    'slt:2500': '/meetings/south-lake-tahoe-city-council/undated-2500/',
    [other.id]: '/meetings/other-agencies/2026-11-02-west-shore-trail-nop/',
  });
});

test('a meeting without an agency page or a shared slug fails loudly', () => {
  assert.throws(() => paths.meetingPaths([record({ id: 'nowhere:1', agencyId: 'nowhere', title: 'x', date: '2026-01-01' })], pages), /no page/);
  assert.throws(() => paths.agencyPages([{ id: 'a', follow: true, page: { slug: 's', name: 'A' } }, { id: 'b', follow: true, page: { slug: 's', name: 'B' } }]), /share the page slug/);
});

test('every stored meeting in the export has a built page at its path, and forecasts have none', async () => {
  for (const meeting of data.meetings.filter((row) => row.kind !== 'forecast')) await access(await meetingFile(meeting));
  const entries = await readdir(resolve(dist, 'meetings'));
  assert.ok(!entries.some((name) => name.includes(':')), 'no page is left at an ID path');
});

test('a body page lists its meetings and states the next one', async () => {
  const slt = data.meetings.filter((meeting) => meeting.agencyId === 'slt' && meeting.kind !== 'forecast');
  const document = new JSDOM(await readFile(resolve(dist, 'meetings', 'south-lake-tahoe-city-council', 'index.html'), 'utf8')).window.document;
  assert.equal(document.querySelector('h1').textContent, 'South Lake Tahoe City Council meetings');
  assert.equal(document.title, 'South Lake Tahoe City Council meetings · Tahoe Public Meetings');
  const hrefs = new Set([...document.querySelectorAll('.meeting-card a')].map((link) => link.getAttribute('href')));
  for (const meeting of slt) assert.ok(hrefs.has(await meetingPath(meeting)), `${meeting.id} is listed`);
  const description = document.querySelector('meta[name="description"]').getAttribute('content');
  // The next meeting that is not canceled is the upcoming regular meeting.
  const nextLink = document.querySelector('.page-intro a[href^="/meetings/"]');
  assert.equal(nextLink.getAttribute('href'), await meetingPath(fixture('slt:2003')));
  assert.match(description, /^Next South Lake Tahoe City Council meeting: /);
});

test('the meetings directory links each body page; the nav and back links go to the home timeline', async () => {
  const directory = new JSDOM(await readFile(resolve(dist, 'meetings', 'index.html'), 'utf8')).window.document;
  const links = [...directory.querySelectorAll('.agency-coverage a')].map((link) => link.getAttribute('href'));
  assert.ok(links.length > 0);
  for (const href of links) await access(resolve(dist, ...href.split('/').filter(Boolean), 'index.html'));
  assert.ok(!links.includes('/meetings/other-agencies/'), 'Other is not a body');
  assert.equal(directory.querySelector('.primary-nav a').getAttribute('href'), '/');
  const stored = data.meetings.find((meeting) => meeting.kind !== 'forecast');
  const page = new JSDOM(await readFile(await meetingFile(stored), 'utf8')).window.document;
  assert.equal(page.querySelector('.back-link').getAttribute('href'), '/');
});
