import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { shown } from './helpers/shown-items.mjs';
import { meetingFile } from './helpers/meeting-paths.mjs';
import { data, dist, record } from './helpers/site.mjs';

const topicRank = new Map(data.topics.map((topic, index) => [topic.id, index]));

async function meetingDocument(meeting) {
  const html = await readFile(await meetingFile(meeting), 'utf8');
  return new JSDOM(html).window.document;
}

test('timed listings offer an add-to-calendar menu while date-only listings explain why one is unavailable', async () => {
  const timed = record('slt:2003');
  const dateOnly = record('truckee:3003');

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
  const meeting = record('truckee:3001');
  assert.ok(meeting.startsAt);
  const document = await meetingDocument(meeting);
  assert.equal(document.querySelector('.calendar-menu'), null);
  assert.equal(document.querySelector('.calendar-unavailable'), null);
  await assert.rejects(readFile(resolve(dist, 'calendar', meeting.agencyId, `${meeting.sourceId}.ics`), 'utf8'), { code: 'ENOENT' });
});

test('agenda item topic tags follow the shared catalog order', async () => {
  const meeting = record('ttd:1001');
  const item = meeting.items.find((candidate) => candidate.title === 'Fleet Electrification Plan');
  assert.deepEqual(item.topics, ['environment', 'transit'], 'stored out of catalog order');
  const document = await meetingDocument(meeting);
  const row = document.querySelector(`[data-item-id="${item.id}"]`);
  const expected = [...item.topics].sort((a, b) => topicRank.get(a) - topicRank.get(b));
  assert.deepEqual(expected, ['transit', 'environment']);
  assert.deepEqual(JSON.parse(row.dataset.topicIds), expected);
  assert.deepEqual([...row.querySelectorAll('.agenda-summary-topics .tag')].map((tag) => tag.textContent), expected.map((id) => data.topics.find((topic) => topic.id === id)?.name ?? id));
});

test('agenda items show only the agency\'s own numbers and one row of topics', async () => {
  const meeting = record('slt:2001');
  assert.ok(meeting.items.some((item) => shown(item) && item.number) && meeting.items.some((item) => shown(item) && !item.number), 'numbered and unnumbered items');
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
  // Minutes or a synopsis are not an agenda; the page must not say one was posted.
  const synopsisOnly = record('south-shore-tma:5101');
  let panel = (await meetingDocument(synopsisOnly)).querySelector('.empty-panel');
  assert.equal(panel.dataset.agendaAvailability, 'elsewhere');
  assert.equal(panel.querySelector('a').getAttribute('href'), synopsisOnly.documents[0].url);

  const posted = record('ivgid:5001');
  panel = (await meetingDocument(posted)).querySelector('.empty-panel');
  assert.equal(panel.dataset.agendaAvailability, 'posted');
  assert.match(panel.textContent, /Open the official agenda/);
  assert.equal(panel.querySelector('a').getAttribute('href'), posted.documents[0].url);

  // Availability is derived from the documents and the date; no stored warning is needed (decision 0033).
  for (const pending of [record('ttd:1004'), record('truckee:3003')]) {
    const document = await meetingDocument(pending);
    assert.equal(document.querySelector('.empty-panel').dataset.agendaAvailability, 'not-posted', pending.id);
    assert.match(document.querySelector('.detail-main h2').textContent, /Agenda not posted yet/);
    assert.match(document.querySelector('.meeting-title-line .status-pill').textContent, /Expected · no agenda yet/);
    // The agenda panel and pill already say the agenda is missing; discovery notes are not for readers.
    assert.doesNotMatch(document.querySelector('.meeting-detail-hero').textContent, /source review|did not (?:link|include) an agenda|No agenda document/i);
  }
  assert.doesNotMatch((await meetingDocument(record('truckee:3003'))).querySelector('.meeting-hero-meta').textContent, /Location/, 'no location row without a location');

  const past = record('tcpud:5201');
  panel = (await meetingDocument(past)).querySelector('.empty-panel');
  assert.equal(panel.dataset.agendaAvailability, 'elsewhere');
  assert.equal(panel.querySelector('a').getAttribute('href'), past.officialUrl);
});

test('the agenda history lists found and changed documents, with the source check as a footnote', async () => {
  const meeting = record('slt:2001');
  assert.ok(meeting.history.length >= 2, 'found, then changed');
  const card = (await meetingDocument(meeting)).querySelector('.agenda-history');
  assert.match(card.querySelector('.eyebrow').textContent, /Agenda history/);
  const entries = [...card.querySelectorAll('.history-list li')];
  assert.ok(entries.length >= 1 && entries.length <= meeting.history.length);
  assert.equal(entries[0].querySelector('time').getAttribute('datetime'), meeting.history[0].at);
  assert.match(entries[0].textContent, new RegExp(`${meeting.history[0].document.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}.* found`));
  assert.match(card.querySelector('.history-note').textContent, /first found each version.*(Last checked|Source capture|Not checked)/);

  const empty = (await meetingDocument(record('truckee:3003'))).querySelector('.agenda-history');
  assert.equal(empty.querySelector('h2').textContent, 'No agenda found yet');
  assert.equal(empty.querySelector('.history-list'), null);
});
