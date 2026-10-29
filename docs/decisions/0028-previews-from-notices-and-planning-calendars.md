# 0028: Previews from notices and agenda planning calendars

Status: accepted (maintainer decisions of 2026-10-29); implemented, and the worker image (tag `a7140adc4582`) deployed to both Cloud Run jobs on 2026-10-29; first production run waits for the scheduler to resume on 2026-11-01
Date: 2026-10-29
Related decisions: [0009 configurable coverage and one-off meetings](0009-configurable-coverage-and-one-off-meetings.md), [0012 forecast meetings](0012-forecast-meetings-from-schedule-patterns.md), [0014 reviewed sources and comment periods](0014-reviewed-sources-and-public-comment-periods.md), [0015 deterministic agenda layouts](0015-deterministic-agenda-layouts.md), [0020 database operation budget](0020-database-operation-budget.md), [0021 participation details from agendas](0021-participation-details-from-agendas.md), [0022 timeline default view](0022-timeline-default-view.md), [0027 agency definitions in the registry](0027-agency-definitions-in-configuration.md). Idea: [IDEA-018](../ideas/IDEA-018-previews-from-notices-and-planning-calendars.md).

## Context

Residents following a topic learned about an agenda item only when the agenda was posted. Agencies say earlier what is coming, in two documents the pipeline already captured but did not use: public notices tracked as comment periods (the West Shore Trail Notice of Preparation announces a scoping open house and a presentation to the TRPA Advisory Planning Commission), and agenda planning calendars (South Lake Tahoe attaches an "Agenda Planning Calendar" to each regular agenda; the TTD board packet carries a "Tentative Agenda Calendar"). Neither meeting from the notice was on the timeline, and a December South Lake Tahoe item about bikes at intersections was visible weeks ahead in a calendar.

## Decision

The maintainer decided (IDEA-018, decisions 1–10):

- **Previews show by default.** An upcoming meeting with a preview appears in the default timeline view and keeps its "Expected · no agenda yet" pill. One-off meetings from notices also show by default: the notice is their posted document.
- **Any organization can host a meeting.** Meetings and comment periods carry an optional `host`. A registry entry whose `meetingHost` is `per-record` (the entry `other`) holds one-off meetings whose host is named on each record; the host replaces the agency name wherever it is shown. The Agency filter offers **Other**, listed last, and a meeting under Other matches only that filter. A one-off goes under a followed body only when the source names exactly one registry entry as host.
- **Extracted meetings and previews publish automatically**, after deterministic checks, with no review step.
- **Only tracked notices are read:** the official notices of configured comment periods. Finding notices automatically is deferred.
- **Planning calendars populate previews** for the South Lake Tahoe City Council and the TTD board and its Regional Transit and Capital Programs Committee. Each newer calendar replaces the previous one's previews.
- **Each calendar format has a deterministic parser**, registered by format name in `planning_calendar_rules/` and chosen by the agency's `planningCalendar` block (decision 0027). No model reads calendars; a redesigned layout stops that agency's previews until the parser is updated. The whole calendar validates or publishes nothing.
- **Planned meetings** are stored records (`<agency>:planned-<date>`) created when a preview names a date with no stored meeting. The same-date bridge adopts them when discovery captures the real agenda. One with a stated start time gets a calendar download whose description starts with "Subject to change."
- **Notes are short.** "Subject to change." and "Check the notice for changes." are the only caveats; the source line names and links the document.

Proposed defaults in the implementation: notices are read by Claude Haiku 5.5 (Sonnet 5.5 fallback) with the `notice-extraction` prompt, and every event is checked against the notice text (quote, dates, times, locations, hosts, body name) before it is stored; rejected events are kept with the reason and never shown. Calendar subjects are tagged by the rule classifier and then by Claude with the topic classification prompt, with a cache keyed on agency and normalized subject so a repeated subject costs no call. Previews are keyed on body and date, not meeting ID; the export attaches them in one query and shows them only while the agenda is not posted. For a notice preview whose project is absent from the posted agenda's item titles, the page says "Not on the posted agenda."

## Alternatives and consequences

- Reading calendars with a model was rejected (decision 6): the layouts are regular, and a deterministic parser with a fixture per format is cheaper and checkable.
- Showing previews only behind the "without a posted agenda" button was rejected (decision 1): the point is to surface planned subjects to readers filtering by topic.
- A separate "co-hosted" agency per combination was rejected in favour of one Other entry with a per-record host; the price is that a co-hosted meeting does not appear under its co-hosts' filters (decision 7).
- Month-only and pending calendar entries are stored but not shown yet.

## Revisit when

- A notice is found through an agency notice page or an environmental review listing rather than a configured comment period.
- Another agency's "future agenda items" section (Lahontan, Fish and Game, Washoe County Planning, the airport district) is worth a format.
- A calendar's "CANCELED" state should cancel the stored meeting, or readers ask to see that an item moved between dates.
