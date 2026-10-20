# SOURCE-002: California State Lands Commission and Tahoe Conservancy pages block automated requests

Status: open, found 2026-10-20 while filling agenda-less meetings
Related: [SOURCE-001](SOURCE-001-placer-downloads.md), [decision 0015](../decisions/0015-deterministic-agenda-layouts.md)

## Affected registry entries

`state-lands` (California State Lands Commission) and `ctc` (California Tahoe Conservancy Board, `tahoe.ca.gov`). Overview: [source coverage](../operations/source-coverage.md).

## Observed behavior

The official meeting pages at `slc.ca.gov` and `tahoe.ca.gov` are hosted behind Cloudflare (WP Engine). A plain request from this worker receives a Cloudflare "Sorry, you have been blocked" page instead of the meeting page, so agenda links cannot be discovered or fetched. Examples:

- [State Lands, October 21, 2026 agenda page](https://www.slc.ca.gov/meeting/10-21-26-agenda/)
- [Tahoe Conservancy, September 17, 2026 board meeting](https://tahoe.ca.gov/california-tahoe-conservancy-board-meeting-september-17-2026/)

Affected records (notice-only; the site says the agenda is on the agency website): State Lands 2026-06-23, 07-28, 08-25, 10-21; Tahoe Conservancy 2026-03-19, 06-18, 09-17.

## Current behavior

Meeting notices stay published with official links. The meeting page says the agenda is available on the agency website instead of claiming it has not been posted. No attempt was made to work around the bot protection.

## Next investigation

Look for an agency-supported, automation-friendly source: a direct document host, an RSS or listserv feed, or the State Lands agenda PDFs on a different host. Or ask the agencies. Keep verified TLS. Do not imitate a browser to evade the block.

## Done when

The worker can retrieve these agendas from an agency-supported endpoint on consecutive runs. Then add layouts for both agencies, with fixtures.
