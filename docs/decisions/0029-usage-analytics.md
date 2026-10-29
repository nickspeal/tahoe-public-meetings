# 0029: Usage analytics with cookieless GoatCounter

Status: accepted
Date: 2026-10-29
Related ideas: [IDEA-019](../ideas/IDEA-019-usage-analytics.md)

## Context
The maintainer wanted to know how many people visit the site, how often, and which parts they use, without a consent banner and without sharing more about readers than those questions need. The site is static (decision 0020), so there are no server logs to read, and measurement needs a browser beacon. Decision 0020 rules out a database write per page view: Prisma Postgres bills every statement, and views would compete with ingestion for the 200,000-operation monthly quota. Decision 0017 already set the privacy posture for the one runtime function: no raw client addresses at rest.

## Decision
Confirmed by the maintainer on 2026-10-29:
- Use hosted GoatCounter (goatcounter.com). It sets no cookies and stores no IP addresses.
- Record each page view as the path only, without the query string or hash. Filter state is captured as named events instead.
- Capture usage as named events: `outbound-agenda`, `outbound-staff-report`, `outbound-attachment`, `outbound-minutes`, `outbound-recording`, `outbound-agency`, `outbound-comment-period`, `calendar-ics`, `calendar-google`, `calendar-outlook`, `feedback-open`, `feedback-sent`, `filter-agency-<agencyId>`, `filter-topic-<topicId>`, `timeline-earlier`, `timeline-upcoming`, and `agenda-filter-topic-<topicId>`. The [operations guide](../operations/analytics.md) says what each means.
- Configure analytics only through the build-time variable `PUBLIC_GOATCOUNTER_URL`, set in Vercel's Production environment. When it is unset or not an `https://` URL, the site emits no analytics script, so previews, local builds, and tests send nothing.
- Keep the dashboard private. Do not set a persistent browser identifier. The tool and the site store nothing that recognizes a returning browser.
- Leave GoatCounter's "individual pageviews" site setting off for now. The Privacy page's statement is written so that it stays true if the maintainer turns the setting on later.
- Exclude the maintainer's own browsers with count.js's built-in `#toggle-goatcounter` opt-out. There is no second mechanism.
- Describe collection on a Privacy page, linked from the footer, in a short statement that links to GoatCounter.

## Alternatives and consequences
IDEA-019 compares the options in a table. Vercel Web Analytics and Umami Cloud have free tiers without custom events on their free plans, so they could not show how people use the site. Cloudflare Web Analytics counts visits by referrer rather than unique visitors, with short unsampled retention. PostHog can do everything asked, but its script is the heaviest and sends the most data off the site. Plausible Community Edition is free software that needs a server of about $5/month, which breaks the budget and adds an operations surface. A home-built counter would need a store that tolerates a write per event, such as Upstash Redis, to respect decision 0020, which means two new moving parts for a question GoatCounter already answers.

Costs accepted:
- Ad blockers drop some visits, so counts are a floor, not a total. count.js is served from the site, but uBlock Origin's lists also block the goatcounter.com endpoint, and a vanity domain is uncloaked, so those readers stay uncounted.
- Visits are eight-hour windows, not people: the same reader on Monday and Thursday counts twice, and return frequency cannot be measured.
- The site relies on a third party. The exit is self-hosting GoatCounter, which is the same open-source program.

## Revisit when
- Blocker loss or reliance on the hosted service starts to matter: self-host GoatCounter, or accept the floor.
- Return frequency becomes the question: reconsider a persistent browser identifier, with a matching update to the privacy statement.
- GoatCounter's terms change, including its free "reasonable public usage" allowance.
