# Product brief

## Accepted outcome

Primary outcome: residents follow a timeline of meetings across agencies, filtered by the topics that matter to them. A resident can answer: **What is coming up that matters to me, what does the agency propose to do, and how can I participate?** [Decision 0007](../decisions/0007-meeting-timeline-and-filters.md) set the current organization and presentation.

The initial audience is residents and existing civic volunteers who already have some interest in local issues but cannot monitor many agency websites. Board members and agency staff are useful secondary testers. Validate this audience with a small group of invited testers before expanding it.

## Core experience

1. Open the meeting timeline, see today and upcoming meetings in Pacific time, and filter by topic or agency. Earlier meetings remain accessible without dominating the initial view.
2. Open a meeting and immediately find its date, location, remote access, and official participation instructions.
3. See the meeting's topics and each item's tags, filter by one or more topics, then expand individual agenda items for details and source links.
4. Follow a source link to verify a claim or use the agency's official participation channel.
5. Return later for recording and minutes links when published.

Distinguish scheduled discussion or proposed action from an adopted decision. Unknown information should be explicit, not inferred. Past meetings are not evidence that an action passed.

## Accepted MVP boundary

Include City of South Lake Tahoe, Town of Truckee, and Tahoe Transportation District; bookmarkable pages for a shared curated topic catalog beginning with Biking, Transit, Housing, and Environmental Protection; upcoming and recent meeting pages; visible agenda tags and filters within a meeting; agenda item summaries with citations; official participation links; calendar downloads for verified start times; and an operator workflow for ingestion, corrections, and failed processing. Automatically publish summaries that pass validation; human review is not required. Start with an accessible date-grouped list. Add a visual calendar only if it improves testing results.

Implement TTD first, then City of South Lake Tahoe and Town of Truckee; all three are the launch scope. Confirm which boards/committees within each agency are covered. Bound initial history to upcoming meetings and a small recent sample; do not require a full archive.

Defer alerts, email subscriptions, resident account dashboards, personal notes, automated comment submission, polling, advocacy campaigns, live chat, translation, and automated social posting. Preserve these in the backlog rather than letting them block launch.

## Trust and operation

- Preserve original source URLs, fetch times, source-version hashes, and page references alongside extracted text. Download PDFs temporarily and link readers to official copies; original-PDF backups are deferred unless source reliability warrants them.
- Label AI-assisted summaries and indicate whether a person reviewed them. Confirmed policy: no prepublication human review. Automated checks verify output structure and citation references; they cannot guarantee factual accuracy. Provide correction controls and source links.
- Keep source-backed meeting logistics available even if summarization fails. Never label automated text as human-reviewed. Failed validation blocks the affected summary, not verified meeting logistics.
- Show source freshness and a correction route. Treat changed agendas as new versions requiring regeneration and validation.
- Display participation instructions from the source, including deadlines only when stated. The service does not submit comments in the MVP.
- Keep coverage mostly neutral, in one editorial voice ([decision 0010](../decisions/0010-single-editorial-voice.md)). Editorial emphasis may guide featured items, but should not hide other relevant items or imply that an opinion is an agency finding.
- AI summaries publish automatically. Perspective notes are optional and authored or explicitly approved by the maintainer; the system should not invent positions on his behalf. Notes never block factual publication.

## Beta validation

Proposed beta: 5–10 invited users over two meeting cycles. These numbers are planning assumptions, not commitments.

Ask each tester to find a relevant upcoming item, explain the proposed action, find supporting material, and identify how to participate. Record task completion and where they hesitate; ask whether they would return before the next meeting.

Proposed launch gates: all published summaries have verifiable source references; no known incorrect logistics; every supported source has a visible failure/freshness state; at least 4 of 5 observed testers complete the core workflow without assistance. Also test cross-agency topic relevance and whether returning users can spot new items, revisions, and cancellations. The maintainer builds, maintains, and funds the service; total recurring cost must remain below $20/month. Track operator minutes and actual processing and delivery cost before expanding.
