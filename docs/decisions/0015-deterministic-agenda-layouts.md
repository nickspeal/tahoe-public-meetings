# 0015: Deterministic agenda layouts for every covered agency

Status: accepted
Date: 2026-10-20

## Context

After coverage expanded to the configured watchlist (decisions 0009 and 0014), 105 of 123 stored meetings showed no agenda items. Only TTD, South Lake Tahoe City Council, and Truckee Town Council had agenda parsers. Other meetings were logistics-only notices, or their agenda text was stored but never split into items. The maintainer asked for agency-specific parsers and asked to avoid the cost of an AI-based parser if deterministic parsers could be shown to work.

## Decision

Extract agenda items deterministically. The three original parsers stay as they are. Every other agency that has a parsable agenda gets a declarative layout in `workers/ingestion/tahoe_ingest/agenda_layouts.py`. Each layout records the agency's cover text, outline markers per level (numbers, letters, Roman numerals, Legistar file numbers, compound labels such as `8.E.2`), its ending marker, and the page furniture to ignore. One shared outline engine (`outline.py`) reads these layouts. NTCD's centered-number table and South Shore TMA's bullet list have small custom parsers (`agenda_custom.py`).

A parse publishes items only when the whole agenda validates:

- the cover matches;
- every level counts in sequence within its parent;
- the agenda reaches its ending marker; and
- every item has an exact quote from one stored page.

A skipped number is accepted only where the layout allows it and no line in the agenda starts with the missing number. Everything else keeps the meeting's text and official links, records the reason, and publishes no guessed items.

Each layout is tested against reviewed text from a real official agenda in `tests/fixtures/agenda-layouts/`, including documents that must be rejected (a malformed TNT/TMA agenda and an IVGID post-meeting synopsis). `tahoe-ingest reparse` re-runs parsers over stored text without fetching sources. Use it after adding or changing a layout.

The public meeting page now explains a missing item list. It distinguishes four cases: the agenda is posted but its items are not listed here; the agenda is not posted yet; the agenda exists only on the agency website (blocked or not captured); and the meeting was canceled.

## Cost and limits

No model calls, so there is no recurring cost. Each new agency or agenda redesign needs a layout and a fixture. Layout changes are reviewed by reading the extracted items, not just their count. During review, one TRPA document passed by parsing a committee agenda that the packet bundles ahead of the Governing Board agenda; the layout now anchors on the board heading.

Titles are the agency's own text. Some long Legistar and Douglas County titles are cut after three lines, and NTCD titles that wrap with no textual cue can be cut short. Summaries remain exact agenda excerpts.

## Revisit when

A layout breaks repeatedly, an agency publishes agendas with no stable structure, or the number of covered agencies makes per-agency layouts more expensive to maintain than a model-based extractor within the budget. That extractor would be Claude per decision 0011, and topic classification is a separate system.

## Update 2026-10-28: layouts are configuration
Layouts moved from code to [`config/agenda-layouts.json`](../../config/agenda-layouts.json), keyed by format name, and each agency entry in `packages/domain/agencies.json` names the layouts its agendas use (`agenda.layouts`). A body whose agendas share an existing format, such as the TTD Regional Transit and Capital Programs Committee, needs only a registry entry. The three original parsers and the NTCD and South Shore TMA parsers are kept as written in `agenda_custom.py`; a layout selects one by name and declares its cover in the same file as every other layout. The TRPA agenda-before-packet rule is the registry flag `agenda.agendaBeforePacket`. Parsing results are unchanged; the [configuration guide](../../config/README.md#agenda-layouts) lists the layout keys.
