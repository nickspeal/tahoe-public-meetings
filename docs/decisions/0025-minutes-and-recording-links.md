# 0025: Minutes and recording links

Status: accepted (maintainer request, 2026-10-27); implemented
Date: 2026-10-27
Related decisions: [0004 source-first storage](0004-source-first-storage.md), [0020 database operation budget](0020-database-operation-budget.md)
Related: [IDEA-017](../ideas/IDEA-017-minutes-and-recordings.md), [minutes and recordings sources](../research/minutes-and-recordings-sources-2026-10-27.md)

## Context
Every meeting page has Minutes and Recording links, and all of them said "Not yet available" because no adapter filled `minutes_url` or `recording_url`. Research across the followed agencies found that most agencies never post approved minutes. The draft goes into the same body's next packet as a consent item, "Approval of Minutes of <date>". Recordings are on YouTube for most agencies that record (often on a channel other than the agency's obvious one), on Granicus, Legistar, or CivicClerk players for others, and not published at all for several.

The maintainer asked for draft-minutes links, preferred YouTube over other hosts, and left Tahoe Transportation District recordings out: TTD shares Zoom clips only on request, and they cannot be discovered systematically.

## Decision
**Three sources, one precedence.** `tahoe_ingest.records` merges links from:

1. **The agency's listing.** Adapters read minutes and video links from the row that lists the agenda: Granicus (including the South Lake Tahoe adapter), Legistar (minutes from the web API's status and file, because department pages link minutes before they are final; video from the Granicus link), CivicClerk, dated tables (Lahontan, Nevada water board, Fish and Game), TRPA meeting pages, NTCD's sheet, and link indexes with `minutesLinks` (STPUD, PCTPA, Truckee North Tahoe TMA). A minutes link for an earlier meeting, such as TRPA's draft September minutes on the October page, becomes a records-only entry matched to a stored meeting by date.
2. **Configured feeds** in `config/meeting-records.json`: YouTube channel or playlist feeds, and the Town of Truckee's Laserfiche minutes folder. A video matches a meeting when its title matches the body's pattern and names the meeting's date, and that date has passed. Channel IDs are pinned per body.
3. **Draft minutes in a later agenda** (`tahoe_ingest.draft_minutes`), from stored agenda items, so it reaches meetings older than the discovery window.

Minutes rank approved, then draft on the listing, then draft found through a later agenda. Recordings rank YouTube before any other host. A run without a link never removes one, and only an equal or better link replaces it.

**Draft minutes are matched conservatively.** A dated item (in its title, its cited agenda text, or the agenda text after it, up to the next item) belongs to the same agency's meeting on that date. When two meetings share the date, the item's special or regular label must agree. An undated item belongs to the immediately preceding meeting only when exactly one meeting was held that day, 10 to 120 days earlier, and the item names no other body. The link opens the packet page where the minutes begin, recognized by "minutes" and the date at the top of a page outside the agenda. Otherwise it opens the agenda at the item, with a note such as "Item 6.1 on the September 8, 2026 agenda". The page labels these as draft minutes.

**Same-day meetings stay apart.** Feed and records-only matching treat closed sessions, joint meetings, and special meetings as different meetings when several share a date. When a body has one non-joint meeting that day, a source's special or regular label is ignored, because sources relabel meetings.

**Cost.** No model calls. Each scheduled run reads about 15 YouTube feeds and 2 Laserfiche listings, one extra Legistar API request per Legistar body, and makes about 6 more database queries plus one update per changed meeting (decision 0020). The 15-video feeds suffice every six hours. A one-time backfill uses `tahoe-ingest meeting-records --since 2026-01-01` with a free YouTube Data API key in `YOUTUBE_API_KEY`, a few dozen quota units in all.

## Not covered
- TTD recordings (Zoom clips shared on request). TTD draft minutes are linked from its next packets.
- Placer County minutes: placer.ca.gov serves a Cloudflare challenge to automated clients.
- Agencies that publish neither minutes nor recordings (South Shore TMA, TRCD, OHMVR Commission, Nevada State Parks), or no recordings (NTCD, the Nevada water board, the Tahoe Conservancy, Truckee North Tahoe TMA).
- NTPUD, whose site returned HTTP 403 during research; the Truckee Tahoe Airport meetings index, which returned 404 (its recordings still match from YouTube).
- Minutes of committees approved by a parent board, such as TCPUD's Parks and Recreation Committee minutes on the Board's agenda.
