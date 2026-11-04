# 0036: YouTube Data API fallback for failing feeds

Status: accepted by the maintainer on 2026-11-04.
Date: 2026-11-04
Related decisions: 0025, 0020

## Context

Decision 0025 matches recordings to meetings from each configured YouTube channel or playlist feed (`https://www.youtube.com/feeds/videos.xml?channel_id=…` or `?playlist_id=…`). The feed is not a documented API and has no stated availability.

The scheduled runs of 2026-11-03 at 02:03 UTC and 2026-11-04 at 01:21 UTC read every configured feed and got HTTP 404 for all twelve (one returned 500), so the meeting-records step reported `degraded` and new recording links waited for a later run. The runs between them (08:04, 14:05, 20:07, and 22:30 UTC on 2026-11-03) read all feeds without error. At 05:04 UTC on 2026-11-04, the feed for every channel tried returned 404 from the server named "YouTube RSS Feeds server": the configured channels and playlists, large unrelated channels, the `user=` form, `http`, and the domain without `www`. A browser user agent, `Accept-Language`, and consent cookies made no difference. The channel pages and playlist pages still returned 200.

Public reports describe the same failure in recent months: the endpoint returns 404 for every channel for hours, often around the same time each day, then recovers. No Google or YouTube announcement explains it, and no deprecation has been announced:

- Google AI Developers Forum, "YouTube RSS feed endpoint returns 404 errors" (reports from December 2025 to May 2026): https://discuss.ai.google.dev/t/youtube-rss-feed-endpoint-returns-404-errors/113379
- n8n community thread with the same title (December 2025; a browser user agent did not help): https://community.n8n.io/t/youtube-rss-feed-endpoint-returns-404-errors/241692
- A GitHub issue reporting all feeds returning 404 from 2026-10-04: https://github.com/joaoccaldas/konam/issues/102
- An Obsidian RSS plugin issue, closed as a recurring YouTube problem (2026-10-27): https://github.com/amatya-aditya/obsidian-rss-dashboard/issues/1625

The project already has a YouTube Data API key in Secret Manager (`tahoe-youtube-api-key`), mounted on the backfill job only, and code that reads a channel's uploads playlist with `playlistItems.list`.

## Decision (proposed)

When a scheduled run's feed request fails and `YOUTUBE_API_KEY` is set, read the same channel's uploads playlist (or the configured playlist) from the Data API instead: one `playlistItems.list` request with `maxResults=50`, which costs one quota unit. The feed stays the first choice, so a working feed spends no quota. A successful fallback is listed under the step's `apiFallbacks` and does not make the step `degraded`. If both fail, the error names both failures, and never the key. Without the key, behavior is unchanged.

## Cost

The free Data API quota is 10,000 units a day. The configuration has 12 distinct channels and playlists (17 bodies; bodies that share a channel read it once). If every feed failed in every run, four runs a day would use 12 × 4 = 48 units a day, under 0.5% of the quota. `search.list` (100 units a request) is not used. There is no charge.

## Alternatives considered

- **Read the Data API every run instead of the feed.** This is just as cheap and more predictable, and it returns 50 videos instead of 15. It would make the scheduled job depend on the key always, and the feed works most of the time. This is a reasonable later step if the outages become more frequent.
- **Read the public playlist page (`page_videos`, already used by keyless backfills).** It needs no key, but it parses YouTube's page data, which changes without notice, and needs larger downloads (2 to 3 MB per channel). It stays the keyless backfill path.
- **Retry the feed later in the run, or change request headers.** Testing showed the outage affects every client, and the reports show it lasts hours, so retrying within one run would not help.

## What would change this

A Google announcement that the feed is retired (switch to the Data API every run), a stop to the outages, or a change to Data API quota or pricing.
