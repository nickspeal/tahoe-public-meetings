# Local implementation status

Updated: 2026-10-20. Work is committed directly to `main` and pushed to the public [GitHub repository](https://github.com/nickspeal/tahoe-public-meetings). Production is live at https://tahoepublicmeetings.org (see [Production](#production)). Read this with the current [implementation handoff](implementation-handoff.md) and [README](../../README.md) when resuming.

## Working locally

From the repository root, run `python3 scripts/project.py setup` once, then `python3 scripts/project.py dev`. Open `http://127.0.0.1:4321/`. The local PostgreSQL cluster uses loopback port 55432 and ignored `data/postgres/`; `python3 scripts/project.py stop` stops it. Useful checks are `python3 scripts/project.py test`, `refresh`, `build`, `status`, and `worker --help`. The [operations guide](../operations.md) covers backups, restore checks, retrying ingestion, and authored corrections.

## Current implementation

The Astro site has 117 pages after the October 20 agency coverage run built around a meeting timeline. Upcoming dates appear below a Today marker; earlier meetings are expandable above it. Topic and agency filters expose related agenda items, and every catalog topic has a bookmarkable route. The design follows maintainer feedback: system sans-serif typography, concise copy, footer links for About and Coverage, and no routine “new” badges. The versioned public JSON boundary is `packages/domain/public.ts`.

The shared catalog in `packages/domain/topics.json` defines six topics (Biking, Transit, Housing, Short Term Rentals, Environment, and Hot Topics) for the worker, operator overrides, exports, and UI. Each meeting shows its covered topics and counts, visible tags on collapsed agenda items, and filters matching any selected topic. Filters preserve agenda order, can be bookmarked, and reset with **All items**. Linked agenda items stay visible when they fall outside the selection. The original four topic IDs remain stable. STRs imply Housing; Biking includes pedestrian and road repair items. The [configuration guide](../../config/README.md) documents editable topic rules, the agency/body registry (41 imported rows plus Truckee), and local one-off notice imports. Imported agency metadata is unverified; reviewed sources now have configurable platform adapters in addition to the original named paths. Meeting notice tags allow workshops to match topic filters before an agenda exists.

Meeting pages also offer individual `.ics` calendar downloads for verified start times. Exports use UTC timestamps, stable event IDs, explicit cancellation status, and official links. Every event has an end time from the meeting's `duration` (published or observed when known, otherwise a long-biased estimate labelled in the event description; decision 0013), and they are snapshots rather than subscriptions. Date-only meetings explain why no calendar download is available. This advances the bounded export portion of IDEA-003; a visual calendar and subscription feeds remain deferred.

Expected meetings and greyed-out cancellations follow [decision 0012](../decisions/0012-forecast-meetings-from-schedule-patterns.md). Agency entries can carry a validated `schedule`. TTD is configured as the first Wednesday at 3 PM. `worker learn-schedules` learned nine more from stored records, requiring at least 3 matches and a 60% match rate; 23 bodies have insufficient data ([findings](../research/meeting-schedule-patterns-2026-10-20.md)). The export, at schema version 2, derives 90 days of `kind: "forecast"` entries without storing them. Any real record within three days, including a cancellation, replaces a forecast. The timeline hides expected meetings unless **Show expected meetings** (`?expected=1`) is selected; expected cards have a dashed outline and link to the agency page, with no meeting page or calendar file. The October 20 export after source review has 29 expected meetings.

The checked-in baseline source bundle has 19 genuine meetings from May through November 2026: six TTD, six South Lake Tahoe, and seven Truckee events. It includes 12 agendas, 55 stored source pages, and 243 ordered agenda entries. Four upcoming listings and one canceled event are included. The November 4 TTD listing retains its confirmed date without inventing a start time. Truckee's September 8 and 22 pages link HTML agendas that have not yet been captured as item text; the site points readers to those official pages.

The source review now brings the local database to 123 confirmed meetings across 35 agencies/bodies, 325 items, and 650 validated citation references. The registry has 44 entries, 38 followed, after the maintainer's six exclusions and two added parks bodies. [Source review resolutions](../operations/source-review.md) document each decision and the remaining Placer 403 issue. Structured HTML capture publishes supported Granicus/Municode headings with source-section citations; other PDF layouts still await parsing. Comment periods are separate participation records and have a dedicated page/importer; no current verified period is published. The original [42-entry audit](../research/agency-meeting-coverage-audit-2026-10-20.md) remains a historical acquisition snapshot.

Three official-source adapters discover TTD WordPress meeting posts, South Lake Tahoe Granicus meetings, and Truckee CivicPlus calendar events. Agency-specific parsers are conservative: unknown or incomplete documents remain review tasks. See the [source inventory](../research/local-poc-sources.md) for per-meeting coverage and provenance.

Published summaries are automatically validated, cited source excerpts. They are not generated analysis. Claude is the selected provider ([decision 0011](../decisions/0011-claude-for-automated-analysis.md)); `worker evaluate-topics` compares Claude Haiku with the rules and has run only as a keyless dry run so far ([evaluation](../research/topic-classification-evaluation-2026-10-20.md)). Each agenda item links to its official document at the cited page. Original PDFs remain temporary inputs, and the existing TTD archive is unchanged.

Topic rules use each item's title and limited agency context, with recorded rule/citation evidence. The funding phrase “Cycle B” no longer triggers Biking; ridership and transit services receive Transit tags. Multi-topic assignments are supported, while routine procedural items can remain untagged. The local database was reclassified with configuration-sensitive version 4 rules. Automatic assignments now total Biking 5, Transit 16, Housing 5, Short Term Rentals 1, Environment 10, and Hot Topics 3; items can have multiple tags. `worker reclassify --dry-run` previews changes, and `worker reclassify` applies them while preserving human overrides and source/summary history. See the [catalog guide](../research/topic-catalog.md) for topic boundaries and additions.

The local backup and restore-check workflow was exercised against a dedicated disposable database. It matched all 12 application tables, 13 extracted source pages, and test correction and topic-override records. The application's database was not used or changed by that test.

## Remaining checks and limits

The final integrated check passed: 144 backend tests, 27 frontend/calendar tests, 650 citation references, clean Astro diagnostics, and 133 built pages with 2,543 internal links. Earlier local HTTP route checks returned 200, including 18 calendar downloads checked for calendar content and MIME type; this configuration change was checked through static build and interaction tests. Earlier topic packaging checks loaded the catalog from an installed wheel built through a source distribution; the build hook now includes both catalogs; the dependency lock check passes. Repeat with `python3 scripts/project.py test` and `python3 scripts/check_site.py --url http://127.0.0.1:4321`. Local database snapshots are saved under ignored `data/backups/`. Browser visual QA and manual calendar-application imports have not been performed.

Coverage is still selected-page source coverage, not a comprehensive meeting feed. The code is public under the MIT License ([decision 0016](../decisions/0016-public-open-source-repository.md)).

## Recent implementation commits

Recent local commits add shared topics, meeting-level filters and visible tags, calendar downloads, and interaction/regression tests. Earlier commits implement the timeline redesign, expanded real source data, explainable topic reclassification, PostgreSQL ingestion, local setup, operator corrections, restore verification, and agency-specific parsing. Check `git log --oneline` for the current sequence and inspect `git status` before editing or committing.

Next proposed local work: keyword search across captured agenda items and source excerpts, then extending item parsing for additional captured PDF layouts. Truckee’s September 22 HTML agenda is now captured and parsed. Keep alerts and accounts deferred.

## Production

Live since 2026-10-20 at https://tahoepublicmeetings.org (`www` redirects to the apex). [Decision 0018](../decisions/0018-scheduled-publication.md) and the [deployment runbook](../operations/deployment.md) describe the setup; no data was uploaded by hand.

- A Cloud Run job (`tahoe-ingest`, us-west1) runs `tahoe-ingest run-scheduled` every 6 hours (Cloud Scheduler, America/Los_Angeles) against Prisma Postgres. It stores each validated export in `public_exports` and calls a Vercel deploy hook; the Vercel build (root `apps/web`, Git-connected) reads the newest export and refuses to publish without one.
- Claude topic classification authenticates with workload identity federation (no API key).
- Spend limits: $5/month Google budget with a guard that pauses the scheduler, a $5/month Claude workspace limit, and the job's $5 monthly AI budget.
- First run (2026-10-20, from an empty database): 25 minutes, status `degraded` only because of source download failures. 134 confirmed meetings across 35 bodies, 29 expected meetings, 1,738 agenda items, all classified by Claude Haiku 5.5 for $0.11. Peak container memory 213 MiB. The deploy hook built 146 pages.
- Known source gaps in production: South Shore TMA agenda PDFs and the Placer Tahoe Basin Design Review index return 403 from the job; Lahontan and Placer agenda items captured locally by one-off scripts are not reproduced by the pipeline.
- Verified on the live site: main routes, 404 page, `robots.txt`, `www` and HTTP redirects, meeting URLs containing `:` (literal and `%3A`), and `.ics` downloads served as `text/calendar`.

