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

[0004: Source-first storage](0004-source-first-storage.md) accepts minimal PDF retention and official source links.

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
