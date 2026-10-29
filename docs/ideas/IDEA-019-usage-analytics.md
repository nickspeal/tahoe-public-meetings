# IDEA-019: Usage analytics

Status: accepted; implementing (decision 0029)
Stage: Retention

## Problem and audience
The maintainer cannot tell how many people use the site, how often they return, or which parts they use (timeline filters, meeting pages, topic pages, outbound links to agendas and staff reports, calendar downloads, feedback). Without that, design and coverage choices rest on guesswork. The audience is Lake Tahoe residents; the site should not make them pay for measurement with a consent banner or with data shared beyond what the question needs.

## Smallest useful version
Aggregate, cookieless page-view and event counting with no stored IP addresses and no cross-site identifier, so no consent banner is needed and the About page can describe collection in two sentences.

Measure, in order of value:
1. Page views and daily unique visits per path family: timeline, meeting pages, topic pages, comment periods, coverage, about.
2. Timeline filter use. Filters already write to the query string (`src/scripts/timeline-filters.ts`), so the path plus query answers which agencies and topics people filter by.
3. Outbound clicks to official agendas, staff reports, minutes and recordings, and clicks on Add to calendar.
4. Feedback dialog opens versus submissions.
5. Referrers (which local sites, newsletters or searches send people) and screen class (phone versus desktop).

Deferred: return frequency across days (needs a persistent browser identifier; see open questions), session recordings and heatmaps (never), per-reader profiles (never).

## Options considered (October 2026)
All free tiers below fit expected traffic (well under 50,000 events a month). Facts were read from each vendor's own pages on 2026-10-29 and will drift.

| Option | Cost | Cookies / IP storage | Custom events | Retention | Notes |
| --- | --- | --- | --- | --- | --- |
| **GoatCounter** (hosted, goatcounter.com) | Free for "reasonable public usage"; donation-supported | No cookies or browser storage; IP and User-Agent hashed in memory for 8 hours, never stored | Yes | No stated expiry; JSON API and exports | Open source (same Go binary self-hosts); data at Hetzner in Finland and Germany; optional public dashboard; custom domain reduces ad-block loss |
| Vercel Web Analytics (Hobby) | Free, 50,000 events/month | First-party endpoint, no cookies | **No** (Pro only) | 1 month | Zero setup, but cannot answer "how are they using it" and keeps no history |
| Umami Cloud (Hobby) | Free, 100,000 events/month, 1 site | No cookies | **No** on Hobby | 6 months | Self-hosting Umami needs a Node server and a database |
| Cloudflare Web Analytics | Free | No cookies stated | No | 7 days unsampled, 6 months sampled | Counts "visits" by referrer, not unique visitors; heavy sampling on small sites |
| PostHog Cloud | Free to 1M events/month | Cookieless mode is opt-in | Yes, plus funnels and paths | Long | Most capable, heaviest script, most data leaving the site; more than this question needs |
| Plausible Community Edition | Software free; about $5/month VPS | No cookies; daily-rotating hash | Yes | Unlimited | Nice product, but the server breaks "free" and adds an operations surface |
| Counterscale | Free on Cloudflare Workers free tier | No cookies; you own the data | Limited | 90 days (Analytics Engine) | Needs a Cloudflare account alongside Vercel |
| Home-built | Free within Vercel and Upstash free tiers | Whatever is designed in | Yes | Unlimited | See below |

**Home-built sketch.** A `/api/hit` Vercel function receiving a `navigator.sendBeacon` call must not write one row per view to Prisma Postgres: decision 0020 budgets 200,000 statements a month for the whole workspace, so page views would compete with ingestion. The workable shape is a counter store that tolerates per-event writes (Upstash Redis on the Vercel Marketplace, 500,000 commands a month free: one `INCR` per day-and-path and one `PFADD` of a daily-salted hash for uniques), rolled up once a day by the existing scheduled job into a few Postgres rows and rendered as a page under `/status` or `/stats`. Roughly a day of work plus ongoing care of two new moving parts. It buys full ownership and a first-party endpoint that ad blockers do not stop. GoatCounter's self-host option gives most of that ownership later without rebuilding.

## Proposed default
Start with hosted GoatCounter, loaded from a first-party custom domain (for example `stats.` on the site's domain) so fewer blockers drop it, with custom events for outbound clicks, Add to calendar and feedback. Add a short collection statement to the About page. Make the dashboard public if the maintainer is comfortable; a civic site that publishes its own usage is consistent with decision 0016. Revisit after three months of data: move to self-hosted GoatCounter or the home-built counters if third-party reliance or blocker loss turns out to matter, or to PostHog if path and funnel analysis becomes the question.

## Evidence and dependencies
- The site is static (decision 0020): no server logs exist to mine, so measurement needs a browser beacon of some kind.
- Decision 0017 already set the privacy posture for the one runtime function: salted IP hashes only, no raw addresses. Analytics should not be looser than that.
- Legal footing (not legal advice): GDPR targets services offered to, or monitoring, people in the EU, which this site does not do; the CCPA/CPRA thresholds apply to for-profit businesses the site is not; CalOPPA asks commercial sites collecting personal information from Californians to post a privacy policy. A cookieless, aggregate-only tool with a published collection statement sits comfortably inside all three without needing a consent banner.
- Expectation setting: without a persistent identifier every cookieless tool reports *daily* uniques; the same reader on Monday and Thursday is two visitors. Weekly or monthly unique counts, and return frequency, need either a first-party random ID in `localStorage` (pseudonymous, never leaves the site except as a hash) or acceptance of the approximation.

## Acceptance criteria
- The maintainer can answer, from one dashboard: visits and daily uniques per week; top pages by family; most-filtered agencies and topics; outbound click counts by destination type; calendar and feedback use; top referrers.
- No cookies, no `localStorage` writes by analytics, no IP addresses at rest; the About page says what is collected and links to the tool's privacy page.
- Page weight grows by under 5 KB and the beacon loads after content; the site works unchanged when the beacon is blocked.
- Zero recurring cost; Prisma operation counts unchanged (decision 0020 tests still pass).

## Open questions
Answered by the maintainer on 2026-10-29 (decision 0029):
- Return frequency: start without a persistent browser identifier and decide after a quarter of daily-unique trend data.
- Public dashboard: no.
- The maintainer's own browsers are excluded with count.js's `#toggle-goatcounter` opt-out, repeated once in each browser.
- GoatCounter's "individual pageviews" setting stays off for now. The Privacy page's statement is written so it stays true if it is turned on later.

## Links and updates
- 2026-10-29: options surveyed; GoatCounter proposed as default. Related: decisions 0016, 0017, 0020; IDEA-009 (promotion, which needs referrer data to evaluate).
- 2026-10-29: accepted as [decision 0029](../decisions/0029-usage-analytics.md): hosted GoatCounter, cookieless, configured only in the Vercel Production environment, no public dashboard. Operations guide: [analytics](../operations/analytics.md). The statement moved from the About page to its own Privacy page, linked from the footer, the same day.
