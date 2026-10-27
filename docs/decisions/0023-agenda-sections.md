# 0023: Agenda sections on meeting pages

Status: proposed default, implemented (awaiting maintainer review)
Date: 2026-10-27
Related decisions: [0008 shared topics and meeting filters](0008-shared-topics-and-meeting-filters.md), [0015 deterministic agenda layouts](0015-deterministic-agenda-layouts.md)
Source: maintainer request, using the El Dorado County Board of Supervisors agenda for October 27, 2026

## Context
Agendas group items under headings such as CLOSED SESSION, CONSENT CALENDAR, GENERAL GOVERNMENT - CONSENT ITEMS, and DEPARTMENT MATTERS. Parsers already stored a `section` for most items, but the meeting page showed it only as small text under each title, so a 30-item agenda read as one long list.

The October 27 El Dorado agenda also showed three parser gaps:

- "DEPARTMENT MATTERS (Items in this category may be called at any time)" was not recognized as a heading, because of the lowercase note in parentheses.
- Items 26 and 27 were filed under "Open Forum". The lines between them, such as "1:00 P.M. HEARING", are time allocations, not headings.
- Items 28 and 29, added in an addendum after ADJOURNMENT, were dropped without a warning. The cover page says "Item 28 is hereby added to Closed Session" and "Item 29 is hereby added to the Consent Calendar".

## Decision
**One rule for display, for every agency.** A section is a run of consecutive items with the same `section` value, in agenda order. The meeting page groups items this way in `apps/web/src/lib/agenda-sections.ts`:

- Each named section is a collapsible group. Its header shows the name, the item count, and the topics found in it. Sections start expanded. "Expand all sections" and "Collapse all sections" appear when an agenda has two or more sections.
- Some parsers also list a section's heading as an item, for example "5 Presentations" above 5.1–5.3, or "Consent Agenda". That heading becomes the section header, with its number, instead of repeating as the first item. A heading item that has topics stays in the list.
- A section whose only item is its own heading ("Roll Call", "Adjournment") is shown as a standalone item.
- Items outside any section, such as El Dorado's time-allocated items 23–27, stay standalone between the sections around them. They are not put in an invented "Other" group, because unsectioned items often fall between sections (TRPA's items 1–4 and 9, for example).
- Topic filters hide sections with no matching item, open sections that have matches, and show counts such as "1 of 4 items". A deep link opens the item's section.

**Parsers own the grouping.** Sections are stored per item, as before. The shared outline engine (`outline.py`) gains three rules that any layout with ALL-CAPS headings can use:

- A heading followed by a parenthetical note is a heading. The words before the note must be ALL-CAPS words, so "APN 1318-16-810-060 (Planner: …)" stays item text.
- A standalone time-allocation line ("10:00 A.M. - TIME ALLOCATION", "9:00 A.M. HEARING") ends the current section. Those items keep their "Not before" badge.
- A layout can declare an `addendum` heading. Numbered items after it must continue the agenda's numbering. Each one is placed after the last item of the section with the same name, or before Adjournment if no section matches. An addendum entry that reprints an existing number, such as October 20's "Staff recommending this item be Continued", leaves that item where it is. Any other number rejects the whole agenda (decision 0015), so items are never silently dropped. Both El Dorado layouts enable this.

Across the 97 stored local meetings, only El Dorado's three agendas changed parse results. The October 20 and October 27 agendas are now layout fixtures.

## Cost and limits
There are no model calls and no schema change. Existing meetings keep their old sections until they are reparsed. Reparsing marks items whose section changed as revised, and each reparsed meeting uses database operations (decision 0020).

Section names are whatever each parser stores. TTD's names keep a trailing "Page" from the packet's page column, and SLT files its closed-session items under "Agenda Planning Calendar". Both now show as section headers, and both are parser fixes, not display rules.

## Revisit when
Readers prefer collapsed sections by default, an agency nests sections more than one level deep in a way readers need to see, or an addendum format adds items without continuing the numbering.
