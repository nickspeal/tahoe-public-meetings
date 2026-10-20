# Existing TTD implementation: initial inspection

Inspected read-only on 2026-10-19. No existing code was modified or executed. This is a source inspection, not validation against the live agency website.

- `TTD Agendas.command` starts `serve.py` on localhost and opens Firefox. It is a launcher, not the ingestion implementation.
- `sync.py` contains TTD WordPress discovery, PDF download, page-separated text extraction using `pdftotext`, a JSONL manifest, and SQLite FTS5 indexing. It documents incremental processing and includes delayed requests and retries. These are promising adapter and storage foundations.
- `items.py` splits packets into agenda items and links embedded minutes to their recorded meeting date. Parsing conventions are TTD-specific and need fixture checks before generalizing.
- `brief.py` extracts monthly groups and renders HTML. Its documentation says analysis JSON is written by a Claude-driven workflow, so autonomous model invocation, validation, and scheduling still need implementation.
- `serve.py` is a local file/notes server with localhost restrictions. Its notes and link-check endpoints should not be exposed as a production public backend without redesign.

## Proposed reuse

Prefer adapting the Python ingestion and extraction utilities, preserving page citations and existing source knowledge. Use the current brief UI as a reference, but reorient the product around topics and alerts. Keep personal notes, priority preferences, historical analyses, and private material outside the new public app.

## Next checks

Inspect full implementations and relevant repository instructions before changing or copying code. Test stable IDs, agenda revisions, parser quality, document coverage, and licensing/ownership assumptions. Audit City of South Lake Tahoe and Town of Truckee independently; do not assume their publishing formats match TTD.
