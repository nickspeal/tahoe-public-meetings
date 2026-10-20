const topicSelect = document.querySelector<HTMLSelectElement>('#topic-filter');
const agencySelect = document.querySelector<HTMLSelectElement>('#agency-filter');
const viewSelect = document.querySelector<HTMLSelectElement>('#date-filter');
// Expected meetings (forecasts without an agenda) stay hidden unless the reader opts in.
const expectedToggle = document.querySelector<HTMLInputElement>('#forecast-filter');
const expectedCount = document.querySelector<HTMLElement>('#forecast-count');
const count = document.querySelector<HTMLElement>('#timeline-count');
const agendaCount = document.querySelector<HTMLElement>('#agenda-count');
const noResults = document.querySelector<HTMLElement>('#timeline-no-results');
const earlier = document.querySelector<HTMLDetailsElement>('#earlier-meetings');
const earlierCount = earlier?.querySelector<HTMLElement>('summary span');
const noUpcoming = document.querySelector<HTMLElement>('#no-upcoming-message');
const rows = [...document.querySelectorAll<HTMLElement>('[data-meeting-row]')];
const groups = [...document.querySelectorAll<HTMLElement>('[data-timeline-group]')];

if (topicSelect && agencySelect && viewSelect) {
  const params = new URLSearchParams(window.location.search);
  const isTopicRoute = window.location.pathname.startsWith('/topics/');
  const queryTopic = isTopicRoute ? null : params.get('topic');
  if (queryTopic && [...topicSelect.options].some((option) => option.value === queryTopic)) topicSelect.value = queryTopic;
  const agency = params.get('agency');
  if (agency && [...agencySelect.options].some((option) => option.value === agency)) agencySelect.value = agency;
  if (params.get('view') === 'all') viewSelect.value = 'all';
  if (viewSelect.value === 'all' && earlier) earlier.open = true;
  if (expectedToggle) expectedToggle.checked = params.get('expected') === '1';

  function applyFilters() {
    const topic = topicSelect!.value;
    const agency = agencySelect!.value;
    const showExpected = Boolean(expectedToggle?.checked);
    let total = 0;
    let earlierTotal = 0;
    let upcomingTotal = 0;
    let matchingAgendaItems = 0;
    let expectedAvailable = 0;
    let expectedShown = 0;

    for (const row of rows) {
      const topics = JSON.parse(row.dataset.topics ?? '[]') as string[];
      const expected = row.dataset.forecast === 'true';
      const filtered = (!topic || topics.includes(topic)) && (!agency || row.dataset.agency === agency);
      if (expected && filtered) expectedAvailable += 1;
      const matches = filtered && (!expected || showExpected);
      row.hidden = !matches;
      if (matches) {
        total += 1;
        if (expected) expectedShown += 1;
        if (row.dataset.bucket === 'earlier') earlierTotal += 1;
        if ((row.dataset.bucket === 'today' || row.dataset.bucket === 'upcoming') && row.dataset.canceled !== 'true') upcomingTotal += 1;
      }

      for (const topicItems of row.querySelectorAll<HTMLElement>('[data-topic-items]')) {
        const showTopic = Boolean(topic && matches && topicItems.dataset.topicItems === topic);
        topicItems.hidden = !showTopic;
        if (showTopic) matchingAgendaItems += topicItems.querySelectorAll('a').length;
      }
    }

    for (const group of groups) {
      const groupRows = [...group.querySelectorAll<HTMLElement>('[data-meeting-row]')];
      group.hidden = groupRows.length > 0 && groupRows.every((row) => row.hidden);
    }
    if (earlier) earlier.hidden = earlierTotal === 0;
    if (earlierCount) earlierCount.textContent = String(earlierTotal);
    if (count) count.textContent = `${total} ${total === 1 ? 'meeting' : 'meetings'}${topic ? ` · ${topicSelect!.selectedOptions[0]?.textContent}` : ''}${expectedShown ? ` · ${expectedShown} expected` : ''}`;
    if (expectedCount) expectedCount.textContent = `(${expectedAvailable})`;
    if (agendaCount) agendaCount.textContent = topic ? `${matchingAgendaItems} related agenda ${matchingAgendaItems === 1 ? 'item' : 'items'}` : '';
    if (noResults) {
      noResults.hidden = total > 0;
      const heading = noResults.querySelector('h2');
      const detail = noResults.querySelector('p');
      if (heading) heading.textContent = topic ? `No agenda items match ${topicSelect!.selectedOptions[0]?.textContent} yet` : 'No meetings match these filters';
      if (detail) detail.textContent = topic ? 'Try another topic or agency, or choose all dates.' : 'Try another agency, or choose all dates.';
    }
    if (noUpcoming) {
      noUpcoming.hidden = !(viewSelect!.value === 'upcoming' && upcomingTotal === 0 && earlierTotal > 0);
      if (topic && upcomingTotal === 0 && earlierTotal > 0) noUpcoming.textContent = `No upcoming agenda items match ${topicSelect!.selectedOptions[0]?.textContent} yet. Earlier matches are available above.`;
      else noUpcoming.textContent = 'No upcoming meetings are listed. Earlier meetings are available above.';
    }

    const url = new URL(window.location.href);
    if (topic && !url.pathname.startsWith('/topics/')) url.searchParams.set('topic', topic);
    else url.searchParams.delete('topic');
    if (agency) url.searchParams.set('agency', agency);
    else url.searchParams.delete('agency');
    if (viewSelect!.value === 'all') url.searchParams.set('view', 'all');
    else url.searchParams.delete('view');
    if (showExpected) url.searchParams.set('expected', '1');
    else url.searchParams.delete('expected');
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
  }

  topicSelect.addEventListener('change', () => {
    const topic = topicSelect!.value;
    if (window.location.pathname.startsWith('/topics/')) {
      const url = new URL(window.location.href);
      url.pathname = topic ? `/topics/${encodeURIComponent(topic)}/` : '/';
      url.searchParams.delete('topic');
      window.location.assign(`${url.pathname}${url.search}`);
    } else applyFilters();
  });
  agencySelect.addEventListener('change', applyFilters);
  expectedToggle?.addEventListener('change', applyFilters);
  viewSelect.addEventListener('change', () => {
    if (earlier) earlier.open = viewSelect!.value === 'all';
    applyFilters();
  });
  applyFilters();
}
