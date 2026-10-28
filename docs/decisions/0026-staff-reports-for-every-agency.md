# 0026: Staff report links and summaries for every agency that publishes them

Status: accepted (maintainer request, 2026-10-28); implemented locally, not yet deployed
Date: 2026-10-28
Related decisions: [0004 source-first storage](0004-source-first-storage.md), [0015 deterministic agenda layouts](0015-deterministic-agenda-layouts.md), [0020 database operation budget](0020-database-operation-budget.md), [0024 verbatim item summaries](0024-verbatim-item-summaries.md)
Related: [IDEA-013](../ideas/IDEA-013-item-supporting-documents.md), [IDEA-016](../ideas/IDEA-016-item-summaries-from-staff-reports.md)

## Context

Decision 0024 gives an agenda item a verbatim summary only when `tahoe-ingest map-staff-reports` has linked it to its own staff report, and only four agencies had a mapping rule (South Lake Tahoe City Council, TTD, Truckee, Lahontan). Every other agency's items showed their title and a "Read in the agenda" link. The maintainer asked for most agencies to be supported: where an agenda or a staff report is not reachable, or the agenda carries too little to match on, the agency is skipped, but wherever the data exists it should be processed.

The maintainer also asked, using TRPA as the example, that each item link inline to the document published for that item (easier for a reader than a 300-page packet), and that a meeting list both its short agenda and its full packet when an agency publishes both.

## Decision

**One rule module per agency or platform**, in `workers/ingestion/tahoe_ingest/staff_report_rules/`, discovered automatically; the core (`staff_reports.py`) keeps the shared context, temporary PDF reading, storage, and export. Each rule still needs two independent signals to agree before an item is linked, and uncertain items store nothing (decision 0024). The rule package README explains how to add a rule and the fixture format.

**Where the reports come from.** The research found five publishing patterns, and the rules follow them:

| Pattern | Agencies | Signals |
| --- | --- | --- |
| A file per item, linked from the agenda PDF or the meeting page | TRPA Governing Board and Advisory Planning Commission; Fish and Game Commission (its document index); State Park and Recreation Commission; Truckee Tahoe Airport District (tabbed files); SLT Planning and Parks commissions; Tahoe City PUD and its parks committee (HTML agenda); Lahontan and SLT (existing) | the agenda's or page's link names the item; the report's first page names the item number or title and the meeting date |
| Pages inside a stored packet with a per-item cover or stamp | Douglas County (three boards: numbered cover pages); South Tahoe PUD ("BOARD AGENDA ITEM 6c" memos); North Tahoe PUD (printed page ranges); Incline Village GID ("ITEM G.1." stamps); Nevada water board (resolution codes); Truckee (existing) | the page heading names the item; the memo's date or subject matches the meeting and title |
| The agenda's page column into a packet | PCTPA; TRPA when an item has no file of its own; TTD (existing) | the page column lands on a page whose stamp or footer names the item |
| A platform API | El Dorado and Washoe counties (open Legistar web API: event items, attachments, legislation text) | the agenda's printed file number matches the API's item; the report prints the meeting date and the item's title or number |

TRPA items prefer their own report file (verified by the footer naming the board item) and fall back to the board packet. They also get an attachment list: the report plus the "Attachment" and "Exhibit" files the meeting page lists after it, so the inline links the maintainer asked for appear under each item.

**Both the short agenda and the full packet are meeting documents.** TRPA discovery now keeps exactly the body's own agenda and its packet, in that order, dropping committee agendas, presentations, and public comments that were being captured as agendas; the shared ordering puts an agenda before a packet for every configured source, which also gives El Dorado Planning Commission meetings their packet. Items are parsed from the agenda, not from the packet's copy of it.

**Non-PDF reports** (El Dorado's legislation text pages, Washoe's Word files) carry a media type through `item_staff_reports` and the export, so the website labels them and does not append a PDF page anchor.

**Requested-action labels** recognised in agendas now include "Required Motion(s)", "Summary and Staff Recommendation", "Requested Board Action", "Recommended Motion", "Recommendation for Action", "Possible Motion", and "Action Recommended". Report-only headings printed without a colon ("STAFF RECOMMENDATION", "ACTION REQUESTED" on its own line) are left to the summary model, which reads the report pages.

## Not supported, and why

- **No staff reports published:** Nevada Tahoe Conservation District (one-page agreement summaries only), Tahoe RCD (its page column refers to an unpublished packet), South Shore TMA (one-page agenda, unnumbered items), Truckee North Tahoe TMA (two-page agenda, numbers restart per section), Nevada State Parks (a hearing notice).
- **Reports without item numbers:** the OHMVR Commission names reports by topic; a title would be the only signal.
- **No agenda items to attach to:** the Tahoe Conservancy and the State Lands Commission publish per-item staff reports, but their HTML agendas have no parsing adapter yet, so no items exist locally (tracked as the next adapter work).
- **Blocked source:** the three Placer County bodies (HTTP 403, no mirror).
- **Partial within supported agencies:** PCTPA consent sub-items share one memo (paragraph-level mapping would need a quote range on the mapping); Douglas packets over 100 MB (one of four local meetings); Fish and Game items whose index entry has only sub-item documents; items whose report is for another meeting date; Washoe legacy `.doc` reports when `antiword` is absent from the worker image.

## Measurements (local data, 2026-10-28)

| Agency group | Items mapped locally |
| --- | --- |
| TRPA (two bodies, 7 meetings) | 30 of 32 eligible; 29 to their own file, 29 attachment lists (64 files) |
| Fish and Game, State Parks (SPRC), Nevada water board | 68, 6, 10 |
| South Tahoe PUD, PCTPA, North Tahoe PUD, airport district | 22, 17, 28, 25 |
| Douglas (three boards), SLT commissions, Tahoe City PUD (two bodies) | 94, 15, 38 |
| El Dorado (two bodies), Washoe (two bodies), Incline Village GID | 121, 51, 11 |

About 536 newly mapped items in all, on top of the 135 from decision 0024, across 25 of the 35 agencies with parsed items. Spot checks by the build agents found each kept first page describing its own item. At about $0.0015 per summary (Haiku 5.5), the local backlog costs under $1 once and the new agencies add roughly $0.50–0.75 a month, within the $5 AI budget; the per-run `--summary-max-cost` ($0.50) spreads the backlog over several runs. Database statements stay at one per scanned meeting and one per summarised meeting (decision 0020); storing TRPA packets adds three statements per new packet version, independent of page count.

Downloads per scheduled run remain bounded by `--staff-report-meetings` (6 meetings) and per-rule caps (about 40 report files per meeting; packets up to 100 MB for Douglas, 80 MB elsewhere).

## Alternatives and consequences

- A single generic rule parameterised by regexes was rejected: the five patterns differ in where the first signal lives (PDF link annotations, page markup, packet pages, an API), and a per-agency module keeps each rule's evidence explicit and testable against a real fixture.
- Resolutions and presentations that carry the item footer are mapped with the title "Staff report"; the summary model skips those with nothing to summarise. A later refinement could title them from their first line.
- Large packets are re-downloaded when a meeting is rescanned (only when its items or documents change, or after a failed download); storing them as documents, as TRPA now does, avoids the download but grows the public export, which already ships every stored page (IDEA-014).

## Revisit when

- An unsupported agency starts publishing per-item reports or a packet with item markers.
- Agenda adapters exist for the Tahoe Conservancy or the State Lands Commission (their rules are described in the research notes and can then be added).
- Export size or AI spend from the added agencies approaches the budgets in decisions 0020 and 0024.
