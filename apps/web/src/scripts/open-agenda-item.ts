function openHashTarget() {
  let id = window.location.hash.slice(1);
  try { id = decodeURIComponent(id); } catch { /* Keep the literal fragment if it is malformed. */ }
  const target = document.getElementById(id);
  if (target instanceof HTMLDetailsElement) {
    target.open = true;
    requestAnimationFrame(() => target.scrollIntoView({ block: 'start' }));
  }
}

openHashTarget();
window.addEventListener('hashchange', openHashTarget);
