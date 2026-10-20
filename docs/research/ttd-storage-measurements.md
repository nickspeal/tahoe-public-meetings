# TTD PDF and extraction storage measurements

Measured read-only on 2026-10-19 from the earlier TTD project's local data archive (outside this repository) to replace the assumed PDF-bytes-per-page range in the MVP sizing. No project scripts were run and no files in the source archive were changed. The scan counted file metadata and PDF page counts; it did not read or reproduce meeting text.

## Results

| Measure | Observed |
| --- | ---: |
| PDF files | 308 |
| PDF bytes | 1,779,171,762 bytes = 1,779.2 MB = 1,696.8 MiB (about 1.78 GB) |
| PDF pages (`pdfinfo`) | 34,648 total; all 308 files returned a page count |
| Individual PDF size | median 3.79 MB; p90 11.86 MB; maximum 63.54 MB |
| Individual PDF pages | median 90.5; p90 246.6; maximum 580 |
| Extracted Markdown files | 308, basename-matched to all 308 PDFs |
| Extracted Markdown bytes | 116,670,780 bytes = 116.7 MB = 111.3 MiB |
| Extracted text / PDF bytes | 6.56% across the matched corpus |

I grouped files by the date, meeting type, and WordPress post ID encoded in each PDF filename. This yields 266 post groups. Grouped totals have a median of 4.18 MB and a p90 of 13.03 MB; the largest group is 69.08 MB. Grouped page counts have a median of 104 pages, a p90 of 265 pages, and a maximum of 605 pages. Twenty-nine groups contain more than one PDF, with at most seven files in one group. These groups represent one meeting post's linked documents, which may include separate attachments or packet parts; they are not necessarily one consolidated agenda packet.

There are two pairs of byte-identical PDFs in the archive, together adding 5.8 MB beyond one copy of each. Twelve filenames contain terms such as “revised,” “addendum,” or “supplement”; these names signal that versions or supplemental material occur, but do not establish a complete revision history. One PDF has no matching local meeting JSON record. The meeting JSON records include source URLs and local PDF paths, while three document entries have no local PDF path. The 308 matching Markdown files are `pdftotext` output; this measurement does not establish OCR coverage for scanned pages.

## Annual sizing implications

The workload projections answer different questions and should remain separate:

- **260 meetings/year, observed mean:** 1,779,171,762 bytes / 266 post groups × 260 gives approximately **1.74 GB/year**. The mean is more useful than the median for estimating aggregate storage because large packets contribute to the total. This still assumes future meetings resemble the sampled groups.
- **260 meetings/year, median-size scenario:** applying the observed median post-group size (4.18 MB) gives about **1.09 GB/year of PDF growth**. At the observed extracted-text/PDF ratio, retaining matching text adds about **0.071 GB/year**, for about **1.16 GB/year together**.
- **300 pages/week:** this is **15,600 pages/year**. The archive averaged 51,350 PDF bytes/page, giving about **0.80 GB/year of PDF growth** at that page volume. Matching extracted text at the observed corpus ratio adds about **0.053 GB/year**, for about **0.85 GB/year together**.

These projections do not describe the same workload. The historical sample's median group has 104 pages, while 300 pages across five weekly meetings implies 60 pages per meeting. The page-based estimate is the closer match to the stated MVP page volume; the meeting-based estimate shows the effect of the observed typical TTD post size. Both assume future material resembles the measured TTD archive and omit backups, replicas, and any files larger than the observed distribution.

## What this says about keeping originals

Original PDFs dominate bytes, but the observed growth is roughly 0.8 GB/year at the stated page volume, or 1.74 GB/year at 260 meetings using the observed mean group size. Extracted text is much smaller at roughly 0.05–0.07 GB/year. This difference alone does not compel discarding originals under a <$20/month ceiling; compare it with the chosen provider's included storage and current account usage. Keeping originals alongside source URLs preserves an auditable copy for page citations and protects against upstream links changing or disappearing. Linking to the agency and retaining extracted text is a workable lower-storage option, but leaves future readers dependent on the agency continuing to serve the same file. The source URL is present in document metadata, so a hybrid can retain both the agency link and the archived original.

The sample is only TTD, not a representative sample of City of South Lake Tahoe or Truckee documents. It is also incomplete: the local TTD instructions describe gaps in 2014–2019, and the on-disk archive contains only sparse PDFs in those years. The archive is strongest in 2020–2026 and older coverage is uneven, so the distribution is useful for order-of-magnitude planning rather than a guarantee for future packets. A few documents exceed 60 MB, so transfer and processing bursts deserve separate attention from average storage growth.

## Method

The scan recursively enumerated `data/pdfs/**/*.pdf` and `data/text/**/*.md`; counted bytes with filesystem metadata; matched PDF and Markdown by filename stem; extracted `Pages:` from `/opt/homebrew/bin/pdfinfo` output for each PDF; and computed medians and inclusive p90 quantiles. Post groups came from the date/type/post-ID filename fields. Exact duplicate content was checked with SHA-256. MB and GB are decimal units; MiB and GiB are binary units. The scan did not invoke `sync.py`, `pdftotext`, or any other existing project script.
