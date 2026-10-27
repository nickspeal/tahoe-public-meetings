# IDEA-013: Direct links to each item's supporting documents

Status: proposed
Stage: MVP polish

## Problem and audience
Residents who find an interesting agenda item want the fuller explanation (the staff report or executive summary) without hunting through a 500-page packet. Meeting pages currently link each item to its cited agenda page (a "Read in the agenda" card showing the page number).

## Smallest useful version
Add a second link per item, "Read staff report" or "Read executive summary", that opens the item's own supporting document or the right packet page. Per-agency sources, verified 2026-10-20:

- **SLT:** the compact agenda PDF embeds a link for each attachment, including "Item N- Executive Summary.pdf". `pdftohtml -xml` exposes these links; `pdftotext` drops them.
- **TTD:** the agenda's Page column refers to the packet's stamped "~ Page N ~" footer. Map that stamp to the PDF page index while the temporary packet is available. Staff memos also carry an "AGENDA ITEM: IV.A." footer.
- **Truckee:** staff-report pages carry an "Item N.N" header.
- **Lahontan:** the agenda links `itemN.pdf` per item.

Store only the URL, page number, and provenance. PDFs stay temporary inputs.

Deferred: summarizing the supporting documents, and fetching them for classification. An optional escalation pass for generic or uncertain items needs its own evaluation.

## Evidence and dependencies
Research: [topic classification evaluation](../research/topic-classification-evaluation-2026-10-20.md), hypothesis B. Depends on per-agency ingestion changes and on validating each link against the agenda item it belongs to.

## Acceptance criteria
Every item with a supporting document links to it, or to its first packet page, verified by fixture tests per agency. No link is shown when the mapping is uncertain.

## Open questions
Should the link go to an agency viewer page or straight to the PDF? How should replaced or revised attachments be handled?

## Links and updates
- 2026-10-20: proposed with [decision 0011](../decisions/0011-claude-for-automated-analysis.md) and the agenda-page link on meeting pages.
