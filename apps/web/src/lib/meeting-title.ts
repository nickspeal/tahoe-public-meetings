/**
 * Card titles for the meeting list. Agencies publish titles like "Town Council Meeting" or
 * "TTD Board of Directors Meeting – November 4, 2026": the card already shows the date, and
 * a title alone should say whose meeting it is. This drops the date and filing words, and
 * puts the agency's short name in front when the title does not already name the agency.
 */
interface TitleAgency {
  name: string;
  shortName: string;
}

const MONTH = String.raw`(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?`;
const WEEKDAY = String.raw`(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+)`;
const DAY = String.raw`\d{1,2}(?:st|nd|rd|th)?(?:\s*[–-]\s*\d{1,2}(?:st|nd|rd|th)?)?`;
const DATE = String.raw`(?:\b${WEEKDAY}?${MONTH}\s+${DAY}(?:,?\s+\d{4})?|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})`;
// A date with the dash, "of", or "on" that attached it to the title.
const DATED = new RegExp(String.raw`\s*[–—-]?\s*(?:\b(?:of|on|for)\s+)?${DATE}\s*[–—-]?\s*`, 'gi');
// "Governing Board Documents October 28, 2026" and "City Council Meeting Agenda": the document, not the meeting.
const FILING = new RegExp(String.raw`\s*\b(?:Agenda|Documents|Packet)\b(?=\s*(?:[–—-]\s*)?(?:${DATE}|$))`, 'gi');
const CANCELED = /\s*(?:[–—-]\s*)?\(?\b(?:canceled|cancelled|notice of cancel+ation)\b\)?/gi;
const STATE = /^(?:California|Nevada)\b/;

function escape(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function mentions(title: string, phrase: string) {
  return phrase.trim().length > 0 && new RegExp(String.raw`(?:^|[^\p{L}])${escape(phrase.trim())}(?:$|[^\p{L}])`, 'iu').test(title);
}

/** "Tahoe City Public Utility District" spells TCPUD. */
function spellsAcronym(title: string, acronym: string) {
  if (!/^[A-Z]{2,}$/.test(acronym)) return false;
  const initials = title.split(/[\s/-]+/).filter((word) => /^\p{Lu}/u.test(word)).map((word) => word[0]).join('');
  return initials.includes(acronym);
}

/** True when the title already says which agency is meeting. */
export function namesAgency(title: string, agency: TitleAgency) {
  // "TRPA — Governing Board" names the agency in its first part; the rest is the body.
  const root = agency.name.split(' — ')[0];
  // State bodies lead with the state ("Nevada Division of State Parks ...").
  return mentions(title, agency.shortName) || mentions(title, root) || spellsAcronym(title, agency.shortName) || STATE.test(title);
}

function tidy(title: string) {
  return title
    .replace(/\(\s*[;,]?\s*/g, '(')
    .replace(/\s*[;,]?\s*\)/g, ')')
    .replace(/\(\s*\)/g, '')
    .replace(/\s+/g, ' ')
    .replace(/^[\s–—\-,:;]+|[\s–—\-,:;]+$/g, '');
}

export function meetingCardTitle(title: string, agency: TitleAgency | undefined, { canceled = false } = {}) {
  let cleaned = title.replace(FILING, '').replace(DATED, ' ');
  cleaned = tidy(cleaned).replace(/\bHybrid\s+(?=Meeting\b)/gi, '');
  // The card shows a Canceled badge, so the title does not need to say it too.
  if (canceled) cleaned = tidy(cleaned.replace(CANCELED, ''));
  if (!cleaned) cleaned = title.trim();
  if (agency && !namesAgency(cleaned, agency)) cleaned = `${agency.shortName} ${cleaned}`;
  return cleaned;
}
