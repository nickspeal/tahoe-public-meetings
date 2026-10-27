import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const publicData = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));
const filterSource = await readFile(resolve(root, 'src/scripts/meeting-agenda-filter.ts'), 'utf8');
const openSource = await readFile(resolve(root, 'src/scripts/open-agenda-item.ts'), 'utf8');
const transpile = (source) => ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const filterScript = transpile(filterSource);
const openScript = transpile(openSource);

function routeId(id) {
  return encodeURIComponent(id).replaceAll('%3A', ':');
}

function topicCombinationMeeting() {
  return publicData.meetings.find((meeting) => {
    const items = meeting.items.filter((item) => !item.suppressed);
    const topicIds = new Set(items.flatMap((item) => item.topics));
    return items.some((item) => item.topics.length) && topicIds.size >= 2;
  });
}

async function openMeeting(meeting, query = '') {
  const html = await readFile(resolve(root, 'dist', 'meetings', routeId(meeting.id), 'index.html'), 'utf8');
  const dom = new JSDOM(html, {
    url: `http://localhost/meetings/${routeId(meeting.id)}/${query}`,
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
  const meeting = topicCombinationMeeting();
  assert.ok(meeting, 'the exported meetings should include at least one agenda with multiple topics');
  const items = meeting.items.filter((item) => !item.suppressed).sort((a, b) => a.order - b.order);
  const [firstTopic, secondTopic] = [...new Set(items.flatMap((item) => item.topics))];
  const expected = items.filter((item) => item.topics.includes(firstTopic) || item.topics.includes(secondTopic));
  assert.ok(expected.length < items.length, 'fixture should include at least one item outside the selected topics');

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

  const unmatched = items.find((item) => item.topics.length && !item.topics.includes(firstTopic) && !item.topics.includes(secondTopic));
  if (unmatched) {
    dom.window.history.replaceState({}, '', `${dom.window.location.pathname}?topic=${firstTopic}#item-${unmatched.id}`);
    dom.window.dispatchEvent(new dom.window.HashChangeEvent('hashchange'));
    const target = document.getElementById(`item-${unmatched.id}`);
    assert.equal(target.hidden, false, 'a deep-linked item remains visible outside the selected topics');
    assert.equal(target.open, true, 'the existing agenda deep-link behavior opens the item');
    assert.equal(document.querySelector('#agenda-deep-link-note').hidden, false);
    assert.ok(dom.window.location.hash.endsWith(`#item-${unmatched.id}`));
  }

  document.querySelector('#agenda-filter-reset').click();
  assert.ok(checkboxes.every((checkbox) => !checkbox.checked));
  // An agenda heading printed as an item ("Consent Calendar") is shown as its section header instead.
  const listed = items.filter((item) => !document.getElementById(`item-${item.id}`)?.matches('[data-agenda-section]'));
  assert.deepEqual(itemIds(visibleItems(document)), listed.map((item) => item.id));
  assert.equal(new URLSearchParams(dom.window.location.search).has('topic'), false);
  if (unmatched) assert.equal(dom.window.location.hash, `#item-${unmatched.id}`);
  dom.window.close();
});

test('uncategorized agenda items are labeled and excluded when a topic is selected', async () => {
  const meeting = publicData.meetings.find((candidate) => candidate.items.some((item) => !item.suppressed && item.topics.length === 0));
  if (!meeting) return;
  const topic = [...new Set(meeting.items.flatMap((item) => item.topics))][0];
  if (!topic) return;
  const dom = await openMeeting(meeting, `?topic=${topic}`);
  const uncategorized = [...dom.window.document.querySelectorAll('[data-agenda-item]')].find((item) => item.dataset.topicIds === '[]');
  assert.ok(uncategorized);
  assert.equal(uncategorized.hidden, true, 'uncategorized items do not match a selected topic');
  assert.match(uncategorized.querySelector('.agenda-summary-topics').textContent, /Not categorized/);
  dom.window.close();
});

test('a meeting without captured agenda items has no empty filter controls', async () => {
  const meeting = publicData.meetings.find((candidate) => candidate.items.every((item) => item.suppressed));
  if (!meeting) return;
  const dom = await openMeeting(meeting);
  assert.equal(dom.window.document.querySelector('#agenda-topic-filter'), null);
  assert.ok(dom.window.document.querySelector('.empty-panel[data-agenda-availability]'));
  dom.window.close();
});

function sectionedMeeting() {
  return publicData.meetings.find((meeting) => {
    const items = meeting.items.filter((item) => !item.suppressed);
    const sections = new Set(items.map((item) => item.section).filter(Boolean));
    return sections.size >= 2 && items.some((item) => item.topics.length) && items.some((item) => !item.topics.length && item.section);
  });
}

test('agenda sections group items, hide when no item matches a topic, and open for a deep link', async () => {
  const meeting = sectionedMeeting();
  assert.ok(meeting, 'the exported meetings should include an agenda with several named sections');
  const dom = await openMeeting(meeting);
  const { document } = dom.window;
  const sections = [...document.querySelectorAll('[data-agenda-section]')];
  assert.ok(sections.length >= 2);
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
