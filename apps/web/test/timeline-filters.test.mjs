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

test('combines topic and agency filters, keeps shareable query state, and opens matching history', async () => {
  const dom = await openPage('index.html', '/?topic=transit&agency=ttd&view=all');
  const { document } = dom.window;
  const rows = visibleRows(document);
  const ids = rows.map(meetingId);

  assert.ok(ids.includes('ttd:6049'));
  assert.ok(ids.includes('ttd:7110'));
  assert.ok(rows.every((row) => row.dataset.agency === 'ttd' && JSON.parse(row.dataset.topics).includes('transit')));
  assert.equal(document.querySelector('#timeline-count').textContent, `${rows.length} ${rows.length === 1 ? 'meeting' : 'meetings'} · Transit`);
  assert.equal(document.querySelector('#earlier-meetings').open, true);
  const earlierCount = rows.filter((row) => row.dataset.bucket === 'earlier').length;
  assert.equal(Number(document.querySelector('#earlier-meetings summary span').textContent), earlierCount);
  assert.equal(dom.window.location.search, '?topic=transit&agency=ttd&view=all');
  assert.ok(document.querySelector('#agenda-count').textContent.includes('related agenda item'));

  const view = document.querySelector('#date-filter');
  view.value = 'upcoming';
  view.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  const upcomingMatches = rows.filter((row) => ['today', 'upcoming'].includes(row.dataset.bucket) && row.dataset.canceled !== 'true').length;
  assert.equal(document.querySelector('#earlier-meetings').open, false);
  assert.equal(document.querySelector('#no-upcoming-message').hidden, upcomingMatches > 0 || earlierCount === 0);
  if (upcomingMatches === 0 && earlierCount > 0) assert.match(document.querySelector('#no-upcoming-message').textContent, /No upcoming agenda items match Transit yet/);
  assert.equal(dom.window.location.search, '?topic=transit&agency=ttd');
  dom.window.close();
});

test('shows a specific empty state and hides history when combined filters have no matches', async () => {
  const dom = await openPage('index.html', '/?topic=housing&agency=ttd&view=all');
  const { document } = dom.window;
  const agency = document.querySelector('#agency-filter');
  const absent = document.createElement('option');
  absent.value = 'no-matching-agency';
  absent.textContent = 'No matching agency';
  agency.add(absent);
  agency.value = absent.value;
  agency.dispatchEvent(new dom.window.Event('change', { bubbles: true }));

  assert.equal(visibleRows(document).length, 0);
  assert.equal(document.querySelector('#timeline-no-results').hidden, false);
  assert.equal(document.querySelector('#earlier-meetings').hidden, true);
  assert.equal(document.querySelector('#timeline-count').textContent, '0 meetings · Housing');
  assert.equal(document.querySelector('#no-upcoming-message').hidden, true);
  dom.window.close();
});

test('topic route wins over a conflicting topic query and keeps the route/query filters bookmarkable', async () => {
  const dom = await openPage('topics/biking/index.html', '/topics/biking/?topic=transit&agency=ttd&view=all');
  const { document } = dom.window;
  const ids = visibleRows(document).map(meetingId);

  assert.ok(ids.includes('ttd:6941'));
  assert.equal(document.querySelector('#topic-filter').value, 'biking');
  assert.equal(document.querySelector('#agency-filter').value, 'ttd');
  assert.equal(document.querySelector('#date-filter').value, 'all');
  assert.equal(dom.window.location.search, '?agency=ttd&view=all');
  assert.equal(document.querySelector('#clear-filters').getAttribute('href'), '/');

  const agency = document.querySelector('#agency-filter');
  agency.value = 'slt';
  agency.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
  const sltRows = visibleRows(document);
  assert.ok(sltRows.some((row) => meetingId(row) === 'slt:2339'));
  assert.ok(sltRows.every((row) => row.dataset.agency === 'slt' && JSON.parse(row.dataset.topics).includes('biking')));
  assert.equal(dom.window.location.pathname, '/topics/biking/');
  assert.equal(dom.window.location.search, '?agency=slt&view=all');
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
