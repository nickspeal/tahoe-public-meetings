# IDEA-020: Short titles for long agenda items

Status: accepted; implemented (decision 0035)
Stage: MVP polish

## Problem and audience
Some agencies print an item's whole recommended action as its title. On 2026-11-03, 23% of published items had more than 200 characters of agenda text, and the median El Dorado County Board of Supervisors item had 410 (the longest, 2,702). Residents scanning a meeting page or a topic list cannot tell what such an item is about without reading a paragraph of department names, statute citations, and numbered sub-actions.

## Smallest useful version
For an item whose whole agenda text is longer than a threshold, Claude writes a headline of 6 to 14 words from the agenda's own words. A deterministic check publishes it only when it adds nothing that is not in the text. The meeting page shows the headline, marked as shortened, and keeps the whole agenda text in the expanded item. Deferred: headlines in calendar downloads, link previews, and search.

## Evidence and dependencies
- Depends on the whole text of cut-off titles (`agenda_items.full_title`, 2026-11-03), so a headline is written from the full recommendation rather than its first lines.
- Reuses the item summary pattern (decision 0024): Haiku first with a Sonnet fallback, a validator, retries with the reason, `ai_usage` accounting under the shared budget, and per-meeting batched writes.
- Measurements and the evaluation are in [decision 0035](../decisions/0035-short-titles-for-long-agenda-items.md).

## Acceptance criteria
- No published headline contains a word, number, or date that is not in the item's agenda text, or drops a "deny", "not", or similar word governing what it names.
- A spot check of headlines finds none that misleads about the action or its subject.
- An unchanged agenda, posted again, sends no new request.
- Cost stays within cents a month under the shared $5 AI budget.

## Open questions
- Is a paraphrased headline acceptable beside the verbatim summary rule (decision 0024), given that every word is the agenda's own?
- Threshold: 200 characters, or only the very long items (300+)?
- Should the shortened headline also be used in calendar event titles?

## Links and updates
- 2026-11-03: prototype on a branch (`tahoe-ingest shorten-titles`, `ai_short_titles`, `shortTitle` in the export, "Shortened" mark on meeting pages); evaluated on every qualifying item of the live export.
- 2026-11-03: accepted by the maintainer (decision 0035) with the 200-character threshold and $0.05 per run. Enabled by default in the scheduled run, before `summarize-items`; the operator can suppress or replace a headline with `short-title`. Paraphrase is accepted for headlines (the open question on decision 0024); calendar titles stay open.
