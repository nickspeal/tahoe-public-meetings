import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const source = await readFile(resolve(root, 'src/lib/agenda-excerpt.ts'), 'utf8');
const javascript = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { itemDetail } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);

const excerpt = (quote) => ({ status: 'published', method: 'deterministic-source-excerpt-v1', text: `Agenda excerpt: “${quote}”`,
  generatedAt: '', citations: [{ documentId: 'd', page: 1, quote }] });

test('an excerpt that only repeats the title is hidden', () => {
  assert.equal(itemDetail({ title: 'Roll Call', summary: excerpt('Roll Call') }), null);
  assert.equal(itemDetail({ title: 'Approval of Minutes of April 1, 2026', summary: excerpt('For Possible Action: Approval of Minutes of April 1, 2026      Page 1') }), null);
  assert.equal(itemDetail({ title: 'Grant Application for the Pine Boulevard Complete Streets Project',
    summary: excerpt('Grant Application for the Pine Boulevard\n    Complete Streets Project') }), null);
});

test('a packet page column inside a wrapped TTD title is not agenda text', () => {
  // TTD, October 7, 2026: the page number sits at the end of the title's first line.
  const quote = 'For Possible Action: Review and Accept the District’s Financial              32\n'
    + '            Statement of Operations for the First Month of Fiscal Year 2027 Through\n            July 31, 2026';
  assert.equal(itemDetail({ title: 'Review and Accept the District’s Financial Statement of Operations for the First Month of Fiscal Year 2027 Through July 31, 2026',
    summary: excerpt(quote) }), null);
  const detail = itemDetail({ title: 'x', summary: excerpt('Adopt Resolution 2026-12 for Phase 2      Page 1') });
  assert.equal(detail.text, 'Adopt Resolution 2026-12 for Phase 2');
});

test('an excerpt with agenda text beyond the title is shown without its label prefix', () => {
  const detail = itemDetail({ title: 'CLOSING STATEMENTS', summary: excerpt('CLOSING STATEMENTS (Statements from the Board and Staff no discussion is permitted)') });
  assert.deepEqual(detail, { label: 'From the agenda', text: 'CLOSING STATEMENTS (Statements from the Board and Staff no discussion is permitted)' });
});

test('unpublished summaries are hidden and other summary methods are labeled as summaries', () => {
  assert.equal(itemDetail({ title: 'x', summary: { ...excerpt('x y z w v u'), status: 'pending' } }), null);
  assert.equal(itemDetail({ title: 'x', summary: null }), null);
  assert.deepEqual(itemDetail({ title: 'x', summary: { ...excerpt('x'), method: 'claude-staff-report-v1', text: 'Staff recommend approval.' } }),
    { label: 'Summary', text: 'Staff recommend approval.' });
});
