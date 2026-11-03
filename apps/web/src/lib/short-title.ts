import type { AgendaItem } from '../../../../packages/domain/public';

/**
 * Short headlines for long agenda titles (decision 0035).
 *
 * The export adds `shortTitle` only to an item whose whole agenda text is long and only after Claude's
 * headline passed the worker's checks. The headline then replaces the shown title, marked as shortened,
 * and the expanded item always carries the agenda's whole text. Self-contained (no imports beyond types)
 * so the rest of the title and detail logic in `agenda-excerpt.ts` stays as it is.
 */
type Detail = { label: string; text: string };
const AGENDA_LABEL = 'From the agenda';

/** The headline to show: the short title when there is one, else the title as `agenda-excerpt.itemTitle` shows it. */
export function itemHeadline(item: Pick<AgendaItem, 'shortTitle'>, shownTitle: string): { text: string; shortened: boolean } {
  return item.shortTitle ? { text: item.shortTitle, shortened: true } : { text: shownTitle, shortened: false };
}

/**
 * The expanded item's blocks (from `agenda-excerpt.itemDetails`), with the agenda's whole text added when a
 * short title replaced a title that was not cut (a cut title's whole text is already among them).
 */
export function detailsWithFullText(item: Pick<AgendaItem, 'title' | 'fullTitle' | 'shortTitle'>, details: Detail[]): Detail[] {
  if (!item.shortTitle || item.fullTitle || details.some((detail) => detail.label === AGENDA_LABEL)) return details;
  return [{ label: AGENDA_LABEL, text: item.title }, ...details];
}

/** Hover text for the "Shortened" mark beside a short title. */
export const SHORTENED_NOTE = 'Headline shortened from the agenda’s wording. Open the item for the full agenda text.';
