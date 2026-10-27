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
