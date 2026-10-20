# Remaining questions

Confirmed scope and stack are in the [implementation handoff](implementation-handoff.md). Do not reopen settled choices merely because an older comparison document lists alternatives.

## Before cloud provisioning/public launch

- Identify the intended Prisma workspace and Google project/billing account. The Vercel scope is designated.
- Select a domain if desired; a provider subdomain is sufficient for initial testing.
- Confirm which boards/committees within South Lake Tahoe, Truckee, and TTD are covered. Main council/board meetings are the proposed first slice.
- Confirm source freshness expectations; six-hour checks are a provisional default.
- The project is MIT licensed; maintainer notes live outside the repository.

## Implementation choices that can proceed with documented defaults

- Astro/TypeScript is the proposed frontend; Python ingestion and Prisma Postgres are selected.
- Claude is the selected AI provider ([decision 0011](../decisions/0011-claude-for-automated-analysis.md)). Still open: whether Haiku 5.5 beats the rules on the maintainer's gold labels, and which model handles summaries.
- Define topic matching examples, packet/attachment coverage, revision handling, and temporary-file cleanup bounds.
- Decide whether extracted text fits in Postgres or needs object storage; no permanent PDF archive initially.
- Local CLI editing is the proposed starting point; a browser admin UI has not been requested.

## Later

Alerts and their cadence, historical backfill depth, languages, personalization, advocacy campaigns, public submissions, moderation, and PDF backups if source links prove unreliable.
