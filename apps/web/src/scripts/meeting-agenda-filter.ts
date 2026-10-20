(() => {
const filter = document.querySelector<HTMLFieldSetElement>('#agenda-topic-filter');
const reset = document.querySelector<HTMLButtonElement>('#agenda-filter-reset');
const count = document.querySelector<HTMLElement>('#agenda-filter-count');
const deepLinkNote = document.querySelector<HTMLElement>('#agenda-deep-link-note');
const items = [...document.querySelectorAll<HTMLDetailsElement>('[data-agenda-item]')];

if (filter && items.length) {
  const checkboxes = [...filter.querySelectorAll<HTMLInputElement>('input[type="checkbox"][name="topic"]')];
  const validTopics = new Set(checkboxes.map((checkbox) => checkbox.value));
  const params = new URLSearchParams(window.location.search);
  for (const checkbox of checkboxes) checkbox.checked = params.getAll('topic').includes(checkbox.value);

  function currentHashItem() {
    let id = window.location.hash.slice(1);
    try { id = decodeURIComponent(id); } catch { /* Keep a literal malformed fragment. */ }
    return items.find((item) => item.dataset.itemId === id.replace(/^item-/, ''));
  }

  function applyFilter() {
    const selected = checkboxes.filter((checkbox) => checkbox.checked).map((checkbox) => checkbox.value);
    const target = currentHashItem();
    const targetTopics = target ? JSON.parse(target.dataset.topicIds ?? '[]') as string[] : [];
    const targetMatches = !target || selected.length === 0 || selected.some((topicId) => targetTopics.includes(topicId));
    let visible = 0;

    for (const item of items) {
      const itemTopics = JSON.parse(item.dataset.topicIds ?? '[]') as string[];
      const matches = selected.length === 0 || selected.some((topicId) => itemTopics.includes(topicId));
      const isDeepLinkTarget = item === target;
      item.hidden = !(matches || isDeepLinkTarget);
      if (!item.hidden) visible += 1;
    }

    if (deepLinkNote) deepLinkNote.hidden = !target || targetMatches;
    if (count) count.textContent = `Showing ${visible} of ${items.length} agenda ${items.length === 1 ? 'item' : 'items'}`;

    const url = new URL(window.location.href);
    url.searchParams.delete('topic');
    for (const topicId of selected) if (validTopics.has(topicId)) url.searchParams.append('topic', topicId);
    window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
  }

  for (const checkbox of checkboxes) checkbox.addEventListener('change', applyFilter);
  reset?.addEventListener('click', () => {
    for (const checkbox of checkboxes) checkbox.checked = false;
    applyFilter();
  });
  window.addEventListener('hashchange', applyFilter);
  applyFilter();
}
})();
