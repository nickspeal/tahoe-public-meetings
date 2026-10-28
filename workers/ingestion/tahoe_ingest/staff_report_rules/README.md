# Staff report mapping rules

`tahoe-ingest map-staff-reports` links an agenda item to its own staff report (IDEA-013, decision 0024). Each module in
this package holds the rule for one agency or platform. The shared core is `tahoe_ingest/staff_reports.py`.

## Adding a rule

1. Create `staff_report_rules/<agency_or_platform>.py`. It needs a module docstring that says what the agency
   publishes and which two signals the rule checks, and a module-level dict:

   ```python
   RULES = {"trpa-governing": map_trpa, "trpa-advisory": map_trpa}
   ```

   Every module is discovered when the package loads (`__init__.py`), so you don't edit a shared registry. Two modules
   that claim the same agency ID raise an error at import. `staff_reports.SUPPORTED`, the CLI's `--agency` choices,
   and the scheduled run all come from the merged registry. Modules whose names start with `_` are skipped, which is
   where private helpers go.
2. A rule is `rule(ctx: Context) -> list[Mapping]`. Return a `Mapping` only for items you verified. For every other
   item call `ctx.skip(item, reason)`. Raise `Uncertain` when the whole meeting can't be verified (for example, the
   agenda changed since it was captured). Download failures raise `FetchFailed`, and the meeting is retried after
   `RETRY_HOURS`. A file over its download limit raises `TooLarge` (a `FetchFailed`): fetching it again would fail
   the same way, so `ctx.read_report` skips that item for good ("report is larger than the download limit") instead
   of retrying; a rule that downloads a whole packet should turn it into `Uncertain`.
3. Add a fixture (see below). The shared test picks it up automatically. Add tests for the rule's own failure modes
   too (another meeting's report, a link to the wrong item, an interrupted page run).

### Two signals must agree

A mapping needs two independent signals. One comes from the agenda or meeting page: a link under the item, a link
whose name gives the item number, or a page number in the agenda's Page column. The other comes from the report itself:
its first page names the item number, the item's title overlaps by at least `MIN_TITLE_OVERLAP` (0.6), and a printed
meeting date, if there is one, must match the meeting. Store nothing when either signal is uncertain. A wrong link is
worse than no link.

Items without a number (many Granicus and WordPress agendas) and agendas that repeat numbers (TMA) can only be matched
by title, using `ctx.by_title`. That match has a higher floor: 0.8 of the title's distinctive words, at least two such
words, and the match must be unique in both directions. It counts as only the agenda-side signal, so the rule must
still check the report's own heading.

## What a rule can use (`tahoe_ingest.staff_reports`)

| Helper | Purpose |
| --- | --- |
| `ctx.meeting` | `Meeting(id, agency_id, title, local_date, items, documents, official_url)`. Items have `number`, `title`, `citations` `[{documentId, page, quote}]`. Documents are the current captured versions. |
| `ctx.numbered()` | Non-procedural items with a unique number. Procedural and repeated-number items are skipped with a reason. Unnumbered items are left out silently. |
| `ctx.by_title(texts, items=None, *, floor=0.8)` | `{item id: key}` from `texts` (`{key: printed link text or heading}`), unique both ways. Unmatched items are skipped. |
| `ctx.stored_pages(version_id)` | Stored page texts `{page: text}` of a captured document. No download. |
| `ctx.pages(document, needed=fn)` | Stored pages, or the PDF downloaded once (and its hash checked) when `needed(stored)` is false. |
| `ctx.read(url, last_page=, links=)` | Temporary PDF download: `{"sha256", "page_count", "pages", "links", "boxes"}`. `links=True` adds link annotations with page and position. |
| `ctx.read_report(item, url)` | First `LEADING_PAGES` pages of one report, or None (with the item skipped) if it can't be read. Download failures are retried with the meeting; a report over the size limit is skipped for good. |
| `ctx.html(url)` | The raw markup of an HTML page, for rules that read more than its links (Granicus agenda tables). |
| `ctx.html_links(url)` | `[{"url", "text"}]`: absolute http(s) links on an HTML page (`ctx.html`), in document order. Raises `Uncertain` when the page has no links. |
| `ctx.fetch_json(url)` | A parsed JSON API response (`Accept: application/json`, one retry on a reset connection), e.g. the Legistar Web API. The cached object is shared between calls: don't modify it. |
| `ctx.fetch_bytes(url, *, accept, max_bytes)` | A file's bytes, for reports that aren't PDFs (Word files). Over `max_bytes` raises `TooLarge`. |
| `linked_reports(ctx, items, candidates, *, verify, kind, title, precheck=None, missing=...)` | The loop for rules where each item links its own report file. `candidates` maps `{normalised number: {url}}`, or `{url: {numbers}}` when one report covers several items. Each URL is read once, then `verify(item, report)` returns an evidence dict or a skip reason. `report["url"]` is set. The mapping covers the whole file. |
| `packet_reports(ctx, packet, pages, items, *, page_label, label_name="Item {number}")` | For runs of packet pages labelled with the item number. `page_label(text)` returns a normalised number or None. A run that another item's label interrupts is skipped. The first page must overlap the title. |
| `agenda_page_column(item, ctx)` | The packet page number the agenda's Page column prints on the item's first line (TTD, TRPA), or None. |
| `report_names_item(first_page_text, item, *, number_patterns, meeting_date=None)` | The generic report-side check. Returns evidence `{"reportHeading", "titleOverlap", "meetingDate"?}` or a skip reason. `number_patterns` are regex templates searched with `re.M`, where `{n}` is the escaped, normalised item number, for example `r"(?i)^\s*Agenda Item No\.?\s*{n}\b"`. |
| `printed_meeting_dates(text)` | `[(first, last)]` for dates printed after a "Meeting", "Meeting Date:", or "Meeting of" label. A memo's own "Date:" doesn't count. |
| `pdf_agenda(ctx, prefer=)`, `leading(pages, start, end)`, `long_date(day)`, `norm_number(n)`, `title_overlap(title, text)`, `procedural(item)`, `clean_text(text)`, `pdf_links(xml)` | Smaller helpers. |
| `requested_actions.ACTION_LABELS` / `ACTION_LABEL` | The action labels agendas and reports print ("Recommended Action:", "Required Motions:", "Requested Board Action:" …), for rules that look for the recommendation in a report. |

`ctx.read`, `ctx.html`, `ctx.fetch_json`, and `ctx.fetch_bytes` each count as a download and wait `delay` seconds
after the previous one. The last three fetch a URL at most once per context and raise `FetchFailed` on network
errors, `TooLarge` over the size limit, and `EndpointSkipped` for a configured skip. Don't call the network yourself:
the context's fetchers are what tests replace with fixture data.

| More helpers | Purpose |
| --- | --- |
| `parse_printed_date(text)`, `PRINTED_DATE` | The date `text` starts with ("October 21, 2026", "Oct. 21, 2026", "JUNE 24, 2026", "September 03, 2026"), or None. Find it after your own label: `re.search(rf"MEETING DATE:\s*({PRINTED_DATE})", page)`. |
| `stored_pdf_packets(ctx, pattern=None, *, documents=None)` | The meeting's captured PDFs, one per file hash (discovery may store a file under two URLs), optionally only those whose stored pages match `pattern`. |

Number normalisation: `norm_number(" iv.a. ") == "IV.A"`. Compare normalised numbers on both sides.

### Mapping and evidence conventions

`Mapping(item_id, fingerprint, kind, title, url, page_start, page_end, page_count, sha256, evidence, pages, media_type)`:

- `kind` is one of `"staff-report"`, `"executive-summary"`, or `"staff-memo"`. The web page labels the link from it.
- `title` is shown with the link, for example `"Item {number} staff report"` or `"Staff report in the agenda packet"`.
- `url` is the official PDF, linked at `page_start`. `pages` holds the kept leading pages, from `leading()`. Never keep
  the PDF itself (decision 0004).
- `media_type` (default `"application/pdf"`) is what `url` opens. A report that is not a PDF keeps its text as
  numbered sections in `pages` and sets it: `"text/html"` for an agency webpage (Legistar's legislation text, title
  `"Legislation text"`), `"application/msword"` or
  `"application/vnd.openxmlformats-officedocument.wordprocessingml.document"` for Word files. The web page links
  those without a `#page=` fragment.
- `evidence` is a small JSON dict with camelCase keys that records what agreed, in the words the documents print. Use
  existing keys where they fit: `agendaLink` (printed link text or file name), `agendaPage` (agenda page the link sits
  on), `agendaPageColumn`, `packetStamp`, `reportHeading`, `header`, `footer`, `pages` (`"12-18"`), `titleOverlap`, and
  `meetingDate` (ISO). Keep it to facts from the documents, not prose.

## Fixtures

Each rule has `tests/fixtures/staff-reports/<name>.json` at the repository root. `test_each_item_cites_pages_of_its_own_staff_report`
runs every file in that directory. The rule comes from `meeting.agency_id`.

```jsonc
{
  "note": "Where this came from (agency, meeting date, what was trimmed).",
  "meeting": {"id": "...", "agency_id": "trpa-governing", "title": "...", "local_date": "2026-10-28",
              "official_url": "https://..."},              // official_url is optional
  "items": [{"id", "number", "title", "section", "fingerprint", "citations"}],
  "documents": [{"version_id", "source_document_id", "title", "url", "sha256", "page_count", "media_type"}],
  "stored": {"<version_id>": {"1": "page text", ...}},     // captured agenda pages
  "downloads": {"<pdf url>": {"sha256", "page_count", "pages": [{"page_number", "text"}], "links": [], "boxes": []}},
  "html": {"<page url>": "<html>...</html>"},              // optional: markup for ctx.html / ctx.html_links
  "json": {"<api url>": {...}},                            // optional: responses for ctx.fetch_json
  "word": {"<file url>": "<base64 bytes>"},                // optional: files for ctx.fetch_bytes
  "expected": {"<item number>": {"page_start": 1, "page_end": 9, "url_end": "item6.pdf", "heading": "ITEM 6"} | null}
}
```

- `expected` must list every item number in `items`. `null` means the item must stay unmapped. `page_end`, `url_end`,
  and `heading` are optional. `heading` is the text the report's first kept page prints for the item. Without it, the
  test accepts `#:N`, `AGENDA ITEM: N.`, `Item N`, or `ITEM N`.
- Trim downloads to the pages the rule reads (`LEADING_PAGES` for linked reports) and keep `page_count` as the real
  count. Fixtures are public: take them from public agency documents only.
- A URL missing from `downloads`, `html`, `json`, or `word` fails as a download (`FetchFailed`). Tests never use the
  network: `run_rule` serves these maps, and `conftest.py` makes the default fetchers fail offline.

Run the tests with `cd workers/ingestion && uv run pytest tests/test_staff_reports.py -q`.

## Trying a rule against the local database

```sh
cd workers/ingestion
export DATABASE_URL=postgresql://tahoe:tahoe-local@127.0.0.1:55432/tahoe   # local only
uv run tahoe-ingest map-staff-reports --agency trpa-governing --meeting <meeting id> --dry-run
uv run tahoe-ingest map-staff-reports --agency trpa-governing --meeting <meeting id> --force
uv run tahoe-ingest summarize-items --meeting <meeting id> --dry-run
```

The JSON report lists `skipped` reasons for each meeting, so read it for every item you expected to map.
`summarize-items --dry-run` shows which mapped items would be summarized, without calling the API. Downloads happen at
`--delay` seconds apart (default 1). Be polite to agency sites.

`METHOD_VERSION` is part of every meeting's scan key. A new rule doesn't need a version bump, because that agency's
meetings have no scans yet. Changing what an existing rule outputs does need one, which rescans every agency's
meetings over the next scheduled runs. Leave that call to whoever merges the change.
