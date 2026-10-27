# IDEA-016: Item summaries from staff reports

Status: proposed
Stage: MVP polish

## Problem and audience
An expanded agenda item used to show an "Agenda excerpt", which is the agenda line the item was parsed from. In the 2026-10-27 local export, 1,848 of 1,908 excerpts were the item title plus at most about 20 characters, so readers saw the title twice. The excerpt is now shown only when it adds agenda text (for example Washoe's descriptions and recommended actions). Residents still lack a plain account of what an item proposes, and that account is usually in the staff report or executive summary, not the agenda.

## Smallest useful version
For items that have a supporting document (IDEA-013), read the first pages of that document while it is temporarily downloaded. Most staff reports open with a recommendation or requested action, then background and fiscal impact. Ask Haiku 5.5 for two or three neutral sentences: what is proposed, who proposes it, and the cost or affected place when stated. Every sentence cites a page, and programmatic validation checks each citation against the stored text before publishing (decision 0010; no human approval step). The meeting page already labels any non-excerpt summary method as "Summary".

Skip procedural items (roll call, adjournment, minutes approval) and items with no supporting document. Persist only the summary, the cited page text, and provenance (decision 0004).

Deferred: summarizing whole packets, and summaries of outcomes, which need minutes rather than agendas.

## Evidence and dependencies
Depends on IDEA-013's per-agency mapping from agenda item to its staff report or packet pages. Cost estimate at Haiku 5.5 list prices recorded in the [topic classification evaluation](../research/topic-classification-evaluation-2026-10-20.md) ($0.10/M input, $0.50/M output, dated): about 2,000 input and 150 output tokens per item, for roughly 450 items a month, is about $0.12 a month, and about $0.50 to backfill the current 1,900 items. Recheck prices before budgeting. The work is in locating the right pages per agency, not model cost.

## Acceptance criteria
Per-agency fixtures show each summarized item citing pages from its own staff report. No summary is published when the item-to-document mapping is uncertain or a citation fails validation.

## Open questions
Should summaries replace the "From the agenda" text when both exist? How should a revised staff report invalidate an existing summary?

## Links and updates
- 2026-10-27: proposed after meeting-page feedback that the agenda excerpt repeats the title.
