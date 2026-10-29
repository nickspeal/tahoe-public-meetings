import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const data = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));
const periods = data.commentPeriods ?? [];

async function page(path) {
  const html = await readFile(resolve(root, 'dist', ...path.split('/').filter(Boolean).map(decodeURIComponent), 'index.html'), 'utf8');
  return new JSDOM(html).window.document;
}

const closed = (period) => period.closesAt ? Date.parse(period.closesAt) < Date.now() : period.closesOn < new Date().toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' });
const deadline = (period) => period.closesAt ? Date.parse(period.closesAt) : Date.parse(`${period.closesOn}T23:59:59-08:00`);

test('the index lists open periods soonest first and past periods most recent first, each linking to its own page', async () => {
  const index = await page('/comment-periods/');
  const cards = (selector) => [...index.querySelectorAll(selector)].map((card) => card.dataset.commentPeriod);
  const current = periods.filter((period) => !closed(period)).sort((a, b) => deadline(a) - deadline(b) || a.id.localeCompare(b.id));
  const past = periods.filter(closed).sort((a, b) => deadline(b) - deadline(a) || a.id.localeCompare(b.id));
  assert.deepEqual(cards('[aria-labelledby="current-comment-periods"] [data-comment-period]'), current.map((period) => period.id));
  assert.deepEqual(cards('[aria-labelledby="past-comment-periods"] [data-comment-period]'), past.map((period) => period.id));
  if (!past.length) assert.equal(index.querySelector('#past-comment-periods'), null);
  for (const period of periods) {
    const link = index.querySelector(`[data-comment-period="${period.id}"] h3 a`);
    assert.equal(link.textContent, period.title);
    assert.equal(link.getAttribute('href'), `/comment-periods/${period.agencyId}/${period.id.slice(period.agencyId.length + 1)}/`);
  }
});

test('each comment period page shows its instructions, notice quote, and closed state', async () => {
  for (const period of periods) {
    const document = await page(`/comment-periods/${period.agencyId}/${period.id.slice(period.agencyId.length + 1)}/`);
    assert.equal(document.querySelector('h1').textContent, period.title);
    assert.equal(document.querySelector('.comment-period-quote').textContent, period.sourceQuote);
    if (period.instructions) assert.equal(document.querySelector('.detail-aside .comment-period-instructions').textContent, period.instructions);
    const project = [...document.querySelectorAll('.comment-period-project dd')].map((dd) => dd.textContent);
    assert.deepEqual(project, [period.projectTitle, period.projectProponent].filter(Boolean));
    assert.equal(document.querySelector('.comment-period-summary')?.textContent ?? null, period.summary ?? null);
    const linked = new Set(period.meetingIds ?? []);
    const expected = data.meetings.filter((meeting) => linked.has(meeting.id));
    assert.equal(document.querySelectorAll('.comment-period-meetings .meeting-card').length, expected.length);
    assert.equal(Boolean(document.querySelector('[aria-labelledby="related-meetings"] .muted-panel')), expected.length === 0);
    for (const meeting of expected.filter((candidate) => candidate.kind !== 'forecast')) {
      assert.ok(document.querySelector(`.comment-period-meetings a[href="/meetings/${encodeURIComponent(meeting.id).replaceAll('%3A', ':')}/"]`), `${meeting.id} should link to its page`);
    }
    for (const link of document.querySelectorAll('.comment-period-summary a')) assert.ok(period.summary.includes(link.getAttribute('href')));
    assert.ok([...document.querySelectorAll('a')].some((link) => link.getAttribute('href') === period.officialUrl));
    assert.equal(Boolean(document.querySelector('.warning-note')), closed(period));
    assert.match(document.querySelector('.status-pill').textContent, closed(period) ? /Closed/ : /Open for comment|Not yet open/);
  }
});
