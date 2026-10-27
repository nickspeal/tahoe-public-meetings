import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const source = await readFile(resolve(root, 'src/lib/source-links.ts'), 'utf8');
const javascript = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const links = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);

const doc = (overrides = {}) => ({ id: 'v1', title: 'agenda', url: 'https://example.gov/agenda.pdf', pageCount: 7, ...overrides });

test('PDF agenda items link to the cited page with a page-number detail', () => {
  const meeting = { documents: [doc()] };
  assert.deepEqual(links.itemSourceLink(meeting, { citations: [{ documentId: 'v1', page: 7, quote: 'x' }] }),
    { href: 'https://example.gov/agenda.pdf#page=7', label: 'Read in the agenda', detail: 'Page 7 · official PDF' });
  const packet = { documents: [doc({ title: 'agenda_packet', url: 'https://example.gov/p.pdf#view=FitH' })] };
  assert.deepEqual(links.itemSourceLink(packet, { citations: [{ documentId: 'v1', page: 2, quote: 'x' }] }),
    { href: 'https://example.gov/p.pdf#view=FitH&page=2', label: 'Read in the agenda packet', detail: 'Page 2 · official PDF' });
});

test('HTML agendas link without a page fragment, and uncited items have no link', () => {
  const html = { documents: [doc({ url: 'https://meetings.example.gov/agenda/view.aspx?id=4', pageCount: 0 })] };
  assert.deepEqual(links.itemSourceLink(html, { citations: [{ documentId: 'v1', page: 1, quote: 'x' }] }),
    { href: 'https://meetings.example.gov/agenda/view.aspx?id=4', label: 'Read in the agenda', detail: 'Official agency webpage' });
  assert.equal(links.itemSourceLink({ documents: [doc()] }, { citations: [] }), null);
  assert.equal(links.itemSourceLink({ documents: [] }, { citations: [{ documentId: 'v1', page: 1, quote: 'x' }] }), null);
});

test('PDF detection covers redirect and document-center URLs', () => {
  assert.equal(links.isPdfDocument(doc({ url: 'https://meetings.municode.com/d/f?u=https://x/Packet.pdf&n=Agenda.pdf' })), true);
  assert.equal(links.isPdfDocument(doc({ url: 'https://www.placer.ca.gov/DocumentCenter/View/117363/Agenda', pageCount: 4 })), true);
  assert.equal(links.isPdfDocument(doc({ url: 'https://example.gov/agenda.html', pageCount: 3 })), false);
  assert.equal(links.isPdfDocument(doc({ url: 'not a url' })), false);
});

test('every cited agenda item on a built meeting page links to its official page', async () => {
  const data = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));
  const meeting = data.meetings.find((candidate) => candidate.items.some((item) => item.citations.length));
  assert.ok(meeting, 'export should include a meeting with cited agenda items');
  const routeId = encodeURIComponent(meeting.id).replaceAll('%3A', ':');
  const html = await readFile(resolve(root, 'dist', 'meetings', routeId, 'index.html'), 'utf8');
  const page = new JSDOM(html).window.document;
  for (const item of meeting.items.filter((candidate) => !candidate.suppressed && candidate.citations.length)) {
    const expected = links.itemSourceLink(meeting, item);
    const anchor = page.querySelector(`[data-item-id="${item.id}"] a.agenda-source-link`);
    assert.ok(anchor, `${item.id} should offer a direct source link`);
    assert.equal(anchor.getAttribute('href'), expected.href);
    assert.equal(anchor.querySelector('strong').textContent, expected.label);
    assert.equal(page.querySelectorAll(`[data-item-id="${item.id}"] a[href]`).length, 1, `${item.id} links to its source once`);
    assert.equal(anchor.getAttribute('target'), '_blank');
  }
});


test('stored HTML source sections never get PDF fragments even on extensionless URLs', () => {
  const html = { documents: [doc({ url: 'https://example.gov/agenda/42', mediaType: 'text/html', pageCount: 1 })] };
  assert.deepEqual(links.itemSourceLink(html, { citations: [{ documentId: 'v1', page: 1, quote: 'Housing' }] }),
    { href: 'https://example.gov/agenda/42', label: 'Read in the agenda', detail: 'Official agency webpage' });
  assert.equal(links.citationHref('https://example.gov/agenda/42', 1, 'text/html'), 'https://example.gov/agenda/42');
});
