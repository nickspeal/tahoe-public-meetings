import type { AgendaItem } from '../../../../packages/domain/public';

const EXCERPT_METHOD = 'deterministic-source-excerpt-v1';
/** Agenda text beyond the title must add at least this many words to be worth showing. */
const MIN_EXTRA_WORDS = 4;

const words = (text: string) => text.toLowerCase().match(/[a-z0-9]+/g) ?? [];

/**
 * Text to show inside an expanded agenda item, or null when it would only repeat the title.
 *
 * Most agenda excerpts are the item's own title line; those are hidden. An excerpt that carries
 * more agenda text (a description, a recommended action) is shown as "From the agenda". Any
 * other published summary method is shown as a summary.
 */
export function itemDetail(item: Pick<AgendaItem, 'title' | 'summary'>): { label: string; text: string } | null {
  const summary = item.summary;
  if (summary?.status !== 'published') return null;
  if (summary.method !== EXCERPT_METHOD) return { label: 'Summary', text: summary.text };
  const quote = (summary.citations[0]?.quote ?? summary.text.replace(/^Agenda excerpt:\s*/, ''))
    .replace(/^[“"]|[”"]$/g, '')
    .replace(/\s+Page\s+\d+\s*$/i, '')
    .replace(/\s+/g, ' ')
    .trim();
  const titleWords = new Set(words(item.title));
  const extra = words(quote).filter((word) => !titleWords.has(word));
  return extra.length >= MIN_EXTRA_WORDS ? { label: 'From the agenda', text: quote } : null;
}
