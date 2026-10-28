# IDEA-016: Item summaries from staff reports

Status: building (implemented locally for 25 agencies, decisions 0024 and 0026; not yet deployed)
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
- 2026-10-27: implemented locally per [decision 0024](../decisions/0024-verbatim-item-summaries.md), with the maintainer's rules that summaries repeat the source verbatim and lead with the agenda's requested action. The export shows, in order of preference: a Claude staff report summary that matches the current item and report, the requested action printed under the item, or the agenda excerpt. Answers to the open questions: the summary replaces the excerpt; a revised agenda or report changes the item fingerprint or report hash and the item falls back to its agenda text until the next run. Samples (agenda passages are cited to the agenda page, report passages to the report page):
  - SLT, October 6, 2026, item 9: "Pass a Motion authorizing and directing the Mayor to execute a Professional Services Agreement with Zenith Economics for the City’s Economic Development Market Analysis and Business Attraction Strategy in an amount not to exceed $80,100.00. The proposed agreement amount is within the approved project budget of $100,000.00. Zenith Economics was selected as the most qualified firm based on their experience, understanding of the City’s needs, proposed approach, capacity, and cost." (The last sentence drops "Following a thorough evaluation by the selection committee,".)
  - TTD, October 7, 2026, item IV.A: "Staff requests the Board to review and approve the selection of the Employee Health Benefit Program for non-bargaining employees for the period December 1, 2026 through November 30, 2027. The Employee Health Benefit Program is expected to cost approximately $393,513 annually. There is a 7.8% increase in medical coverage, 1.9% increase for dental coverage, and 8.3% for short term disability." (The second sentence drops "If these plans are selected,".)
  - Truckee, October 27, 2026, item 7.2: "That Council: receive an update on the process of developing the next solid waste collection services franchise agreement; and provide feedback on items to include in the request for proposals (RFP). The current ten-year solid waste franchise agreement with Tahoe Truckee Sierra Disposal (TTSD) ends on June 30, 2028, unless extended by the Town, for up to a period of one year, which may occur up to three times."
  - Lahontan, October 1, 2026, item 6: "Should the Water Board adopt revised waste discharge requirements for the Truckee-Tahoe Water Reclamation Plant to reflect changes in discharge conditions, update monitoring and reporting requirements, and incorporate provisions consistent with current regulatory standards and policies? The Facility is located approximately 3 miles east of the Town of Truckee."
