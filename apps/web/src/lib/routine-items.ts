import type { AgendaItem } from '../../../../packages/domain/public';

/**
 * Meeting mechanics that every agenda lists and residents do not need to read:
 * opening, roll call, pledge, approving the agenda, and adjournment.
 * A title is routine when every part of it (split on "/", ",", "and", dashes) is one of these.
 * Public comment, closed session, minutes, and reports are not routine: they carry substance.
 */
const ROUTINE_PARTS = [
  /^call (?:meeting )?to order$/,
  /^call to order (?:regular |special )?meeting$/,
  /^(?:(?:directors|supervisor|board|commissioner)s? )?roll call(?: of trustees)?$/,
  /^(?:pledge of allegiance|flag salute|salute to the flag|invocation)$/,
  /^open session$/,
  /^(?:determination|establishment|establish|confirmation) of (?:a )?quorum(?: of \w+)?$/,
  /^establish quorum$/,
  /^introduction$/,
  /^adjourn(?:ment)?(?: of (?:the )?meeting)?$/,
  /^to (?:the )?next regular meeting$/,
  /^adjourn as .+/,
  /^(?:re)?convene as .+/,
  /^(?:approval|adoption|approve|adopt|review)(?: of)? (?:the )?agenda$/,
  /^corrections to the agenda(?: or consent calendar)?$/,
  /^agenda review$/,
  /^changes or additions to the agenda$/,
  /^consider approving (?:the )?agenda$/,
  /^order of items$/,
];
/** Only after a call to order: "Call to Order – Chair Jane Doe". */
const DATE = /(?:,?\s*\bon\s+)?\b(?:january|february|march|april|may|june|july|august|september|october|november|december) \d{1,2},? \d{4},?/g;
const PRESIDING = /^(?:chair|chairperson|mayor|president|vice chair)\b/;

function parts(title: string) {
  return title
    .toLowerCase()
    .replace(/[’']/g, '')
    .replace(/\((?:discussion|no action|information|informational)\)/g, ' ')
    .replace(DATE, ' ')
    .replace(/\bquorum of (\w+)\/\w+/g, 'quorum of $1')
    .replace(/[.:]+\s*$/, '')
    .replace(/\s+(?:-|–|—)\s*action$/, '')
    .replace(/\s+for\s*$/, '')
    .split(/\s*(?:\/|,|;|&|\band\b|–|—|\s-\s)\s*/)
    .map((part) => part.replace(/\s+/g, ' ').trim())
    .filter(Boolean);
}

export function isRoutineTitle(title: string) {
  const pieces = parts(title);
  if (!pieces.length) return false;
  const opening = pieces.some((part) => /^call (?:meeting )?to order$/.test(part));
  return pieces.every((part) => ROUTINE_PARTS.some((pattern) => pattern.test(part)) || (opening && PRESIDING.test(part)));
}

/** Routine items are hidden unless something about them matters: a topic or a stated time. */
export function isRoutineItem(item: Pick<AgendaItem, 'title' | 'topics' | 'timeCertain'>) {
  return item.topics.length === 0 && !item.timeCertain && isRoutineTitle(item.title);
}
