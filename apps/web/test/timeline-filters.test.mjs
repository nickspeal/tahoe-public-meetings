import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const filterSource = await readFile(resolve(root, 'src/scripts/timeline-filters.ts'), 'utf8');
const filterScript = ts.transpileModule(filterSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;

async function openPage(file, route) {
  const html = await readFile(resolve(root, 'dist', file), 'utf8');
  const dom = new JSDOM(html, { url: `http://localhost${route}`, runScripts: 'outside-only' });
  dom.window.scrollBy = () => {}; // jsdom has no layout to scroll.
  dom.window.eval(filterScript);
  return dom;
}

function visibleRows(document) {
  return [...document.querySelectorAll('[data-meeting-row]')].filter((row) => !row.hidden);
}

function meetingId(row) {
  const href = row.querySelector('.meeting-card-main').getAttribute('href');
  return decodeURIComponent(new URL(href, 'http://localhost').pathname.split('/').filter(Boolean).at(-1));
}

const click = (dom, element) => element.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true }));
const change = (dom, element) => element.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
const earlierRows = (document) => [...document.querySelectorAll('[data-meeting-row][data-bucket="earlier"]')];

test('the default view shows upcoming meetings with posted agendas and no earlier meetings', async () => {
  const dom = await openPage('index.html', '/');
  const { document } = dom.window;
  const rows = visibleRows(document);
  assert.ok(rows.length > 0);
  assert.ok(rows.every((row) => row.dataset.bucket !== 'earlier' && row.dataset.awaitingAgenda === 'false'));
  const awaiting = [...document.querySelectorAll('[data-meeting-row][data-awaiting-agenda="true"]')];
  assert.ok(awaiting.length > 0 && awaiting.every((row) => row.hidden));
  assert.equal(document.querySelector('#upcoming-toggle-label').textContent, `Show ${awaiting.length} upcoming meetings without a posted agenda`);
  assert.equal(document.querySelector('#upcoming-toggle').getAttribute('aria-expanded'), 'false');
  const earlier = earlierRows(document);
  assert.ok(earlier.length > 20, 'the fixture needs enough history to page through');
  assert.equal(document.querySelector('#earlier-more-label').textContent, 'Show 10 earlier meetings');
  assert.equal(document.querySelector('#earlier-remaining').textContent, `${earlier.length} earlier`);
  assert.equal(document.querySelector('#earlier-today-jump').hidden, true, 'Today is at the top, so there is nothing to scroll back to');
  assert.equal(document.querySelector('#date-filter'), null, 'the Dates filter is gone');
  dom.window.close();
});

test('earlier meetings open ten at a time, most recent first, and reset when a filter changes', async () => {
  const dom = await openPage('index.html', '/?view=all');
  const { document } = dom.window;
  assert.equal(dom.window.location.search, '', 'the retired view parameter is dropped');
  const earlier = earlierRows(document);
  const more = document.querySelector('#earlier-more');
  const shown = () => earlier.filter((row) => !row.hidden);

  click(dom, more);
  assert.deepEqual(shown(), earlier.slice(-10), 'the first click shows the ten most recent earlier meetings');
  assert.equal(document.querySelector('#earlier-more-label').textContent, 'Show 10 earlier meetings');
  assert.equal(document.querySelector('#earlier-remaining').textContent, `${earlier.length - 10} not shown`);
  const todayJump = document.querySelector('#earlier-today-jump');
  assert.equal(todayJump.hidden, false, 'with earlier meetings open, a link scrolls back to Today');
  assert.equal(todayJump.getAttribute('href'), '#today-divider');
  assert.ok(shown().every((row) => !row.closest('[data-timeline-group]').hidden));
  click(dom, more);
  assert.deepEqual(shown(), earlier.slice(-20));
  while (!more.hidden) click(dom, more);
  assert.equal(shown().length, earlier.length, 'the button disappears once every earlier meeting is shown');
  assert.equal(todayJump.hidden, false, 'the Today link stays after the button is gone');

  const agency = document.querySelector('#agency-filter');
  agency.value = 'ttd';
  change(dom, agency);
  assert.equal(shown().length, 0, 'changing a filter collapses earlier meetings again');
  assert.equal(todayJump.hidden, true);
  assert.equal(more.hidden, false);
  click(dom, more);
  const ttdEarlier = earlier.filter((row) => row.dataset.agency === 'ttd');
  assert.deepEqual(shown(), ttdEarlier.slice(-10), 'the ten counts meetings that match the current filters');
  dom.window.close();
});

test('combines topic and agency filters and keeps shareable query state', async () => {
  const dom = await openPage('index.html', '/?topic=transit&agency=ttd');
  const { document } = dom.window;
  const more = document.querySelector('#earlier-more');
  while (!more.hidden) click(dom, more);
  const rows = visibleRows(document);
  const ids = rows.map(meetingId);

  assert.ok(ids.includes('ttd:6049'));
  assert.ok(ids.includes('ttd:7110'));
  assert.ok(rows.every((row) => row.dataset.agency === 'ttd' && JSON.parse(row.dataset.topics).includes('transit')));
  assert.equal(document.querySelector('#timeline-count').textContent, `${rows.length} ${rows.length === 1 ? 'meeting' : 'meetings'} · Transit`);
  assert.equal(dom.window.location.search, '?topic=transit&agency=ttd');
  assert.ok(document.querySelector('#agenda-count').textContent.includes('related agenda item'));
  const upcomingMatches = rows.filter((row) => row.dataset.bucket !== 'earlier' && row.dataset.canceled !== 'true').length;
  assert.equal(document.querySelector('#no-upcoming-message').hidden, upcomingMatches > 0);
  if (upcomingMatches === 0) assert.equal(document.querySelector('#no-upcoming-message').textContent, 'No upcoming agendas match Transit yet.');
  dom.window.close();
});

test('shows a specific empty state and hides history when combined filters have no matches', async () => {
  const dom = await openPage('index.html', '/?topic=housing&agency=ttd');
  const { document } = dom.window;
  const agency = document.querySelector('#agency-filter');
  const absent = document.createElement('option');
  absent.value = 'no-matching-agency';
  absent.textContent = 'No matching agency';
  agency.add(absent);
  agency.value = absent.value;
  change(dom, agency);

  assert.equal(visibleRows(document).length, 0);
  assert.equal(document.querySelector('#timeline-no-results').hidden, false);
  assert.equal(document.querySelector('#earlier-meetings').hidden, true);
  assert.equal(document.querySelector('#upcoming-more').hidden, true);
  assert.equal(document.querySelector('#timeline-count').textContent, '0 meetings · Housing');
  assert.equal(document.querySelector('#no-upcoming-message').hidden, true);
  dom.window.close();
});

test('topic route wins over a conflicting topic query and keeps the route/query filters bookmarkable', async () => {
  const dom = await openPage('topics/biking/index.html', '/topics/biking/?topic=transit&agency=ttd&view=all');
  const { document } = dom.window;
  assert.equal(document.querySelector('#topic-filter').value, 'biking');
  assert.equal(document.querySelector('#agency-filter').value, 'ttd');
  assert.equal(dom.window.location.search, '?agency=ttd');
  assert.equal(document.querySelector('#clear-filters').getAttribute('href'), '/');
  const more = document.querySelector('#earlier-more');
  while (!more.hidden) click(dom, more);
  assert.ok(visibleRows(document).map(meetingId).includes('ttd:6941'));

  const agency = document.querySelector('#agency-filter');
  agency.value = 'slt';
  change(dom, agency);
  while (!more.hidden) click(dom, more);
  const sltRows = visibleRows(document);
  assert.ok(sltRows.some((row) => meetingId(row) === 'slt:2339'));
  assert.ok(sltRows.every((row) => row.dataset.agency === 'slt' && JSON.parse(row.dataset.topics).includes('biking')));
  assert.equal(dom.window.location.pathname, '/topics/biking/');
  assert.equal(dom.window.location.search, '?agency=slt');
  dom.window.close();
});

test('renders compact date badges and truthful agenda availability on a meeting card', async () => {
  const dom = await openPage('index.html', '/');
  const { document } = dom.window;
  const row = [...document.querySelectorAll('[data-meeting-row]')].find((candidate) => meetingId(candidate) === 'truckee:1796');
  assert.ok(row);
  assert.equal(row.querySelector('.date-day').textContent, '27');
  assert.equal(row.querySelector('.meeting-date > span:nth-child(2)').textContent, 'Oct');
  assert.match(row.querySelector('.meeting-card-copy p').textContent, /Agenda not posted/);
  assert.doesNotMatch(row.querySelector('.meeting-card-copy p').textContent, /0 agenda/);
  const canceled = [...document.querySelectorAll('[data-meeting-row]')].find((candidate) => meetingId(candidate) === 'truckee:1771');
  assert.equal(canceled.dataset.bucket, 'earlier');
  assert.match(canceled.textContent, /Canceled/);
  dom.window.close();
});
