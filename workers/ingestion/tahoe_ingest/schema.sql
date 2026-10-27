CREATE TABLE IF NOT EXISTS agencies (
  id text PRIMARY KEY, name text NOT NULL, short_name text NOT NULL,
  official_url text NOT NULL, source_url text NOT NULL,
  last_checked_at timestamptz, status text NOT NULL DEFAULT 'never_checked', error text
);
CREATE TABLE IF NOT EXISTS meetings (
  id text PRIMARY KEY, agency_id text NOT NULL REFERENCES agencies(id), source_id text NOT NULL,
  title text NOT NULL, starts_at timestamptz, local_date date, timezone text NOT NULL DEFAULT 'America/Los_Angeles',
  status text NOT NULL DEFAULT 'unknown', location text, official_url text NOT NULL,
  participation jsonb NOT NULL DEFAULT '{}'::jsonb, minutes_url text, recording_url text,
  last_checked_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(),
  change text, warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
  UNIQUE(agency_id, source_id)
);
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS local_date date;
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS observed_duration_minutes integer CHECK(observed_duration_minutes IS NULL OR observed_duration_minutes > 0);
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS duration_source text;
-- Minutes links (tahoe_ingest.records): approved or draft, and for draft minutes found through a
-- later meeting's agenda, where they are ("Item 6.1 on the September 8, 2026 agenda").
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS minutes_status text CHECK(minutes_status IS NULL OR minutes_status IN ('approved','draft'));
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS minutes_note text;
CREATE TABLE IF NOT EXISTS document_versions (
  id text PRIMARY KEY, meeting_id text NOT NULL REFERENCES meetings(id), source_document_id text NOT NULL,
  title text NOT NULL, url text NOT NULL, sha256 text NOT NULL,
  fetched_at timestamptz NOT NULL, page_count integer NOT NULL, status text NOT NULL,
  warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
  UNIQUE(meeting_id, source_document_id, sha256)
);
CREATE TABLE IF NOT EXISTS document_current (
  meeting_id text NOT NULL REFERENCES meetings(id), source_document_id text NOT NULL,
  version_id text NOT NULL REFERENCES document_versions(id),
  PRIMARY KEY(meeting_id, source_document_id)
);
CREATE TABLE IF NOT EXISTS document_pages (
  document_version_id text NOT NULL REFERENCES document_versions(id),
  page_number integer NOT NULL CHECK(page_number > 0), text text NOT NULL,
  PRIMARY KEY(document_version_id, page_number)
);
CREATE TABLE IF NOT EXISTS agenda_items (
  id text PRIMARY KEY, meeting_id text NOT NULL REFERENCES meetings(id), source_key text NOT NULL,
  number text NOT NULL, item_order integer NOT NULL, title text NOT NULL, section text,
  source_fingerprint text NOT NULL, citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  change text, active boolean NOT NULL DEFAULT true, updated_at timestamptz NOT NULL DEFAULT now(),
  classifier_version text,
  UNIQUE(meeting_id, source_key)
);
ALTER TABLE agenda_items ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
ALTER TABLE agenda_items ADD COLUMN IF NOT EXISTS classifier_version text;
CREATE TABLE IF NOT EXISTS summaries (
  item_id text PRIMARY KEY REFERENCES agenda_items(id), text text NOT NULL,
  status text NOT NULL, method text NOT NULL, human_reviewed boolean NOT NULL DEFAULT false,
  generated_at timestamptz NOT NULL, source_fingerprint text NOT NULL,
  citations jsonb NOT NULL DEFAULT '[]'::jsonb, error text
);
CREATE TABLE IF NOT EXISTS summary_versions (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  item_id text NOT NULL REFERENCES agenda_items(id), text text NOT NULL,
  status text NOT NULL, method text NOT NULL, human_reviewed boolean NOT NULL DEFAULT false,
  generated_at timestamptz NOT NULL DEFAULT now(), source_fingerprint text NOT NULL,
  citations jsonb NOT NULL DEFAULT '[]'::jsonb, error text
);
CREATE INDEX IF NOT EXISTS ix_summary_versions_item ON summary_versions(item_id,id DESC);
CREATE TABLE IF NOT EXISTS item_topics (
  item_id text NOT NULL REFERENCES agenda_items(id), topic_id text NOT NULL,
  source text NOT NULL, evidence jsonb, PRIMARY KEY(item_id, topic_id)
);
ALTER TABLE item_topics ADD COLUMN IF NOT EXISTS evidence jsonb;
CREATE TABLE IF NOT EXISTS topic_overrides (
  item_id text NOT NULL REFERENCES agenda_items(id), topic_id text NOT NULL,
  included boolean NOT NULL, author text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(item_id, topic_id)
);
CREATE TABLE IF NOT EXISTS editorial (
  item_id text PRIMARY KEY REFERENCES agenda_items(id), suppressed boolean NOT NULL DEFAULT false,
  correction jsonb, perspective jsonb, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS processing_runs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  agency_id text REFERENCES agencies(id), meeting_id text REFERENCES meetings(id),
  stage text NOT NULL, status text NOT NULL, attempts integer NOT NULL DEFAULT 1,
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  error text, detail jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_meetings_start ON meetings(starts_at);
CREATE INDEX IF NOT EXISTS ix_runs_status ON processing_runs(status, started_at DESC);

ALTER TABLE meetings ADD COLUMN IF NOT EXISTS declared_topics jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS media_type text NOT NULL DEFAULT 'application/pdf';
CREATE TABLE IF NOT EXISTS comment_periods (
  id text PRIMARY KEY,
  agency_id text NOT NULL REFERENCES agencies(id),
  source_id text NOT NULL,
  title text NOT NULL,
  opens_on date,
  closes_on date NOT NULL,
  closes_at timestamptz,
  official_url text NOT NULL,
  participation_url text,
  instructions text,
  topics jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_quote text NOT NULL,
  source_text text NOT NULL,
  source_sha256 text NOT NULL,
  checked_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (agency_id, source_id)
);
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS quarantine_reason text;
CREATE TABLE IF NOT EXISTS meeting_source_aliases (
  agency_id text NOT NULL REFERENCES agencies(id),
  alias text NOT NULL,
  meeting_id text NOT NULL REFERENCES meetings(id),
  PRIMARY KEY (agency_id,alias)
);
-- Claude topic classifications (decision 0011). The export uses a row only while it
-- matches the item's current source fingerprint; otherwise the rule tags stand.
CREATE TABLE IF NOT EXISTS ai_topic_classifications (
  item_id text PRIMARY KEY REFERENCES agenda_items(id),
  input_sha256 text NOT NULL, source_fingerprint text NOT NULL, prompt_sha256 text NOT NULL,
  model text NOT NULL, tahoe_relevant boolean NOT NULL, topics jsonb NOT NULL, reason text NOT NULL,
  classified_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ai_usage (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, recorded_at timestamptz NOT NULL DEFAULT now(),
  purpose text NOT NULL, model text NOT NULL, meeting_id text, ok boolean NOT NULL,
  input_tokens integer NOT NULL DEFAULT 0, output_tokens integer NOT NULL DEFAULT 0,
  cache_creation_input_tokens integer NOT NULL DEFAULT 0, cache_read_input_tokens integer NOT NULL DEFAULT 0,
  cost_usd numeric(12,6) NOT NULL, error text
);
CREATE INDEX IF NOT EXISTS ix_ai_usage_recorded ON ai_usage(recorded_at);
-- Validated public exports written by `tahoe-ingest run-scheduled` / `publish-export`.
-- The website build reads the newest row; only the latest 30 are retained.
CREATE TABLE IF NOT EXISTS public_exports (
  id bigserial PRIMARY KEY,
  schema_version integer NOT NULL,
  generated_at timestamptz NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
-- Participation details Claude read from a meeting's agenda pages (tahoe_ingest.participation).
-- The export uses a row only while version_ids are still the meeting's current agenda documents.
CREATE TABLE IF NOT EXISTS ai_participation (
  meeting_id text PRIMARY KEY REFERENCES meetings(id), version_ids jsonb NOT NULL, prompt_sha256 text NOT NULL,
  model text NOT NULL, fields jsonb NOT NULL, rejected jsonb NOT NULL DEFAULT '[]'::jsonb,
  extracted_at timestamptz NOT NULL DEFAULT now()
);
