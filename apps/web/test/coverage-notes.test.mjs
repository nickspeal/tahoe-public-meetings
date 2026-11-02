import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const data = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));

test('each body with a coverage note shows it in its own Coverage row, and only there', async () => {
  const html = await readFile(resolve(root, 'dist', 'coverage', 'index.html'), 'utf8');
  const { document } = new JSDOM(html).window;
  const noted = data.agencies.filter((agency) => agency.coverageNote);
  assert.ok(noted.length > 0, 'the fixture export carries registry coverage notes (ttd has one)');
  const shown = [...document.querySelectorAll('[data-coverage-note]')];
  assert.deepEqual(shown.map((note) => note.dataset.coverageNote).sort(), noted.map((agency) => agency.id).sort());
  for (const agency of noted) {
    const note = document.querySelector(`[data-coverage-note="${agency.id}"]`);
    assert.equal(note.textContent, agency.coverageNote);
    assert.equal(note.closest('li').querySelector('h3').textContent, agency.name, 'the note sits under its own body');
  }
});
