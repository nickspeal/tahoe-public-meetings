# 0040: Private and non-profit groups' meetings, and Word agendas

Status: accepted by the maintainer on 2026-11-09. The groups added, their discovery types, and the Word reader's details are proposed defaults.
Date: 2026-11-09
Related decisions: 0009, 0014, 0027, 0028, 0033

## Context

Coverage so far was public agencies. Several groups that shape South Shore decisions are tourism or business organizations: the Lake Tahoe Visitors Authority (a destination marketing organization), the South Lake Tahoe Tourism Improvement District, and the Tahoe Blue Event Center stakeholder meetings that the Tahoe Douglas Visitors Authority hosts. Some of them post agendas only as Word files, which the worker could not read: Word stores automatic list numbers ("A.", "ii.") in the list definitions rather than in the text, and agenda layouts depend on those labels.

## Decision

The maintainer decided on 2026-11-09 to include private and non-profit groups' meetings when they publish their own dates, and to read DOCX agendas.

- **A group is added only when it publishes its own meeting dates** on a page, feed, or API the worker can read, without getting around a block. A meeting date that only appears on another organization's calendar does not qualify. Each group gets a registry entry and a source review like any agency (decision 0027).
- **Word (.docx) documents are read like PDFs** by file type, never by agency: their text and provenance are stored, the file is not kept, and each automatically numbered paragraph's label is written into the text so agenda layouts can read it.

Added on 2026-11-09 under this decision: the TDVA board, the Tahoe Blue Event Center stakeholder meetings, the LTVA board and its Marketing Services Committee, and the SLTTID board, through three new discovery types registered by format name (`wordpress-media`, `file-links`, `tribe-events`). The Lake Tahoe Unified School District board was added the same day with a `simbli` type. The groups considered and left out are listed in the [source review](../operations/source-review.md).

## Consequences

- Readers see these groups' meetings beside the agencies' meetings, under their own names. Joint workshops of several followed bodies are not collected yet: under decision 0028 they belong under Other with a named host, and Other has no live discovery.
- The Word reader uses only the Python standard library (no LibreOffice in the image). It does not read legacy binary .doc files, headers, footers, footnotes, or text boxes. Its source sections follow hard page breaks, not printed pages.
- No staff report rules were added, so the new groups add no summarization cost. Topic classification and short titles for about six more meetings a month cost well under $0.05 a month.

## Revisit when

A group's source starts blocking the worker, a group the maintainer wants publishes its dates only through someone else's calendar, or legacy .doc agendas appear.
