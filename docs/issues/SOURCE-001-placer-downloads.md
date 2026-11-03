# SOURCE-001: Placer public agenda downloads return HTTP 403

Status: deferred, per the maintainer's October 20, 2026 source review
Related reviews: SR-001 (download issue only; committee identity corrected), SR-013, SR-014

## Affected registry entries

`placer-supervisors` (Board of Supervisors), `placer-planning` (Planning Commission), and `placer-tahoe-design-review` (Tahoe Basin Design Review Committee, whose archive lists notices but whose agenda PDFs are blocked). `placer-transportation` is published separately at `pctpa.net`, is not affected, and now has live discovery. Overview: [source coverage](../operations/source-coverage.md).

## Observed behavior

The maintainer can open the official archive and PDFs in a browser. This worker receives HTTP 403 for the examples below through both Python and the native curl fallback using normal TLS verification. The cause is not established; DocAccess or automated-request filtering is a hypothesis, not a diagnosis. The URL fragment `#docaccess-...` is a browser interface anchor, not a separate downloadable document.

- [Board archive](https://www.placer.ca.gov/10616/_2026)
- [October 6 Board agenda](https://www.placer.ca.gov/DocumentCenter/View/117996/2026-100626-FINAL-Agenda?bidId=)
- [Planning archive](https://www.placer.ca.gov/10617/_2026)
- [October 15 Planning agenda](https://www.placer.ca.gov/DocumentCenter/View/118212/2026-101526-FINAL-Agenda)
- [Tahoe Basin committee archive](https://www.placer.ca.gov/AgendaCenter/Tahoe-Basin-Design-Review-Committee-13)
- [August 25 committee agenda](https://www.placer.ca.gov/AgendaCenter/ViewFile/Agenda/_08252026-6577)
- [September 22 committee agenda](https://www.placer.ca.gov/AgendaCenter/ViewFile/Agenda/_09222026-6619)

Retest 2026-10-20: after the fetcher's User-Agent was changed to identify the project URL, one request to `https://www.placer.ca.gov/` still returned HTTP 403, so the User-Agent text is not the cause here.

Retest 2026-11-03: one request to `https://www.placer.ca.gov/` returned HTTP 403 whose body is a Cloudflare "Attention Required" block page. Alternatives checked the same day, one request each, to replace the blocked host after the hand-built records were retired (decision 0033):

- `placer.legistar.com` answers, but its calendar holds only 1997 to July 2015 ("Agenda Info 1997 - July 2015"); the Legistar web API for the `placer` client returns HTTP 500.
- No Granicus (`placer.granicus.com`) or CivicClerk (`placerca`) tenant exists.
- The county's GovDelivery bulletins attach some agenda PDFs on `content.govdelivery.com`, which answers, but the account publishes no bulletin archive or topic feed (`/accounts/CAPLACER/bulletins` and topic `feed.rss` return 404), so there is no index to discover them from.
- Recordings already arrive from the county's YouTube channel (`config/meeting-records.json`).

So the three bodies have no live meeting source; their coverage notes say so.

## Current behavior

The whole `www.placer.ca.gov` host is listed in [`config/skipped-endpoints.json`](../../config/skipped-endpoints.json), so scheduled and backfill runs make no requests to it until the entry is removed.

Keep the sources followed and retain verified notices and any previously extracted documents. A failed PDF no longer discards its meeting notice. Errors are recorded and the command returns a partial-failure result. No empty agenda is represented as complete. The misattributed October 1 OVDRC cancellation is quarantined with its original source metadata, independently of these download problems.

## Next investigation

Compare a permitted browser download with a normal public request; check agency-supported alternate download/RSS endpoints or ask the county for an automation-friendly public source. Avoid repeated request bursts. Do not disable certificate verification. No remote issue or external message has been sent.

## Done when

The reusable worker can retrieve the small official agenda PDFs and category index on consecutive runs, preserve stable source IDs and citations, and clearly report any genuine missing/failed documents. No permanent PDF archive is required.
