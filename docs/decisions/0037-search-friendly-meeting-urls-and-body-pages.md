# 0037: Search-friendly meeting URLs, body pages, sitemap, and structured data

Status: accepted by the maintainer on 2026-11-09 (URL scheme, body pages, sitemap, structured data). The path qualifier list, hub wording, and JSON-LD field choices are proposed defaults. Visible changes to existing pages (meeting page titles and headings, links to body pages) are not yet decided.
Date: 2026-11-09
Related decisions: 0012, 0027, 0028

## Context

A search for "south lake tahoe city council meeting" did not find the site on its first page. Meeting pages were titled with the agency's raw filing name ("10 26 2026 Special Closed Session City Council") and never named the city; no page stood for a body over time, only one page per dated meeting; there was no sitemap and no structured data; and `/` and `/meetings/` served the same timeline.

## Decision

- **Each followed agency has a `page`** in `packages/domain/agencies.json`: a permanent URL `slug` and the `name` residents search for ("South Lake Tahoe City Council"). It is never inherited through `extends`; the registry requires it for every followed body and rejects duplicate slugs.
- **Body pages** at `/meetings/<slug>/` state the next meeting, the regular schedule when the export forecasts one, the official website, and the body's upcoming and past meetings. `/meetings/` lists the body pages. The meeting timeline lives only on `/`.
- **Meeting URLs** are `/meetings/<slug>/<YYYY-MM-DD>[-<qualifier>]/`, replacing `/meetings/<id>/` without redirects (the site was weeks old). The date is the local date. A regular meeting keeps the bare date, so a planned placeholder and the confirmed record that replaces it share one URL; a meeting whose title states a kind (special, closed session, workshop, budget, joint, hearing, …) always carries it. A numeric suffix breaks any remaining tie, and the build fails on a duplicate path. One-off meetings under Other add their source ID. The rule is in `apps/web/src/lib/meeting-paths.ts`.
- **A sitemap** at `/sitemap.xml` lists every built page whose canonical URL is its own and that is not noindex, a redirect, `/calendar/`, or `/data/`; `robots.txt` names it.
- **Structured data**: meeting pages carry a schema.org `Event` (local start time, status, in-person and virtual location, organizer, a description from agenda items) and a breadcrumb; body pages carry a `GovernmentOrganization` and a breadcrumb. Nothing is invented: no end time from duration estimates, no location when none is published. Google requires an Event location, so a meeting that publishes neither a place nor a join or watch link gets only its breadcrumb (added 2026-11-13 after a Search Console report).

## Consequences

Old meeting links and analytics paths from before 2026-11-09 no longer resolve. A title that gains or loses a qualifier word moves its meeting's URL; bodies that hold several kinds of meeting under one agency (Fish and Game, State Parks) could fall to the numeric suffix if two meet on one day.

## Revisit

When Search Console shows which queries reach the site, and when the maintainer decides on meeting page titles and links to body pages.
