const topicSelect = document.querySelector<HTMLSelectElement>('#topic-filter');
const agencySelect = document.querySelector<HTMLSelectElement>('#agency-filter');
const count = document.querySelector<HTMLElement>('#timeline-count');
const agendaCount = document.querySelector<HTMLElement>('#agenda-count');
const noResults = document.querySelector<HTMLElement>('#timeline-no-results');
const noUpcoming = document.querySelector<HTMLElement>('#no-upcoming-message');
// Earlier meetings open backward in time, a fixed number per click.
const earlier = document.querySelector<HTMLElement>('#earlier-meetings');
const earlierMore = document.querySelector<HTMLButtonElement>('#earlier-more');
const earlierLabel = document.querySelector<HTMLElement>('#earlier-more-label');
const earlierRemaining = document.querySelector<HTMLElement>('#earlier-remaining');
// With earlier meetings open, Today is no longer at the top of the timeline, so offer a way back to it.
const earlierTodayJump = document.querySelector<HTMLElement>('#earlier-today-jump');
// Upcoming meetings without a posted agenda, including expected ones, stay hidden unless the reader asks.
const upcomingMore = document.querySelector<HTMLElement>('#upcoming-more');
const upcomingToggle = document.querySelector<HTMLButtonElement>('#upcoming-toggle');
const upcomingLabel = document.querySelector<HTMLElement>('#upcoming-toggle-label');
const upcomingHint = document.querySelector<HTMLElement>('#upcoming-hint');
rollTimelineForward();
const rows = [...document.querySelectorAll<HTMLElement>('[data-meeting-row]')];
const groups = [...document.querySelectorAll<HTMLElement>('[data-timeline-group]')];
// The last topic option is "Suggest a topic…": it opens the feedback dialog instead of filtering.
const SUGGEST_TOPIC = '__suggest';
const EARLIER_STEP = Number(earlier?.dataset.step) || 10;

const plural = (total: number, word: string) => `${total} ${word}${total === 1 ? '' : 's'}`;

if (topicSelect && agencySelect) {
  const params = new URLSearchParams(window.location.search);
  const isTopicRoute = window.location.pathname.startsWith('/topics/');
  const queryTopic = isTopicRoute ? null : params.get('topic');
  if (queryTopic && queryTopic !== SUGGEST_TOPIC && [...topicSelect.options].some((option) => option.value === queryTopic)) topicSelect.value = queryTopic;
  let currentTopic = topicSelect.value;
  const agency = params.get('agency');
  if (agency && [...agencySelect.options].some((option) => option.value === agency)) agencySelect.value = agency;
  // `expected=1` is the bookmark from the former "Show expected meetings" checkbox.
  let showAwaiting = params.get('upcoming') === 'all' || params.get('expected') === '1';
  // The type-to-filter boxes (filter-combobox.ts) show the values just read from the URL.
  for (const select of [topicSelect, agencySelect]) select.dispatchEvent(new Event('filter:sync'));
  let earlierLimit = 0;

  function applyFilters() {
    const topic = topicSelect!.value;
    const agency = agencySelect!.value;
    const topicName = topic ? topicSelect!.selectedOptions[0]?.textContent : '';
    const earlierMatches: HTMLElement[] = [];
    let awaitingTotal = 0;
    let expectedAvailable = 0;
    let expectedShown = 0;
    let upcomingShown = 0;

    for (const row of rows) {
      const topics = JSON.parse(row.dataset.topics ?? '[]') as string[];
      const matches = (!topic || topics.includes(topic)) && (!agency || row.dataset.agency === agency);
      if (row.dataset.bucket === 'earlier') {
        row.hidden = true;
        if (matches) earlierMatches.push(row);
        continue;
      }
      const awaiting = row.dataset.awaitingAgenda === 'true';
      const expected = row.dataset.forecast === 'true';
      if (matches && awaiting) awaitingTotal += 1;
      if (matches && expected) expectedAvailable += 1;
      row.hidden = !(matches && (!awaiting || showAwaiting));
      if (!row.hidden && expected) expectedShown += 1;
      if (!row.hidden && row.dataset.canceled !== 'true') upcomingShown += 1;
    }
    // Rows are oldest first, so the most recent earlier meetings are at the end.
    const earlierShown = earlierMatches.slice(Math.max(0, earlierMatches.length - earlierLimit));
    for (const row of earlierShown) row.hidden = false;

    let total = 0;
    let matchingAgendaItems = 0;
    for (const row of rows) {
      if (row.hidden) continue;
      total += 1;
      for (const topicItems of row.querySelectorAll<HTMLElement>('[data-topic-items]')) {
        const showTopic = Boolean(topic && topicItems.dataset.topicItems === topic);
        topicItems.hidden = !showTopic;
        if (showTopic) matchingAgendaItems += topicItems.querySelectorAll('a').length;
      }
      matchingAgendaItems += showPreviews(row, topic);
    }
    for (const group of groups) {
      const groupRows = [...group.querySelectorAll<HTMLElement>('[data-meeting-row]')];
      group.hidden = groupRows.length > 0 && groupRows.every((row) => row.hidden);
    }

    const earlierLeft = earlierMatches.length - earlierShown.length;
    const nextEarlier = Math.min(EARLIER_STEP, earlierLeft);
    if (earlier) earlier.hidden = earlierMatches.length === 0;
    if (earlierMore) earlierMore.hidden = earlierLeft === 0;
    if (earlierTodayJump) earlierTodayJump.hidden = earlierShown.length === 0;
    if (earlierLabel) earlierLabel.textContent = `Show ${nextEarlier} earlier ${nextEarlier === 1 ? 'meeting' : 'meetings'}`;
    if (earlierRemaining) earlierRemaining.textContent = earlierShown.length ? `${earlierLeft} not shown` : `${earlierLeft} earlier`;

    if (upcomingMore) upcomingMore.hidden = awaitingTotal === 0;
    if (upcomingToggle) upcomingToggle.setAttribute('aria-expanded', String(showAwaiting));
    if (upcomingLabel) upcomingLabel.textContent = `${showAwaiting ? 'Hide' : 'Show'} ${plural(awaitingTotal, 'upcoming meeting')} without a posted agenda`;
    if (upcomingHint) upcomingHint.textContent = expectedAvailable ? `Includes ${expectedAvailable} expected from each agency’s usual schedule` : '';

    if (count) count.textContent = `${plural(total, 'meeting')}${topicName ? ` · ${topicName}` : ''}${expectedShown ? ` · ${expectedShown} expected` : ''}`;
    if (agendaCount) agendaCount.textContent = topic ? `${matchingAgendaItems} related agenda ${matchingAgendaItems === 1 ? 'item' : 'items'}` : '';
    const anyMatches = total + awaitingTotal + earlierMatches.length > 0;
    if (noResults) {
      noResults.hidden = anyMatches;
      const heading = noResults.querySelector('h2');
      if (heading) heading.textContent = topic ? `No agenda items match ${topicName} yet` : 'No meetings match these filters';
    }
    if (noUpcoming) {
      noUpcoming.hidden = upcomingShown > 0 || !anyMatches;
      noUpcoming.textContent = topic ? `No upcoming agendas match ${topicName} yet.`
        : showAwaiting ? 'No upcoming meetings are listed.' : 'No upcoming agendas are posted yet.';
    }

    const url = new URL(window.location.href);
    if (topic && !url.pathname.startsWith('/topics/')) url.searchParams.set('topic', topic);
    else url.searchParams.delete('topic');
    if (agency) url.searchParams.set('agency', agency);
    else url.searchParams.delete('agency');
    if (showAwaiting) url.searchParams.set('upcoming', 'all');
    else url.searchParams.delete('upcoming');
    url.searchParams.delete('expected');
    url.searchParams.delete('view');
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
  }

  // A filter change returns earlier meetings to the default, collapsed view.
  function changeFilters() {
    earlierLimit = 0;
    applyFilters();
  }

  topicSelect.addEventListener('change', () => {
    const topic = topicSelect!.value;
    if (topic === SUGGEST_TOPIC) {
      const option = topicSelect!.selectedOptions[0];
      topicSelect!.value = currentTopic;
      window.dispatchEvent(new CustomEvent('feedback:open', { detail: { kind: option?.dataset.feedbackKind, prompt: option?.dataset.feedbackPrompt } }));
      return;
    }
    currentTopic = topic;
    if (window.location.pathname.startsWith('/topics/')) {
      const url = new URL(window.location.href);
      url.pathname = topic ? `/topics/${encodeURIComponent(topic)}/` : '/';
      url.searchParams.delete('topic');
      window.location.assign(`${url.pathname}${url.search}`);
    } else changeFilters();
  });
  agencySelect.addEventListener('change', changeFilters);
  // Earlier meetings open above the reader's place: the Today marker and everything after it stay put on screen.
  earlierMore?.addEventListener('click', () => {
    const anchor = earlier?.nextElementSibling;
    const before = anchor?.getBoundingClientRect().top;
    earlierLimit += EARLIER_STEP;
    applyFilters();
    if (anchor && before !== undefined) window.scrollBy({ top: anchor.getBoundingClientRect().top - before, behavior: 'instant' });
  });
  upcomingToggle?.addEventListener('click', () => {
    showAwaiting = !showAwaiting;
    applyFilters();
  });
  applyFilters();
}

// A card's "Planned:" line lists the subjects matching the selected topic, else the first few and how many more.
// Returns how many subjects match the topic, which count with the related agenda items.
function showPreviews(row: HTMLElement, topic: string) {
  const line = row.querySelector<HTMLElement>('[data-meeting-previews]');
  if (!line) return 0;
  const subjects = [...line.querySelectorAll<HTMLElement>('[data-preview-topics]')];
  const matching = topic ? subjects.filter((subject) => (JSON.parse(subject.dataset.previewTopics ?? '[]') as string[]).includes(topic)) : [];
  const shown = new Set(matching.length ? matching : subjects.slice(0, Number(line.dataset.previewLimit) || 3));
  for (const subject of subjects) subject.hidden = !shown.has(subject);
  const more = line.querySelector<HTMLElement>('[data-preview-more]');
  if (more) {
    more.hidden = subjects.length === shown.size;
    more.textContent = `and ${subjects.length - shown.size} more`;
  }
  return matching.length;
}

// The page is a static snapshot, so its Today is the build's date. When the reader's Lake Tahoe date is
// later, move meetings that have since passed into Earlier and the new day's meetings under Today.
function rollTimelineForward() {
  const pacific = (options: Intl.DateTimeFormatOptions, locale = 'en-US') => new Intl.DateTimeFormat(locale, { timeZone: 'America/Los_Angeles', ...options });
  const divider = document.querySelector<HTMLElement>('#today-divider');
  const todayGroup = document.querySelector<HTMLElement>('[data-timeline-group="today"]');
  const todayList = todayGroup?.querySelector<HTMLElement>('.timeline-meeting-list');
  const earlierList = document.querySelector<HTMLElement>('#earlier-list');
  const builtToday = divider?.dataset.today;
  const now = new Date();
  const today = pacific({ year: 'numeric', month: '2-digit', day: '2-digit' }, 'en-CA').format(now);
  if (!divider || !todayGroup || !todayList || !earlierList || !builtToday || today <= builtToday) return;

  const earlierGroupList = (key: string) => {
    let group = earlierList.querySelector<HTMLElement>(`[data-timeline-group="earlier-${key}"]`);
    if (!group) {
      group = document.createElement('section');
      group.className = 'timeline-date-group earlier-date-group';
      group.dataset.timelineGroup = `earlier-${key}`;
      group.dataset.date = key;
      group.hidden = true;
      const heading = document.createElement('h2');
      heading.textContent = pacific({ weekday: 'long', month: 'long', day: 'numeric' }).format(new Date(`${key}T12:00:00-08:00`));
      const list = document.createElement('div');
      list.className = 'timeline-meeting-list';
      group.append(heading, list);
      earlierList.append(group);
    }
    return group.querySelector<HTMLElement>('.timeline-meeting-list')!;
  };

  // Today's rows, then future rows by date: newly earlier groups are appended oldest first, like the rest of Earlier.
  const futureGroups = [...document.querySelectorAll<HTMLElement>('[data-timeline-group^="future-"]')];
  const candidates = [todayGroup, ...futureGroups].flatMap((group) => [...group.querySelectorAll<HTMLElement>('[data-meeting-row]')]);
  for (const row of candidates) {
    const key = row.dataset.date;
    if (!key || key > today) continue;
    if (key === today) {
      row.dataset.bucket = 'today';
      todayList.append(row);
    } else if (row.dataset.forecast === 'true') {
      row.remove(); // Expected meetings are never history.
    } else {
      row.dataset.bucket = 'earlier';
      row.dataset.awaitingAgenda = 'false';
      row.querySelector('[data-meeting-previews]')?.remove(); // Past meetings never show previews.
      earlierGroupList(key).append(row);
    }
  }
  for (const group of futureGroups) if (!group.querySelector('[data-meeting-row]')) group.remove();

  const time = divider.querySelector('time');
  if (time) time.textContent = pacific({ weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }).format(now);
  let empty = todayGroup.querySelector<HTMLElement>('[data-today-empty]');
  if (todayList.querySelector('[data-meeting-row]')) empty?.remove();
  else if (!empty) {
    empty = document.createElement('p');
    empty.className = 'timeline-empty-inline';
    empty.dataset.todayEmpty = '';
    empty.textContent = 'No meetings scheduled today.';
    todayGroup.append(empty);
  }
}
