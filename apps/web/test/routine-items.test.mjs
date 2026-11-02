import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { isPlaceholderItem, isRoutineItem, isRoutineTitle } from './helpers/shown-items.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const data = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));

test('meeting mechanics are routine', () => {
  for (const title of ['Adjournment', 'ADJOURN', 'Roll Call', 'Pledge of Allegiance', 'Flag Salute', 'Call to Order/Pledge of Allegiance',
    'Call to Order and Determination of Quorum', 'Call to Order – Chair Jane Doe', 'Roll Call and Determination of Quorum of TTD',
    'Directors’ Roll Call', 'Call to Order/Open Session/Pledge of Allegiance', 'Introduction / Establish Quorum (Discussion)',
    'Approval of Agenda', 'Adoption of Agenda', 'Changes or Additions to the Agenda', 'Consider Approving Agenda and Order of Items',
    'Adjournment of Meeting', 'Review Agenda', 'Agenda Review', 'CALL TO ORDER SPECIAL MEETING – PLEDGE OF ALLEGIANCE',
    'Approval of the July 7, 2026, Agenda', 'Approval of Agenda for May 6, 2026', 'APPROVAL OF AGENDA – ACTION', 'Roll Call and Determination of Quorum of TTD/TTC',
    'ADJOURNMENT – To next regular meeting, on October 6, 2026.', 'ADJOURN AS TTD AND CONVENE AS TTC', 'CORRECTIONS TO THE AGENDA OR CONSENT CALENDAR', 'ROLL CALL OF TRUSTEES']) {
    assert.equal(isRoutineTitle(title), true, title);
  }
});

test('items with substance are not routine', () => {
  for (const title of ['Public Comment', 'Closed Session Report', 'Closed Session', 'City Council Meeting Minutes', 'Approval of Minutes',
    'Agenda Planning Calendar', 'Proclamations', 'Public Communications', 'Executive Director’s Report', 'Mayor’s Report',
    'Introduction and First Reading of an Ordinance', 'Roll Call Vote on Resolution 2026-14', 'Chair Report',
    'Adjournment to Closed Session', 'Call to Order and Election of Temporary Chairperson', 'ROLL CALL OF TRUSTEE CANDIDATES', 'Agenda Management',
    'Future agenda items', 'General Public Comment for Items Not on the Agenda']) {
    assert.equal(isRoutineTitle(title), false, title);
  }
});

test('a routine title with a topic or a stated time stays visible', () => {
  assert.equal(isRoutineItem({ title: 'Adjournment', topics: [], timeCertain: null }), true);
  assert.equal(isRoutineItem({ title: 'Adjournment', topics: ['housing'], timeCertain: null }), false);
  assert.equal(isRoutineItem({ title: 'Call to Order', topics: [], timeCertain: { time: '9:00 AM', quote: '', documentId: null, page: null } }), false);
});

test('built meeting pages leave out routine items', async () => {
  const meeting = data.meetings.find((candidate) => candidate.items.some((item) => !item.suppressed && isRoutineItem(item)));
  assert.ok(meeting, 'export should include a meeting with a routine item');
  const routeId = encodeURIComponent(meeting.id).replaceAll('%3A', ':');
  const page = new JSDOM(await readFile(resolve(root, 'dist', 'meetings', routeId, 'index.html'), 'utf8')).window.document;
  const routine = meeting.items.filter((item) => !item.suppressed && isRoutineItem(item));
  for (const item of routine) assert.equal(page.querySelector(`[data-item-id="${item.id}"]`), null, `${item.id} is not listed`);
  assert.ok(page.querySelector('[data-agenda-item]'), 'other items are still listed');
});

test('a placeholder for a number the agenda skips is a muted line, not an agenda item', async (t) => {
  assert.equal(isPlaceholderItem({ title: 'Not Found In Agenda', citations: [] }), true);
  assert.equal(isPlaceholderItem({ title: 'Not Found In Agenda', citations: [{ documentId: 'd', page: 1, quote: 'Not Found In Agenda' }] }), false);
  const meeting = data.meetings.find((candidate) => candidate.items.some(isPlaceholderItem));
  if (!meeting) return t.skip('the export has no placeholder item');
  const item = meeting.items.find(isPlaceholderItem);
  const routeId = encodeURIComponent(meeting.id).replaceAll('%3A', ':');
  const page = new JSDOM(await readFile(resolve(root, 'dist', 'meetings', routeId, 'index.html'), 'utf8')).window.document;
  const line = page.querySelector(`[data-item-id="${item.id}"]`);
  assert.ok(line?.classList.contains('agenda-item-missing'), 'listed as a muted line');
  assert.equal(line.hasAttribute('data-agenda-item'), false, 'not counted by the topic filter');
  assert.equal(line.querySelector('a, details, summary'), null, 'no source link or disclosure');
  assert.match(line.textContent, new RegExp(`${item.number}\\s*Not Found In Agenda`));
});
