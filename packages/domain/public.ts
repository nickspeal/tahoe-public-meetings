/** Public export v2. No credentials, private notes, or write operations. */
export interface Citation {
  documentId: string;
  page: number;
  quote: string;
}
export interface AgendaItem {
  id: string;
  number: string;
  order: number;
  title: string;
  section: string | null;
  topics: string[];
  summary: { text: string; status: 'published' | 'validation_failed' | 'pending'; method: string; generatedAt: string; citations: Citation[] } | null;
  citations: Citation[];
  change: 'new' | 'revised' | null;
  updatedAt: string;
  suppressed?: boolean;
  /** The agenda says this item will not be heard before this time (meeting's local time), with the source line that says so. */
  timeCertain?: { time: string; quote: string; documentId: string | null; page: number | null } | null;
}
export interface PublicDocument {
  id: string;
  title: string;
  url: string;
  sha256: string;
  fetchedAt: string;
  /** HTML/text documents use numbered source sections, not PDF pages. */
  mediaType?: "application/pdf" | "text/html" | "text/plain";
  pageCount: number;
  status: string;
  warnings: string[];
  pages: { number: number; text: string }[];
}
/**
 * When this site first found an official document (`added`) or a copy of it with
 * different text (`revised`). Not the agency's posting time; oldest first.
 */
export interface MeetingHistoryEvent {
  at: string;
  change: 'added' | 'revised';
  /** Document title as published with that version. */
  document: string;
}
/** Meeting length. Estimates are long-biased and must be labelled as estimates wherever shown. */
export interface MeetingDuration {
  minutes: number;
  source: 'published' | 'observed' | 'estimated';
  /** Human-readable explanation of where the length came from. */
  basis: string;
}
/** How an expected meeting was derived from an agency's usual schedule. */
export interface MeetingForecast {
  /** Label of the schedule rule that produced this date. */
  ruleLabel: string;
  /** Plain-language pattern, e.g. "Usually the first Wednesday at 3:00 PM". */
  basis: string;
  /** `configured` by an operator, or `learned` from stored meeting records. */
  source: 'configured' | 'learned';
}
export interface Meeting {
  id: string;
  agencyId: string;
  sourceId: string;
  /**
   * `confirmed`: a stored agency record (its agenda may not be posted yet).
   * `forecast`: an expected date derived at export time from the agency's usual
   * schedule, with no documents, items, topics, meeting page, or calendar file.
   */
  kind: 'confirmed' | 'forecast';
  /** Present only when `kind` is `forecast`. */
  forecast?: MeetingForecast;
  title: string;
  /** Operator-assigned topics from a meeting notice, independent of agenda items. */
  topics?: string[];
  startsAt: string | null;
  /** Verified local meeting date, including when an exact start time is unknown. */
  date?: string | null;
  timezone: string;
  status: 'scheduled' | 'canceled' | 'rescheduled' | 'unknown';
  location: string | null;
  officialUrl: string;
  participation: { instructions: string | null; url: string | null; remoteUrl: string | null; deadline: string | null };
  minutesUrl: string | null;
  recordingUrl: string | null;
  lastCheckedAt: string | null;
  updatedAt: string;
  change: 'new' | 'revised' | 'rescheduled' | 'canceled' | null;
  warnings: string[];
  documents: PublicDocument[];
  /** Absent from exports written before the history was added. */
  history?: MeetingHistoryEvent[];
  items: AgendaItem[];
  duration: MeetingDuration;
}
export interface PublicCommentPeriod {
  id: string;
  agencyId: string;
  title: string;
  opensOn: string | null;
  closesOn: string;
  /** Optional exact deadline, with a verified timezone. */
  closesAt: string | null;
  officialUrl: string;
  participationUrl: string | null;
  instructions: string | null;
  topics: string[];
  sourceQuote: string;
  sourceSha256: string;
  lastCheckedAt: string;
  updatedAt: string;
}
export interface PublicData {
  /** v2: meetings carry `kind`, and `meetings` can include forecast entries after confirmed records. */
  schemaVersion: 2;
  generatedAt: string;
  mode: 'fixture' | 'live';
  coverageNote: string;
  agencies: { id: string; name: string; shortName: string; officialUrl: string; sourceUrl: string; lastCheckedAt: string | null; status: 'ok' | 'error' | 'never_checked'; error: string | null }[];
  topics: { id: string; name: string; description: string }[];
  meetings: Meeting[];
  commentPeriods?: PublicCommentPeriod[];
}
