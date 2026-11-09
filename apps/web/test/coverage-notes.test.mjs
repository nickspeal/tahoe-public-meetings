import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { data, dist } from './helpers/site.mjs';

test('each body with a coverage note shows it in its own Coverage row, and only there', async () => {
  const html = await readFile(resolve(dist, 'coverage', 'index.html'), 'utf8');
  const { document } = new JSDOM(html).window;
  const noted = data.agencies.filter((agency) => agency.coverageNote);
  assert.ok(noted.some((agency) => agency.id === 'ttd'), 'the fixture export carries registry coverage notes (ttd has one)');
  assert.ok(data.agencies.some((agency) => !agency.coverageNote), 'and bodies without one');
  const shown = [...document.querySelectorAll('[data-coverage-note]')];
  assert.deepEqual(shown.map((note) => note.dataset.coverageNote).sort(), noted.map((agency) => agency.id).sort());
  for (const agency of noted) {
    const note = document.querySelector(`[data-coverage-note="${agency.id}"]`);
    assert.equal(note.textContent, agency.coverageNote);
    assert.equal(note.closest('li').querySelector('h3').textContent, agency.name, 'the note sits under its own body');
  }
});

test('each body with a page links its Coverage name to that page, and the page exists', async () => {
  const html = await readFile(resolve(dist, 'coverage', 'index.html'), 'utf8');
  const { document } = new JSDOM(html).window;
  const links = [...document.querySelectorAll('.agency-coverage h3 a')];
  assert.ok(links.some((link) => link.getAttribute('href') === '/meetings/south-lake-tahoe-city-council/'));
  for (const link of links) {
    const href = link.getAttribute('href');
    assert.match(href, /^\/meetings\/[a-z0-9-]+\/$/);
    await readFile(resolve(dist, `.${href}index.html`), 'utf8');
  }
});
