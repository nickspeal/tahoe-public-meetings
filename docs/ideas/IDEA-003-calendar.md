# IDEA-003: Calendar view and export

Status: shipped locally (single-event downloads); calendar view and subscriptions deferred
Stage: Retention

## Problem and audience
Residents need to coordinate meetings with their schedules.

## Smallest useful version
Accessible calendar view if useful; single-event export before subscription feeds. Preserve stable event UIDs and correctly represent updates/cancellations.

## Evidence and dependencies
Origin: initial design; demand has not yet been validated. Reliable times, named time zones, status, and stable IDs.

## Acceptance criteria
Single-event downloads use stable UIDs, verified start times converted correctly across daylight-saving boundaries, cancellation status, official source links, and escaped/folded iCalendar text. Do not invent a duration. When only a date is known, explain that a confirmed start time is needed instead of creating a misleading all-day event. Keep file downloads local and explain that imported snapshots do not update automatically. A future subscription feed would update the same event.

## Open questions
Single-event export or a continuously updating agency/topic feed first?

## Links and updates
See the [roadmap](../planning/roadmap.md) and [MVP build plan](../planning/mvp-build-plan.md).

2026-10-20: Selected as the next small local feature under the maintainer's instruction to advance useful repository ideas. See [decision 0008](../decisions/0008-shared-topics-and-meeting-filters.md). Calendar account access and subscription feeds are out of scope.

2026-10-20: Implemented 18 downloadable events from the captured meetings, with the date-only TTD listing excluded. Tests cover both daylight-saving offsets, stable UIDs, cancellations, escaping, UTF-8 line folding, and generated routes. Files follow snapshot semantics from [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545), with no scheduling `METHOD` or invented end time. Import into desktop calendar applications has not been manually tested.
