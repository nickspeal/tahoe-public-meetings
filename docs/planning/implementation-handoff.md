# Implementation handoff

This is the scope and architecture brief. Read it before earlier comparison documents. A local proof of concept was implemented on 2026-10-19; see [implementation status](implementation-status.md) and the root README for commands, verification, and remaining limits. Production runs at https://tahoepublicmeetings.org from the scheduled pipeline ([decision 0018](../decisions/0018-scheduled-publication.md), [deployment runbook](../operations/deployment.md)). Code is public on GitHub ([decision 0016](../decisions/0016-public-open-source-repository.md)); commits go directly to `main` and are pushed to `origin`.

## Current coverage update (2026-10-20)

[Decision 0009](../decisions/0009-configurable-coverage-and-one-off-meetings.md) supersedes the earlier three-agency-only scope and topic selection below. The maintainer now requests a configurable agency watchlist seeded from all 41 CSV entries, retaining Truckee (42 entries), six topics (Biking including pedestrian/road repair, Transit, Housing, Short Term Rentals implying Housing, Environment, Hot Topics), and code-configured one-off meetings. Every agency is defined by its entry in the registry ([decision 0027](../decisions/0027-agency-definitions-in-configuration.md)); [source coverage](../operations/source-coverage.md) is generated from it and gives the current counts of followed, live, blocked, and snapshot-only entries. To add an agency, follow the [checklist](../../config/README.md#adding-an-agency); see also the [adapter expansion brief](../operations/adapter-expansion.md). See the [configuration guide](../../config/README.md). Stack, budget, editorial rules, and storage constraints remain unchanged.

## Source review and participation update (2026-10-20)

[Decision 0014](../decisions/0014-reviewed-sources-and-public-comment-periods.md) applies the maintainer's SR decisions: six entries disabled (preserved in the registry), two parks bodies added (current counts in [source coverage](../operations/source-coverage.md)), federal sources retained for public comment windows, and statewide Parks coverage retained. Comment periods are separate participation records with verified deadlines, not multi-day meetings. Reusable body-specific discovery and verified-TLS PDF/HTML capture are implemented; persistent Placer 403 errors remain a tracked deferred issue. See the [source review resolutions](../operations/source-review.md) and [configuration guide](../../config/README.md).

## Claude topic classification update (2026-10-20)

[Decision 0011](../decisions/0011-claude-for-automated-analysis.md) makes Claude the topic classifier: `worker classify-topics` sends new or changed meetings to Haiku 5.5, falls back to Sonnet 5.5, and leaves rule tags when both fail. It runs only by hand today.

**Scheduling work** (done 2026-10-20 except where noted; see decision 0018):

- Done: `classify-topics` runs in the scheduled Cloud Run job after ingestion and before `export`; the image installs the worker's `ai` extra.
- Done, differently: the job authenticates with Anthropic workload identity federation from its Google service account, so no `ANTHROPIC_API_KEY` is stored anywhere.
- Done: a $5/month Claude workspace spend limit backs the job's `--max-cost` and `--monthly-budget` guards. Failed executions email the operator; failed meetings and cost stops appear in the run's JSON summary and the `ai_usage` table and also send an ntfy alert.
- Done: a GitHub Actions freshness check alerts through ntfy when the published `/status.json` is more than 14 hours old, which covers a paused or non-firing scheduler (deployment runbook, "Silent-failure alerts"). Operator steps remain: store the `tahoe-ntfy-url` job secret and the repository `NTFY_URL` secret.
- Consider the Message Batches API (50% cheaper) once volume justifies the delay; an incremental run costs about $0.02; the first full classification of 1,738 items cost $0.11.

## Item summaries and staff report links (2026-10-27)

[Decision 0024](../decisions/0024-verbatim-item-summaries.md) applies the maintainer's rules: a summary repeats the source verbatim, changed only for brevity, and leads with the requested action the agenda prints under the item. Implemented locally, not yet deployed:

- `worker map-staff-reports` links items to their own staff reports (SLT, TTD, Truckee, Lahontan; two independent signals must agree) and lists SLT items' attachments. It downloads PDFs temporarily and keeps URL, page range, hash, evidence, and the report's first three pages of text.
- The export leads with the agenda's requested action (deterministic, no model) and, once `worker summarize-items` has run, adds one or two staff report sentences selected by Haiku 5.5 (Sonnet fallback) and validated against the stored page text.
- Both run in `run-scheduled` after `participation`: `staff-reports` (at most `--staff-report-meetings 6` meetings per run) and `summarize-items` (its own `--summary-max-cost $0.50` under the shared $5 monthly budget). Each adds about one database statement per affected meeting (about 15 per ordinary run); the export reads all of it in one more query.

## Staff reports for every agency (2026-10-28)

[Decision 0026](../decisions/0026-staff-reports-for-every-agency.md) applies the maintainer's request that every agency whose published data allows it gets staff report links and summaries. Implemented locally, not yet deployed:

- `tahoe_ingest/staff_report_rules/` holds one rule module per publishing format (TRPA, Douglas, Legistar counties, SLT commissions, Tahoe City PUD, South Tahoe PUD, North Tahoe PUD, PCTPA, the airport district, Incline Village GID, Fish and Game, State Parks, the Nevada water board, plus the four from decision 0024); the core discovers them. Rules register under a format name (`ttd-packet`, `legistar`), and each agency picks one, with its parameters, in its `staffReports` entry in `agencies.json`, so an agency on a supported format needs only configuration ([rules README](../../workers/ingestion/tahoe_ingest/staff_report_rules/README.md)). Rules share `Context` helpers for meeting-page links, JSON APIs, Word files, linked reports, and packet page runs. About 536 more items map locally.
- TRPA items link to their own report file, list its attachments, and fall back to the board packet; TRPA meetings capture both the short agenda and the full packet.
- Unsupported: NTCD, Tahoe RCD, the two TMAs, Nevada State Parks, OHMVR (no numbered reports); the Tahoe Conservancy and State Lands Commission until their HTML agendas have adapters; Placer County (HTTP 403).
- Deployment notes: the worker image adds `antiword` for Washoe's legacy Word reports; the first production runs re-capture the six TRPA meetings on the index (a few hundred one-time database operations) and work through the summary backlog at `--summary-max-cost` per run.

## Previews from notices and planning calendars (2026-10-29)

[Decision 0028](../decisions/0028-previews-from-notices-and-planning-calendars.md) applies the maintainer's IDEA-018 decisions. The worker image (tag `a7140adc4582`) was deployed to the `tahoe-ingest` and `tahoe-backfill` jobs on 2026-10-29 without running them. The first production run waits for the scheduler to resume when the Prisma operations quota resets on 2026-11-01. Until then the public site shows the previews from a locally built export:

- `notices` (after `comment-periods`; also `tahoe-ingest extract-notices`) has Claude Haiku 5.5 (Sonnet fallback) read each tracked comment period notice once per notice version, keeps only events whose quote, dates, times, locations, hosts, and body name appear verbatim in the notice, and stores one-off meetings (the West Shore Trail scoping open house, under Other with host "TRPA and El Dorado County") and planned-meeting previews (the TRPA Advisory Planning Commission presentation).
- `planning-calendars` (after `staff-reports`; also `tahoe-ingest read-planning-calendars`) reads each agency's `planningCalendar` with a deterministic parser registered by format (`slt-apc-attachment` downloads the calendar attached to the South Lake Tahoe agenda; `ttd-packet-pages` reads the stored packet pages). A newer calendar replaces the previous one's previews; a calendar that fails validation publishes nothing and is named in the run summary. Subjects get rule tags, then Claude tags with a cache keyed on agency and normalized subject.
- The export adds `host`, `planned`, `previews`, and `previewNotOnAgenda` on meetings and `host`, `meetingIds`, and `previousIds` on comment periods, all optional, in one more bulk read. The website shows upcoming meetings with previews by default under the "Expected · no agenda yet" pill, lists planned subjects on cards and meeting pages with a linked source line, offers an Other agency filter, and redirects the moved comment period page.
- Deployment notes: the new tables and columns reach production through `init-db`; the first run reads one notice and the stored calendars (five South Lake Tahoe downloads, a few Haiku calls under the shared budget).

## Accepted product scope (earlier baseline; see update above)

Build a public webpage residents revisit to discover upcoming agenda items across **City of South Lake Tahoe, Town of Truckee, and Tahoe Transportation District**. The central view is a meeting timeline spanning past and future with today marked and upcoming meetings emphasized. A shared, extensible topic catalog starts with **Biking**, **Transit**, **Housing**, and **Environmental Protection** and provides bookmarkable timeline filters alongside agency filtering. Meeting pages show all covered topics, visible tags on agenda items, and filters for any selected topics. See [decision 0007](../decisions/0007-meeting-timeline-and-filters.md) for the timeline and [decision 0008](../decisions/0008-shared-topics-and-meeting-filters.md) for the expanded catalog and meeting controls. Include meeting and item details, cited summaries, official document links, logistics, participation instructions, and local calendar downloads where start times are verified. Show cancellations, material revisions, freshness, and missing information clearly without routine "new" badges. Add recordings/minutes when available; do not confuse proposed actions with adopted decisions.

No alerts/subscriptions or resident accounts in MVP. Defer polling, advocacy campaigns, public comment submission, chat, translations, and personalized bookmarks/notes. Preserve these in `docs/ideas/`.

Summaries publish automatically after programmatic validation; no mandatory human approval. Coverage is mostly neutral. Tahoe Public Meetings has one editorial voice (decision 0010), shaped by the maintainer through topic selection, classification rules, and summarization steering; there are no attributed personal perspective notes. The public sees only the latest summary, not revision history.

## Accepted architecture

| Responsibility | Selected direction |
| --- | --- |
| Public website | Vercel, under the project's designated account/scope |
| Structured data | Prisma Postgres |
| Ingestion/analysis | Python container on Google Cloud Run Jobs |
| Scheduling | Google Cloud Scheduler |
| Source PDFs | Temporary downloads; readers follow official links |
| Durable derived data | Page-separated text, source metadata/hashes, summaries, topic assignments, internal corrections and overrides |
| AI | Claude via the Anthropic API ([decision 0011](../decisions/0011-claude-for-automated-analysis.md)); topic classification uses Haiku 5.5 with a Sonnet 5.5 fallback and rule tags as the last resort (`worker classify-topics`) |

See [decision 0005](../decisions/0005-selected-stack.md) for account verification. Do not use another Vercel account because it happens to be logged in. Prisma Postgres is provisioned through the Vercel Marketplace on the designated scope, and the ingestion job runs in its own Google Cloud project; see the deployment runbook.

## Proposed implementation defaults, not additional maintainer commitments

- Astro/TypeScript frontend generating static HTML and small client-side filters. Use a versioned public JSON export so the frontend does not depend on Python internals or expose credentials.
- Start local with PostgreSQL to match the selected hosted database. Existing TTD SQLite utilities may remain in source tooling where useful, but SQLite is not the selected production datastore.
- Reuse suitable TTD Python extraction/discovery code, package native PDF dependencies in Docker, and use a database job table/lease rather than adding a queue service immediately.
- Keep extracted text in Postgres initially if measured usage fits comfortably; isolate storage access so larger text can later move to object storage. No R2 or Cloud Storage bucket is a prerequisite merely to archive PDFs. Independent application backups may justify a small bucket.
- Publish generated pages after successful processing and at least daily so date grouping stays correct. Keep the prior site live if a build fails. Automating Vercel CLI/API publication can work without Git; verify the deployment identity and current tooling when implementing.
- Provisional six-hour source-check cadence, with source-specific backoff. The maintainer has not selected a freshness target yet.
- Local CLI for internal steering (corrections, tag overrides, suppression) initially. Add a remote admin interface only if justified; any remote write endpoint requires authentication and authorization.
- Use Claude for automated analysis ([decision 0011](../decisions/0011-claude-for-automated-analysis.md)). Evaluate Claude Haiku 5.5 against the rule classifier with `tahoe-ingest evaluate-topics` before adopting it; see the [evaluation](../research/topic-classification-evaluation-2026-10-20.md). Adopt a cheaper non-Claude model only for a strong cost or convenience reason. Classify from agenda-level text by default rather than whole packets. Do not bind application schemas to one provider or promise that a chat subscription covers API use.

## Source material and workload evidence

The earlier TTD project lives outside this repository. Inspect relevant instructions before reuse; do not run the launcher as an ingestion command or modify the original project inadvertently. Source inspection: [TTD reuse](../research/ttd-reuse.md).

The maintainer's expected workload: 5 meetings/week, roughly 300 total PDF pages/week, and 100 visitors making 5 visits/week. This is about 22 meetings, 1,300 pages, and 2,167 visits per month. Pages per visit remain an estimate. Documents may arrive in bursts; 1–100 PDFs/meeting is not a simultaneous average consistent with 300 total pages/week.

Measurements: [TTD](../research/ttd-storage-measurements.md) contains 308 PDFs, 34,648 pages, 1.78 GB originals and 117 MB text. [SLT samples](../research/slt-document-storage.md) include a 7-page agenda and a 505-page/38 MB apparent packet for one meeting. Size worker memory/scratch usage for large packets, not only the weekly average. Truckee has not yet been audited.

## Retention and reliability

Follow [decision 0004](../decisions/0004-source-first-storage.md). Stream PDFs into bounded temporary storage, persist text/source hash/page references, then clean up after success. Use bounded retry retention for failures. Do not create an automatic full PDF backup. Existing local archives remain untouched. Monitor source availability and changed bytes; reconsider archival copies if originals prove unreliable. A stored hash cannot reconstruct a removed document.

Preserve human edits across reprocessing. A source revision invalidates affected generated summaries and leaves operator corrections and overrides in place. De-duplicate identical document bytes and make ingestion retries idempotent. Detect packet/attachment overlap without silently omitting unique supporting material. Persist extraction failures instead of publishing empty text as complete coverage.

## First implementation slice

1. Read product brief, build plan, decisions 0002–0005, and source research.
2. Set up the local app structure, schemas/migrations, environment examples, and one representative fixture. Track implementation changes in Git.
3. Implement one TTD meeting end to end: source metadata → page text → agenda items → topic assignment/summary → public topic and meeting page.
4. Check citations, dates, missing fields, mobile/keyboard usability, source changes, and retry behavior. Measure extraction/model usage on the large SLT packet as a separate feasibility check.
5. Generalize to SLT and Truckee, then add scheduling, publication, cost controls, recovery procedures, and beta validation.

All three agencies remain the launch target even though TTD is the first development slice. Use the acceptance criteria in [MVP build plan](mvp-build-plan.md).

## Operating constraints and public-source preparation

The project is volunteer-run. Total recurring expense must be **under $20/month**, preferably much lower. Track quota consumption, model tokens, retries, storage, and auxiliary Google charges; use current provider documentation when implementing. Free-tier figures in research are dated estimates, not guarantees.

The repository is public under the MIT License ([decision 0016](../decisions/0016-public-open-source-repository.md)). Credentials, local data, private notes, generated database files, and deployment identity files are not distributable source and stay out of Git. Everything committed is publicly visible.
