import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { shown } from './helpers/shown-items.mjs';
import ts from 'typescript';
import { meetingFile, meetingPath } from './helpers/meeting-paths.mjs';
import { record } from './helpers/site.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const filterSource = await readFile(resolve(root, 'src/scripts/meeting-agenda-filter.ts'), 'utf8');
const openSource = await readFile(resolve(root, 'src/scripts/open-agenda-item.ts'), 'utf8');
const transpile = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const filterScript = transpile(filterSource);
const openScript = transpile(openSource);

async function openMeeting(meeting, query = '') {
  const html = await readFile(await meetingFile(meeting), 'utf8');
  const dom = new JSDOM(html, {
    url: `http://localhost${await meetingPath(meeting)}${query}`,
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  });
  dom.window.requestAnimationFrame = () => 0;
  dom.window.eval(filterScript);
  dom.window.eval(openScript);
  return dom;
}

const visibleItems = (document) => [...document.querySelectorAll('[data-agenda-item]')].filter((item) => !item.hidden);
const itemIds = (rows) => rows.map((item) => item.dataset.itemId);

test('meeting topic filters use OR matching, restore query state, and reset without losing deep links', async () => {
  // Transit, biking, housing, and one uncategorized item.
  const meeting = record('ttd:1002');
  const items = meeting.items.filter((item) => shown(item)).sort((a, b) => a.order - b.order);
  const [firstTopic, secondTopic] = ['transit', 'biking'];
  const expected = items.filter((item) => item.topics.includes(firstTopic) || item.topics.includes(secondTopic));
  assert.equal(expected.length, 2);

  const dom = await openMeeting(meeting, `?topic=${firstTopic}&topic=${secondTopic}&agency=ttd#item-${items.find((item) => item.topics.length)?.id}`);
  const { document } = dom.window;
  const checkboxes = [...document.querySelectorAll('#agenda-topic-filter input[name="topic"]')];
  assert.ok(checkboxes.find((checkbox) => checkbox.value === firstTopic)?.checked);
  assert.ok(checkboxes.find((checkbox) => checkbox.value === secondTopic)?.checked);
  const visible = visibleItems(document);
  assert.deepEqual(itemIds(visible), expected.map((item) => item.id));
  assert.equal(Number(document.querySelector('#agenda-filter-count').textContent.match(/Showing (\d+)/)?.[1]), expected.length);
  assert.ok(visible.every((item) => item.querySelector('.agenda-summary-topics .tag')));
  const restored = new URLSearchParams(dom.window.location.search);
  assert.deepEqual(restored.getAll('topic').sort(), [firstTopic, secondTopic].sort());
  assert.equal(restored.get('agency'), 'ttd');

  const unmatched = items.find((item) => item.topics.includes('housing'));
  dom.window.history.replaceState({}, '', `${dom.window.location.pathname}?topic=${firstTopic}#item-${unmatched.id}`);
  dom.window.dispatchEvent(new dom.window.HashChangeEvent('hashchange'));
  const target = document.getElementById(`item-${unmatched.id}`);
  assert.equal(target.hidden, false, 'a deep-linked item remains visible outside the selected topics');
  assert.equal(target.open, true, 'the existing agenda deep-link behavior opens the item');
  assert.equal(document.querySelector('#agenda-deep-link-note').hidden, false);
  assert.ok(dom.window.location.hash.endsWith(`#item-${unmatched.id}`));

  document.querySelector('#agenda-filter-reset').click();
  assert.ok(checkboxes.every((checkbox) => !checkbox.checked));
  // An agenda heading printed as an item ("Consent Calendar") is shown as its section header instead.
  const listed = items.filter((item) => !document.getElementById(`item-${item.id}`)?.matches('[data-agenda-section]'));
  assert.deepEqual(itemIds(visibleItems(document)), listed.map((item) => item.id));
  assert.equal(new URLSearchParams(dom.window.location.search).has('topic'), false);
  assert.equal(dom.window.location.hash, `#item-${unmatched.id}`);
  dom.window.close();
});

test('uncategorized agenda items are unlabeled and excluded when a topic is selected', async () => {
  const meeting = record('ttd:1002');
  assert.ok(meeting.items.some((item) => shown(item) && item.topics.length === 0));
  const dom = await openMeeting(meeting, '?topic=transit');
  const uncategorized = [...dom.window.document.querySelectorAll('[data-agenda-item]')].find((item) => item.dataset.topicIds === '[]');
  assert.ok(uncategorized);
  assert.equal(uncategorized.hidden, true, 'uncategorized items do not match a selected topic');
  assert.equal(uncategorized.querySelector('.agenda-summary-topics'), null, 'uncategorized items carry no topic label');
  dom.window.close();
});

test('a meeting without captured agenda items has no empty filter controls', async () => {
  const meeting = record('truckee:3003');
  assert.equal(meeting.items.length, 0);
  const dom = await openMeeting(meeting);
  assert.equal(dom.window.document.querySelector('#agenda-topic-filter'), null);
  assert.ok(dom.window.document.querySelector('.empty-panel[data-agenda-availability]'));
  dom.window.close();
});

test('agenda sections group items, hide when no item matches a topic, and open for a deep link', async () => {
  // Consent Calendar and New Business, each with tagged items, and an uncategorized item in New Business.
  const meeting = record('slt:2002');
  const dom = await openMeeting(meeting);
  const { document } = dom.window;
  const sections = [...document.querySelectorAll('[data-agenda-section]')];
  assert.deepEqual(sections.map((section) => section.querySelector('.agenda-section-summary h3').textContent), ['Consent Calendar', 'New Business']);
  assert.ok(sections.every((section) => section.open), 'sections start expanded');
  for (const section of sections) {
    const name = section.querySelector('.agenda-section-summary h3').textContent;
    for (const row of section.querySelectorAll('[data-agenda-item]')) {
      assert.equal(meeting.items.find((item) => item.id === row.dataset.itemId)?.section, name);
      assert.equal(row.querySelector('.agenda-section-name'), null, 'a nested item does not repeat its section name');
    }
  }

  document.querySelector('[data-section-toggle="close"]').click();
  assert.ok(sections.every((section) => !section.open));
  document.querySelector('[data-section-toggle="open"]').click();
  assert.ok(sections.every((section) => section.open));

  const topic = meeting.items.find((item) => item.topics.length && item.section).topics[0];
  const checkbox = document.querySelector(`#agenda-topic-filter input[value="${topic}"]`);
  checkbox.checked = true;
  checkbox.dispatchEvent(new dom.window.Event('change'));
  for (const section of sections) {
    const shown = [...section.querySelectorAll('[data-agenda-item]')].filter((item) => !item.hidden).length;
    assert.equal(section.hidden, shown === 0);
  }
  const target = meeting.items.find((item) => item.section && document.getElementById(`item-${item.id}`)?.matches('[data-agenda-item]'));
  dom.window.close();

  const linked = await openMeeting(meeting, `#item-${target.id}`);
  const row = linked.window.document.getElementById(`item-${target.id}`);
  assert.equal(row.open, true);
  assert.equal(row.closest('[data-agenda-section]').open, true);
  linked.window.close();
});
