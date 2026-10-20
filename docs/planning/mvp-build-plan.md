# MVP build plan

This is an ordered delivery plan, not a calendar estimate. Agencies and service vendors are selected; set dates after validating source coverage, reuse, and one measured ingestion cycle. Read the [implementation handoff](implementation-handoff.md) first.

## 0. Validate scope and sources

Confirm the questions in [open questions](open-questions.md). Inspect the TTD code for reusable parsing, prompting, and rendering. For each candidate agency, record its official meeting index, calendar, agenda/packet formats, stable identifiers, participation links, and how revisions, cancellations, recordings, and minutes appear. Inspect relevant source access rules and choose a respectful fetch cadence.

**Done when:** the named agencies have defined board/committee coverage; representative documents and tricky examples are identified; the accepted editorial policy and the maintainer’s operator workflow are implemented in the plan; and reuse versus replacement is documented. Source feasibility can change agency selection.

## 1. Ship a manual end-to-end prototype

Create one upcoming-meeting page from verified source material. Include a date-grouped meeting list, full agenda order and item numbers, progressive detail, citations, topic tags, and participation links. Provide an explicit missing-information state. Use the prototype with 2–3 intended users before building general ingestion.

**Done when:** a tester can find an item, verify it, and locate official participation instructions on mobile and desktop; links and keyboard navigation work. Record feedback in research notes.

## 2. Build one reliable ingestion adapter

Fetch the first agency's meeting index and documents. Store immutable source-version metadata, extracted text, and content hashes. Use official PDF links and temporary downloads only; preserve extracted text and provenance after cleanup. See the accepted [retention policy](../architecture/document-retention.md). Extract text with page boundaries; retain extraction warnings and use OCR when required. Identify agenda items without losing consent-calendar grouping. Persist structured metadata and source references. Provide a manual override when automation cannot parse a meeting.

**Done when:** replaying a fixture produces no duplicate meetings or documents; a revised agenda creates a new version; a failed fetch preserves previously published content and surfaces an operator warning; scanned or malformed files become review tasks rather than silently empty agendas.

## 3. Add summaries and editorial publication

Generate structured draft summaries from extracted material. Require page-level citations for substantive claims. Treat document text as input data, never executable instructions. Validate the output shape, flag missing evidence, and automatically publish validated output. Let the operator correct or suppress published content and resolve failures. Store model/prompt version and the exact source version used.

There are no attributed commentary notes; one automated summarization system speaks in a single editorial voice ([decision 0010](../decisions/0010-single-editorial-voice.md)). Campaigns and polling remain deferred.

Separate source status from editorial status: `discovered → fetched → extracted` and `draft → validated → published` (or `validation_failed`). Failures belong to processing runs; they must not erase a prior publication. A source change invalidates the affected summary and displays a revision warning until regenerated and validated.

**Done when:** summaries publish automatically only after validation; an operator can correct a summary; a changed source cannot reuse stale validation; and a failed model call still permits publishing verified logistics with source links.

## 4. Generalize to the other two launch agencies

Use an adapter contract for discovery and source retrieval; keep extraction, summary generation, validation, and presentation shared. Add agency and topic filters. Make curated topics editable by the operator. Schedule jobs with bounded retries and expose last successful checks, failures, and validation failures.

**Done when:** all three agencies work through the same core workflow; time zones and daylight-saving transitions are handled; canceled or rescheduled meetings are clearly marked; a failed agency does not prevent the others from updating. Verify behavior against actual representative source fixtures.

## 5. Build the timeline and topic return experience

Use a shared, extensible topic catalog beginning with Biking, Transit, Housing, and Environmental Protection. Every catalog topic has a bookmarkable view of the central meeting timeline. Emphasize upcoming meetings below a Today marker, with earlier dates available above it in an expandable section. Let one item belong to multiple topics and provide an agency filter. Link every result to the meeting detail and primary material.

On a meeting page, show the topics covered and item counts, visible tags on collapsed agenda rows, and multi-select filters that match any selected topic. Preserve item order and numbering, count a multi-topic item once, support reset and bookmarked filter state, and keep item deep links usable. See [decision 0008](../decisions/0008-shared-topics-and-meeting-filters.md).

Automatically classify agenda items into the curated taxonomy with evidence references, keeping classification separate from summary generation so a summary failure need not hide a relevant item. Measure missed and irrelevant matches with a small labeled fixture set. Give the operator a tag override.

Make repeat visits useful without accounts: show publication/update dates and clear revised, rescheduled, and canceled labels. The maintainer's feedback removes routine "new" badges. Display the last successful source check and empty versus failed-source states separately. Mobile-friendly topic URLs should be bookmarkable and shareable.

**Done when:** users can locate relevant items across all three agencies, distinguish changes on a return visit, and reach authoritative material. Test multi-topic matches, irrelevant items, expired meetings, and missing summaries. No email delivery or resident subscriptions are required for MVP.

## 6. Run and evaluate the beta

Deploy a private or limited beta, check accessibility and small-screen layouts, verify the entire publication flow, and add recording/minutes links through the admin workflow. Minutes may be approved later; link them to the meeting they describe instead of assuming the containing agenda is their meeting.

Document how to retry a job, correct a published item, restore stored data, and report a problem. Use basic aggregate analytics and opt-in interviews rather than requiring resident accounts.

**Done when:** product-brief launch gates are met, operator workload is recorded, restore behavior is checked, and the maintainer can maintain the supported sources within the sub-$20/month ceiling. Decide public launch versus another beta cycle from that evidence.

## Proposed system boundaries

Use one web application, one scheduled background worker, a relational database, and document storage. These are logical components, not necessarily separate services. See the [architecture overview](../architecture/overview.md) for the data flow and local-to-production options. Selected services: Vercel, Prisma Postgres, Python on Cloud Run Jobs, and Cloud Scheduler. Astro/TypeScript is the proposed frontend; Claude is the AI provider (decision 0011); the model for each task is chosen by evaluation. Avoid microservices and a vector database until a demonstrated retrieval requirement warrants them.

| Entity | Essential data |
| --- | --- |
| Agency / source | Name, official URLs, adapter, check cadence, last successful check |
| Meeting | Agency, source ID, title, start time/time zone, status, location, access and comment links |
| Document version | Meeting, source URL, fetched time, hash, storage reference, extracted pages, warnings |
| Agenda item | Meeting, source item number/order, title, section, document/page references |
| Summary version | Item or meeting, source version references, generated text, edited text, validation status, optional correction author, prompt/model version |
| Topic / assignment | Curated topic, item, automated or operator-overridden assignment |
| Processing run | Source, stage, attempts, timestamps, errors |
| Publication / correction | Published version, time, correction reason, operator |

Use agency-native identifiers when stable. Otherwise define a documented matching rule with operator conflict resolution; a title or date alone is not a reliable identity. Store event time with its named time zone and normalize for sorting. Keep source text exportable as page-separated plain text for future agent queries; add full-text search only when user needs justify it.

## Verification priorities

Test identity and duplicate prevention, changed-source invalidation, topic matching and topic-page ordering, failed-job recovery, citation mapping, and publication authorization. Exercise time changes and cancellations. Use manual visual and accessibility checks for the prototype; do not build a large test suite around throwaway layouts. Production admin actions require authentication and authorization even though residents do not need accounts.
