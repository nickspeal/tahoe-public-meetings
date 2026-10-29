import type { Meeting, PublicData } from '../../../../packages/domain/public';
import registry from '../../../../packages/domain/agencies.json';

type Agency = PublicData['agencies'][number];
type Topic = PublicData['topics'][number];

// Read from the registry at build time, like the topic catalog: aliases are search vocabulary, not exported meeting data.
// The raw file is safe to read here because `aliases` is one of the keys `extends` never passes on (catalog.OWN_KEYS).
const entries = registry as { id: string; aliases?: string[]; meetingHost?: string }[];
const aliasesById = new Map(entries.map((entry) => [entry.id, entry.aliases ?? []]));
// `meetingHost` is also an own key, so the raw entry says whether meetings under it name their own host (Other).
const perRecordHosts = new Set(entries.filter((entry) => entry.meetingHost === 'per-record').map((entry) => entry.id));

/** True for an entry that is not a body: its meetings name their own host (Other). */
export const isPerRecordHost = (agencyId: string) => perRecordHosts.has(agencyId);

/** Agencies as the filter lists them: bodies in export order, then the entries whose meetings name their own host (Other). */
export function filterAgencies(agencies: Agency[]): Agency[] {
  return [...agencies.filter((agency) => !perRecordHosts.has(agency.id)), ...agencies.filter((agency) => perRecordHosts.has(agency.id))];
}

/** Other names a reader might type for an agency: its short name, acronyms, and full official names. */
export function agencySearchTerms(agency: Agency, meetings: Pick<Meeting, 'agencyId' | 'host'>[] = []): string[] {
  // Other has no names of its own: typing a host ("El Dorado") finds it through its meetings' hosts.
  const hosts = perRecordHosts.has(agency.id) ? meetings.filter((meeting) => meeting.agencyId === agency.id).map((meeting) => meeting.host) : [];
  return [...new Set([agency.shortName, ...(aliasesById.get(agency.id) ?? []), ...hosts])].filter((term): term is string => Boolean(term) && term !== agency.name);
}

/** A topic also matches words from its description, so "road repair" finds Biking. */
export function topicSearchTerms(topic: Topic): string[] {
  return topic.description ? [topic.description] : [];
}
