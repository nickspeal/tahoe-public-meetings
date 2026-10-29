import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { shown } from './helpers/shown-items.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const data = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));
const topicRank = new Map(data.topics.map((topic, index) => [topic.id, index]));

function routeId(id) {
  return encodeURIComponent(id).replaceAll('%3A', ':');
}

async function meetingDocument(meeting) {
  const html = await readFile(resolve(root, 'dist', 'meetings', routeId(meeting.id), 'index.html'), 'utf8');
  return new JSDOM(html).window.document;
}

test('timed listings offer an add-to-calendar menu while date-only listings explain why one is unavailable', async () => {
  const timed = data.meetings.find((meeting) => meeting.startsAt && meeting.updatedAt && meeting.status !== 'canceled' && meeting.change !== 'canceled');
  const dateOnly = data.meetings.find((meeting) => !meeting.startsAt && meeting.date && meeting.status !== 'canceled' && meeting.change !== 'canceled');
  assert.ok(timed, 'export should include a meeting with a verified start time');
  assert.ok(dateOnly, 'export should include a date-only meeting');

  const timedDocument = await meetingDocument(timed);
  const menu = timedDocument.querySelector('.calendar-download-group details.calendar-menu');
  assert.ok(menu);
  assert.match(menu.querySelector('summary').textContent, /Add to calendar/);
  const links = [...menu.querySelectorAll('.calendar-options a')];
  assert.equal(links[0].getAttribute('href'), `/calendar/${timed.agencyId}/${timed.sourceId}.ics`);
  assert.ok(links[0].hasAttribute('download'));
  assert.match(links[1].getAttribute('href'), /^https:\/\/calendar\.google\.com\/calendar\/render\?action=TEMPLATE&/);
  assert.match(links[2].getAttribute('href'), /^https:\/\/outlook\.live\.com\/calendar\/0\/action\/compose\?/);
  assert.match(timedDocument.querySelector('.calendar-download-group > small')?.textContent ?? '', /will not change if the meeting is rescheduled/);

  const dateOnlyDocument = await meetingDocument(dateOnly);
  assert.equal(dateOnlyDocument.querySelector('.calendar-menu'), null);
  assert.match(dateOnlyDocument.querySelector('.calendar-unavailable')?.textContent ?? '', /date but not a confirmed start time/);
});

test('canceled meetings offer no calendar event and no calendar file', async () => {
  const canceled = data.meetings.filter((meeting) => meeting.startsAt && (meeting.status === 'canceled' || meeting.change === 'canceled'));
  assert.ok(canceled.length > 0, 'export should include a timed canceled meeting');
  for (const meeting of canceled) {
    const document = await meetingDocument(meeting).catch(() => null);
    if (document) {
      assert.equal(document.querySelector('.calendar-menu'), null);
      assert.equal(document.querySelector('.calendar-unavailable'), null);
    }
    await assert.rejects(readFile(resolve(root, 'dist', 'calendar', meeting.agencyId, `${meeting.sourceId}.ics`), 'utf8'), { code: 'ENOENT' });
  }
});

test('agenda item topic tags follow the shared catalog order', async () => {
  const meeting = data.meetings.find((candidate) => candidate.items.some((item) => item.topics.length > 1 &&
    JSON.stringify(item.topics) !== JSON.stringify([...item.topics].sort((a, b) => (topicRank.get(a) ?? Number.MAX_SAFE_INTEGER) - (topicRank.get(b) ?? Number.MAX_SAFE_INTEGER) || a.localeCompare(b)))));
  assert.ok(meeting, 'export should include an item whose topic order can verify catalog sorting');
  const document = await meetingDocument(meeting);
  const item = meeting.items.find((candidate) => candidate.topics.length > 1 &&
    JSON.stringify(candidate.topics) !== JSON.stringify([...candidate.topics].sort((a, b) => (topicRank.get(a) ?? Number.MAX_SAFE_INTEGER) - (topicRank.get(b) ?? Number.MAX_SAFE_INTEGER) || a.localeCompare(b))));
  const row = document.querySelector(`[data-item-id="${item.id}"]`);
  const expected = [...item.topics].sort((a, b) => (topicRank.get(a) ?? Number.MAX_SAFE_INTEGER) - (topicRank.get(b) ?? Number.MAX_SAFE_INTEGER) || a.localeCompare(b));
  assert.deepEqual(JSON.parse(row.dataset.topicIds), expected);
  assert.deepEqual([...row.querySelectorAll('.agenda-summary-topics .tag')].map((tag) => tag.textContent), expected.map((id) => data.topics.find((topic) => topic.id === id)?.name ?? id));
});

test('agenda items show only the agency\'s own numbers and one row of topics', async () => {
  const meeting = data.meetings.find((candidate) => candidate.kind !== 'forecast' &&
    candidate.items.some((item) => shown(item) && item.number) && candidate.items.some((item) => shown(item) && !item.number));
  assert.ok(meeting, 'export should include a meeting mixing numbered and unnumbered items');
  const document = await meetingDocument(meeting);
  for (const item of meeting.items.filter((candidate) => shown(candidate))) {
    const row = document.querySelector(`[data-item-id="${item.id}"]`);
    if (!row) continue; // a section heading shown as its section's header
    assert.equal(row.querySelector('.agenda-order').textContent, item.number, `${item.id} shows only its printed number`);
    assert.equal(row.querySelectorAll('.tag').length, item.topics.length, `${item.id} lists each topic once`);
  }
  assert.doesNotMatch(document.querySelector('.meeting-time').textContent, /America\/Los_Angeles|P[DS]T/);
  assert.doesNotMatch(document.querySelector('.detail-main').textContent, /AGENDA AT A GLANCE|Not categorized/i);
});

test('a meeting without listed items explains whether its agenda is posted, pending, or only on the agency site', async () => {
  const listed = (meeting) => meeting.items.some((item) => shown(item));
  const stored = data.meetings.filter((meeting) => meeting.kind !== 'forecast' && !listed(meeting) && meeting.status !== 'canceled');
  const today = new Date().toISOString().slice(0, 10);
  const isAgenda = (document) => /agenda|packet/i.test(document.title) && !/supplement/i.test(document.title);
  const posted = stored.find((meeting) => meeting.documents.some(isAgenda));
  const synopsisOnly = stored.find((meeting) => meeting.documents.length && !meeting.documents.some(isAgenda));
  if (synopsisOnly) {
    // Minutes or a synopsis are not an agenda; the page must not say one was posted.
    const panel = (await meetingDocument(synopsisOnly)).querySelector('.empty-panel');
    assert.equal(panel.dataset.agendaAvailability, 'elsewhere');
    assert.equal(panel.querySelector('a').getAttribute('href'), synopsisOnly.documents[0].url);
  }
  if (posted) {
    const panel = (await meetingDocument(posted)).querySelector('.empty-panel');
    assert.equal(panel.dataset.agendaAvailability, 'posted');
    assert.match(panel.textContent, /Open the official agenda/);
    assert.ok(posted.documents.some((document) => document.url === panel.querySelector('a').getAttribute('href')));
  }
  const pending = stored.find((meeting) => !meeting.documents.length && meeting.date && meeting.date > today &&
    meeting.warnings.some((warning) => /no agenda document is linked|did not link an agenda|not posted/i.test(warning)));
  const blocked = stored.find((meeting) => !meeting.documents.length && meeting.date && meeting.date > today &&
    meeting.warnings.some((warning) => /includes the agenda|agenda PDF was verified/i.test(warning)));
  if (blocked) {
    // The agency has an agenda that could not be captured, so the page must not claim it is unpublished.
    const document = await meetingDocument(blocked);
    assert.equal(document.querySelector('.empty-panel').dataset.agendaAvailability, 'elsewhere');
    assert.doesNotMatch(document.querySelector('.detail-main').textContent, /not posted yet/);
  }
  if (pending) {
    const document = await meetingDocument(pending);
    assert.equal(document.querySelector('.empty-panel').dataset.agendaAvailability, 'not-posted');
    assert.match(document.querySelector('.detail-main h2').textContent, /Agenda not posted yet/);
    assert.match(document.querySelector('.meeting-title-line .status-pill').textContent, /Expected · no agenda yet/);
    if (!pending.location) assert.doesNotMatch(document.querySelector('.meeting-hero-meta').textContent, /Location/);
    // The agenda panel and pill already say the agenda is missing; discovery notes are not for readers.
    assert.doesNotMatch(document.querySelector('.meeting-detail-hero').textContent, /source review|did not (?:link|include) an agenda|No agenda document/i);
  }
  const past = stored.find((meeting) => !meeting.documents.length && meeting.date && meeting.date < today &&
    !meeting.warnings.some((warning) => /Municode/.test(warning)));
  if (past) {
    const panel = (await meetingDocument(past)).querySelector('.empty-panel');
    assert.equal(panel.dataset.agendaAvailability, 'elsewhere');
    assert.equal(panel.querySelector('a').getAttribute('href'), past.officialUrl);
  }
  assert.ok(posted || pending || past, 'export should include at least one meeting without listed items');
});

test('the agenda history lists found and changed documents, with the source check as a footnote', async () => {
  const meeting = data.meetings.find((candidate) => candidate.kind !== 'forecast' && candidate.history?.length);
  assert.ok(meeting, 'export should include a meeting with document history');
  const card = (await meetingDocument(meeting)).querySelector('.agenda-history');
  assert.match(card.querySelector('.eyebrow').textContent, /AGENDA HISTORY/);
  const entries = [...card.querySelectorAll('.history-list li')];
  assert.ok(entries.length >= 1 && entries.length <= meeting.history.length);
  assert.equal(entries[0].querySelector('time').getAttribute('datetime'), meeting.history[0].at);
  assert.match(entries[0].textContent, new RegExp(`${meeting.history[0].document.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}.* found`));
  assert.match(card.querySelector('.history-note').textContent, /first found each version.*(Last checked|Source capture|Not checked)/);

  const pending = data.meetings.find((candidate) => candidate.kind !== 'forecast' && candidate.history && !candidate.history.length);
  if (pending) {
    const empty = (await meetingDocument(pending)).querySelector('.agenda-history');
    assert.equal(empty.querySelector('h2').textContent, 'No agenda found yet');
    assert.equal(empty.querySelector('.history-list'), null);
  }
});
