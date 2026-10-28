# Local ingestion worker

This worker uses PostgreSQL and stores extracted page text, immutable PDF hashes,
source URLs, item evidence, topic tags, and editorial changes. Source PDFs are
temporary downloads. `refresh-document` requires `pdfinfo` and `pdftotext`.

From the repository root:

```sh
UV_CACHE_DIR=/private/tmp/tahoe-uv-cache uv sync --project workers/ingestion --extra test
export DATABASE_URL='postgresql://tahoe:tahoe-local@127.0.0.1:55432/tahoe'
workers/ingestion/.venv/bin/tahoe-ingest init-db
workers/ingestion/.venv/bin/tahoe-ingest ingest-fixtures
workers/ingestion/.venv/bin/tahoe-ingest export --out apps/web/src/data/public.json
workers/ingestion/.venv/bin/tahoe-ingest status
```

The checked-in source bundle contains selected meetings and extracted source
pages, not complete agency archives. The public export describes the covered
records and links to the official sources without claiming exhaustive coverage.
Fixture replay does not claim a new live source check. An explicit
`source_checked_at` from an actual source-index check is retained; archive
records without one do not gain a fabricated check time. Meetings with a known
date but no published start time remain date-only in the export.
`ingest-manifest FILE.json` accepts the same bundle shape for reviewed live
captures; it does not infer meeting dates or policy outcomes from PDFs.
For one already-known meeting, `refresh-document MEETING_ID DOC_ID HTTPS_PDF_URL`
downloads and extracts with bounded bytes/pages, then records the hash and text.
That marks affected summaries pending until items are reprocessed. An OCR or
item parsing failure remains visible as an operator task.

`discover ttd|slt|truckee --limit 5 --out candidates.json` inspects the
official indexes with bounded result counts. Truckee also accepts `--year`
and `--month`. The initial filters cover SLT City Council and Truckee Town
Council; TTD uses its meeting posts. `capture candidates.json` downloads up
to five meetings and two PDFs each into temporary scratch space and stores
page text and hashes. It recognizes the tested TTD Board, SLT City Council,
and Truckee Town Council agenda layouts, plus one declarative layout per other
covered agency (`tahoe_ingest/agenda_layouts.py`, decision 0015). It requires
sequential numbering and an ending marker, and publishes item excerpts only
with exact page quotes.
For an unknown, incomplete, or conflicting agenda layout, it keeps the PDF
text and meeting record, leaves prior items in place, invalidates affected
summaries if the source changed, and flags item review. These parsers do not
interpret votes, adoption, or supporting attachments. Scanned PDFs without
extractable text require OCR/manual review. The parsing pass inspects only
the first 6 TTD, 12 SLT, or 8 Truckee pages, and the PDF downloader caps each
file at 80 MiB and 800 pages.

### Agency agenda layouts

Each layout in `agenda_layouts.py` names an agency's cover text, outline
markers per level, ending marker, and page furniture (running headers, page
or time columns, packet page numbers) for the shared engine in `outline.py`.
NTCD's centered-number table and South Shore TMA's bullets use
`agenda_custom.py`. To add or fix an agency:

1. Capture one of its agendas, add or edit the layout, and read the extracted
   items against the PDF, not just the count.
2. Add the agenda's pages and reviewed items to
   `tests/fixtures/agenda-layouts/AGENCY--MEETING.json` (`"expected": null`
   for a document that must be rejected).
3. Run `tahoe-ingest reparse --agency AGENCY --dry-run`, then without
   `--dry-run`, to re-parse stored text with no source fetch. By default it
   only touches meetings with no items or with items waiting to be
   reprocessed. `--force` reparses every stored meeting for that agency.

Legistar agencies expose agenda PDFs through the public web API
(`https://webapi.legistar.com/v1/CLIENT/events`); the meeting-detail pages
alone sometimes return cache errors.

Item summaries follow decision 0024. The export leads with the requested
action an agenda prints under the item (`requested_actions.py`, exact page
text, no model), else the agenda excerpt. `map-staff-reports` links items to
their own staff reports with one rule module per agency or platform
(`tahoe_ingest/staff_report_rules/`, 25 agencies; decision 0026) and lists SLT
and TRPA attachments; `summarize-items` adds one or two sentences that Claude selects
and trims from the report, published only when every sentence is the cited
passage with words deleted (`item_summaries.validate`). Use `--dry-run` for
pending items and cost; `--meeting ID` to try one meeting. Claude topic classification (decision 0011) runs in `classify-topics`, which the scheduled pipeline calls when Claude credentials are available: `ANTHROPIC_API_KEY` locally, or workload identity federation on Cloud Run (see the [deployment runbook](../../docs/operations/deployment.md)).

`evaluate-topics` compares the title rules with Claude Haiku 5.5 on stored
agenda items, given either the title alone or the title plus the item's own
agenda-page excerpt. It only reads the database. Without `ANTHROPIC_API_KEY`
(or with `--dry-run`) it makes no network calls. It prints the sample, one
meeting's prompt, the rule results, and a monthly cost projection, and writes
`data/topic-eval/disagreements.{json,md}` for labeling. The live run needs
the optional SDK: `uv sync --project workers/ingestion --extra test --extra ai`.
Edit the prompt in `tahoe_ingest/prompts/topic_classification.md`. See
`docs/research/topic-classification-evaluation-2026-10-20.md`.

The ordered topic catalog lives at `packages/domain/topics.json`; the worker,
public export, and export validator read that one catalog. Build the worker
container from the repository root so the catalog is available:
`docker build -f workers/ingestion/Dockerfile .`. The build hook bundles a
catalog copy in the installed wheel; there is no runtime dependency on the
checkout path. A newly added catalog topic may initially have no automatic
rule and can still receive operator overrides.

Generated topic tags use versioned, explainable title rules. They classify
each agenda item's own title, because page excerpts can include neighboring
items and attachments. The stored tag evidence records the matched title span,
rule, and source citation. A funding “Cycle B” never counts as cycling; explicit
cycling and bike or street safety actions do. TTD transit terms include service,
ridership, fleet, and clearly named operating budgets, but generic financial
statements do not receive a transit tag by agency alone. An item can have
several topics. Road names in property addresses and park names in geographic
descriptions do not alone trigger those topics. This is a conservative filter
and can miss actions whose topic appears only in supporting materials. The maintainer's
tag overrides remain separate.
After changing rules, run `tahoe-ingest reclassify --dry-run` to preview counts,
then `tahoe-ingest reclassify` to update generated tags in place. That command
does not rewrite source PDFs, extracted text, or summary versions.

Operator edits: `correct ITEM_ID --text ... --reason ... --author ...`,
`tag ITEM_ID biking --author ...`, `tag ITEM_ID biking --exclude --author ...`,
and `suppress ITEM_ID`.
These local commands have no remote write endpoint. They are internal steering;
the public export carries only the latest summary text.

## Configured coverage and one-off notices

See the [configuration guide](../../config/README.md). Shared topic rules and agencies live in `packages/domain/`; `ingest-one-offs --dry-run` validates `config/one-off-meetings.json`, and `ingest-one-offs` upserts notices without fabricating agendas or start times. `agencies` lists followed entries and adapter readiness. Run `init-db` after registry edits, and `reclassify` after topic edits. Entries without a `discoveryAdapter` are snapshot-only; see [source coverage](../../docs/operations/source-coverage.md).

`learn-schedules [--agency ID] [--dry-run | --write] [--registry PATH] [--json]` infers regular meeting patterns from stored meetings. It needs 3+ matches and a 60%+ match rate, and `--write` never replaces a `configured` schedule. `export --forecast-days N` (default 90, or `TAHOE_FORECAST_DAYS`; 0 disables) adds expected meetings derived from those schedules. Forecasts are never written to the database. See [Meeting schedules and expected meetings](../../config/README.md#meeting-schedules-and-expected-meetings).

## Reviewed source acquisition and public comment windows

[Decision 0014](../../docs/decisions/0014-reviewed-sources-and-public-comment-periods.md) and the [configuration guide](../../config/README.md) describe the current sources. Configured discovery supports reviewed Granicus/Legistar/CivicClerk/Municode/category indexes and rolling links. PDF, HTML, and plain text are captured through a bounded fetcher; curl/system CA certificates provide a verified-TLS fallback. HTML uses source sections rather than invented PDF pages. Partial fetch failures retain the notice and successful documents. `quarantine-meeting ID --reason TEXT` hides a misattributed record without deleting its provenance.

`ingest-comment-periods [FILE]` verifies official notice text and imports a separate comment window; default `config/public-comment-periods.json`. It never submits a comment or manufactures a multi-day meeting. No active period was verified in this review. Placer's persistent 403 errors remain [SOURCE-001](../../docs/issues/SOURCE-001-placer-downloads.md).

## Scheduled pipeline (Cloud Run Job)

`tahoe-ingest run-scheduled` builds an empty database from scratch and keeps it current. Production data comes only from this command: tracked config and fixtures in the image plus live official sources, never a local dump. Steps, in order:

1. `init-db` (idempotent; includes `public_exports`).
2. `seed-fixtures`: `tests/fixtures/meetings.json`, only meetings not yet stored, so a later live capture is never overwritten.
3. `one-offs` and 4. `comment-periods` from `config/` (comment notices are fetched and verified).
5. `verified-sources`: `config/verified-agency-meetings-*.json` candidates not yet stored; missing documents are retried at most once a day. Candidates in `config/quarantined-meeting-candidates.json` are skipped.
6. `discover`: every followed agency with a discovery adapter. History before the scheduled window comes from the separate one-time backfill; this step only keeps recent and upcoming meetings current. The policy constants live at the top of `tahoe_ingest/scheduled.py`:
   - Each index is read up to the adapters' maximum (`--discovery-limit 20`; TTD's undated posts, which each cost a page fetch, stay at 6). The window is applied after reading, so far-future postings never crowd out recent past meetings.
   - Window: 60 days back (`--past-days`) to 120 days ahead (`--future-days`). The look-back lets minutes and revised packets linked after a monthly or bimonthly body's next meeting still be picked up. Dated meetings before the coverage floor, 2026-01-01, are never captured; undated TTD posts are exempt.
   - Upcoming meetings and meetings from the last 7 days are re-downloaded every run, which catches revised agendas and cancellations. An older meeting is settled, and skipped, once every document a capture would fetch (the first 2) is already stored at the same URL. A newly linked document makes it captured again. Its failed downloads are retried at most once a day.
   - At most 10 meetings per agency are captured per run, nearest to today first; the rest wait for the next run. At most 2 documents per meeting, 1 second between downloads.
   - The legacy Truckee calendar adapter reads the current and next month, plus the previous month during the first 14 days of a month. The followed Truckee entry uses the configured Municode feed, which already spans 90 days on each side.
   - Stored meetings outside the window are never deleted or hidden.
   - Minutes and video links on each listing row are kept with the candidate. For meetings not captured this run (settled, deferred, or outside the window) they update the stored meeting directly, without a download (decision 0025).
7. `quarantine`, 8. `reparse`, 9. `reclassify`.
10. `meeting-records`: YouTube feeds and the Truckee Laserfiche minutes folder from `config/meeting-records.json`, matched to stored meetings (skipped with `--skip-discovery`). 11. `draft-minutes`: draft minutes found through "Approval of minutes" items in later agendas. See [decision 0025](../../docs/decisions/0025-minutes-and-recording-links.md).
12. `classify-topics` (only with Claude credentials, within `--max-cost`/`--monthly-budget`), 13. `participation` (same guards).
14. `staff-reports`: up to `--staff-report-meetings` (6) meetings are scanned for item staff reports and attachment links (`map-staff-reports`; one rule per agency, decision 0026). 15. `summarize-items`: Claude staff report summaries within `--summary-max-cost` ($0.50) and the monthly budget (decision 0024).
16. `export` (mode `live`), 17. `validate` (the `scripts/check_export.py` checks, ported to `tahoe_ingest/export_check.py`, plus `--min-meetings 1`), 18. `store` in `public_exports` (latest 30 kept), 19. POST to `VERCEL_DEPLOY_HOOK_URL` if set (30-second timeout; the URL never appears in output).

A failure in steps 2–15 is recorded (`processing_runs`, agency status, the summary's `failures`) and the run continues with status `degraded`. The command exits non-zero only when export, validation, storage, or the deploy hook fails; an invalid export is never stored. stdout gets one JSON summary line (`severity`, per-step status and timings, counts, failures, `aiCostUsd`, `peakMemory`); progress lines go to stderr as JSON. A PostgreSQL advisory lock skips a run that overlaps another. `publish-export --file PATH` validates and stores an existing export file (`--no-hook` skips the deploy).

### History backfill

`tahoe-ingest backfill --since YYYY-MM-DD [--until YYYY-MM-DD] [--agency ID ...] [--dry-run]` captures every meeting dated in a range. Coverage starts on 2026-01-01, so the full-year run is `backfill --since 2026-01-01`. `--until` defaults to 120 days ahead. Each followed agency with a discovery adapter is enumerated through the date-range variant of its adapter:

| Adapter | Range function | How it reaches back |
| --- | --- | --- |
| `ttd` | `discovery.discover_ttd_range` | Pages WordPress meeting posts until they are 180 days older than `--since`. The date comes from the post title. An undated board post published in the range is kept |
| `slt` | `discovery.discover_slt_range` | Reads the Granicus publisher, a full archive |
| `truckee` (custom calendar) | `discovery.discover_truckee_range` | Reads the calendar month by month |
| `configured` | `source_discovery.discover_configured_range` | Granicus, link-index, and TRPA read the whole index page. `dated-table` reads one yearly page per year. CivicPlus with a Municode embed queries Municode by date. Legistar queries the web API by date and adds department-page rows for dates the API does not cover. CivicClerk is paged until events are older than `--since` |

Meetings already stored with documents are skipped, and quarantined candidates are ignored, so the command is resumable. A rerun downloads only what is missing, including documents that failed earlier. Capture uses the scheduled run's path: `capture.capture_candidates`, incremental commits, 2 documents per meeting, and at least 1 second between downloads (`--delay`). Each failing index or document is recorded and never stops the run. Stored meetings still take the minutes and video links their listings show. After capture, the backfill runs the scheduled post-steps from `scheduled.py`: quarantine, reparse, reclassify, meeting-records, draft-minutes, classify-topics within the same cost guards, export, validate, store, and the deploy hook. With `YOUTUBE_API_KEY` set (a free YouTube Data API key), meeting-records reads each configured channel's uploads back to `--since` instead of the 15-video feed; without it, older recordings wait for a later backfill with a key. `--no-publish` stops after validation. `--dry-run` only discovers and reports.

`tahoe-ingest meeting-records [--since YYYY-MM-DD] [--skip-feeds] [--dry-run]` runs the meeting-records and draft-minutes steps alone; the next export publishes the links. `--since` needs `YOUTUBE_API_KEY`.

The backfill takes the scheduled run's advisory lock. It waits up to `--lock-wait-minutes` (default 45) for a running scheduled run and then exits as `skipped`. A scheduled run that starts during a backfill skips itself.

The JSON summary extends the `run-scheduled` summary with:

- `window`
- `byAgency`: `found`, `alreadyStored`, `toCapture`, `captured`, `failed`, `documentFailures`, `earliestListed`, `earliestInRange`, `reachesSince`, `error`, and per-agency seconds
- `totals`, including `agenciesNotReachingSince`
- `downloads` (documents and bytes)
- `phaseSeconds`
- `database` (`pg_database_size` and row counts before and after)
- `exportBytes`

A live dry run on 2026-10-20 for `--since 2026-01-01` found 201 meetings in 19 agencies. Every source reached 2026-01-01 except four whose indexes list only recent items: `trcd` (earliest 2026-01-13), `trpa-advisory` (2026-03-11), `trpa-governing` (2026-07-22), and `truckee-north-shore-tma` (2026-10-01). Placer returned its known 403 ([SOURCE-001](../../docs/issues/SOURCE-001-placer-downloads.md)). See the [deployment runbook](../../docs/operations/deployment.md#history-backfill) for the separate `tahoe-backfill` Cloud Run job.

Environment: `DATABASE_URL` (required; hosted URLs need `?sslmode=require`), `VERCEL_DEPLOY_HOOK_URL` (optional, from Secret Manager), the `ANTHROPIC_*` federation IDs with `TAHOE_IDENTITY_TOKEN_SOURCE=gcp-metadata` (optional; `ANTHROPIC_API_KEY` also works locally), `TAHOE_FORECAST_DAYS`. `TAHOE_SOURCE_ROOT` (`/app` in the image) locates `config/` and `tests/fixtures/`; `TAHOE_SCRATCH_DIR` overrides the scratch parent (default `TMPDIR`).

Build from the repository root; the root `.dockerignore` is an allowlist, and `.gcloudignore` mirrors it for Cloud Build (keep the two in step):

```sh
docker build --platform linux/amd64 -f workers/ingestion/Dockerfile -t tahoe-ingest .
docker run --rm -e DATABASE_URL=... tahoe-ingest            # run-scheduled
```

Sizing: PDFs are read whole into memory and also written to a per-run directory under `TMPDIR`, removed after each file and at the end of the run. Cloud Run's `/tmp` is in memory, so one large file counts about twice (the 80 MiB download cap bounds it near 160 MiB plus extracted text). The 505-page SLT packet is about 38 MB. A from-scratch run in the amd64 image with `/tmp` on tmpfs (including that packet and two other ~500-page packets) peaked at about 170 MiB for the container and took about 6 minutes; a rerun took about 2 minutes. Give the job 1 GiB of memory and 1 vCPU, a task timeout of at least 30 minutes, and `--max-retries 0` or 1 (the next scheduled run retries sources anyway).
