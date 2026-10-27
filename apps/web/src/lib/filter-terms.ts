import type { PublicData } from '../../../../packages/domain/public';
import registry from '../../../../packages/domain/agencies.json';

type Agency = PublicData['agencies'][number];
type Topic = PublicData['topics'][number];

// Read from the registry at build time, like the topic catalog: aliases are search vocabulary, not exported meeting data.
const aliasesById = new Map((registry as { id: string; aliases?: string[] }[]).map((entry) => [entry.id, entry.aliases ?? []]));

/** Other names a reader might type for an agency: its short name, acronyms, and full official names. */
export function agencySearchTerms(agency: Agency): string[] {
  return [...new Set([agency.shortName, ...(aliasesById.get(agency.id) ?? [])])].filter((term) => term && term !== agency.name);
}

/** A topic also matches words from its description, so "road repair" finds Biking. */
export function topicSearchTerms(topic: Topic): string[] {
  return topic.description ? [topic.description] : [];
}
