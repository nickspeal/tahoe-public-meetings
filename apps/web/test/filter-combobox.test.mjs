import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const transpile = async (file) => ts.transpileModule(await readFile(resolve(root, file), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const comboboxScript = await transpile('src/scripts/filter-combobox.ts');
const filterScript = await transpile('src/scripts/timeline-filters.ts');

async function openPage(file, route) {
  const html = await readFile(resolve(root, 'dist', file), 'utf8');
  const dom = new JSDOM(html, { url: `http://localhost${route}`, runScripts: 'outside-only' });
  dom.window.scrollBy = () => {};
  dom.window.eval(comboboxScript);
  dom.window.eval(filterScript);
  return dom;
}

function type(dom, input, text) {
  input.dispatchEvent(new dom.window.FocusEvent('focus'));
  input.value = text;
  input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
}
const key = (dom, input, name) => input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: name, bubbles: true }));
const shownLabels = (document, id) => [...document.querySelectorAll(`#${id}-list [role="option"]`)].filter((item) => !item.hidden).map((item) => item.querySelector('.combobox-option-label').textContent);
const hintFor = (document, id, label) => [...document.querySelectorAll(`#${id}-list [role="option"]`)].find((item) => item.querySelector('.combobox-option-label').textContent === label).querySelector('.combobox-option-hint').textContent;

test('agency filter matches short names, acronyms, and full official names', async () => {
  const dom = await openPage('index.html', '/');
  const { document } = dom.window;
  const input = document.querySelector('#agency-filter-input');

  type(dom, input, 'tahoe regional planning');
  assert.deepEqual(shownLabels(document, 'agency-filter'), ['TRPA — Advisory Planning Committee', 'TRPA — Governing Board']);
  assert.equal(hintFor(document, 'agency-filter', 'TRPA — Governing Board'), 'Tahoe Regional Planning Agency', 'the list says why an option matched');
  assert.equal(input.getAttribute('aria-expanded'), 'true');

  type(dom, input, 'Tahoe Regional governing');
  assert.deepEqual(shownLabels(document, 'agency-filter'), ['TRPA — Governing Board'], 'a query can mix a full name and the body');

  type(dom, input, 'I.V.G.I.D.');
  assert.deepEqual(shownLabels(document, 'agency-filter'), ['IVGID — Board']);
  type(dom, input, 'incline village general');
  assert.deepEqual(shownLabels(document, 'agency-filter'), ['IVGID — Board']);
  type(dom, input, 'sstma');
  assert.deepEqual(shownLabels(document, 'agency-filter'), ['South Shore Transportation Management Association — Board']);
  type(dom, input, 'tahoe city pud');
  assert.ok(shownLabels(document, 'agency-filter').every((label) => label.startsWith('TCPUD')));
  type(dom, input, 'douglas planning');
  assert.deepEqual(shownLabels(document, 'agency-filter'), ['Douglas County — Planning Commission']);

  type(dom, input, 'zzz');
  assert.deepEqual(shownLabels(document, 'agency-filter'), []);
  assert.equal(document.querySelector('#agency-filter').closest('[data-filter-combobox]').querySelector('.combobox-empty').textContent, 'No agencies match “zzz”');
  dom.window.close();
});

test('choosing a typed match filters the timeline, and Escape restores the current choice', async () => {
  const dom = await openPage('index.html', '/');
  const { document } = dom.window;
  const input = document.querySelector('#agency-filter-input');

  type(dom, input, 'tahoe transportation district');
  key(dom, input, 'Enter');
  assert.equal(document.querySelector('#agency-filter').value, 'ttd');
  assert.equal(input.value, 'Tahoe Transportation District');
  assert.equal(input.getAttribute('aria-expanded'), 'false');
  assert.equal(dom.window.location.search, '?agency=ttd');
  assert.ok([...document.querySelectorAll('[data-meeting-row]')].filter((row) => !row.hidden).every((row) => row.dataset.agency === 'ttd'));

  type(dom, input, 'placer');
  key(dom, input, 'Escape');
  assert.equal(input.value, 'Tahoe Transportation District');
  assert.equal(document.querySelector('#agency-filter').value, 'ttd');

  type(dom, input, '');
  key(dom, input, 'Enter');
  assert.equal(document.querySelector('#agency-filter').value, '', 'clearing the text and pressing Enter chooses All agencies');
  assert.equal(input.value, '');
  assert.equal(dom.window.location.search, '');
  dom.window.close();
});

test('topic filter shows the value from the URL, matches descriptions, and keeps Suggest a topic listed', async () => {
  const dom = await openPage('index.html', '/?topic=transit&agency=ttd');
  const { document } = dom.window;
  const input = document.querySelector('#topic-filter-input');
  assert.equal(input.value, 'Transit');
  assert.equal(document.querySelector('#agency-filter-input').value, 'Tahoe Transportation District');

  type(dom, input, 'road repair');
  assert.deepEqual(shownLabels(document, 'topic-filter'), ['Biking', 'Suggest a topic…']);

  type(dom, input, 'parking garages');
  assert.deepEqual(shownLabels(document, 'topic-filter'), ['Suggest a topic…'], 'with no match the reader can still suggest a topic');
  let opened = null;
  dom.window.addEventListener('feedback:open', (event) => { opened = event.detail; });
  key(dom, input, 'ArrowDown');
  key(dom, input, 'Enter');
  assert.equal(opened?.kind, 'topic');
  assert.equal(document.querySelector('#topic-filter').value, 'transit', 'suggesting a topic keeps the current filter');
  assert.equal(input.value, 'Transit');
  dom.window.close();
});

test('arrow keys browse every option from the current choice', async () => {
  const dom = await openPage('index.html', '/?topic=housing');
  const { document } = dom.window;
  const input = document.querySelector('#topic-filter-input');
  key(dom, input, 'ArrowDown');
  assert.equal(shownLabels(document, 'topic-filter')[0], 'All topics');
  const active = () => document.getElementById(input.getAttribute('aria-activedescendant')).querySelector('.combobox-option-label').textContent;
  assert.equal(active(), 'Housing');
  key(dom, input, 'ArrowDown');
  key(dom, input, 'Enter');
  assert.equal(document.querySelector('#topic-filter').value, 'short-term-rentals');
  assert.equal(dom.window.location.search, '?topic=short-term-rentals');
  dom.window.close();
});
