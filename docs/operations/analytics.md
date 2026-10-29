# Usage analytics

The site counts page views and a small set of named events with hosted [GoatCounter](https://www.goatcounter.com/), so the maintainer can see how many people visit and which pages and links they use. GoatCounter sets no cookies and does not store IP addresses. The dashboard is private. See [decision 0029](../decisions/0029-usage-analytics.md) and [IDEA-019](../ideas/IDEA-019-usage-analytics.md). The Privacy page (`apps/web/src/pages/privacy.astro`, linked from the footer) carries the reader-facing statement.

## How it works

| Piece | Location |
| --- | --- |
| Configuration helper (`analyticsConfig` accepts only an `https://` URL and otherwise returns `null`) | `apps/web/src/lib/analytics.ts` |
| Script tag: count.js served from this site plus an inline setting that records only the path | `apps/web/src/layouts/SiteLayout.astro`, emitted only when configured |
| count.js itself: a verbatim copy of upstream's pinned `count.v5.js` (ISC licensed) | `apps/web/public/goatcounter.js`, served at `/goatcounter.js` |
| Event attributes (`data-goatcounter-click`) on outbound links and the calendar menu | Components such as `CalendarDownload.astro` and the agenda, meeting, and planned-meeting cards |
| Event calls (`window.goatcounter?.count(...)`, a no-op when the script is absent) | `apps/web/src/scripts/feedback-dialog.ts`, `timeline-filters.ts`, and `meeting-agenda-filter.ts` |
| Baseline beacon (`POST /api/seen` with the path as the body) | Inline script in `SiteLayout.astro`, emitted only when `PUBLIC_PAGEVIEW_BASELINE` is set |
| Baseline function: forwards the path to a second GoatCounter site through the count API | `apps/web/api/seen.js`, using `apps/web/server/pageview-baseline.mjs` |
| Tests | `apps/web/test/analytics.test.mjs`, `apps/web/test/pageview-baseline.test.mjs` |

The build reads `PUBLIC_GOATCOUNTER_URL`. When it is unset or empty, no analytics script is emitted, so local development, tests, and preview deployments send nothing. Event attributes are always present in the HTML but do nothing without the script. localhost and private network addresses are skipped by count.js itself.

count.js is served from the site rather than from gc.zgo.at because list-based blockers stop that host. The copy is pinned: `GOATCOUNTER_SCRIPT.integrity` in `apps/web/src/lib/analytics.ts` holds upstream's SRI hash for the version copied, and the analytics test fails if the file no longer matches it. To update, download the new versioned file from https://www.goatcounter.com/help/countjs-versions, check its hash against that page, replace `public/goatcounter.js`, and update `upstream` and `integrity` together. The `/count` endpoint is guaranteed to stay compatible with older count.js, so updates are optional.

Each page view is recorded as the path only, with no query string or hash. Outbound-link events carry the page they were clicked from as the referrer and, where known, the agency id as the title.

## Setup

1. Create a GoatCounter account and a site at goatcounter.com. Choose a site code.
2. In the Vercel project, under Settings, then Environment Variables, add `PUBLIC_GOATCOUNTER_URL=https://<code>.goatcounter.com/count`. Select the **Production** environment only. Leaving out Preview and Development is what keeps previews and local builds from counting.
3. Redeploy production (a new commit, a deploy hook run from the ingestion job, or Redeploy in the dashboard). The value is read at build time, so changing it has no effect until the next build.
4. Visit the live site, then check that the GoatCounter dashboard records the visit.

## Site settings in GoatCounter

- Leave **individual pageviews** off. Turning it on is covered under [later options](#later-options).
- Optionally add home or office IP addresses under Settings, then Tracking, then Ignore IPs.
- Do not make the dashboard public.

## Excluding your own browsers

Visit `https://tahoepublicmeetings.org/#toggle-goatcounter` once in each browser you use, on desktop and on a phone. A confirmation popup appears and count.js stores an opt-out flag in that browser's `localStorage`. Visit the same address again to switch counting back on.

- The flag lives in one browser profile. Repeat the visit in each browser, and again after clearing site data.
- Safari can delete script-written storage after seven days without a visit to the site, so repeat the visit if your own traffic starts to appear.

## Events

| Event | Meaning |
| --- | --- |
| `outbound-agenda` | Click on an agenda or agenda packet link, including agenda entries in a meeting's document list |
| `outbound-staff-report` | Click on an item's staff report link |
| `outbound-attachment` | Click on an agenda attachment |
| `outbound-minutes` | Click on a meeting's minutes link |
| `outbound-recording` | Click on a meeting's recording link |
| `outbound-agency` | Click on a link to the agency's own site or meetings page, including planned-meeting cards and the coverage page |
| `outbound-comment-period` | Click on an official comment period notice or submission page |
| `calendar-ics` | Add to calendar, iCalendar file download |
| `calendar-google` | Add to calendar, Google Calendar |
| `calendar-outlook` | Add to calendar, Outlook |
| `feedback-open` | Feedback dialog opened |
| `feedback-sent` | Feedback submitted |
| `filter-agency-<agencyId>` | Timeline agency filter changed, or the timeline loaded with that filter in its address |
| `filter-topic-<topicId>` | Timeline topic filter changed, or the timeline loaded with that filter in its address |
| `timeline-earlier` | Show earlier meetings toggled on the timeline |
| `timeline-upcoming` | Show upcoming meetings toggled on the timeline |
| `agenda-filter-topic-<topicId>` | Topic filter used on a meeting's agenda |

"Suggest a topic..." in the topic dropdown opens the feedback dialog and is not counted as a filter. Events appear in the dashboard's events list (each event name is its path), so filtering or searching for `outbound-` or `filter-` groups them.

## Reading the dashboard

| Question | Where to look |
| --- | --- |
| How many people visit? | Total pageviews and visits over time. A visit is one browser within an eight-hour window, so a weekly figure is a sum of those windows, not of people |
| Which pages matter? | Top pages table. Meeting pages appear as `/meetings/<id>/`, topic pages by their own paths |
| What do people filter by? | Events named `filter-agency-` and `filter-topic-` |
| Do people follow through to official documents? | `outbound-` events against meeting-page visits |
| Do people use the calendar and feedback features? | `calendar-` and `feedback-` events |
| Who sends visitors? | Referrers, which show local sites, newsletters, and search |

## Baseline counter: measuring what blockers hide

uBlock Origin's lists block the count request to goatcounter.com, so the main dashboard misses those readers. To know how many, pages also send a bare first-party request, the path and nothing else, to `/api/seen` on the site's own domain. A Vercel function forwards it to a **second** GoatCounter site through the server-side count API with `no_sessions` set and no client address, so that site records pageviews only: no visits, no location, no random visit code. The route name was checked against EasyPrivacy's generic path rules so blockers leave it alone. The beacon honours the same `#toggle-goatcounter` flag as count.js and skips local hosts, so the maintainer's browsers are missing from both dashboards alike.

The Privacy page statement already covers this: page views counted with GoatCounter, no cookies, no stored IP addresses. The baseline collects less than the main counter, not more.

Setup:

1. In GoatCounter, add a second site for the baseline (for example `<code>-baseline`) from the account menu's Sites page.
2. Create an API key from the account menu, the entry named **API** under your username at the top right, not under the site's Settings. Give it permission to count pageviews, and, if the key form offers a site restriction, limit it to the baseline site.
3. In the Vercel project's Production environment set `GOATCOUNTER_BASELINE_URL=https://<baseline code>.goatcounter.com`, `GOATCOUNTER_BASELINE_TOKEN=<the key>` (sensitive), and `PUBLIC_PAGEVIEW_BASELINE=1`. The first two are read by the function at request time; the third is read at build time, so redeploy.
4. Load a page and check that the baseline site's dashboard shows it within a minute (the API persists pageviews every ten seconds).

Reading the comparison: the tracked fraction is the main site's pageviews divided by the baseline site's pageviews over the same days. Compare per path too, since blocker use differs between a shared meeting link and a regular reader. Expect the baseline to be slightly higher for reasons other than blockers: crawlers that run JavaScript are counted unless GoatCounter's bot detection recognises their User-Agent; the function drops views when GoatCounter answers 429 (four API requests a second), which makes the baseline a floor as well; and a reader whose browser blocks all scripts appears in neither. The function never returns an error to the page; failures are visible only in the Vercel function log.

Turn the baseline off by removing `PUBLIC_PAGEVIEW_BASELINE` and redeploying; the function then answers 204 to nothing. The maintainer may run it for a few weeks to measure the gap, or leave it on.

## Limitations

- uBlock Origin's default lists (EasyPrivacy) block the request to goatcounter.com, so readers with it are never counted and every figure is a floor. Serving count.js from the site only removes the script-load error and keeps the opt-out working for those browsers. A custom domain for the endpoint does not help either: blockers uncloak the CNAME, and EasyPrivacy lists known GoatCounter custom domains.
- Visits are eight-hour windows, not people. A reader who returns on Monday and Thursday counts twice, and return frequency cannot be measured.
- Query strings are not recorded in paths. Filter use shows up only as the events above.
- Preview deployments and local builds are not counted.

## Later options

- Turn on **individual pageviews** in GoatCounter to see which pages a visit viewed together and to export pageviews as CSV. GoatCounter then stores each view with the visit's random code, which it otherwise keeps only in memory for eight hours. The Privacy page statement already describes this.
- Self-host GoatCounter (the same open-source program) to remove reliance on the hosted service. This adds a server to run and pay for, so weigh it against the monthly budget and record it as a new decision.
