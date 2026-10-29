import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import ts from 'typescript';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const dist = resolve(root, 'dist');
const source = await readFile(resolve(root, 'src/lib/analytics.ts'), 'utf8');
const javascript = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const analytics = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
const transpile = async (file) => ts.transpileModule(await readFile(resolve(root, file), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const timelineScript = await transpile('src/scripts/timeline-filters.ts');
const feedbackScript = await transpile('src/scripts/feedback-dialog.ts');
const agendaFilterScript = await transpile('src/scripts/meeting-agenda-filter.ts');

async function htmlFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const path = join(dir, entry.name);
    return entry.isDirectory() ? htmlFiles(path) : entry.name.endsWith('.html') ? [path] : [];
  }));
  return nested.flat();
}

const click = (window, element) => element.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
const change = (window, element) => element.dispatchEvent(new window.Event('change', { bubbles: true }));

/**
 * Open a built page and run its scripts. `goatcounter` is what window.goatcounter holds beforehand: undefined
 * (analytics not configured or blocked), `{ path }` (configured, count.js not loaded yet), or a recorder.
 */
async function openPage(file, route, goatcounter, scripts) {
  const html = await readFile(resolve(dist, file), 'utf8');
  const dom = new JSDOM(html, { url: `https://tahoepublicmeetings.org${route}`, runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;
  window.scrollBy = () => {};
  window.requestAnimationFrame = () => 0;
  window.HTMLDialogElement.prototype.showModal = function showModal() { this.open = true; };
  window.HTMLDialogElement.prototype.close = function close() { this.open = false; this.dispatchEvent(new window.Event('close')); };
  if (goatcounter !== undefined) window.goatcounter = goatcounter;
  for (const script of scripts) window.eval(script);
  return dom;
}

function recorder() {
  const calls = [];
  return { calls, goatcounter: { path: () => '/', count: (vars) => calls.push(vars) } };
}

test('analyticsConfig accepts only an https:// count endpoint', () => {
  assert.equal(analytics.analyticsConfig({}), null);
  assert.equal(analytics.analyticsConfig({ PUBLIC_GOATCOUNTER_URL: undefined }), null);
  assert.equal(analytics.analyticsConfig({ PUBLIC_GOATCOUNTER_URL: '' }), null);
  assert.equal(analytics.analyticsConfig({ PUBLIC_GOATCOUNTER_URL: '   ' }), null);
  assert.equal(analytics.analyticsConfig({ PUBLIC_GOATCOUNTER_URL: 'http://example.goatcounter.com/count' }), null);
  assert.equal(analytics.analyticsConfig({ PUBLIC_GOATCOUNTER_URL: '//example.goatcounter.com/count' }), null);
  assert.equal(analytics.analyticsConfig({ PUBLIC_GOATCOUNTER_URL: 'javascript:alert(1)' }), null);
  assert.equal(analytics.analyticsConfig({ PUBLIC_GOATCOUNTER_URL: 'not a url' }), null);
  assert.deepEqual(analytics.analyticsConfig({ PUBLIC_GOATCOUNTER_URL: 'https://example.goatcounter.com/count', DEV: true }), { endpoint: 'https://example.goatcounter.com/count' });
  assert.deepEqual(analytics.analyticsConfig({ PUBLIC_GOATCOUNTER_URL: ' https://example.goatcounter.com/count\n' }), { endpoint: 'https://example.goatcounter.com/count' });
});

test('click attributes name the event, the page, and the agency when known', () => {
  assert.deepEqual(analytics.clickEvent('outbound-agenda', '/meetings/x/', 'ttd'), {
    'data-goatcounter-click': 'outbound-agenda', 'data-goatcounter-referrer': '/meetings/x/', 'data-goatcounter-title': 'ttd',
  });
  assert.deepEqual(analytics.clickEvent('calendar-ics', '/', null), { 'data-goatcounter-click': 'calendar-ics', 'data-goatcounter-referrer': '/' });
  assert.equal(analytics.documentEvent('Official agenda (PDF)'), 'outbound-agenda');
  assert.equal(analytics.documentEvent('agenda_packet'), 'outbound-agenda');
  assert.equal(analytics.documentEvent('Official Board meeting minutes'), 'outbound-minutes');
  assert.equal(analytics.documentEvent('Official Board of Trustees meeting synopsis'), 'outbound-minutes');
  assert.equal(analytics.documentEvent('Official supplemental material for RTC Item 3 (not the full agenda)'), 'outbound-attachment');
  assert.equal(analytics.documentEvent('FY26 Budget and Capital Plan'), 'outbound-attachment');
});

test('the Privacy page carries the privacy statement and the footer links to it', async () => {
  const html = await readFile(resolve(dist, 'privacy/index.html'), 'utf8');
  const text = new JSDOM(html).window.document.querySelector('main').textContent.replace(/\s+/g, ' ');
  assert.match(html, /<h1[^>]*>Privacy<\/h1>/);
  assert.match(html, /<a href="\/privacy\/">Privacy<\/a>/);
  assert.ok(text.includes("I count page views with GoatCounter, an open-source tool with no cookies and no stored IP addresses, so I can see which pages and links people use. Each visit gets a random code that expires after eight hours, so I can tell which pages are viewed together, but not who you are or whether you've been here before. Nothing is shared with advertisers."));
  assert.match(html, /href="https:\/\/www\.goatcounter\.com\/"/);
});

test('an unconfigured build loads no analytics script on any page', async () => {
  const files = await htmlFiles(dist);
  assert.ok(files.length > 10);
  for (const file of files) {
    const html = await readFile(file, 'utf8');
    // The page scripts' guarded `window.goatcounter?.count?.(…)` calls are bundled everywhere; nothing defines it.
    assert.ok(!html.includes('gc.zgo.at') && !html.includes('goatcounter.com/count') && !/window\.goatcounter\s*=/.test(html), `${file} loads analytics`);
  }
});

test('meeting pages mark official source links and the calendar menu with click events', async () => {
  const files = (await htmlFiles(resolve(dist, 'meetings'))).filter((file) => file.endsWith('/index.html'));
  let agendaPage = null;
  let calendarPage = null;
  for (const file of files) {
    const html = await readFile(file, 'utf8');
    if (!agendaPage && html.includes('agenda-source-link')) agendaPage = file;
    if (!calendarPage && html.includes('calendar-options')) calendarPage = file;
    if (agendaPage && calendarPage) break;
  }
  assert.ok(agendaPage && calendarPage, 'the fixture needs a meeting with agenda items and one with a calendar menu');

  const agenda = new JSDOM(await readFile(agendaPage, 'utf8')).window.document;
  const path = new URL(agenda.querySelector('link[rel="canonical"]').href).pathname;
  const sourceLinks = [...agenda.querySelectorAll('a.agenda-source-link')];
  assert.ok(sourceLinks.length > 0);
  for (const link of sourceLinks) {
    assert.match(link.dataset.goatcounterClick, /^outbound-(agenda|staff-report)$/);
    assert.equal(link.dataset.goatcounterReferrer, path);
    assert.ok(link.dataset.goatcounterTitle);
  }
  for (const link of agenda.querySelectorAll('a.source-document:not(.agenda-source-link)')) assert.match(link.dataset.goatcounterClick, /^outbound-(agenda|minutes|attachment)$/);
  for (const link of agenda.querySelectorAll('a[target="_blank"]')) assert.ok(link.dataset.goatcounterClick, `${link.href} has no click event`);
  for (const link of agenda.querySelectorAll('a[href^="/"]:not([download])')) assert.equal(link.dataset.goatcounterClick, undefined, 'internal links are not events');

  const calendar = new JSDOM(await readFile(calendarPage, 'utf8')).window.document;
  assert.deepEqual([...calendar.querySelectorAll('.calendar-options a')].map((link) => link.dataset.goatcounterClick), ['calendar-ics', 'calendar-google', 'calendar-outlook']);
});

test('page scripts never throw without count.js, before it loads, or when it fails', async () => {
  const throwing = { path: () => '/', count: () => { throw new Error('storage blocked'); } };
  for (const goatcounter of [undefined, { path: () => '/' }, throwing]) {
    const dom = await openPage('index.html', '/?topic=transit&agency=ttd', goatcounter, [feedbackScript, timelineScript]);
    const { window } = dom;
    const { document } = window;
    const agency = document.querySelector('#agency-filter');
    agency.value = '';
    change(window, agency);
    click(window, document.querySelector('#earlier-more'));
    click(window, document.querySelector('#upcoming-toggle'));
    click(window, document.querySelector('.nav-feedback'));
    assert.equal(document.querySelector('#feedback-dialog').open, true);
    window.close();
  }
});

test('timeline filters and feedback are counted as events with the page as referrer', async () => {
  const { calls, goatcounter } = recorder();
  const dom = await openPage('index.html', '/?topic=transit&agency=ttd', goatcounter, [feedbackScript, timelineScript]);
  const { window } = dom;
  const { document } = window;
  assert.deepEqual(calls.map((call) => call.path), ['filter-topic-transit', 'filter-agency-ttd'], 'filters carried in the URL are counted on load');
  assert.ok(calls.every((call) => call.event === true && call.referrer === '/'));

  calls.length = 0;
  const agency = document.querySelector('#agency-filter');
  agency.value = 'ttd';
  change(window, agency);
  agency.value = '';
  change(window, agency);
  const topic = document.querySelector('#topic-filter');
  topic.value = topic.options[topic.options.length - 1].value; // "Suggest a topic…" opens feedback, it is not a filter.
  change(window, topic);
  document.querySelector('#feedback-dialog').close();
  click(window, document.querySelector('#earlier-more'));
  click(window, document.querySelector('#upcoming-toggle'));
  click(window, document.querySelector('#upcoming-toggle'));
  assert.deepEqual(calls.map((call) => call.path), ['filter-agency-ttd', 'feedback-open', 'timeline-earlier', 'timeline-upcoming']);

  calls.length = 0;
  window.fetch = async () => ({ ok: true, json: async () => ({}) });
  click(window, document.querySelector('.nav-feedback'));
  document.querySelector('#feedback-message').value = 'The agenda link is broken.';
  document.querySelector('#feedback-form').dispatchEvent(new window.Event('submit', { cancelable: true }));
  await new Promise((done) => setTimeout(done, 0));
  assert.deepEqual(calls.map((call) => call.path), ['feedback-open', 'feedback-sent']);
  window.close();
});

test('a meeting page counts agenda topic filter choices', async () => {
  const files = (await htmlFiles(resolve(dist, 'meetings'))).filter((file) => file.endsWith('/index.html'));
  let file = null;
  for (const candidate of files) if (/type="checkbox" name="topic"/.test(await readFile(candidate, 'utf8'))) { file = candidate; break; }
  assert.ok(file, 'the fixture needs a meeting with an agenda topic filter');
  const route = `/${file.slice(dist.length + 1).replace(/index\.html$/, '')}`;
  for (const goatcounter of [undefined, { path: () => '/' }]) {
    const dom = await openPage(file.slice(dist.length + 1), route, goatcounter, [agendaFilterScript]);
    const checkbox = dom.window.document.querySelector('#agenda-topic-filter input[name="topic"]');
    checkbox.checked = true;
    change(dom.window, checkbox);
    dom.window.close();
  }
  const { calls, goatcounter } = recorder();
  const dom = await openPage(file.slice(dist.length + 1), route, goatcounter, [agendaFilterScript]);
  const checkbox = dom.window.document.querySelector('#agenda-topic-filter input[name="topic"]');
  checkbox.checked = true;
  change(dom.window, checkbox);
  checkbox.checked = false;
  change(dom.window, checkbox);
  assert.deepEqual(calls.map((call) => call.path), [`agenda-filter-topic-${checkbox.value}`]);
  assert.equal(calls[0].referrer, new URL(route, 'https://x').pathname);
  dom.window.close();
});
