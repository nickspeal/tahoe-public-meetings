# 0024: Verbatim item summaries from requested actions and staff reports

Status: accepted (the verbatim rule and the requested-action rule are maintainer instructions); proposed default for the rest
Date: 2026-10-27
Related ideas: IDEA-013, IDEA-016
Related decisions: 0004, 0010, 0011, 0015, 0020

## Context

An expanded agenda item showed only an agenda excerpt, which usually repeated the title (IDEA-016). The fuller account is in the item's requested action, which many agendas print under the item, and in its staff report or executive summary, which agencies publish separately or inside the agenda packet (IDEA-013).

## Decision

**Accepted maintainer instructions (2026-10-27):**

- **A summary repeats the source verbatim as much as possible**, changing it only where needed for brevity. Sentences are selected from the agenda and the staff report and trimmed; nothing is paraphrased, characterized, or added.
- **The summary leads with the item's requested or recommended action, verbatim, whenever the agenda states one** ("Requested Action / Suggested Motions:", "Recommended Action:", "Recommendation:", "Requested Action:", "Action Requested:"). An item with a printed requested action but no mapped staff report still gets a summary from that text alone. A TTD-style "For Possible Action:" line is the item's title and is not repeated.
- **Show links to every attachment the agenda lists under an item**, labeled from the printed file name, with the file type and the printed name kept for matching.

**Proposed defaults (this implementation):**

1. *Staff report links* (`tahoe-ingest map-staff-reports`, `tahoe_ingest/staff_reports.py`). One rule per agency, each requiring two independent signals to agree:
   - South Lake Tahoe: the agenda PDF's "Item N- Executive Summary.pdf" link, and that file's first page reading "Agenda Item #:N" with the item's title and meeting date.
   - TTD: the agenda's Page column, the packet's "~ Page N ~" stamp on that page, and the board's "AGENDA ITEM: N." footer on it.
   - Truckee: the agenda packet (stored, or Municode's MEET-Packet file beside the captured agenda PDF, which must open with that agenda), with a single run of pages headed "Item N.N" whose first page carries the item's title.
   - Lahontan: the agenda's `itemN.pdf` link, headed "ITEM N" with the item's title and meeting.
   Uncertain items store nothing. Only the URL, page range, hash, evidence, and the text of the report's first three pages (for summary citations) are kept; the PDFs are temporary (decision 0004). The meeting page shows "Read the staff report" (or "Read the executive summary") beside "Read in the agenda".
2. *Attachment lists* (South Lake Tahoe). Every link between an item's "N." heading and the next left-margin heading of the agenda PDF belongs to that item, in agenda order. Each printed name must also appear on that page of the stored agenda text, and an executive summary numbered for another item makes the whole list uncertain. URL, printed name, readable label, and file type are stored, never the files.
3. *Requested actions* (`tahoe_ingest/requested_actions.py`, no model). The label must start a line between the item's cited title and the next item's in the same agenda; the action must be one complete statement ending in a period, 5–120 words. It is the exact page text, cited to its page, and is computed at export time from stored text (no extra queries).
4. *Staff report sentences* (`tahoe-ingest summarize-items`, `tahoe_ingest/item_summaries.py`, prompt `prompts/staff_report_summary.md`). Haiku 5.5 at low effort selects one or two further sentences (two or three when the agenda prints no action), each with the cited page and the passage it trimmed. Haiku gets a second try with the validator's objection, then Sonnet 5.5 gets one. Procedural items (roll call, minutes, adjournment, public comment, closed session) are skipped.
5. *Validation before publication* (decision 0010; no human approval). Each passage must be found on its cited page in the stored text (words compared, so line breaks and misread quote characters do not matter), and each sentence must be that passage with words deleted: at most one inserted "the", "a", "an", or "and" per ten words, no new words or numbers, at most three cuts from the middle of a passage of at most 12 words each, and no dropped negation within or next to the kept words. Each passage must begin where a sentence begins on its page, and a sentence may drop only an opening clause ending in a comma or colon, or a list marker such as "(1)", so every published sentence is a whole sentence of the source; cutting the end is free. One failing sentence fails the summary, and the item keeps its agenda text. Failed and skipped inputs are recorded and not sent again until something the model sees changes.
6. *Precedence and invalidation.* The export shows the staff report summary while it matches the item's source fingerprint and its current staff report hash; otherwise the agenda's requested action; otherwise the agenda excerpt. A revised agenda changes the fingerprint, so a stale summary is never shown; operator corrections and suppression (`editorial`) apply on top and survive reprocessing. Published summaries are also appended to `summary_versions` (internal audit).
7. *Budget.* Summaries have their own per-run limit (`--summary-max-cost`, default $0.50) under the shared $5 monthly AI budget; a run takes as many pending items as fit and leaves the rest for the next. At most 6 meetings are scanned for staff reports per run (`--staff-report-meetings`), nearest to today first. Mapping writes one statement per scanned meeting and summaries one per meeting; the export reads all of it in one query (13 statements in total, still fixed).

## Measurements (local data, 2026-10-27)

- Staff report links: 135 items in 18 meetings (South Lake Tahoe 60, TTD 36, Truckee 21, Lahontan 18). SLT attachment lists: 75 items, 364 files; one item was left out because a wrapped file name did not match the agenda text.
- Summaries: 117 from staff reports (SLT 53, TTD 30, Truckee 19, Lahontan 15), 42 of them led by the agenda's printed requested action; 40 more items show the agenda's requested action alone (SLT City Council 17, Planning Commission 10, Parks and Recreation Commission 4, Truckee 9). TTD's agenda lines are "For Possible Action:" titles, so its actions come from the staff memos.
- Validation, final rules: of 135 items, 117 published, 8 were skipped by the model (enclosure covers, calendars, or nothing beyond the requested action), and 10 failed validation and kept their agenda text (7%). Before the whole-sentence rule, 1 of 135 failed, but several published sentences were verbatim fragments.
- Cost: about $0.0015 per item with Haiku 5.5 (Sonnet 5.5 fallbacks included). These four agencies add roughly 60–150 items a month: $0.10–0.25 a month.
- Database statements per scheduled run: about 4 reads for each of the two steps, plus about 2 per meeting scanned (at most 6) and 1 per meeting summarized; about 15 on an ordinary run, under 2,000 a month (1% of the free quota).

## Alternatives and consequences

- Paraphrased summaries read more smoothly but cannot be checked mechanically and drift from the record; the maintainer chose verbatim.
- Letting Claude find the requested action would cost more and add a failure mode for text that is already printed.
- Agencies without a rule keep their agenda excerpt or requested action. A staff report replaced at the same URL without a new agenda is not noticed until the agenda changes.

## Revisit when

- Validation failure rates rise above about 10%, or readers report a misleading trimmed sentence.
- Another agency's agendas or packets expose per-item reports or attachments in a verifiable way.
