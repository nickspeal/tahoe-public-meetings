import assert from 'node:assert/strict';
import { access, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const data = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));
const filterSource = await readFile(resolve(root, 'src/scripts/timeline-filters.ts'), 'utf8');
const filterScript = ts.transpileModule(filterSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const forecasts = data.meetings.filter((meeting) => meeting.kind === 'forecast');

async function openPage(file, route) {
  const html = await readFile(resolve(root, 'dist', file), 'utf8');
  const dom = new JSDOM(html, { url: `http://localhost${route}`, runScripts: 'outside-only' });
  dom.window.eval(filterScript);
  return dom;
}

const rows = (document) => [...document.querySelectorAll('[data-meeting-row]')];
const visible = (document) => rows(document).filter((row) => !row.hidden);
const expectedRows = (document) => rows(document).filter((row) => row.dataset.forecast === 'true');
const change = (dom, element) => element.dispatchEvent(new dom.window.Event('change', { bubbles: true }));
const exists = (path) => access(path).then(() => true, () => false);

test('expected meetings are hidden by default and a bookmarkable toggle reveals them with counts', async () => {
  assert.ok(forecasts.length > 0, 'the export should include expected meetings derived from agency schedules');
  const dom = await openPage('index.html', '/');
  const { document } = dom.window;
  const expected = expectedRows(document);
  assert.ok(expected.length > 0);
  assert.ok(expected.every((row) => row.hidden), 'expected meetings start hidden');
  assert.ok(expected.every((row) => row.dataset.bucket !== 'earlier'), 'expected meetings are never history');
  const toggle = document.querySelector('#forecast-filter');
  assert.equal(toggle.checked, false);
  const confirmed = visible(document).length;
  assert.equal(document.querySelector('#timeline-count').textContent, `${confirmed} meetings`);
  assert.equal(document.querySelector('#forecast-count').textContent, `(${expected.length})`);

  toggle.checked = true;
  change(dom, toggle);
  assert.equal(visible(document).length, confirmed + expected.length);
  assert.equal(document.querySelector('#timeline-count').textContent, `${confirmed + expected.length} meetings · ${expected.length} expected`);
  assert.equal(dom.window.location.search, '?expected=1');
  const group = expected[0].closest('[data-timeline-group]');
  assert.equal(group.hidden, false, 'a date with only expected meetings appears once they are shown');

  toggle.checked = false;
  change(dom, toggle);
  assert.equal(visible(document).length, confirmed);
  assert.equal(dom.window.location.search, '');
  dom.window.close();
});

test('expected meetings combine with agency filters and disappear for any topic', async () => {
  const dom = await openPage('index.html', '/?agency=ttd&expected=1');
  const { document } = dom.window;
  assert.equal(document.querySelector('#forecast-filter').checked, true);
  const shown = visible(document);
  const expected = shown.filter((row) => row.dataset.forecast === 'true');
  assert.ok(expected.length > 0, 'TTD has a configured first-Wednesday schedule');
  assert.ok(shown.every((row) => row.dataset.agency === 'ttd'));
  assert.equal(document.querySelector('#forecast-count').textContent, `(${expected.length})`);

  const card = expected[0];
  const link = card.querySelector('.meeting-card-main');
  assert.equal(link.getAttribute('href'), data.agencies.find((agency) => agency.id === 'ttd').officialUrl);
  assert.equal(link.getAttribute('target'), '_blank');
  assert.ok(card.querySelector('.meeting-card.meeting-card-forecast'));
  assert.match(card.querySelector('.status-pill').textContent, /Expected · no agenda yet/);
  assert.match(card.textContent, /Usually the first Wednesday at 3:00 PM/);
  assert.equal(card.querySelector('.timeline-topic-items'), null);

  const topic = document.querySelector('#topic-filter');
  topic.value = 'transit';
  change(dom, topic);
  assert.ok(visible(document).every((row) => row.dataset.forecast !== 'true'));
  assert.equal(document.querySelector('#forecast-count').textContent, '(0)');
  assert.doesNotMatch(document.querySelector('#timeline-count').textContent, /expected/);
  assert.deepEqual(Object.fromEntries(new URLSearchParams(dom.window.location.search)), { topic: 'transit', agency: 'ttd', expected: '1' });
  dom.window.close();
});

test('topic pages keep expected meetings out even when the bookmark opts in', async () => {
  const dom = await openPage('topics/transit/index.html', '/topics/transit/?expected=1');
  const { document } = dom.window;
  assert.ok(expectedRows(document).length > 0);
  assert.ok(expectedRows(document).every((row) => row.hidden));
  assert.equal(document.querySelector('#forecast-count').textContent, '(0)');
  dom.window.close();
});

test('expected meetings get no meeting page or calendar download', async () => {
  for (const meeting of forecasts) {
    const route = encodeURIComponent(meeting.id).replaceAll('%3A', ':');
    assert.equal(await exists(resolve(root, 'dist', 'meetings', route, 'index.html')), false, `${meeting.id} has no page`);
    assert.equal(await exists(resolve(root, 'dist', 'calendar', meeting.agencyId, `${meeting.sourceId}.ics`)), false);
  }
  const html = await readFile(resolve(root, 'dist', 'about', 'index.html'), 'utf8');
  const confirmed = data.meetings.length - forecasts.length;
  assert.match(html, new RegExp(`${confirmed} meetings from`), 'coverage counts only stored records');
});

test('canceled meetings are greyed out with a readable, announced status', async () => {
  const dom = await openPage('index.html', '/?view=all');
  const { document } = dom.window;
  const canceled = rows(document).filter((row) => row.dataset.canceled === 'true');
  assert.ok(canceled.length > 0);
  for (const row of canceled) {
    assert.ok(row.querySelector('.meeting-card.meeting-card-canceled'));
    assert.equal(row.querySelector('.status-pill.pill-red').textContent, 'Canceled');
    assert.equal(row.querySelector('h3 .sr-only').textContent, 'Canceled: ');
  }
  assert.ok(rows(document).filter((row) => row.dataset.canceled !== 'true').every((row) => !row.querySelector('.meeting-card-canceled')));
  const css = await readFile(resolve(root, 'src/styles/global.css'), 'utf8');
  assert.match(css, /\.meeting-card-canceled \.meeting-card-copy h3 \{[^}]*line-through/);
  dom.window.close();
});
