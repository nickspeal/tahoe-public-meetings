# 0030: Publish agendas with irregular numbering

Status: accepted (maintainer direction); the gap limit and note wording are proposed defaults
Date: 2026-11-02
Related decisions: [0015](0015-deterministic-agenda-layouts.md) (amended)

## Context
Decision 0015 published agenda items only when the whole outline validated. One numbering oddity voided an entire agenda: the meeting kept a "review required" warning and listed nothing. In the public export of 2026-10-31 this hid several real agendas:

- Douglas County Board of Commissioners, Nov 5, 2026: the consent calendar lists 9.A to 9.D, then 9.F to 9.U, so E is missing. A description line in item 12 ("6.68 acres ...") also reads like a dotted label.
- Washoe County Board of Commissioners, Oct 27, 2026: items 8 to 20 are printed indented like subitems, then 21 returns to the margin.
- IVGID (Sep 9, Sep 23, Oct 14) and NTPUD (Oct 13 regular and special, Nov 3 special): the agenda and the packet both parsed, but the packet's copy adds page references ("(Pages 4-5)", "– pages 4 - 22") or cuts a long title at another line. That counted as "Multiple agenda PDFs contain differing item sequences", so nothing was published.

The maintainer decided to publish as much of an agenda as possible, oddities included.

## Decision
- **Skipped numbers.** When a level skips one to three labels and none of them is printed anywhere in the agenda, parsing continues. Each skipped label becomes a placeholder item: its number (for example "9.E"), the title exactly "Not Found In Agenda", and no source quote. The meeting gets a warning naming it: "… skips item 9.E; it is listed as “Not Found In Agenda”; review required." This applies to the outline engine, including addenda, the free-form Douglas child labels, and NTCD's parser. Gaps a layout already allows (`maxGap`) are still accepted silently, without placeholders.
- **Numbering that cannot be reconciled.** When indentation contradicts a sequential number, the number wins, so the result is a flatter list instead of no list. A numbered line that goes backwards, repeats, or jumps too far is passed over. Either way, the parse keeps the items it has already read and the ones it can still read. The meeting gets the existing warning text ("… numbering is not sequential near “…”; review required."), only for the first such line. A numbered ending ("18. Adjournment") still ends the agenda. The parse falls back to zero items only when it recognizes fewer than the layout's `minItems` real items. A missing cover, a missing ending, an untitled item, or a title without an exact quote still publishes nothing, as before.
- **Differing copies.** When several agenda PDFs parse to different item lists, the agenda is used over a packet (document ID containing `-agenda-packet`), then the copy with the most items. Copies that differ only in page references, where a long title was cut, or in placeholders count as the same. Copies that really differ get the note "Multiple agenda PDFs contain differing item sequences; items are taken from “Agenda”; review required."
- **Placeholders are excluded explicitly.** Ingestion never searches the page for a placeholder's title, and gives it no citations, summary, or topic tags. Topic classification (rule-based and Claude), staff report mapping (and therefore staff report summaries), and topic evaluation all skip it through one SQL condition (`outline.NOT_PLACEHOLDER_SQL`). The quote check in `parse_layout` skips placeholders by name only; every real item still needs its exact quote. The website lists a placeholder as a muted line (number and title, no disclosure, no source link), and the meeting's topic filter does not count it.

Every note keeps the "review required" wording. Operator tooling and `reparse` still find these meetings, and `reparse` replaces the notes each time it parses a meeting again.

## Alternatives and consequences
Keeping strict validation hid whole agendas over one letter. Guessing a skipped item's title from the packet would put words in the agenda that it does not contain. A placeholder states only what is true: the number is missing. Accepting longer gaps, or a skipped label that is printed elsewhere, would let a numbered list inside an item's description pass as agenda items. So those lines are passed over and noted instead.

Meetings with irregular numbering now publish items while still carrying a review warning. The operator should treat that warning as a request to check the layout, not as a sign that the meeting has no items. A page reference in a packet title no longer blocks publication. When the agenda is used instead of its packet, an item's citations point to the agenda.

## Revisit when
Placeholders appear where the agenda does print the item (the parser misread a label), notes become routine for a layout that should be fixed instead, or readers find the muted line confusing.
