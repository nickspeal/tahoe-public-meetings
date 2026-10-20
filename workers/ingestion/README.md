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

Generated summaries are deterministic source excerpts,
not AI analysis. Only excerpts whose exact quotes occur on their cited pages
are published. Claude is the selected provider for future automated analysis
(decision 0011), but no model call is part of ingestion yet.

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

See the [configuration guide](../../config/README.md). Shared topic rules and agencies live in `packages/domain/`; `ingest-one-offs --dry-run` validates `config/one-off-meetings.json`, and `ingest-one-offs` upserts notices without fabricating agendas or start times. `agencies` lists followed entries and adapter readiness. Run `init-db` after registry edits, and `reclassify` after topic edits. New CSV entries remain unverified and have no automatic discovery adapter.

`learn-schedules [--agency ID] [--dry-run | --write] [--registry PATH] [--json]` infers regular meeting patterns from stored meetings. It needs 3+ matches and a 60%+ match rate, and `--write` never replaces a `configured` schedule. `export --forecast-days N` (default 90, or `TAHOE_FORECAST_DAYS`; 0 disables) adds expected meetings derived from those schedules. Forecasts are never written to the database. See [Meeting schedules and expected meetings](../../config/README.md#meeting-schedules-and-expected-meetings).

## Reviewed source acquisition and public comment windows

[Decision 0014](../../docs/decisions/0014-reviewed-sources-and-public-comment-periods.md) and the [configuration guide](../../config/README.md) describe the current sources. Configured discovery supports reviewed Granicus/Legistar/CivicClerk/Municode/category indexes and rolling links. PDF, HTML, and plain text are captured through a bounded fetcher; curl/system CA certificates provide a verified-TLS fallback. HTML uses source sections rather than invented PDF pages. Partial fetch failures retain the notice and successful documents. `quarantine-meeting ID --reason TEXT` hides a misattributed record without deleting its provenance.

`ingest-comment-periods [FILE]` verifies official notice text and imports a separate comment window; default `config/public-comment-periods.json`. It never submits a comment or manufactures a multi-day meeting. No active period was verified in this review. Placer's persistent 403 errors remain [SOURCE-001](../../docs/issues/SOURCE-001-placer-downloads.md); no cloud scheduling was added.
