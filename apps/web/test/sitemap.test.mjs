import assert from 'node:assert/strict';
import { access, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { data, dist } from './helpers/site.mjs';

const SITE = 'https://tahoepublicmeetings.org';
const xml = await readFile(resolve(dist, 'sitemap.xml'), 'utf8');
const locs = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) => match[1]);
const paths = locs.map((loc) => new URL(loc).pathname);
const built = (path) => resolve(dist, ...path.split('/').filter(Boolean).map(decodeURIComponent), 'index.html');

test('the sitemap lists the home page and every meeting page on the production origin', () => {
  assert.match(xml, /^<\?xml version="1.0" encoding="UTF-8"\?>\n<urlset xmlns="http:\/\/www.sitemaps.org\/schemas\/sitemap\/0.9">/);
  assert.ok(locs.every((loc) => loc.startsWith(`${SITE}/`)));
  assert.equal(new Set(locs).size, locs.length);
  assert.ok(paths.includes('/'));
  assert.ok(paths.includes('/about/'));
  // Meeting pages sit below /meetings/ whatever their URL shape; body hubs may add more.
  const meetingPages = paths.filter((path) => /^\/meetings\/.+\/$/.test(path));
  assert.ok(meetingPages.length >= data.meetings.filter((meeting) => meeting.kind !== 'forecast').length);
});

test('the sitemap leaves out the 404 page, calendar files, raw data, and endpoints', async () => {
  await access(resolve(dist, '404.html'));
  assert.ok(!paths.some((path) => path.startsWith('/404') || path.startsWith('/calendar/') || path.startsWith('/data/')));
  assert.ok(!paths.some((path) => /\.(?:json|txt|ics)$/.test(path)));
});

test('every listed page is built and is its own canonical URL', async () => {
  for (const path of paths) {
    const document = new JSDOM(await readFile(built(path), 'utf8')).window.document;
    assert.equal(document.querySelector('link[rel="canonical"]')?.getAttribute('href'), new URL(path, SITE).href, path);
    assert.equal(document.querySelector('meta[name="robots"]'), null, path);
  }
});

test('robots.txt points crawlers at the sitemap', async () => {
  const robots = await readFile(resolve(dist, 'robots.txt'), 'utf8');
  assert.match(robots, new RegExp(`^Sitemap: ${SITE}/sitemap\\.xml$`, 'm'));
});

test('the home page names the site in structured data', async () => {
  const document = new JSDOM(await readFile(built('/'), 'utf8')).window.document;
  const graph = JSON.parse(document.querySelector('script[type="application/ld+json"]').textContent);
  assert.equal(graph['@context'], 'https://schema.org');
  assert.deepEqual(graph['@graph'].map((node) => node['@type']), ['WebSite']);
});
