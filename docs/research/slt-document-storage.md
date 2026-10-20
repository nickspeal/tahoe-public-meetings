# South Lake Tahoe meeting document storage research

Research date: 2026-10-19

## Finding

South Lake Tahoe publishes its meeting archive through Granicus. The City's meeting page links to Granicus, and the publisher archive describes date-ordered meetings with separate agenda, minutes, video, and agenda-packet links. Search-indexed publisher content confirms that packet content can include an agenda plus separately named staff reports and supporting PDFs. This makes the public archive useful for discovery and source links, but does not establish that any individual file URL is permanent.

For the MVP, keeping extracted text and a source URL is a reasonable low-storage approach, provided ingestion records a fetch timestamp and checks source links for availability. If the project needs faithful historical reproduction, or resilience to changed/removed vendor objects, keep a local copy of the source PDF (or a content-addressed copy) as well. A text-only archive cannot reproduce the original page layout, scans, charts, exhibits, or later corrections.

## Sources inspected

1. [City of South Lake Tahoe: View Agendas and Agenda Packets](https://cityofslt.gov/84/Watch-City-CouncilCommission-Meetings) — page title is “View Agendas and Agenda Packets”; its visible page links out to `slt.granicus.com` for hosted content and provides a City Council schedule, master calendar, and planning calendar.
2. [Granicus publisher archive](https://slt.granicus.com/ViewPublisher.php?view_id=6) — indexed archive says meetings are arranged by date, newest first; offers Agenda, Minutes, Video, and Agenda Packet links; archive years shown are 2007–2026. This is a search-indexed view, because direct publisher retrieval exceeded the web reader's 4 MiB content cap.
3. [Example PDF, S3 host](https://granicus_production_attachments.s3.amazonaws.com/slt/fb52e3c531af6893429f30df879554620.pdf) — a 7-page City Council meeting agenda for October 6, 2026. Downloaded 137,657 bytes; response was `application/pdf`.
4. [Example PDF, CloudFront host](https://d3n9y02raazwpg.cloudfront.net/slt/56c75fd5-9a53-11f1-bb61-005056a89546-5c7c4fe4-84cb-4317-b8ef-0c329738dcd2-1790799016.pdf) — a 505-page, 38,079,276-byte PDF for the same October 6, 2026 City Council agenda. The cover repeats the agenda title and date; the much larger file appears to be the full agenda packet with supporting material. Response was `application/pdf`.
5. [Example Granicus minutes/meeting document](https://slt.granicus.com/MinutesViewer.php?clip_id=2231&doc_id=dce86518-c996-11f1-89ef-005056a84798&view_id=6) — search-indexed as the May 19, 2026 City Council meeting; the content describes the meeting agenda and public packet availability. This supports the archive's meeting/document relationship, but does not establish that either user-supplied PDF is this meeting's attachment.
6. [Example agenda packet PDF indexed by search](https://granicus_production_attachments.s3.amazonaws.com/slt/445e52546627ac0e29ddcb871cc475660.pdf) — another SLT S3 attachment indexed with agenda text and linked exhibit filenames; demonstrates that the S3 host carries packet PDFs, not the identity of the supplied samples.

## Metadata and access limitations

| Supplied URL | Observed host / path | Actual bytes downloaded | PDF pages | Content type | Document date/type |
|---|---|---:|---:|---|---|
| `granicus_production_attachments.s3.amazonaws.com/slt/fb52e3c531af6893429f30df879554620.pdf` | Amazon S3 attachment host; opaque 32-hex object key | 137,657 bytes received; HTTP `Content-Length: 137657` | 7 | `application/pdf` | City Council agenda, October 6, 2026; PDF metadata title “Agenda,” created Sep 30, 2026 |
| `d3n9y02raazwpg.cloudfront.net/slt/56c75fd5-9a53-11f1-bb61-005056a89546-5c7c4fe4-84cb-4317-b8ef-0c329738dcd2-1790799016.pdf` | CloudFront distribution; `/slt/` path and UUID-like components | 38,079,276 bytes received; HTTP `Content-Length: 38079276` | 505 | `application/pdf` | Same City Council meeting agenda date; appears to be the agenda packet. PDF metadata title “Agenda,” created Sep 30, 2026 |

The samples point to two distinct representations for the same meeting: a compact agenda (about 134 KiB) and a 505-page packet (about 36.3 MiB). This is a large per-meeting size difference, so a storage policy could retain the small agenda PDF while keeping extracted text and a link for the full packet, or preserve both if reprocessing matters. The values above came from successful downloads plus HTTP response headers and `pdfinfo`; direct reading in the web reader had failed, and the initial non-escalated shell request was blocked by sandbox DNS.

## URL and archival implications

- The first sample has an opaque, hash-like S3 object key; the second has a UUID-like CloudFront path. Neither filename embeds a human-readable meeting date or agenda item.
- The official City page routes visitors to Granicus, and the publisher index exposes separate meeting records and document choices. A sensible discovery workflow is therefore City page → Granicus meeting entry → Agenda/Agenda Packet link → attachment URL. Both samples' extracted cover text identifies the October 6, 2026 City Council meeting, and the dates match. The CloudFront file's packet role is inferred from its 505-page size and the separate compact agenda PDF; its cover and PDF metadata still label it “Agenda.”
- These are direct object/CDN URLs, not a City-controlled archive contract. A stable-looking object key can help the URL continue to work, but it does not promise continued retention, unchanged bytes, or indefinite hosting. Treat the link as provenance, not as the sole archival copy.
- At approximately 300 pages per week, extracted text and metadata should remain small compared with PDFs. The storage cost is likely not the main factor at MVP scale; retaining PDFs adds fetch, indexing, and backup work but makes later audit and reprocessing possible. This is a design inference, based on the measured difference between these two examples.

