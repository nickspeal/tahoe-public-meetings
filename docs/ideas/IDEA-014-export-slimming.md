# IDEA-014: Slim the public export and bound stored page text

Status: parked until a storage review threshold is crossed
Stage: Operations

## Problem and audience
Ingestion stores every extracted PDF page in Postgres, and the build-time export `public.json` embeds all of that text. On 2026-10-20 the export was 2.1 MB, 72% page text, while only 9% of stored pages were cited by any agenda item. The maintainer decided to keep this as is because it lasts at least a year under the measured scenarios, and to engineer a fix only when needed.

## Smallest useful version
Stop embedding page text in the export and send only metadata plus cited quotes. Postgres is unchanged, nothing is lost, and the export drops to about 8 KB per meeting. The site build already does not ship page text to browsers, so the visible site does not change.

Further steps if full-packet ingestion for many agencies goes ahead: keep only agenda-level pages and cited pages in Postgres and link the official PDF for the rest; or prune uncited packet pages after about twelve months.

## Evidence and dependencies
[Storage growth estimate](../research/storage-growth-estimate-2026-10-20.md) and `scripts/storage_report.py`. Review thresholds are in the [operations guide](../operations.md#check-database-storage).

## Acceptance criteria
Export size stays well under the Vercel upload limit at the projected meeting volume, citation validation still passes, and no page text is lost from Postgres.

## Open questions
Whether future AI classification or summarization needs uncited packet pages, which argues against pruning text early.

## Links and updates
2026-10-20: parked by the maintainer after reviewing the estimate; monitor and revisit.
