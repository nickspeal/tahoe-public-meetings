const dialog = document.querySelector<HTMLDialogElement>('#feedback-dialog');
const form = document.querySelector<HTMLFormElement>('#feedback-form');

interface FeedbackOptions { kind?: string; prompt?: string }

if (dialog && form) {
  const message = form.elements.namedItem('message') as HTMLTextAreaElement;
  const kind = form.elements.namedItem('kind') as HTMLInputElement;
  const trap = form.elements.namedItem('website') as HTMLInputElement;
  const status = form.querySelector<HTMLElement>('#feedback-status')!;
  const submit = form.querySelector<HTMLButtonElement>('.feedback-submit')!;
  const title = dialog.querySelector<HTMLElement>('#feedback-title')!;
  const help = dialog.querySelector<HTMLElement>('#feedback-help')!;
  const generalHelp = help.textContent;
  let opener: HTMLElement | null = null;
  let sent = false;

  function open({ kind: requestedKind = 'general', prompt = '' }: FeedbackOptions = {}) {
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const topic = requestedKind === 'topic';
    // Keep an unsent draft unless a different starter prompt was asked for.
    if (sent || !message.value.trim() || (prompt && !message.value.startsWith(prompt.trim()))) message.value = prompt;
    sent = false;
    kind.value = topic ? 'topic' : 'general';
    title.textContent = topic ? 'Suggest a topic' : 'Send feedback';
    help.textContent = topic ? 'What subject should the site follow? Examples of agenda items, places, or agencies help.' : generalHelp;
    status.textContent = '';
    status.classList.remove('is-error');
    submit.disabled = false;
    dialog!.showModal();
    message.focus();
    message.setSelectionRange(message.value.length, message.value.length);
  }

  function close() {
    dialog!.close();
  }

  dialog.addEventListener('close', () => opener?.focus());
  dialog.addEventListener('click', (event) => { if (event.target === dialog) close(); });
  for (const button of dialog.querySelectorAll('[data-feedback-close]')) button.addEventListener('click', close);
  document.addEventListener('click', (event) => {
    const trigger = (event.target as Element | null)?.closest<HTMLElement>('[data-feedback-open]');
    if (!trigger) return;
    event.preventDefault();
    open({ kind: trigger.dataset.feedbackKind, prompt: trigger.dataset.feedbackPrompt });
  });
  window.addEventListener('feedback:open', (event) => open((event as CustomEvent<FeedbackOptions>).detail ?? {}));

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const text = message.value.trim();
    if (text.length < 3) {
      status.textContent = 'Please write a little more before sending.';
      status.classList.add('is-error');
      message.focus();
      return;
    }
    submit.disabled = true;
    status.classList.remove('is-error');
    status.textContent = 'Sending…';
    try {
      const response = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, kind: kind.value, page: `${location.pathname}${location.search}`, website: trap.value }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.error || 'Feedback could not be sent. Please try again later.');
      sent = true;
      status.textContent = 'Thank you. Your feedback was sent.';
      message.value = '';
      window.setTimeout(() => { if (sent && dialog!.open) close(); }, 2500);
    } catch (error) {
      status.textContent = error instanceof Error && error.message !== 'Failed to fetch' ? error.message : 'Feedback could not be sent. Check your connection and try again.';
      status.classList.add('is-error');
      submit.disabled = false;
    }
  });
}
