import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const load = async (file) => {
  const source = await readFile(resolve(root, file), 'utf8');
  const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
};
const { itemHeadline, detailsWithFullText } = await load('src/lib/short-title.ts');
const { itemDetails, itemTitle } = await load('src/lib/agenda-excerpt.ts');

const LONG = 'Department of Transportation recommending the Board approve and authorize the Chair to sign the Local Agency Agreement for services, for a term of three years, between the County of El Dorado and Arts and Culture El Dorado concerning the Meyers Roundabout Public Art Project. (District 5)';
const SHORT = 'Approve agreement with Arts and Culture El Dorado for Meyers Roundabout Public Art Project';
const excerpt = (quote) => ({ status: 'published', method: 'deterministic-source-excerpt-v1', text: `Agenda excerpt: “${quote}”`,
  generatedAt: '', citations: [{ documentId: 'd', page: 1, quote }] });
const staffSummary = { status: 'published', method: 'claude-staff-report-v1', text: 'The project installs art at the roundabout.', generatedAt: '', citations: [] };

test('a short title replaces the shown title and is marked as shortened', () => {
  const item = { title: LONG, shortTitle: SHORT, summary: excerpt(LONG) };
  assert.deepEqual(itemHeadline(item, itemTitle(item)), { text: SHORT, shortened: true });
  const plain = { title: LONG, summary: excerpt(LONG) };
  assert.deepEqual(itemHeadline(plain, itemTitle(plain)), { text: LONG, shortened: false });
});

test('the expanded item shows the whole agenda text beside a short title, once', () => {
  // A title that was not cut: its excerpt only repeats it, so the title itself is added.
  const item = { title: LONG, shortTitle: SHORT, summary: staffSummary };
  assert.deepEqual(detailsWithFullText(item, itemDetails(item)).map((d) => d.label), ['From the agenda', 'Summary']);
  assert.equal(detailsWithFullText(item, itemDetails(item))[0].text, LONG);
  // A cut title already shows its whole text (fullTitle); nothing is added.
  const cut = { title: LONG.slice(0, 120), fullTitle: LONG, shortTitle: SHORT, summary: staffSummary };
  assert.deepEqual(detailsWithFullText(cut, itemDetails(cut)).map((d) => d.text), [LONG, staffSummary.text]);
  // An excerpt with more agenda text than the title already contains the title.
  const longer = { title: LONG, shortTitle: SHORT, summary: excerpt(`${LONG} CEQA Consideration: categorical exemption. (Staff: M. Harless)`) };
  assert.equal(detailsWithFullText(longer, itemDetails(longer)).length, 1);
  // Without a short title nothing changes.
  const plain = { title: LONG, summary: staffSummary };
  assert.deepEqual(detailsWithFullText(plain, itemDetails(plain)), itemDetails(plain));
});
