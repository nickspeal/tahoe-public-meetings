import type { AgendaItem, Meeting } from '../../../../packages/domain/public';
import { allItems } from './data';

/**
 * One top-level entry on a meeting page: a named agenda section with its items,
 * or (`name` null) items the agenda lists outside any section.
 */
export interface AgendaGroup {
  name: string | null;
  /** The section's own agenda number when the agenda printed the heading as a numbered entry ("6 Consent Calendar"). */
  number: string | null;
  /** Item ID of that heading entry; it is shown as the section header instead of as an item. */
  headingItemId: string | null;
  items: AgendaItem[];
}

const words = (text: string) => text.toLowerCase().match(/[a-z0-9]+/g) ?? [];

/** "CONSENT CALENDAR" and "Consent Calendar", or a heading whose stored title lost a word or a page number. */
export function sameHeading(a: string, b: string) {
  const [short, long] = [words(a), words(b)].sort((x, y) => x.length - y.length);
  if (!short.length) return false;
  const present = new Set(long);
  return short.filter((word) => present.has(word)).length / short.length >= 0.8 && short.length / long.length >= 0.5;
}

/**
 * Agenda items grouped as the agency's agenda groups them.
 *
 * - A section is a run of consecutive items with the same `section` value, in agenda order.
 * - An agenda heading that the parser also listed as an item ("Presentations" above 5.1 and 5.2)
 *   becomes the section header rather than repeating as the first item, unless it has topics.
 * - A section whose only item is its own heading ("Roll Call") is a standalone item.
 * - Items without a section stay standalone, between the sections around them; consecutive ones share a group.
 * - A meeting with no sections at all returns one unnamed group, which renders as a flat list.
 */
export function agendaGroups(meeting: Meeting): AgendaGroup[] {
  const groups: AgendaGroup[] = [];
  const standalone = (item: AgendaItem) => {
    const last = groups.at(-1);
    if (last && last.name === null) last.items.push(item);
    else groups.push({ name: null, number: null, headingItemId: null, items: [item] });
  };
  let run: AgendaItem[] = [];
  const close = () => {
    if (!run.length) return;
    const name = run[0].section!;
    const [first, ...rest] = run;
    const isHeading = sameHeading(first.title, name);
    if (run.length === 1 && isHeading) standalone(first);
    else if (isHeading && first.topics.length === 0) groups.push({ name, number: first.number || null, headingItemId: first.id, items: rest });
    else groups.push({ name, number: null, headingItemId: null, items: run });
    run = [];
  };
  for (const item of allItems(meeting)) {
    if (run.length && item.section !== run[0].section) close();
    if (item.section) run.push(item);
    else standalone(item);
  }
  close();
  return groups;
}

/** Items in the order the meeting page shows them. */
export function displayedItems(groups: AgendaGroup[]): AgendaItem[] {
  return groups.flatMap((group) => group.items);
}
