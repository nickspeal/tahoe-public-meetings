# Local meeting source coverage

Checked 2026-10-20. The machine-readable bundle is [`tests/fixtures/meetings.json`](../../tests/fixtures/meetings.json); curated topic examples are in [`tests/fixtures/topic-expectations.json`](../../tests/fixtures/topic-expectations.json). It contains 19 actual meetings: 6 Tahoe Transportation District (TTD), 6 South Lake Tahoe (SLT), and 7 Truckee Town Council records. The bundle stores complete agenda text for 12 posted agendas (243 parsed entries) and logistics/status records for seven events where no agenda text is available. It does not represent a complete agency archive or live feed.

## Meeting inventory

Dates, times, event status, and native IDs below are taken from the official meeting page, event page, or agenda cover. Pacific time is `America/Los_Angeles`. A blank time is intentionally unknown.

| Agency | Native ID | Date / time | Official source | Coverage |
|---|---:|---|---|---|
| TTD | 6049 | May 6, 2026 · 3:00 p.m. | [Board meeting](https://tahoetransportation.org/meeting/ttd-board-of-directors-meeting-may-6-2026-2/) | Complete agenda, PDF pp. 1–3; 15 ordered entries |
| TTD | 7144 | June 3, 2026 · 3:00 p.m. | [Board meeting](https://tahoetransportation.org/meeting/ttd-c-board-of-directors-meeting/) | Complete agenda, PDF pp. 1–3; 18 ordered entries |
| TTD | 6941 | August 5, 2026 · 3:00 p.m. | [Board/commission meeting](https://tahoetransportation.org/meeting/ttd-board-of-directors-commission-meeting/) | Complete agenda, PDF pp. 1–3; 26 ordered entries |
| TTD | 7070 | September 16, 2026 · 10:00 a.m. | [Board meeting and strategic session](https://tahoetransportation.org/meeting/ttd-board-of-directors-meeting-and-strategic-session/) | Complete agenda, PDF pp. 1–3; 12 ordered entries |
| TTD | 7110 | October 7, 2026 · 3:00 p.m. | [Board meeting](https://tahoetransportation.org/meeting/ttd-board-of-directors-meeting-october-7-2026/) | Complete agenda, PDF pp. 1–3; 16 ordered entries |
| TTD | 7172 | November 4, 2026 · time not announced | [Board meeting listing](https://tahoetransportation.org/meeting/ttd-board-of-directors-meeting-november-4-2026/) | Listing only; no agenda or location published when checked |
| SLT | 2295 | August 11, 2026 · 9:00 a.m. | [Granicus agenda](https://slt.granicus.com/AgendaViewer.php?view_id=6&clip_id=2295) | Complete compact agenda, 9 pages; 38 ordered entries |
| SLT | 2303 | August 25, 2026 · 9:00 a.m. | [Granicus agenda](https://slt.granicus.com/AgendaViewer.php?view_id=6&clip_id=2303) | Complete budget workshop agenda, 5 pages; 18 ordered entries |
| SLT | 2307 | September 8, 2026 · 9:00 a.m. | [Granicus agenda](https://slt.granicus.com/AgendaViewer.php?view_id=6&clip_id=2307) | Complete compact agenda, 8 pages; 34 ordered entries |
| SLT | 2330 | September 22, 2026 · 5:30 p.m. | [Granicus agenda](https://slt.granicus.com/AgendaViewer.php?view_id=6&clip_id=2330) | Complete compact agenda, 5 pages; 24 ordered entries |
| SLT | 2339 | October 6, 2026 · 5:30 p.m. | [Granicus agenda](https://slt.granicus.com/AgendaViewer.php?view_id=6&clip_id=2339) | Complete compact agenda, 7 pages; 28 ordered entries |
| SLT | 2351 | October 13, 2026 · 10:00 a.m. | [Granicus agenda](https://slt.granicus.com/AgendaViewer.php?view_id=6&clip_id=2351) | Complete special-meeting agenda, 3 pages; 7 ordered entries |
| Truckee | 1770 | September 8, 2026 · 5:00–10:00 p.m. | [Town event](https://townoftruckee.gov/Calendar.aspx?EID=1770&calType=0&day=8&month=9&year=2026) | Event logistics only; Town page links Municode HTML agenda not yet captured as source-page text |
| Truckee | 1795 | September 22, 2026 · 5:00–10:00 p.m. | [Town event](https://townoftruckee.gov/Calendar.aspx?EID=1795&calType=0&day=22&month=9&year=2026) | Event logistics only; Town page links Municode HTML agenda not yet captured as source-page text |
| Truckee | 1771 | October 13, 2026 · 5:00–10:00 p.m. | [Canceled Town event](https://townoftruckee.gov/Calendar.aspx?EID=1771&calType=0&day=13&month=10&year=2026) | Canceled; cancellation notice linked; replacement IDs 1894 and 1895 remain separate events |
| Truckee | 1895 | October 13, 2026 · 5:30 p.m. | [Joint Council / Hospital District event](https://townoftruckee.gov/Calendar.aspx?EID=1895&calType=0&day=13&month=10&year=2026) | Complete agenda, packet pp. 1–3; 7 ordered entries; joint meeting |
| Truckee | 1796 | October 27, 2026 · 5:00–10:00 p.m. | [Town event](https://townoftruckee.gov/Calendar.aspx?EID=1796&calType=0&day=27&month=10&year=2026) | Event logistics only; no agenda document linked when checked |
| Truckee | 1772 | November 10, 2026 · 5:00–10:00 p.m. | [Town event](https://townoftruckee.gov/Calendar.aspx?EID=1772&calType=0&day=10&month=11&year=2026) | Upcoming event logistics only; no agenda document linked at check time |
| Truckee | 1797 | November 24, 2026 · 5:00–10:00 p.m. | [Town event](https://townoftruckee.gov/Calendar.aspx?EID=1797&calType=0&day=24&month=11&year=2026) | Upcoming event logistics only; no agenda document linked at check time |

The Truckee event IDs are independent source records, including the canceled EID 1771 and later EID 1895 joint replacement. November IDs 1772 and 1797 were discovered in the official November calendar index; their calendar query parameters can be inconsistent, so fixture dates follow the event's embedded calendar start dates. Logistics-only entries have empty document, page, and item arrays plus an explicit warning. They are not treated as complete agendas and cannot withdraw earlier agenda text.

## Document provenance

TTD records reuse extracted page text and PDFs already present in the maintainer's local TTD archive, read-only. The archive manifest supplies the original URL, byte count, SHA-256, and page count. It does not record original download timestamps, so `fetched_at` stays null; local modification times are not used as a substitute. Only pages 1–3 of the original packets are stored in this fixture. No TTD archive files were changed or copied into the repository.

| Agency / ID | Official PDF URL | Pages / bytes | SHA-256 | Fetch timestamp |
|---|---|---:|---|---|
| TTD 6049 | [Agenda packet](https://tahoetransportation.org/wp-content/uploads/2026/05/2026-5-6-TTD-Board-Agenda-Packet-1.pdf) | 383 / 61,494,423 | `e9bf6755cd409e314135ac22f50e63e2b10b2ac4fb469d07b111c8d1039c1cf9` | Not recorded by archive |
| TTD 7144 | [Agenda packet](https://tahoetransportation.org/wp-content/uploads/2026/10/2026-6-3-TTD-Board-Mtg-Agenda-Packet.pdf) | 170 / 12,175,414 | `0dd64706f501a389439f19367bd5a44daba1f31a00c467a25df18f47e6aaa145` | Not recorded by archive |
| TTD 6941 | [Agenda packet](https://tahoetransportation.org/wp-content/uploads/2026/09/2026-8-5-TTD-C-Board-Agenda-Packet.pdf) | 540 / 35,799,488 | `3bc4e3dc255a0a66d90f0829a27a9c786a6bd5b2695b295debf0b03fdb4a7fff` | Not recorded by archive |
| TTD 7070 | [Agenda packet](https://tahoetransportation.org/wp-content/uploads/2026/09/2026-9-16-TTD-C-Board-Agenda-Packet.pdf) | 166 / 10,514,032 | `faae65dd549fea09efea7ec15d7583df4704cd88188796a533cfdf46428f3382` | Not recorded by archive |
| TTD 7110 | [Agenda packet](https://tahoetransportation.org/wp-content/uploads/2026/10/2026-10-7-TTD-Board-Meeting-Agenda-Packet.pdf) | 197 / 16,523,859 | `4f7285062fd10432a9d33e6ee30ba4fe4683a2e0113f93295ab4c9afdd1061ad` | Not recorded by archive |
| SLT 2295 | [Compact agenda PDF](https://s3.amazonaws.com/granicus_production_attachments/slt/a47d2ac23bdc23578199fc989a57ca350.pdf) | 9 / 170,565 | `c991b0fc8cf887965cc8cd512c0ca7569a3c0d0adafab8d03ec624b45eb30352` | 2026-10-20T03:55:48.917931Z |
| SLT 2303 | [Compact agenda PDF](https://s3.amazonaws.com/granicus_production_attachments/slt/bb95405b7bf211ebc6648b31f0b03e440.pdf) | 5 / 114,678 | `c0c06d33914f095802f552bb0d806bb99afa607956b9eaadb2ca90acda3ec7e6` | 2026-10-20T03:55:48.310963Z |
| SLT 2307 | [Compact agenda PDF](https://s3.amazonaws.com/granicus_production_attachments/slt/86816bc77e7f211d715cab5a0233e20b0.pdf) | 8 / 167,030 | `71889e666c61d516d0bb1849e38b12211f31a4de846f123e8c0967c3c5c84ba` | 2026-10-20T03:55:47.716395Z |
| SLT 2330 | [Compact agenda PDF](https://s3.amazonaws.com/granicus_production_attachments/slt/556be58dc9b107e060b26b210d0e9e0f0.pdf) | 5 / 122,387 | `7b580ea7d1c9819c07683f013d12078d0b6d045f380b7861858fde8456438909` | 2026-10-20T03:55:47.148697Z |
| SLT 2339 | [Compact agenda PDF](https://s3.amazonaws.com/granicus_production_attachments/slt/fb52e3c531af6893429f30df879554620.pdf) | 7 / 137,657 | `c0c6e6986dfdc05e8ad10a6f33429128edbc00584a68391639611f75973967be` | 2026-10-19T22:23:30Z |
| SLT 2351 | [Compact agenda PDF](https://s3.amazonaws.com/granicus_production_attachments/slt/33e44d971626eb8dd4eea7cb7b4cc2b90.pdf) | 3 / 88,093 | `5b2ae03798bb701e46da2afb579fe96b8285465edf4ffe855ba45fc88a28aae7` | 2026-10-20T03:55:46.542586Z |
| Truckee 1895 | [Joint meeting packet](https://meetings.municode.com/d/f?u=https://mccmeetings.blob.core.usgovcloudapi.net/truckeeca-pubu/MEET-Packet-5a3555fa0d2047ab871634c123f88ae4.pdf&n=AgendaPacket-Joint%20Town%20Council%20and%20Tahoe%20Forest%20Hospital%20District%20Meeting-October%2013,%202026%205.30%20PM.pdf) | 60 / 3,148,409 | `d2a7c6502ac035eacc6da0a88103bea8a66e723613d60aed9bc7e2f48bb132a4` | 2026-10-19T22:23:30Z |

South Lake Tahoe's compact agenda attachment URLs are opaque S3 objects reached through Granicus `AgendaViewer.php?view_id=6&clip_id=...` links. The virtual-host attachment host returned a TLS name mismatch in this environment; the path-style S3 URL above returned the corresponding object and was used for the temporary download and hash. The official Granicus meeting links remain the public source pages. Extracted SLT pages preserve the complete compact agenda; the separate 505-page packet is not copied into the fixture. Truckee's packet is also retained only as source citation metadata and extracted agenda pages; its agenda is on original PDF pages 1–3, with supporting material beginning on page 4.

`source_checked_at` is a per-record time when the official index/event listing was actually checked; PDF `fetched_at` is the actual temporary document download time. For reused TTD archive entries, neither value is invented. New official listing checks were made 2026-10-20T03:53:47Z (Truckee November), 03:54:09Z (Truckee September), 03:54:06Z (SLT Granicus index), and 03:56:29Z (TTD November meeting index). The November TTD listing identifies November 4, but states no time or location and links no agenda as of that check.

## Agenda order and parsing limits

- TTD stores every agenda section and subitem in published order. The agenda text on pages 1–3 includes the notice, meeting logistics, full numbered agenda, and continuation. Packet reports/attachments after page 3 are omitted. Item order is the published order, not an assertion about hearing order; TTD states items may be reordered. Table extraction can place pagination words/numbers into text, so display titles normalize those obvious table artifacts while source quotes and page text remain unmodified.
- SLT includes the full compact agenda, not the complete meeting packet. Numbered items and unnumbered sections are ordered as published; exact cited page excerpts remain in each item. Agenda-number wrapping across line breaks is retained from source text and handled conservatively by the parser.
- Truckee EID 1895 includes its complete agenda on packet pages 1–3. The Town's separate October 13 regular Council/agency event is canceled (EID 1771); it is distinct from the 4:00 p.m. special event (EID 1894) and the 5:30 p.m. joint event (EID 1895). Other event-only rows carry no agenda item claims.

## Discovery notes

- **TTD:** The official WordPress REST API at `/wp-json/wp/v2/meeting` exposes native post IDs, meeting links, titles, modified timestamps, and taxonomy IDs. The `/meeting_year` and `/meeting-type` archives supply year/type views. The board post links the agenda packet. The existing local archive's `CLAUDE.md` describes rate limiting and delayed fetching; reads for this fixture were local and read-only. Do not issue parallel request bursts to the source.
- **South Lake Tahoe:** The City's [watch/agendas page](https://cityofslt.gov/84/Watch-City-CouncilCommission-Meetings) links its [Granicus publisher archive](https://slt.granicus.com/ViewPublisher.php?view_id=6). Archive entries expose clip IDs and `AgendaViewer.php` pages; compact agenda links are distinct from the full packet. The chosen documents are compact agenda PDFs only.
- **Truckee:** CivicPlus month-list pages expose native EIDs and event links; event detail pages provide the embedded date, time range, address, and any linked agenda. For November entries, the native ID and embedded event date are the authority where URL day parameters disagree. The September 8 and September 22 event pages link Municode HTML agendas; this fixture has not converted those HTML records into page-cited text, and marks the agenda coverage incomplete instead of representing an empty agenda. November event pages had no agenda document linked at check time.

The fixture contains no inferred outcomes, generated summaries, or fabricated meeting records. The topic expectation file supplies curated positive, multi-topic, and irrelevant-negative examples backed by source citations; those labels are parser/classifier checks rather than comprehensive policy guidance. PDFs were processed in temporary storage or reused from the already existing TTD archive; no source PDFs were added to this repository.
