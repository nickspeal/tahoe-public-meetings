# Document retention options

Status: source-first, minimal PDF storage accepted on 2026-10-19; see [decision 0004](../decisions/0004-source-first-storage.md). Existing local archives remain unchanged.

## Separate fetching, keeping, and serving

The pipeline must obtain PDF bytes to extract and analyze them. That does not require keeping them permanently or serving our copies to visitors. A source-first website can link to the official PDF while retaining a private processing copy, a temporary cache, or no original after extraction.

| Policy | Durable records | Benefits | Limitations |
| --- | --- | --- | --- |
| Text only | Page-separated extracted text, metadata, hashes, summaries, source URLs | Small archive; links remain with agency | Cannot recover exact source bytes after removal/replacement; maps/charts/layout may be lost |
| Rolling PDF cache | Text records plus recent PDFs, e.g. 90 days | Supports retries and near-term corrections with bounded growth | Old originals can disappear after expiry |
| Selective archive | Text records plus explicitly retained PDF versions | Preserve cited/revised/visually important evidence | Policy and operational complexity; cannot retroactively recover an already deleted version |
| Complete archive | Every distinct PDF version and derived records | Best reproducibility and recovery from link rot | Accumulating storage; retention and backup costs |

All four policies can default public links to official sources rather than proxying PDFs through our application.

## Suggested source-first ingestion contract

1. Discover a document through its official meeting page. Record both that page and the document URL.
2. Fetch to temporary storage; record resolved URL, fetch time, byte count, SHA-256, content type, and HTTP validators when present. An ETag is a cache validator, not a substitute for our own content hash.
3. Extract page-separated text, record page count and extraction warnings, and classify whether visual/OCR content needs retention.
4. Persist the extracted text and structured output with the source hash and processing version. Apply the chosen PDF retention policy only after durable writes succeed.
5. Link citations to the official document and page where supported. Keep a visible source-version/fetch date; a current URL may later resolve to different bytes.
6. Recheck active meetings with conditional requests where supported. A change at the same URL creates a new document version. De-duplicate identical bytes across different URLs while preserving provenance.
7. If an old source disappears, keep the summary/text and mark the original unavailable. Link to a stored copy only if retained; never imply a hash can reconstruct the missing original.

Large files should stream to bounded scratch storage rather than load fully into memory. Processing concurrency and temporary disk/memory limits are independent of long-term archive size. Do not refetch a PDF for every visitor or every summary request.

## Historical evaluation before decision 0004

Use actual TTD archive and SLT sample measurements before choosing retention. A 90-day cache is a candidate, not a commitment. If complete retention costs only a small amount, its simpler audit trail may justify it; if deep historical backfill dominates, rolling/selective retention becomes more attractive. Preserve original evidence for visual material when text alone is insufficient.

Original-host links reduce our serving bandwidth regardless of whether we privately retain PDFs. They trade away control of availability. Keeping text permits many later text-based analyses, but not re-running a better extractor or verifying lost visual details without the PDF.

## Measured evidence and earlier recommendation

Two Luna agents measured the [TTD archive](../research/ttd-storage-measurements.md) and [SLT samples](../research/slt-document-storage.md). TTD: 308 PDFs, 34,648 pages, 1.779 GB; matching extracted text 116.7 MB. Median meeting-post group is 4.18 MB; mean is 6.69 MB. SLT's October 6, 2026 examples are a 0.138 MB/7-page agenda and a 38.08 MB/505-page apparent full packet.

At 15,600 pages/year, the measured TTD byte/page ratio yields 0.80 GB/year; the SLT packet ratio yields 1.18 GB/year. At 260 meetings/year, the TTD group mean yields 1.74 GB/year. A deliberately heavier scenario of 260 meetings each with a packet like the SLT example yields 9.90 GB/year and 131,300 pages/year—far above the original page assumption. These are scenarios, not population forecasts; Truckee has not been sampled.

Earlier recommendation (superseded by maintainer choice): retain originals for audit/reprocessing. Accepted direction: link to official PDFs, keep page-separated extracted text and provenance, and discard temporary PDFs after durable extraction/processing succeeds. Retaining originals does not require serving them to visitors. Make retention configurable so a rolling cache or text-only installation remains possible. Avoid archiving a full packet plus every component attachment by default when they contain the same material; preserve document/item links and determine coverage before skipping anything. Exact hashing only removes byte-identical duplicates, not overlap between a combined packet and its constituent reports.

For cost context, R2 Standard is $0.015/GB-month with 10 GB-month free. An archive held at 100 GB costs about $1.35/month for storage alone; at 1 TB (1,000 GB), about $14.85/month. Operations and additional backup copies are separate. Prices checked 2026-10-19: [R2 pricing](https://developers.cloudflare.com/r2/pricing/). Storage at MVP scale does not justify sacrificing source recoverability solely to maintain a strictly zero-dollar bill. Long historical backfills or many more agencies should trigger a fresh retention review.

## Accepted first-version policy

Do not build a permanent PDF archive or rolling PDF backup initially. Fetch PDFs into bounded temporary storage, persist extracted text and provenance, and clean up temporary files after successful processing; failed jobs may retry within a bounded temporary retention window. Retain structured data, human commentary, and corrections independently. Monitor broken links and changed source hashes for supported meetings. Revisit PDF backups if official sources prove unreliable; later backup cannot recover an original already removed upstream. This policy reduces PDF storage, not protection of unique application data.
