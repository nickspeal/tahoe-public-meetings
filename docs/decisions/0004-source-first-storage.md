# 0004: Minimize original-file storage

Status: accepted
Date: 2026-10-19

## Decision
For initial versions, avoid a permanent copy of official PDFs. Fetch temporarily for extraction and analysis; retain page-separated text, document URL and meeting-page URL, fetch timestamp, content hash, page metadata, and generated records. Readers follow official source links. Reconsider PDF backup if link availability or source-version reliability proves inadequate.

## Consequences
Source PDFs may become unavailable or change after analysis. A hash identifies the analyzed version but cannot reconstruct it. Text alone does not preserve visual exhibits or permit improved PDF extraction after source removal. Track availability and revisions, disclose unavailable originals, and preserve application records and the maintainer's unique contributions. Existing local TTD data is not deleted by this decision.

## Open implementation choices
Set temporary-file cleanup/retry bounds and the cadence of link checks during implementation. Database choice remains proposed; the maintainer has no SQL/NoSQL preference.
