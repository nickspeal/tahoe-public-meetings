# Implementation handoff

This is the scope and architecture brief. Read it before earlier comparison documents. A local proof of concept was implemented on 2026-10-19; see [implementation status](implementation-status.md) and the root README for commands, verification, and remaining limits. No cloud resources or deployment have been created. Code is public on GitHub ([decision 0016](../decisions/0016-public-open-source-repository.md)); commits go directly to `main` and are pushed to `origin`.

## Current coverage update (2026-10-20)

[Decision 0009](../decisions/0009-configurable-coverage-and-one-off-meetings.md) supersedes the earlier three-agency-only scope and topic selection below. The maintainer now requests a configurable agency watchlist seeded from all 41 CSV entries, retaining Truckee (42 entries), six topics (Biking including pedestrian/road repair, Transit, Housing, Short Term Rentals implying Housing, Environment, Hot Topics), and code-configured one-off meetings. Existing discovery adapters still cover only SLT, Truckee, and TTD; other registry entries are unverified research/manual coverage pending adapter work. See the [configuration guide](../../config/README.md). Stack, budget, editorial rules, and storage constraints remain unchanged.

## Source review and participation update (2026-10-20)

[Decision 0014](../decisions/0014-reviewed-sources-and-public-comment-periods.md) applies the maintainer's SR decisions: six entries disabled (preserved in the registry), two parks bodies added (44 entries / 38 followed), federal sources retained for public comment windows, and statewide Parks coverage retained. Comment periods are separate participation records with verified deadlines, not multi-day meetings. Reusable body-specific discovery and verified-TLS PDF/HTML capture are implemented; persistent Placer 403 errors remain a tracked deferred issue. See the [source review resolutions](../operations/source-review.md) and [configuration guide](../../config/README.md).

## Claude topic classification update (2026-10-20)

[Decision 0011](../decisions/0011-claude-for-automated-analysis.md) makes Claude the topic classifier: `worker classify-topics` sends new or changed meetings to Haiku 5.5, falls back to Sonnet 5.5, and leaves rule tags when both fail. It runs only by hand today.

**Pending scheduling work** (part of the scheduling step in the first implementation slice):

- Add `classify-topics` to the scheduled Cloud Run job after ingestion and before `export`, so new agendas get Claude tags without a manual run. Until then, they show rule tags.
- Store `ANTHROPIC_API_KEY` as a Secret Manager secret for the job, never in the image or repository, and install the worker's optional `ai` extra in the job image.
- Set Anthropic console spend limits as a backstop to the job's `--max-cost` and `--monthly-budget` guards, and alert on failed meetings or a run stopped by a cost limit (both are in the command's JSON output and the `ai_usage` table).
- Consider the Message Batches API (50% cheaper) once volume justifies the delay; at current volume a run costs about $0.02.

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

See [decision 0005](../decisions/0005-selected-stack.md) for account verification. Do not use another Vercel account because it happens to be logged in. Prisma workspace and Google project/billing identity still require identification before provisioning. No deployment has been requested as part of this documentation handoff.

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
