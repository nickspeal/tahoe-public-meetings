# IDEA-002: Topic timelines and subscriptions

Status: shipped locally (shared topic catalog and meeting filters); alerts deferred
Stage: MVP topic pages; alerts deferred

## Problem and audience
Residents cannot repeatedly check every agenda for issues they follow.

## Smallest useful version
MVP: an extensible global catalog used consistently in cross-agency timelines and within each meeting. Start with Biking, Transit, Housing, and Environmental Protection, then add defined civic topics. Show visible agenda-item tags and let residents select multiple topics to filter a meeting. Later: opt-in email digests with unsubscribe and duplicate prevention. Keep revisions and cancellations distinguishable from routine new-item labels.

## Evidence and dependencies
Origin: initial design; demand has not yet been validated. Stable item identity, validated topic assignments, dependable ingestion; basic filtering belongs to IDEA-001.

## Acceptance criteria
MVP: readers find relevant upcoming items and recent changes across all three agencies. The same topic IDs and names appear in ingestion, operator overrides, exports, and every public view. Meeting filters use any-selected-topic matching, count unique items, preserve agenda order, support bookmarked URLs, and clear back to the full agenda. Item links remain reachable under active filters. Later: a subscriber receives relevant newly published items once per digest and can unsubscribe; changed logistics follow the documented notification policy.

## Open questions
Topic granularity, frequency, delivery provider, and popular-issue editorial criteria?

## Links and updates
See the [roadmap](../planning/roadmap.md) and [MVP build plan](../planning/mvp-build-plan.md).

2026-10-20: the maintainer requested visible meeting topics, item tags, filters within a meeting, and a global list beyond the four starting topics. Implementation is tracked by [decision 0008](../decisions/0008-shared-topics-and-meeting-filters.md).

2026-10-20: Implemented a 12-topic catalog shared by ingestion, overrides, exports, and the website. Meeting filters support any-selected-topic matching, reset, URL state, visible item tags, and item deep links. Reclassified the 243 captured agenda items without changing source or summary history.
