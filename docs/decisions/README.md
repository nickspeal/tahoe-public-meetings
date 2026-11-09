# Decision log

[0001: Topic-alert MVP and operating constraints](0001-mvp-scope.md) records confirmed maintainer direction. [0002: Web-first MVP](0002-web-first-mvp.md) defers alerts and confirms four launch topics. Current stack selection is recorded in decision 0005.

Use `NNNN-short-title.md` for choices with lasting consequences: source scope, editorial review, stack, hosting, or notifications. Keep replaced records and link the superseding decision.

## Template

```markdown
# NNNN: Title
Status: proposed | accepted | superseded
Date: YYYY-MM-DD
Related ideas:

## Context
What problem or constraint requires a decision?

## Decision
What did we choose, and who confirmed it?

## Alternatives and consequences
What tradeoffs did we accept?

## Revisit when
What new evidence would change the decision?
```

[0003: Editorial stance](0003-editorial-stance.md) confirms mostly neutral coverage with limited attributed commentary (superseded by 0010).

[0004: Source-first storage](0004-source-first-storage.md) accepts minimal PDF retention and official source links; updated 2026-10-29 for oversized packets and 2026-11-03 to keep the text of every document discovery lists for a meeting (safety cap 10).

[0005: Selected stack and Vercel identity](0005-selected-stack.md) accepts Vercel, Prisma Postgres, Cloud Run Jobs, and Cloud Scheduler.

[0006: Local proof-of-concept defaults](0006-local-proof-of-concept.md) records the local Astro/PostgreSQL implementation and extractive summary baseline; cloud services and AI provider selection remain unchanged.

[0007: Meeting timeline and filters](0007-meeting-timeline-and-filters.md) records the maintainer's feedback: a shared past/future timeline, topic and agency filters, simpler design, and broader verified data.

[0008: Shared topics and meeting filters](0008-shared-topics-and-meeting-filters.md) expands the topic catalog, adds filtering within a meeting, and records the next local calendar-download improvement.

[0009: Configurable coverage and one-off meetings](0009-configurable-coverage-and-one-off-meetings.md) replaces the topic selection, seeds the agency/body watchlist, and adds a local one-off notice workflow.

[0010: Single editorial voice](0010-single-editorial-voice.md) supersedes 0003: one automated summarization system with one editorial voice shaped by the maintainer, no attributed perspective notes, and only the latest summary shown publicly.

[0011: Claude for automated analysis](0011-claude-for-automated-analysis.md) makes Claude the default provider for classification and summaries, with Haiku 5.5 the first classification candidate, a cheaper option only for a strong cost or convenience reason, and provider-neutral stored results.

[0012: Forecast meetings from schedule patterns](0012-forecast-meetings-from-schedule-patterns.md) adds configured and learned agency schedules, export-time expected meetings that are hidden by default and replaced by real records, schema version 2, and greyed-out canceled meetings.

[0013: Estimated meeting durations](0013-estimated-meeting-durations.md) exports a labelled, long-biased duration for every meeting so calendar events always have an end time.

[0014: Reviewed sources and public comment periods](0014-reviewed-sources-and-public-comment-periods.md) records the maintainer's source decisions, two added parks bodies, separate comment windows, and the reusable acquisition path.

[0015: Deterministic agenda layouts](0015-deterministic-agenda-layouts.md) adds validated per-agency agenda layouts (no model cost), a `reparse` command for stored text, and empty-state wording that says why a meeting lists no items.

[0016: Public open-source repository](0016-public-open-source-repository.md) publishes the code on GitHub under the MIT License, keeps maintainer notes outside Git, and lifts the local-only Git constraint.

[0017: Reader feedback](0017-reader-feedback.md) adds an anonymous feedback dialog and a "Suggest a topic…" Topic dropdown entry, stores submissions in Postgres, alerts the operator through ntfy, and treats feedback as untrusted input for agents.

[0018: Scheduled publication pipeline](0018-scheduled-publication.md) publishes from a 6-hourly Cloud Run job through a Postgres export table and a Vercel deploy hook, authenticates Claude with workload identity federation, and caps Google and Claude spend at $5/month each.

[0019: Coverage from January 1, 2026](0019-coverage-from-2026.md) sets the coverage start for every agency, keeps stored meetings forever, and fills history with a one-time backfill job.

[0020: Stay within the Prisma Postgres free operations quota](0020-database-operation-budget.md) keeps the free plan, treats database statements as a budget (bulk reads, no unchanged rewrites, tests that guard query counts), and records the October 2026 near-exhaustion.

[0022: Timeline default view](0022-timeline-default-view.md) opens the timeline on upcoming meetings with posted agendas, folds expected meetings into one reveal for meetings without an agenda, opens earlier meetings ten at a time, and removes the Dates filter.

[0021: Read participation details from agendas with Claude](0021-participation-details-from-agendas.md) (proposed default) fills each meeting's location, remote access, comment instructions, and deadline from its agenda pages with Claude, keeps only fields found verbatim in the source, and adds standing agency details as a fallback.

[0023: Agenda sections on meeting pages](0023-agenda-sections.md) (proposed default) groups meeting-page items into collapsible agenda sections. It keeps unsectioned items standalone, ends a section at a time-allocation line, and places addendum items in their named sections.

[0024: Verbatim item summaries from requested actions and staff reports](0024-verbatim-item-summaries.md) (accepted maintainer instructions; proposed defaults for the implementation) leads each summary with the agenda's printed requested action, adds sentences trimmed verbatim from the item's own staff report and checked against the stored page text before publishing, links each item's staff report, and lists SLT items' attachments.

[0025: Minutes and recording links](0025-minutes-and-recording-links.md) (accepted) links each meeting's minutes and recording from agency listings, YouTube and Laserfiche feeds, and draft minutes in the next meeting's agenda. Approved minutes outrank drafts, YouTube outranks other hosts, and TTD's on-request Zoom clips are left out.

[0026: Staff report links and summaries for every agency that publishes them](0026-staff-reports-for-every-agency.md) (accepted) adds one mapping rule module per publishing format (21 more bodies: TRPA, Douglas, El Dorado, Washoe, the SLT commissions, Tahoe City PUD, South Tahoe PUD, North Tahoe PUD, PCTPA, the airport district, Incline Village GID, Fish and Game, State Parks, the Nevada water board), links TRPA items to their own report files and attachments, captures each TRPA meeting's short agenda and full packet, and records which agencies cannot be supported and why.

[0027: Agency definitions live in the registry](0027-agency-definitions-in-configuration.md) (accepted) makes `packages/domain/agencies.json` the one place an agency is defined, with discovery types, agenda layouts, and staff report rules registered by format name; adds `extends` for sibling bodies, a canonical key order, and a generated source coverage page; moves per-agency meeting durations into the registry; and keeps topic hints out of the classifier version.

[0028: Previews from notices and agenda planning calendars](0028-previews-from-notices-and-planning-calendars.md) (accepted) reads tracked public notices with Claude into one-off meetings and planned agenda subjects, parses the South Lake Tahoe and TTD agenda planning calendars deterministically by format, shows planned subjects on upcoming meetings by default, and adds an Other agency whose meetings name their host per record.

[0029: Usage analytics with cookieless GoatCounter](0029-usage-analytics.md) (accepted) counts page views and named events (outbound clicks to official documents, calendar downloads, feedback, timeline and agenda filters) with hosted GoatCounter: no cookies, no stored IP addresses, no persistent browser identifier, no public dashboard, and the script is emitted only when `PUBLIC_GOATCOUNTER_URL` is set in Vercel's Production environment.

[0030: Roll out the ingestion image from GitHub Actions](0030-automatic-image-rollout.md) (accepted) builds and deploys the ingestion image to both Cloud Run jobs for every push to `main` that changes the image's inputs, after the worker tests pass, using workload identity federation scoped to `main` of this repository and a service account that can change only the two jobs' image.

[0031: Lake Calendar visual design](0031-lake-calendar-visual-design.md) (accepted) replaces the sage green look with a blue and white palette in the existing layout and system font, turns date boxes into calendar pages, reserves red for Canceled and amber for notes that change what the reader should do, adopts a calendar-with-wave brand mark, and removes topic names from the link preview image.

[0032: Publish agendas with irregular numbering](0032-publish-irregular-agendas.md) (accepted; gap limit and wording are proposed defaults) amends 0015: a skipped number becomes a "Not Found In Agenda" placeholder with no quote, numbering that cannot be reconciled keeps the items that can be read, differing agenda copies publish the agenda over the packet, and each case leaves a "review required" warning on the meeting.

[0033: Production meetings come only from the live pipeline](0033-production-data-only-from-live-pipeline.md) (accepted; cleanup rules and warning ownership are proposed defaults) removes the hand-built seed bundle and the verified-candidate lists from the scheduled run and the image, treats agenda availability as derived state instead of a stored warning, makes each discovery capture own a meeting's warning list, and adds the one-time `clean-hand-built` cleanup.

[0034: Item fingerprints without document versions, and one choice of cited copy](0034-fingerprints-without-document-versions.md) (accepted; the HTML rule in `reparse` is a proposed default) amends 0024: an item's fingerprint hashes its title, section, and cited pages and quotes but not the document version, so a re-posted file or the same text in another copy is not a revision; capture, reparse, and backfill choose the cited copy the same way and keep the copy stored items cite; and a one-time `init_db` migration moves every stored fingerprint and its derived rows without marking anything revised.

[0035: Short titles for long agenda items](0035-short-titles-for-long-agenda-items.md) (accepted 2026-11-03; prompt, validator rules, and model choice are proposed defaults) has Claude write a 6-to-14-word headline from the agenda's own words for items with more than 200 characters of agenda text, within $0.05 per run, publishes it only when every word and number is in the text and governing negations are kept, shows it with a "Shortened" mark and the whole text in the expanded item, and lets the operator suppress or replace a headline with `short-title`.

[0036: YouTube Data API fallback for failing feeds](0036-youtube-data-api-fallback.md) (accepted) reads a channel's newest 50 uploads from the YouTube Data API, one free quota unit each, when its public feed fails during a scheduled run, so recording links keep arriving while YouTube's feed endpoint returns 404.

[0037: Search-friendly meeting URLs, body pages, sitemap, and structured data](0037-search-friendly-meeting-urls-and-body-pages.md) (accepted; qualifier list, hub wording, and JSON-LD fields are proposed defaults) gives each followed agency a permanent page slug and searchable name, serves body pages at `/meetings/<slug>/` with meetings at `/meetings/<slug>/<date>[-<qualifier>]/`, keeps the timeline only on `/`, and adds a sitemap and schema.org Event data.

[0038: The agenda's start time wins, and CivicClerk times are local](0038-agenda-start-times-win.md) (accepted 2026-11-09) publishes the start time printed on the agenda's first page even when a listing gave another (a revised TCPUD agenda moved a workshop from 4:00 to 3:45 p.m.), reads CivicClerk API times as local wall-clock times despite their "Z" (reversing the October 2026 choice; 4 of 4 IVGID events matched their agendas), and stops storing and exporting the archive-playback and CivicClerk start-time caveats.

[0039: Meeting warnings name a known, specific issue](0039-warnings-name-known-issues.md) (accepted; the "not an agenda" title rule and the retired-warning list are proposed defaults) amends 0028 and 0032: no blanket caveats are written (discovery, planned meetings, the export coverage note), a WordPress post titled as canceled is canceled, "await reprocessing" is added only when a published item lost its summary and `reparse` always rewrites its own stale notes, canceled meetings and documents that are not agendas record no parse failure, indentation alone is not noted as irregular numbering, and the one-time `clean-retired-warnings` removes what no code writes any more.

[0040: Private and non-profit groups' meetings, and Word agendas](0040-private-groups-and-word-agendas.md) (accepted 2026-11-09; the groups added and the Word reader's details are proposed defaults) includes private and non-profit groups' meetings when the group publishes its own dates on a source the worker can read without getting around a block, and reads Word (.docx) agendas by file type with their automatic list numbering written into the text.

[0041: Operator review notes are kept apart from resident warnings](0041-review-notes-apart-from-warnings.md) (accepted; exporting the notes as `reviewNotes` and the reworded "refresh" warning are proposed defaults) stores parser and capture outcomes in `meetings.review_notes` and exports them as `reviewNotes`, so `warnings` holds only text residents read as written; `init_db` moves the notes stored among warnings once, and the website splits exports written before the change by the patterns it used to hide them.
