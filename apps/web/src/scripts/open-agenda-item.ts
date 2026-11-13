function openHashTarget() {
  let id = window.location.hash.slice(1);
  try { id = decodeURIComponent(id); } catch { /* Keep the literal fragment if it is malformed. */ }
  const target = document.getElementById(id);
  if (target instanceof HTMLDetailsElement) {
    target.open = true;
    // An item inside a collapsed agenda section.
    for (let section = target.parentElement?.closest('details'); section; section = section.parentElement?.closest('details')) section.open = true;
    requestAnimationFrame(() => target.scrollIntoView({ block: 'start' }));
  }
}

openHashTarget();
window.addEventListener('hashchange', openHashTarget);

// "Copy link to this item": copies the item's permalink (the meeting page and `#item-<id>`, without the page's
// topic filter) and puts the anchor in the address bar too, so the browser's own share menu shares the item.
// Without script, or where the clipboard is unavailable, the link simply goes to the item's anchor.
const statusTimers = new WeakMap<HTMLElement, number>();
for (const link of document.querySelectorAll<HTMLAnchorElement>('a[data-item-permalink]')) {
  link.addEventListener('click', async (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href);
    const status = link.parentElement?.querySelector<HTMLElement>('.agenda-item-permalink-status');
    if (!navigator.clipboard?.writeText) return;
    event.preventDefault();
    try {
      await navigator.clipboard.writeText(url.href);
    } catch {
      window.location.hash = url.hash;
      return;
    }
    // replaceState, not a hash change: the item is already open where the reader is.
    window.history.replaceState(window.history.state, '', `${window.location.pathname}${window.location.search}${url.hash}`);
    try { window.goatcounter?.count?.({ path: 'agenda-item-link-copied', event: true, referrer: location.pathname }); } catch { /* Analytics never breaks the page. */ }
    if (status) {
      status.textContent = 'Link copied';
      window.clearTimeout(statusTimers.get(status));
      statusTimers.set(status, window.setTimeout(() => { status.textContent = ''; }, 4000));
    }
  });
}
