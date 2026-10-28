# Minutes and recordings sources (2026-10-27)

Where each followed body publishes meeting recordings and minutes, and how a scheduled job could link them. Gathered with plain HTTP requests from a residential connection, not from Cloud Run; YouTube and Cloudflare behavior from Cloud Run addresses is untested. Proposal: [IDEA-017](../ideas/IDEA-017-minutes-and-recordings.md); implementation: [decision 0025](../decisions/0025-minutes-and-recording-links.md).

## Why the fields are empty

`meetings.minutes_url` and `meetings.recording_url` exist in the schema, the export (`minutesUrl`, `recordingUrl`) and the meeting page, and `core.py` keeps an earlier value when a later partial scrape omits it. Before decision 0025, no adapter set either field, so all 162 meetings in the 2026-10-27 local export show "Not yet available". Some adapters capture minutes PDFs as ordinary documents (for example NTCD's sheet), and `link-index` drops non-agenda links on purpose.

## Draft minutes in later packets

Most agencies post only draft minutes, as a consent item in the same body's next meeting ("Approval of Minutes of June 3, 2026", "Approval of the March 3, 2026, Draft Minutes", "TAB 05 - July 22, 2026 Regular Meeting Minutes"). The 2026-10-27 export has about 80 such agenda items across 25 bodies. Most name the meeting date; some say only "Minutes" or "City Council Meeting Minutes". Some packets carry another body's minutes (PCTPA's Technical Advisory Committee, Douglas County Commissioners minutes in a Regional Transportation Commission packet). Typical lag is 2 to 9 weeks and about 3 months for quarterly bodies.

## YouTube

The free channel feed `https://www.youtube.com/feeds/videos.xml?channel_id=UC…` (or `?playlist_id=PL…`) returned the latest 15 videos without a key for every channel tested. That covers several weeks to four months per channel, which is ample at a six-hour cadence. Backfilling to 2026-01-01 needs the YouTube Data API v3 uploads playlist (channel ID with `UC` replaced by `UU`; `playlistItems.list` costs 1 quota unit per 50 videos, free key) or a one-off local scrape. Several old per-body playlists stopped years ago and their feeds return the oldest 15 videos, so use the channel uploads feed unless a playlist is confirmed current.

Match a video to a meeting by a per-body title pattern plus the date parsed from the title, not the publish time (uploads lag 0 to 14 days). Normalize abbreviations such as "Sept." first. Only attach a video after the meeting date (feeds include scheduled livestreams). Exclude closed-session, Spanish-interpretation, promo and "original/untrimmed" uploads; when one agency has a single meeting that day, ignore special versus regular labels; a joint-meeting video can fill both bodies. Several obvious official channels are empty or unrelated, so pin channel IDs per body.

## By body

| Body | Recording | Minutes | Notes |
| --- | --- | --- | --- |
| TTD (all bodies) | Zoom clips, not published (see below) | Draft only, next same-body packet, consent item C, about page 5; 3–9 weeks | Packet URLs change on re-upload |
| SLT City Council | YouTube `UC3LYne8Ih-kTPLnnuyJW9SQ`; Granicus player as fallback | Granicus row, about 2 weeks | Granicus row IDs (`granicus-row-ids` discovery); publisher page is 4.5 MB and can take 30 s |
| SLT Planning Commission | Same channel | Granicus row, about 4 weeks | |
| SLT Parks and Recreation | Granicus player only | Granicus row, 4–5 weeks | |
| Town of Truckee Council | YouTube, Tahoe Truckee Media `UCNQLUz8QV3qa8SZDqAr129g`, playlist `PLr_DSJ6rtN1Zms9Rs8SazB8Tf1EBfhf6p` | Laserfiche minutes folder (JSON listing, documents named `YYYY-MM-DD - Regular`), 2–3 weeks | The Town's own channel has no meetings |
| TCPUD Board, Parks and Recreation Committee | Granicus player (MP4 available) | Granicus row and a Granicus minutes RSS feed, 4–6 weeks | Minutes links redirect through a Google Docs viewer |
| Douglas County Commissioners, Planning | YouTube `UCj6Vp4YBriA8dhsZjB4wKIw`; Granicus fallback | Granicus row; an "Action Summary" uses the same link pattern, so check the link text | Some meetings split across two rows; "Supplemental" rows |
| Douglas County RTC | None | None since March 2024 | |
| El Dorado Supervisors | YouTube `UCUMjDk3NUltZJrpw2CL7Zkg`; Granicus clip from Legistar `EventMedia` | Legistar `EventMinutesFile` when status is Final, about 8 days | `EventVideoPath` is always null |
| El Dorado Planning | Granicus `eldorado.granicus.com/MediaPlayer.php?view_id=2&clip_id={EventMedia}` | Legistar, about 14 days | Draft files exist before Final; accept Final only |
| Washoe Commissioners | YouTube `UCyj1MIPXwvfcb-bhXNDSzQg`; Granicus view 6 | Attached to a later meeting's minutes item (Legistar `eventitems` attachments), 6–7 weeks | |
| Washoe Planning | Same channel | Inconsistent county web page | |
| Placer Supervisors, Planning | YouTube `UC2yOsPQxXvDt8phGqqKbTTQ`; titles use varied date formats | Unreachable: placer.ca.gov serves a Cloudflare challenge to all automated clients | Placer Legistar API returns 500 |
| Placer Tahoe Design Review | None found | Unreachable | |
| PCTPA | YouTube `UCJDCugElcgh2hsb5i7LaOLw` (exclude SPRTA titles) | "Minutes" link in the same pctpa.net row, 4–9 weeks; take the date from the row, not the file name | The channel linked from pctpa.net stopped in 2025 |
| IVGID | CivicClerk event media (`hasMedia`, `/event/{id}/media`) | CivicClerk files of type Minutes on the same event, 2–8 weeks | Vimeo titles are generic and unmatchable |
| TRPA Governing Board | YouTube `UCTfd_gYe2i9nBOfY53lCnLA`, playlist `PLKbZed26aeuA8fh-HWQcwdhIwQM6uFTdL`; also linked from the meeting page | Final minutes linked from the meeting page, about 1 month | |
| TRPA APC | Playlist `PLKbZed26aeuC-_H0pVaaKhTScREvsGH-k` | Draft only, next packet | |
| STPUD | YouTube `UCCTZ5fNgs42NvLIseQd7OIw`, titles `M/D/YYYY [Special ]Board Meeting` | "Minutes" link in the same row, 2–6 weeks | |
| Truckee Tahoe Airport | YouTube, Tahoe Truckee Media playlist `PLr_DSJ6rtN1YYUKFqxioyQ-kNF1N0q_Qr` | Draft only, as a "TAB nn" attachment of the next meeting, about 1 month | Configured meetings index returned 404 on 2026-10-27 |
| Lahontan Water Board | "Meeting Video" link in the same row, mostly CAL-SPAN | Same row once approved, about 2 months | CAL-SPAN is behind Cloudflare: link, never fetch |
| Fish and Game Commission | Committees: row YouTube link or `UCTrLyrErKM3UNqI-3gBWLSg`; full Commission: generic CAL-SPAN folder only | None published | Some row links are wrapped in Outlook SafeLinks |
| NTCD | None (Teams) | Sheet Minutes column, under 1 month; use the row date | |
| Nevada Board for Financing Water Projects | None (Teams) | Same row, "Draft Minutes" then "Minutes"; URL changes on approval | |
| State Lands Commission | CAL-SPAN, per-meeting URL unverified | Public `jsondb/YYYYMMDD-items.json` with transcript and voting-record URLs | |
| Tahoe Conservancy | None (its "Board Meeting" video is a short promo) | Draft as item 3a of the next quarterly meeting, about 3 months | |
| Truckee North Tahoe TMA | None | One "Most Recent Minutes" link, no archive | |
| NTPUD | Reportedly on ntpud.org; not on YouTube | Not checked | ntpud.org returned 403 on 2026-10-27 |
| TRCD, South Shore TMA, OHMVR Commission, Nevada State Parks, State Park and Recreation Commission | None current | None online | |

## TTD Zoom clips

TTD records meetings in Zoom and shares clips on request. The three August 2026 clip links the maintainer received are viewable without signing in. An unauthenticated metadata request (`https://zoomclips.zoom.us/nws/marvel/2.0/clips/share/<token>?pageType=web`) returns the clip title (`YYYY-M-D TTD <body>`), creation time (1 to 4 days after the meeting) and duration, which is enough to verify that a supplied link belongs to a meeting. Transcripts are also served without sign-in, but through undocumented endpoints.

New clips cannot be discovered. The share tokens are random 22-character strings unrelated to the webinar ID or date, Zoom offers no public per-account clip library, and webinar registration pages show no recording after the meeting. Only the February 2026 meeting posts linked recordings, and those links no longer work. Minutes say the meeting was recorded and direct requests to the Clerk to the Board. TTD's YouTube channel has one video, from 2012. Clips stay up until TTD deletes them; do not re-host video or store signed stream URLs.
