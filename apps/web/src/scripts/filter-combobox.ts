// Type-to-filter dropdowns for the timeline filters (FilterCombobox.astro). The hidden <select> stays the
// source of truth: choosing an option sets its value and fires `change`, and a `filter:sync` event on it
// (sent after its value is set from the URL) refreshes the text shown.
{
  /** Lowercase, accent-free words; periods and apostrophes join letters, so "T.R.P.A." reads as "trpa". */
  const searchWords = (text: string) => text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/&/g, ' and ').replace(/[.'’]/g, '').split(/[^a-z0-9]+/).filter(Boolean);
  /** Every query word starts a word of the term, or the query starts the term run together ("sstma" for "SS-TMA"). */
  const termMatches = (words: string[], query: string[]) =>
    query.every((part) => words.some((word) => word.startsWith(part))) || words.join('').startsWith(query.join(''));

  interface Entry { option: HTMLOptionElement; item: HTMLLIElement; hint: HTMLElement; terms: string[][]; always: boolean }

  for (const root of document.querySelectorAll<HTMLElement>('[data-filter-combobox]')) {
    const input = root.querySelector<HTMLInputElement>('input[role="combobox"]')!;
    const select = root.querySelector<HTMLSelectElement>('select')!;
    const popup = root.querySelector<HTMLElement>('.combobox-popup')!;
    const list = root.querySelector<HTMLUListElement>('[role="listbox"]')!;
    const empty = root.querySelector<HTMLElement>('.combobox-empty')!;
    const status = root.querySelector<HTMLElement>('[role="status"]')!;
    const toggle = root.querySelector<HTMLButtonElement>('.combobox-toggle')!;
    const clear = root.querySelector<HTMLButtonElement>('.combobox-clear')!;
    const noun = root.dataset.noun ?? 'options';
    const placeholder = input.dataset.placeholder ?? '';

    const entries: Entry[] = [...select.options].map((option, index) => {
      const item = document.createElement('li');
      item.id = `${list.id}-${index}`;
      item.setAttribute('role', 'option');
      item.className = 'combobox-option';
      if ('always' in option.dataset) item.classList.add('combobox-action');
      const label = document.createElement('span');
      label.className = 'combobox-option-label';
      label.textContent = option.text;
      const hint = document.createElement('span');
      hint.className = 'combobox-option-hint';
      item.append(label, hint);
      list.append(item);
      const names = [option.text, ...(option.dataset.terms?.split('\n') ?? [])];
      return { option, item, hint, terms: names.map(searchWords), always: 'always' in option.dataset };
    });
    let shown: Entry[] = [];
    let active = -1;

    const isOpen = () => !popup.hidden;
    const termText = (entry: Entry, index: number) => index === 0 ? entry.option.text : entry.option.dataset.terms!.split('\n')[index - 1];

    function sync() {
      const chosen = select.value ? select.selectedOptions[0] : null;
      input.value = chosen?.text ?? '';
      clear.hidden = !chosen;
      root.classList.toggle('has-value', Boolean(chosen));
    }

    function setActive(index: number) {
      active = index;
      shown.forEach((entry, position) => entry.item.classList.toggle('is-active', position === index));
      const current = shown[index];
      if (current) {
        input.setAttribute('aria-activedescendant', current.item.id);
        current.item.scrollIntoView?.({ block: 'nearest' });
      } else input.removeAttribute('aria-activedescendant');
    }

    /** `text` is what the reader typed; null lists every option to browse from the current choice. */
    function render(text: string | null) {
      const query = text ? searchWords(text) : [];
      shown = [];
      let matches = 0;
      for (const entry of entries) {
        let visible = entry.always || query.length === 0;
        let hint = '';
        if (!entry.always && entry.option.value && query.length) {
          const termIndex = entry.terms.findIndex((words) => termMatches(words, query));
          // A query can also mix names, such as "Tahoe Regional governing".
          visible = termIndex >= 0 || termMatches(entry.terms.flat(), query);
          // Say why an option matched when it was not by the name shown.
          const reason = termIndex >= 0 ? termIndex
            : entry.terms.findIndex((words, index) => index > 0 && query.some((part) => words.some((word) => word.startsWith(part))));
          if (visible && reason > 0) hint = termText(entry, reason);
        }
        entry.item.hidden = !visible;
        entry.hint.textContent = hint;
        entry.item.setAttribute('aria-selected', String(entry.option.selected && !entry.always));
        if (!visible) continue;
        shown.push(entry);
        if (!entry.always && entry.option.value) matches += 1;
      }
      empty.hidden = !(query.length && matches === 0);
      empty.textContent = `No ${noun} match “${text?.trim()}”`;
      status.textContent = query.length ? (matches ? `${matches} ${matches === 1 ? 'match' : 'matches'}` : empty.textContent) : '';
      // Typing highlights the best match (or, once cleared, the "All" option); browsing starts at the current choice.
      setActive(query.length ? (matches ? 0 : -1) : text === null ? Math.max(shown.findIndex((entry) => entry.option.selected), 0) : 0);
    }

    function open(text: string | null) {
      render(text);
      popup.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      toggle.setAttribute('aria-expanded', 'true');
      root.classList.add('is-open');
    }

    function close() {
      popup.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      root.classList.remove('is-open');
    }

    function choose(entry: Entry) {
      close();
      if (entry.option.value !== select.value || entry.always) {
        select.value = entry.option.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
      // The change handler may keep the old value (as "Suggest a topic…" does), so show what the select holds.
      sync();
    }

    function move(step: number) {
      if (!isOpen()) return open(null);
      if (!shown.length) return;
      setActive(active < 0 ? (step > 0 ? 0 : shown.length - 1) : (active + step + shown.length) % shown.length);
    }

    input.addEventListener('focus', () => {
      input.placeholder = `Type to search ${noun}`;
      input.select();
    });
    input.addEventListener('click', () => { if (!isOpen()) open(null); });
    input.addEventListener('input', () => open(input.value));
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        move(event.key === 'ArrowDown' ? 1 : -1);
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (isOpen() && shown[active]) choose(shown[active]);
      } else if (event.key === 'Escape') {
        if (isOpen()) event.preventDefault();
        close();
        sync();
      } else if (event.key === 'Tab') {
        close();
        sync();
      }
    });
    root.addEventListener('focusout', (event) => {
      if (event.relatedTarget instanceof Node && root.contains(event.relatedTarget)) return;
      close();
      sync();
      input.placeholder = placeholder;
    });
    // Keep focus in the text box while choosing with a pointer.
    popup.addEventListener('pointerdown', (event) => event.preventDefault());
    popup.addEventListener('mousedown', (event) => event.preventDefault());
    list.addEventListener('mousemove', (event) => {
      const position = shown.findIndex((entry) => entry.item === (event.target as Element).closest('li'));
      if (position >= 0 && position !== active) setActive(position);
    });
    list.addEventListener('click', (event) => {
      const entry = shown.find((candidate) => candidate.item === (event.target as Element).closest('li'));
      if (entry) {
        choose(entry);
        input.focus();
      }
    });
    toggle.addEventListener('mousedown', (event) => event.preventDefault());
    toggle.addEventListener('click', () => {
      if (isOpen()) close();
      else {
        input.focus();
        open(null);
      }
    });
    clear.addEventListener('mousedown', (event) => event.preventDefault());
    clear.addEventListener('click', () => {
      choose(entries[0]);
      input.focus();
    });
    select.addEventListener('change', sync);
    select.addEventListener('filter:sync', sync);
    sync();
  }
}
