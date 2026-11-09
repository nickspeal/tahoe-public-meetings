// Permanent meeting page paths: /meetings/<body page slug>/<local date>[-<qualifier>]/.
// Pure (no Astro, no export data) so tests load it directly. Stability is the point: a path must survive a planned
// placeholder being replaced by the confirmed record, a title tweak between agenda versions, a cancellation, and
// another meeting appearing on the same day. So the qualifier comes only from a fixed keyword list, never from
// whether the date is shared, and a regular meeting always keeps the bare date.

/** The parts of an agencies.json entry the paths need. `page` and `meetingHost` are read raw: neither is inherited through `extends`. */
export type AgencyEntry = { id: string; follow?: boolean; page?: { slug: string; name: string }; meetingHost?: string };
export type AgencyPage = { slug: string; name: string; perRecordHost: boolean };
type PathMeeting = { id: string; agencyId: string; sourceId: string; title: string; startsAt: string | null; date?: string | null; timezone?: string | null };

/** Body pages by agency ID, for followed entries that define one. */
export function agencyPages(entries: AgencyEntry[]): Map<string, AgencyPage> {
  const pages = new Map<string, AgencyPage>();
  const slugs = new Map<string, string>();
  for (const entry of entries) {
    if (!entry.follow || !entry.page) continue;
    const other = slugs.get(entry.page.slug);
    if (other) throw new Error(`Agencies ${other} and ${entry.id} share the page slug ${entry.page.slug}`);
    slugs.set(entry.page.slug, entry.id);
    pages.set(entry.id, { slug: entry.page.slug, name: entry.page.name, perRecordHost: entry.meetingHost === 'per-record' });
  }
  return pages;
}

/**
 * Words that mark a meeting as not the body's regular one, in the order they are joined. "Regular" and
 * "canceled" are deliberately absent: a regular meeting is the default, and canceling one must not move it.
 * "joint" skips "jointly hosted" (who holds a workshop) and "Joint Powers Authority" (a body's name), neither a kind of meeting.
 */
const QUALIFIERS: [string, RegExp][] = [
  ['special', /\bspecial\b/i],
  ['emergency', /\bemergency\b/i],
  ['joint', /\bjoint\b(?! powers)/i],
  ['closed-session', /\bclosed session\b/i],
  ['budget', /\bbudget\b/i],
  ['workshop', /\bworkshops?\b/i],
  ['study-session', /\bstudy session\b/i],
  ['retreat', /\bretreat\b/i],
  ['hearing', /\bhearings?\b/i],
  ['adjourned', /\badjourned\b/i],
  ['continued', /\bcontinued\b/i],
];

/** "special-closed-session" for "09 08 2026 Special Closed Session City Council"; "" for a regular meeting. */
export function meetingQualifier(title: string) {
  return QUALIFIERS.filter(([, pattern]) => pattern.test(title)).map(([slug]) => slug).join('-');
}

export function slugify(value: string) {
  return value.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

/** The meeting's calendar date where it is held: the start time in its timezone, else its date-only value. */
export function meetingLocalDate(meeting: Pick<PathMeeting, 'startsAt' | 'date' | 'timezone'>) {
  if (meeting.startsAt) {
    const start = new Date(meeting.startsAt);
    if (!Number.isNaN(start.valueOf())) return new Intl.DateTimeFormat('en-CA', { timeZone: meeting.timezone || 'America/Los_Angeles', year: 'numeric', month: '2-digit', day: '2-digit' }).format(start);
  }
  return meeting.date && /^\d{4}-\d{2}-\d{2}$/.test(meeting.date) ? meeting.date : null;
}

/**
 * The last path segment before any collision suffix. A host-per-record entry (Other) gathers unrelated
 * meetings, so each is named by its source ID, which is its permanent identity ("one-off-" and a trailing
 * date dropped as noise). An undated meeting is named by its source ID too, since there is no date to keep.
 */
export function meetingSegment(meeting: PathMeeting, page: Pick<AgencyPage, 'perRecordHost'>) {
  const date = meetingLocalDate(meeting);
  const sourceSlug = () => slugify(meeting.sourceId.replace(/^one-off-/, '').replace(/-?\d{4}-\d{2}-\d{2}$/, '')) || slugify(meeting.sourceId);
  if (!date) return `undated-${slugify(meeting.sourceId)}`;
  const qualifier = page.perRecordHost ? sourceSlug() : meetingQualifier(meeting.title);
  return qualifier ? `${date}-${qualifier}` : date;
}

/**
 * Paths for every meeting with a page, by meeting ID. Meetings that would share a path (two regular meetings of
 * one body on one day) get -2, -3, ... in start-time then ID order: a last resort, since a later, earlier-starting
 * arrival renumbers them. Throws on a meeting whose agency has no page, or on any path still shared.
 */
export function meetingPaths(meetings: PathMeeting[], pages: Map<string, AgencyPage>) {
  const groups = new Map<string, PathMeeting[]>();
  for (const meeting of meetings) {
    const page = pages.get(meeting.agencyId);
    if (!page) throw new Error(`Meeting ${meeting.id} has no page: agency ${meeting.agencyId} needs a page slug in packages/domain/agencies.json`);
    const base = `/meetings/${page.slug}/${meetingSegment(meeting, page)}/`;
    groups.set(base, [...(groups.get(base) ?? []), meeting]);
  }
  const paths = new Map<string, string>();
  const owners = new Map<string, string>();
  for (const [base, group] of groups) {
    const ordered = [...group].sort((a, b) => (a.startsAt ?? '').localeCompare(b.startsAt ?? '') || a.id.localeCompare(b.id));
    ordered.forEach((meeting, index) => {
      const path = index === 0 ? base : `${base.slice(0, -1)}-${index + 1}/`;
      const owner = owners.get(path);
      if (owner) throw new Error(`Meetings ${owner} and ${meeting.id} share the page path ${path}`);
      owners.set(path, meeting.id);
      paths.set(meeting.id, path);
    });
  }
  return paths;
}
