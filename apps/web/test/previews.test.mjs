import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';
import { dataLib } from './helpers/load-data.mjs';

// IDEA-018: previews from notices and planning calendars, one-off meetings from notices, planned meetings.
const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const publicData = JSON.parse(await readFile(resolve(root, 'src/data/public.json'), 'utf8'));
const filterSource = await readFile(resolve(root, 'src/scripts/timeline-filters.ts'), 'utf8');
const filterScript = ts.transpileModule(filterSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const calendarSource = await readFile(resolve(root, 'src/lib/calendar.ts'), 'utf8');
const calendar = await import(`data:text/javascript;base64,${Buffer.from(ts.transpileModule(calendarSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText).toString('base64')}`);
const unfold = (content) => content.replace(/\r\n[ \t]/g, '');

const future = new Date(Date.now() + 20 * 86_400_000);
const futureDay = future.toISOString().slice(0, 10);

function meeting(overrides = {}) {
  return {
    id: 'slt:planned-2026-12-01', agencyId: 'slt', sourceId: 'planned-2026-12-01', kind: 'confirmed',
    title: 'City Council Meeting', topics: [], startsAt: `${futureDay}T17:00:00Z`, date: futureDay,
    timezone: 'America/Los_Angeles', status: 'scheduled', location: null, officialUrl: 'https://city.example/apc.pdf',
    participation: { instructions: null, url: null, remoteUrl: null, deadline: null },
    minutesUrl: null, recordingUrl: null, lastCheckedAt: null, updatedAt: '2026-10-29T12:00:00Z', change: 'new',
    warnings: [], documents: [], items: [], duration: { minutes: 180, source: 'estimated', basis: 'x' },
    ...overrides,
  };
}

function preview(subject, topics = [], source = {}) {
  return { subject, section: null, topics, source: { kind: 'planning-calendar', title: 'Agenda Planning Calendar (Updated 09 30 2026)',
    url: 'https://city.example/apc.pdf', publishedOn: '2026-09-30', quote: subject, page: 2, ...source } };
}

const bikes = preview('Prioritization of Bikes at City Intersections and Bike Lanes', ['biking']);

test('an upcoming meeting with previews shows by default but keeps the no-agenda pill', () => {
  const bare = meeting();
  assert.equal(dataLib.awaitingAgenda(bare), true);
  const withPreviews = meeting({ previews: [bikes] });
  assert.equal(dataLib.hasPreviews(withPreviews), true);
  assert.equal(dataLib.awaitingAgenda(withPreviews), false, 'not behind the reveal');
  assert.equal(dataLib.agendaNotPosted(withPreviews), true);
  assert.equal(dataLib.meetingStateLabel(withPreviews), 'Expected · no agenda yet');
  assert.equal(dataLib.agendaAvailability(withPreviews).kind, 'not-posted');
  assert.deepEqual(dataLib.meetingPreviews(meeting()), []);
});

test('a one-off meeting with only its notice is a posted notice, shown by default without the pill', () => {
  const oneOff = meeting({ id: 'other:one-off-wst-2026-11-02', agencyId: 'other', sourceId: 'one-off-wst-2026-11-02', host: 'TRPA and El Dorado County',
    officialUrl: 'https://agency.example/notice.pdf', warnings: [] });
  const availability = dataLib.agendaAvailability(oneOff);
  assert.deepEqual(availability, { kind: 'notice', label: 'Meeting notice posted', heading: 'Meeting notice',
    body: 'The notice has the details for this meeting.', href: 'https://agency.example/notice.pdf', linkText: 'Open the notice' });
  assert.equal(dataLib.awaitingAgenda(oneOff), false);
  assert.equal(dataLib.meetingStateLabel(oneOff), null);
  assert.equal(dataLib.agendaAvailability({ ...oneOff, status: 'canceled' }).kind, 'canceled');
  assert.equal(dataLib.agendaAvailability({ ...oneOff, sourceId: 'abc' }).kind, 'not-posted', 'only one-off IDs count the notice as posted');
});

test('meeting topics include preview topics, and previews filter by topic', () => {
  const housing = preview('Housing Element Update', ['housing']);
  const planned = meeting({ topics: ['environment'], previews: [bikes, housing] });
  assert.deepEqual(dataLib.meetingTopicIds(planned), ['environment', 'biking', 'housing']);
  assert.deepEqual(dataLib.previewsForTopic(planned, 'biking'), [bikes]);
  assert.deepEqual(dataLib.previewsForTopic(planned), [bikes, housing]);
});

test('source lines name the calendar update date or the notice; planned notes pass through unchanged', () => {
  assert.equal(dataLib.previewSourceLabel(bikes.source), 'From the agenda planning calendar updated September 30, 2026');
  assert.equal(dataLib.previewSourceLabel({ ...bikes.source, kind: 'notice', title: 'West Shore Trail Notice of Preparation' }), 'From the West Shore Trail Notice of Preparation');
  assert.equal(dataLib.residentWarning('Subject to change.'), 'Subject to change.');
  assert.equal(dataLib.residentWarning('Check the notice for changes.'), '', 'the old blanket notice caveat is hidden');
});

test('a planned meeting calendar event starts its description with "Subject to change."', () => {
  const planned = unfold(calendar.buildCalendarEvent(meeting({ planned: true })));
  assert.match(planned, /\r\nDESCRIPTION:Subject to change\.\\n/);
  const regular = unfold(calendar.buildCalendarEvent(meeting()));
  assert.doesNotMatch(regular, /Subject to change/);
  const published = unfold(calendar.buildCalendarEvent(meeting({ duration: { minutes: 90, source: 'published', basis: 'x' } })));
  assert.doesNotMatch(published, /Estimated length/);
});

// The filter script against a minimal timeline: one meeting with five planned subjects, one awaiting its agenda.
function syntheticTimeline(route) {
  const subject = (text, topics) => `<span class="meeting-preview" data-preview-topics='${JSON.stringify(topics)}'>${text}</span>`;
  const html = `<!doctype html><body>
    <select id="topic-filter"><option value="">All topics</option><option value="biking">Biking</option><option value="housing">Housing</option></select>
    <select id="agency-filter"><option value="">All agencies</option></select>
    <span id="timeline-count"></span><span id="agenda-count"></span>
    <section data-timeline-group="future-x">
      <div data-meeting-row data-agency="slt" data-topics='["biking","housing"]' data-bucket="upcoming" data-canceled="false" data-forecast="false" data-awaiting-agenda="false" data-previews="true">
        <p data-meeting-previews data-preview-limit="3">Planned: ${subject('A', ['housing'])}${subject('B', [])}${subject('C', [])}${subject('Bikes one', ['biking'])}${subject('Bikes two', ['biking'])}<span data-preview-more>and 2 more</span></p>
        <div data-topic-items="biking" hidden><a data-topic-planned href="#"><strong>Bikes one</strong></a><a data-topic-planned href="#"><strong>Bikes two</strong></a></div>
        <div data-topic-items="housing" hidden><a data-topic-planned href="#"><strong>A</strong></a></div>
      </div>
      <div data-meeting-row data-agency="ttd" data-topics='["biking"]' data-bucket="upcoming" data-canceled="false" data-forecast="false" data-awaiting-agenda="true" data-previews="false" hidden></div>
    </section>
    <div id="upcoming-more"><button id="upcoming-toggle" type="button"><span id="upcoming-toggle-label"></span></button><p id="upcoming-hint"></p></div>
  </body>`;
  const dom = new JSDOM(html, { url: `http://localhost${route}`, runScripts: 'outside-only' });
  dom.window.eval(filterScript);
  return dom;
}
const shownSubjects = (document) => [...document.querySelectorAll('[data-preview-topics]')].filter((span) => !span.hidden).map((span) => span.textContent);

test('the timeline shows the first three planned subjects, and lists the matching ones under the card with a topic', () => {
  const dom = syntheticTimeline('/');
  const { document } = dom.window;
  const [planned, awaiting] = document.querySelectorAll('[data-meeting-row]');
  assert.equal(planned.hidden, false);
  assert.equal(awaiting.hidden, true);
  assert.equal(document.querySelector('#upcoming-toggle-label').textContent, 'Show 1 upcoming meeting without a posted agenda');
  assert.deepEqual(shownSubjects(document), ['A', 'B', 'C']);
  assert.equal(document.querySelector('[data-preview-more]').textContent, 'and 2 more');
  dom.window.close();

  const biking = syntheticTimeline('/?topic=biking');
  assert.equal(biking.window.document.querySelector('[data-meeting-previews]').hidden, true);
  assert.equal(biking.window.document.querySelector('[data-topic-items="biking"]').hidden, false);
  assert.equal(biking.window.document.querySelector('[data-topic-items="housing"]').hidden, true);
  assert.equal(biking.window.document.querySelector('#agenda-count').textContent, '2 related agenda items');
  biking.window.close();
});

// Against the built site, when the export carries IDEA-018 records.
const routeId = (id) => encodeURIComponent(id).replaceAll('%3A', ':');
const builtToday = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
const upcoming = (record) => (record.date ?? record.startsAt?.slice(0, 10) ?? '') > builtToday;
const stored = publicData.meetings.filter((record) => record.kind !== 'forecast');
const withPreviews = stored.filter((record) => record.previews?.length && upcoming(record));
const planned = stored.filter((record) => record.planned);
const noticeOneOffs = stored.filter((record) => record.sourceId.startsWith('one-off-') && !record.documents.length && !record.items.length && record.status !== 'canceled' && upcoming(record));
const skip = (records, what) => (records.length ? false : `the export has no ${what} yet`);

async function openPage(file, route) {
  const html = await readFile(resolve(root, 'dist', file), 'utf8');
  const dom = new JSDOM(html, { url: `http://localhost${route}`, runScripts: 'outside-only' });
  dom.window.scrollBy = () => {};
  dom.window.eval(filterScript);
  return dom;
}
const rowFor = (document, id) => [...document.querySelectorAll('[data-meeting-row]')].find((row) => row.dataset.meetingId === id);
const pageFor = async (record) => new JSDOM(await readFile(resolve(root, 'dist', 'meetings', routeId(record.id), 'index.html'), 'utf8')).window.document;

test('upcoming meetings with previews show by default with the pill and are not counted as awaiting', { skip: skip(withPreviews, 'upcoming meeting with previews') }, async () => {
  const dom = await openPage('index.html', '/');
  const { document } = dom.window;
  for (const record of withPreviews) {
    const row = rowFor(document, record.id);
    assert.ok(row, record.id);
    assert.equal(row.hidden, false, `${record.id} is shown by default`);
    assert.equal(row.dataset.awaitingAgenda, 'false');
    assert.equal(row.dataset.previews, 'true');
    const pill = dataLib.agendaNotPosted(record) && record.status === 'scheduled' && !['revised', 'rescheduled', 'canceled'].includes(record.change);
    if (pill) assert.match(row.querySelector('.status-pill')?.textContent ?? '', /Expected · no agenda yet/);
    const line = row.querySelector('[data-meeting-previews]');
    assert.match(line.textContent, /^Planned:/);
    const subjects = [...line.querySelectorAll('[data-preview-topics]')];
    assert.deepEqual(subjects.map((span) => span.textContent), record.previews.map((entry) => entry.subject));
    assert.equal(subjects.filter((span) => !span.hidden).length, Math.min(3, subjects.length));
    const more = line.querySelector('[data-preview-more]');
    assert.equal(more.hidden, subjects.length <= 3);
    if (subjects.length > 3) assert.equal(more.textContent, `and ${subjects.length - 3} more`);
    for (const topic of record.previews.flatMap((entry) => entry.topics)) assert.ok(JSON.parse(row.dataset.topics).includes(topic));
  }
  const awaiting = [...document.querySelectorAll('[data-meeting-row][data-awaiting-agenda="true"]')];
  const label = document.querySelector('#upcoming-toggle-label').textContent;
  if (awaiting.length) assert.equal(label, `Show ${awaiting.length} upcoming ${awaiting.length === 1 ? 'meeting' : 'meetings'} without a posted agenda`);
  dom.window.close();
});

test('a topic filter matches through preview topics and lists the matching subjects under the card', { skip: skip(withPreviews.filter((record) => record.previews.some((entry) => entry.topics.length)), 'tagged preview') }, async () => {
  const record = withPreviews.find((candidate) => candidate.previews.some((entry) => entry.topics.includes('biking'))) ?? withPreviews.find((candidate) => candidate.previews.some((entry) => entry.topics.length));
  const topic = record.previews.some((entry) => entry.topics.includes('biking')) ? 'biking' : record.previews.find((entry) => entry.topics.length).topics[0];
  const expected = record.previews.filter((entry) => entry.topics.includes(topic)).map((entry) => entry.subject);
  for (const [file, route] of [['index.html', `/?topic=${topic}`], [`topics/${topic}/index.html`, `/topics/${topic}/`]]) {
    const dom = await openPage(file, route);
    const { document } = dom.window;
    const row = rowFor(document, record.id);
    assert.equal(row.hidden, false, `${record.id} matches ${route}`);
    assert.equal(row.querySelector('[data-meeting-previews]').hidden, true, 'the Planned line yields to the topic list');
    const list = row.querySelector(`[data-topic-items="${topic}"]`);
    assert.equal(list.hidden, false);
    assert.deepEqual([...list.querySelectorAll('[data-topic-planned] strong')].map((node) => node.textContent), expected);
    for (const link of list.querySelectorAll('[data-topic-planned]')) assert.match(link.getAttribute('href'), /#planned-heading$/);
    const count = Number(document.querySelector('#agenda-count').textContent.match(/^(\d+)/)?.[1]);
    assert.ok(count >= expected.length, 'planned subjects count with related agenda items');
    dom.window.close();
  }
});

test('the planned APC meeting appears under ?topic=biking', { skip: skip(withPreviews.filter((record) => record.agencyId === 'trpa-advisory'), 'planned TRPA APC meeting with previews') }, async () => {
  const record = withPreviews.find((candidate) => candidate.agencyId === 'trpa-advisory');
  const dom = await openPage('index.html', '/?topic=biking');
  assert.equal(rowFor(dom.window.document, record.id)?.hidden, false);
  dom.window.close();
});

test('meeting pages list previews above the empty agenda with their source line', { skip: skip(withPreviews, 'upcoming meeting with previews') }, async () => {
  for (const record of withPreviews) {
    const document = await pageFor(record);
    const section = document.querySelector('.meeting-previews-section');
    assert.ok(section, record.id);
    assert.equal(section.querySelector('h2').textContent, 'Planned for this agenda · subject to change');
    assert.deepEqual([...section.querySelectorAll('.preview-item strong')].map((node) => node.textContent), record.previews.map((entry) => entry.subject));
    const sources = [...section.querySelectorAll('.preview-source a')];
    for (const entry of record.previews) {
      // One link per source document, opened at the first page its subjects were read from (#page=N); a notice links as is.
      const firstPage = record.previews.filter((other) => other.source.url === entry.source.url).find((other) => other.source.page)?.source.page;
      const expectedHref = firstPage ? `${entry.source.url}#page=${firstPage}` : entry.source.url;
      const link = sources.find((anchor) => anchor.getAttribute('href') === expectedHref);
      assert.ok(link, `${record.id} links ${expectedHref}`);
      assert.match(link.textContent, entry.source.kind === 'planning-calendar' ? /^From the agenda planning calendar updated [A-Z][a-z]+ \d{1,2}, \d{4}/ : new RegExp(`^From the ${entry.source.title.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
    }
    const empty = document.querySelector('.empty-panel');
    if (empty) assert.ok(section.compareDocumentPosition(empty) & 4, 'the section comes before the empty-agenda panel');
  }
});

test('planned meetings say "Subject to change" once, and timed ones get a calendar file that says so first', { skip: skip(planned, 'planned meeting') }, async () => {
  for (const record of planned) {
    const document = await pageFor(record);
    assert.equal(document.querySelector('.meeting-time-note')?.textContent, 'Subject to change');
    assert.ok(![...document.querySelectorAll('.warning-note p')].some((node) => node.textContent === 'Subject to change.'));
    if (!record.startsAt) continue;
    assert.ok(document.querySelector('a.calendar-download'), `${record.id} has a calendar download`);
    const ics = unfold(await readFile(resolve(root, 'dist', 'calendar', record.agencyId, `${record.sourceId}.ics`), 'utf8'));
    assert.match(ics, /\r\nDESCRIPTION:Subject to change\.(\\n|\r\n)/);
  }
});

test('one-off meetings with only their notice show by default as a posted notice', { skip: skip(noticeOneOffs, 'upcoming one-off meeting from a notice') }, async () => {
  const dom = await openPage('index.html', '/');
  for (const record of noticeOneOffs) {
    const row = rowFor(dom.window.document, record.id);
    assert.equal(row.hidden, false, `${record.id} is shown by default`);
    assert.equal(row.dataset.awaitingAgenda, 'false');
    assert.doesNotMatch(row.querySelector('.status-pill')?.textContent ?? '', /no agenda yet/);
    assert.match(row.querySelector('.meeting-card-copy').textContent, /Meeting notice posted/);
    const document = await pageFor(record);
    const panel = document.querySelector('[data-agenda-availability="notice"]');
    assert.ok(panel, record.id);
    assert.equal(panel.querySelector('a').getAttribute('href'), record.officialUrl);
    if (record.duration?.source === 'published') assert.doesNotMatch(document.querySelector('.calendar-download-group small')?.textContent ?? '', /estimate/);
  }
  dom.window.close();
});

test('a project missing from the posted agenda gets a one-line note', { skip: skip(stored.filter((record) => record.previewNotOnAgenda?.length), 'meeting with previewNotOnAgenda') }, async () => {
  for (const record of stored.filter((candidate) => candidate.previewNotOnAgenda?.length)) {
    const document = await pageFor(record);
    assert.deepEqual([...document.querySelectorAll('.preview-not-on-agenda')].map((node) => node.textContent), record.previewNotOnAgenda.map((project) => `${project}: not on the posted agenda.`));
  }
});
