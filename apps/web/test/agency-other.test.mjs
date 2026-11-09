import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';
import { meetingFile } from './helpers/meeting-paths.mjs';
import { data, dist } from './helpers/site.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const registry = JSON.parse(await readFile(resolve(root, '../../packages/domain/agencies.json'), 'utf8'));
const filterSource = await readFile(resolve(root, 'src/scripts/timeline-filters.ts'), 'utf8');
const filterScript = ts.transpileModule(filterSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
// filter-terms.ts imports the registry JSON and only types otherwise; inline the registry to load it here.
const termsSource = (await readFile(resolve(root, 'src/lib/filter-terms.ts'), 'utf8'))
  .replace(/import registry from .*;\n/, `const registry = ${JSON.stringify(registry)};\n`);
const termsJs = ts.transpileModule(termsSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { filterAgencies, agencySearchTerms, isPerRecordHost } = await import(`data:text/javascript;base64,${Buffer.from(termsJs).toString('base64')}`);

const other = { id: 'other', name: 'Other', shortName: 'Other' };
const body = (id) => ({ id, name: id, shortName: id });

test('Other is the only per-record-host entry, and the filter lists it last', () => {
  assert.deepEqual(registry.filter((entry) => entry.meetingHost === 'per-record').map((entry) => entry.id), ['other']);
  assert.equal(isPerRecordHost('other'), true);
  assert.equal(isPerRecordHost('ttd'), false);
  assert.deepEqual(filterAgencies([body('a'), other, body('z')]).map((agency) => agency.id), ['a', 'z', 'other']);
});

test('Other is searchable by the hosts of its meetings; bodies keep their own terms', () => {
  const meetings = [{ agencyId: 'other', host: 'TRPA and El Dorado County' }, { agencyId: 'other', host: 'TRPA and El Dorado County' },
    { agencyId: 'ttd', host: null }, { agencyId: 'other', host: 'Placer County' }];
  assert.deepEqual(agencySearchTerms(other, meetings), ['TRPA and El Dorado County', 'Placer County']);
  assert.ok(!agencySearchTerms(body('ttd'), meetings).includes('Placer County'));
  assert.deepEqual(agencySearchTerms(other, []), []);
});

async function openPage(route) {
  const html = await readFile(resolve(dist, 'index.html'), 'utf8');
  const dom = new JSDOM(html, { url: `http://localhost${route}`, runScripts: 'outside-only' });
  dom.window.scrollBy = () => {};
  dom.window.eval(filterScript);
  return dom;
}

const optionValues = (document) => [...document.querySelectorAll('#agency-filter option')].map((option) => option.value).filter(Boolean);
// The fixture has two meetings under Other, hosted by "TRPA and El Dorado County" and by "Placer County".
const otherMeetings = data.meetings.filter((meeting) => meeting.agencyId === 'other');

test('the Agency filter offers Other last, searchable by its meetings\' hosts', async () => {
  const { document } = (await openPage('/')).window;
  const values = optionValues(document);
  assert.equal(values.at(-1), 'other');
  const terms = document.querySelector('#agency-filter option[value="other"]').dataset.terms.split('\n');
  assert.ok(terms.includes('TRPA and El Dorado County') && terms.includes('Placer County'));
});

test('?agency=other shows only Other meetings', async () => {
  const dom = await openPage('/?agency=other');
  const { document } = dom.window;
  const shown = [...document.querySelectorAll('[data-meeting-row]')].filter((row) => !row.hidden);
  assert.equal(document.querySelector('#agency-filter').value, 'other');
  assert.ok(shown.length > 0 && shown.every((row) => row.dataset.agency === 'other'));
  dom.window.close();
});

test('?agency=other is ignored when the filter does not offer Other', async () => {
  const html = (await readFile(resolve(dist, 'index.html'), 'utf8')).replace(/<option[^>]*value="other"[^>]*>[^<]*<\/option>/, '');
  assert.ok(!html.includes('value="other"'));
  const dom = new JSDOM(html, { url: 'http://localhost/?agency=other', runScripts: 'outside-only' });
  dom.window.scrollBy = () => {};
  dom.window.eval(filterScript);
  assert.equal(dom.window.document.querySelector('#agency-filter').value, '');
  dom.window.close();
});

test('a meeting under Other names its host on its card and page', async () => {
  assert.equal(otherMeetings.length, 2);
  for (const meeting of otherMeetings) {
    const page = await readFile(await meetingFile(meeting), 'utf8');
    const document = new JSDOM(page).window.document;
    assert.equal(document.querySelector('.meeting-detail-hero .eyebrow').textContent, meeting.host);
    assert.ok(!page.includes('>Other<'));
  }
});
