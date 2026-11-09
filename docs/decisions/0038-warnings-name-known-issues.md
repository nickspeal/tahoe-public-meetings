# 0038: Meeting warnings name a known, specific issue

Status: accepted by the maintainer on 2026-11-09 (the principle and the listed changes); the "not an agenda" title rule and the retired-warning list are proposed defaults.
Date: 2026-11-09
Related decisions: 0028, 0032, 0033

## Context

A review of every meeting with warnings in the 2026-11-09 export found 69 warning strings on 51 of 227 meetings. None of the 26 that residents could see described a known problem they could act on, and most of the hidden ones were blanket caveats, stale state, or parser failures on documents that were never agendas:

- "A source PDF changed; affected agenda items await reprocessing." was added whenever a document set changed and the capture had no items, including on meetings with no items at all, so nothing awaited reprocessing. `reparse` then selected those meetings on every run but never cleared the note, because it wrote only when the parse failure text was new. An agency with no layout was skipped outright.
- Every WordPress post and Granicus publisher row carried "… agenda items require source review." A TTD committee post titled "… – October 5, 2026- CANCELED" was published as an ordinary meeting, because the WordPress discovery path never set a status.
- Planned meetings stored "Subject to change.", which the website already derives from the export's `planned` flag.
- Canceled meetings, and meetings whose only document was minutes or a supplement, recorded "No complete … agenda recognized" or "… agenda items were not found" (with a doubled "agenda agenda").
- The outline engine noted "numbering is not sequential" for a Washoe agenda whose item 21 only returned to the margin, and for a wrapped Douglas description line beginning "6.68 acres".

The maintainer's rule: warnings appear only for known, specific issues; there are no blanket "check the source" caveats, and production state comes only from the live pipeline.

## Decision

- **No blanket caveats are written.** Discovery candidates start with no warnings; a failed parse records its own specific warning at capture. Planned meetings store none. The export's `coverageNote` drops "Check each agency's source and meeting warnings for gaps."
- **A WordPress post titled as canceled is a canceled meeting** (`discovery.CANCELED_TITLE`).
- **"Await reprocessing" means a published item lost its summary.** `ingest_bundle` adds it only when a source change voided the summary of at least one active item, and only if it is not already there.
- **`reparse` always settles its own notes.** A failed parse rewrites the meeting's parse warnings whenever they differ from what is stored, including when the failure is unchanged, so a stale note is dropped. A meeting of an agency with no layout loses only the "await reprocessing" note (reported under `cleared`). The stale-note pattern also matches "… require source review."
- **No parse failure for a canceled meeting or for documents that are not agendas.** Capture and `reparse` record no parse failure when the meeting is canceled; items are still stored if its agenda parses. `parse_agenda` reports nothing when every captured document is named (title or ID) as minutes, a synopsis, a supplement, or a notice of cancellation. Otherwise its message names the agency by its registry name.
- **Indentation alone is not irregular numbering** (amends 0032). When a lenient reading accepts the next expected number with nothing skipped, the number wins without a note. A numbered line at or past the open item's title column on the same page is that item's description and is passed over without a note. A stray label at the margin, a skipped label, and a forced numbered ending are still noted. Layout failure messages no longer repeat "agenda" after the layout's label.
- **One-time cleanup.** `tahoe-ingest clean-retired-warnings` (dry run unless `--apply`, idempotent, one read and one write) removes the warnings no code writes any more (`retired_warnings.RETIRED`) and the "await reprocessing" note from meetings with no active item. A test fails if any module still contains a retired string.

## Alternatives and consequences

Filtering retired strings at export would cost no database writes but would leave the stored rows wrong and the filter to maintain; the one-statement cleanup was preferred. A meeting wrongly marked canceled would hide a parse failure, but a successful parse is still published. A real out-of-sequence line that differs only by indentation is no longer noted; it is still published in sequence. The website keeps its filters for the retired texts until the cleanup has run in production.

## Revisit when

A canceled meeting turns out to have an agenda that silently failed to parse, a document named "minutes" or "supplement" turns out to be the agenda, or a new warning reaches the export that names no specific issue.
