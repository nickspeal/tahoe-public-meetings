# 0022: The timeline opens on posted agendas and expands on request
Status: accepted
Date: 2026-10-27
Related decisions: [0007 meeting timeline](0007-meeting-timeline-and-filters.md), [0012 forecast meetings](0012-forecast-meetings-from-schedule-patterns.md)
Source: maintainer request

## Context
The timeline showed every upcoming meeting, including confirmed meetings with no agenda yet. Earlier meetings sat in one collapsed list that opened all at once (122 meetings by late October 2026). A **Dates** dropdown (Upcoming / All dates) and a **Show expected meetings** checkbox each controlled part of what was visible.

## Decision
- **Default view.** The timeline shows today's and upcoming meetings whose agenda is posted. This includes agendas with listed items, agendas captured but not parsed, agendas on the agency site, and canceled meetings. Earlier meetings start hidden.
- **Upcoming meetings without an agenda share one reveal.** One button after the upcoming list shows or hides confirmed meetings whose agenda is not posted yet. The same button covers expected meetings (decision 0012). Its hint says how many of them are expected. The open state persists across filter changes and is bookmarkable as `?upcoming=all`. The old `?expected=1` bookmark still opens it.
- **Earlier meetings open backward in steps of ten.** A button above the Today marker shows the ten most recent earlier meetings that match the current filters. Each further click shows ten more, and the button disappears when none remain. Any topic or agency change returns to the default (no earlier meetings shown). This state is not kept in the URL.
- **The Dates filter is removed.** The two buttons now control which dates are visible, and a `?view=all` bookmark is ignored.

## Alternatives and consequences
Keeping expected meetings as a separate checkbox was rejected: both groups answer the same question (what is coming that has no agenda yet), and two controls made that harder to see. Resetting the upcoming reveal on filter changes was also considered. It stays open instead, because it replaces a persistent checkbox. Earlier meetings reset because "ten more" only makes sense for one set of filters.

A meeting today without a posted agenda is hidden by default like any other. Without JavaScript, earlier meetings and meetings without an agenda cannot be revealed.

## Revisit when
Readers miss meetings that have no agenda yet, or history browsing needs deep links, such as a bookmarkable earlier count or a jump to a month.
