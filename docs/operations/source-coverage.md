# Source coverage and blockers

Updated October 20, 2026 (adapter expansion). This is the per-agency answer to "how does each followed body get into the site?" The registry is [`packages/domain/agencies.json`](../../packages/domain/agencies.json): 44 entries, 38 followed. Six disabled entries ([source review](source-review.md)) are omitted below.

## How meetings arrive

The scheduled job (`tahoe-ingest run-scheduled`, [decision 0018](../decisions/0018-scheduled-publication.md)) has two inputs:

1. **Live discovery.** Each run checks the website of every followed entry that has a `discoveryAdapter`. Only these entries stay current on their own.
2. **Tracked config.** The job loads `config/verified-agency-meetings-*.json`, `config/one-off-meetings.json`, and `config/public-comment-periods.json`. These are snapshots found and checked by hand. Nothing refreshes them. An entry that has only these goes stale and misses new meetings.

An entry's coverage is therefore one of: **automatic**, **snapshot only**, or **blocked** (the official source refuses automated requests, so neither a better adapter nor a retry helps until the source or the agency changes). "Blocked" and "snapshot only" are different. A snapshot-only entry may just need an adapter written.

## Automatic: live discovery (29 followed entries)

| Registry ID | Platform / adapter |
| --- | --- |
| `ttd` | Built-in (WordPress meeting posts) |
| `slt` | Built-in (Granicus, City Council) |
| `truckee` | CivicPlus calendar, plus Municode agenda index |
| `slt-planning`, `slt-parks-recreation-commission` | Granicus, body filter |
| `douglas-transportation`, `douglas-commissioners`, `douglas-planning` | Granicus (one county publisher), body filter. Commissioners and Planning exclude the duplicate "Supplemental" rows. Rows with a video link are archive recordings, so their listed time is not used as a start time |
| `tcpud`, `tcpud-parks-recreation-committee` | Granicus, body filter |
| `el-dorado-planning`, `el-dorado-supervisors`, `washoe-planning`, `washoe-commissioners` | Legistar department pages. Only meetings the county has made public appear (unpublished future rows are "not viewable"). Cancellations are read from the calendar |
| `south-shore-tma`, `stpud`, `placer-transportation` | Link index of agenda links (`stpud` and `placer-transportation` use the same hosted special-district site). Cancelled meetings and meetings without a posted agenda are not discovered |
| `ntpud` | WordPress category RSS feed, filtered to the Board of Directors. Special meetings get their own IDs |
| `ndep` | Dated table of Board for Financing Water Projects meetings (two-digit years). Dates are listed before agendas; placeholders more than 120 days ahead are ignored |
| `truckee-tahoe-airport` | New `meeting-files` type: dated table plus the "next meeting" block, each linking to a per-meeting file list from which the agenda is read |
| `ivgid` | CivicClerk |
| `trpa-governing`, `trpa-advisory` | TRPA meeting-materials archive |
| `lahontan-board`, `california-fish-game` | Annual dated table (`indexUrlTemplate` with `{year}`) |
| `truckee-north-shore-tma`, `trcd`, `ntcd` | Link index (NTCD reads a published Google Sheet) |
| `placer-tahoe-design-review` | CivicPlus Agenda Center. **Notices are found; agenda PDFs are blocked, see below** |

Item parsing is separate from discovery. Some agencies' agenda layouts have no item parser yet (for example Fish and Game), so those meetings show the notice and official link without itemized, topic-tagged agenda items.

## Blocked

These sources refuse automated requests. The project does not evade bot protection or disable TLS verification, and it does not request in bursts. Detailed evidence and next steps are in the linked issue.

| Registry ID | Body | What is blocked | Why | Issue |
| --- | --- | --- | --- | --- |
| `placer-supervisors` | Placer County Board of Supervisors | Agenda PDFs and archive page: HTTP 403 | Cause not established. Same result from Python and from curl with normal TLS. DocAccess or automated-request filtering is a hypothesis only | [SOURCE-001](../issues/SOURCE-001-placer-downloads.md) |
| `placer-planning` | Placer County Planning Commission | Agenda PDFs and archive page: HTTP 403 | Same as above | [SOURCE-001](../issues/SOURCE-001-placer-downloads.md) |
| `placer-tahoe-design-review` | Tahoe Basin Design Review Committee | Agenda PDFs: HTTP 403. The Agenda Center archive lists notices, so dates and times are kept | Same as above. The official `rss.aspx` index also returns 403, so no feed is configured | [SOURCE-001](../issues/SOURCE-001-placer-downloads.md) |

Behavior while blocked: the meeting notice stays published with its official link, a failed document does not discard the notice, the meeting page says the agenda is on the agency website instead of claiming it has not been posted, and the run records the error and returns a partial-failure result.

`placer-transportation` is not part of the Placer County site. It is published by the agency itself at `pctpa.net`, which is not blocked, and it now has live discovery.

**Update on `ctc` and `state-lands`.** These were listed as blocked (SOURCE-002). On October 20, 2026, after the fetcher began identifying itself as `TahoePublicMeetings/0.1 (+https://tahoepublicmeetings.org)` instead of `(+local research prototype)`, one request each to the Tahoe Conservancy and State Lands meeting pages returned real pages (HTTP 200), not the Cloudflare block page. A User-Agent rule is a plausible explanation, but it is a hypothesis. `www.placer.ca.gov` still returned 403 with the new identification. Both are now snapshot-only with fetchable sources; no adapter has been written yet.

## Snapshot only, not blocked (7 followed entries)

These have no adapter. Their meetings come only from tracked config snapshots, so they do not update.

| Registry ID | Source | Why there is no adapter |
| --- | --- | --- |
| `ctc` | `tahoe.ca.gov` board meeting posts | Fetchable since the User-Agent change (see above). Not yet built: needs a post index or feed and agenda layouts ([SOURCE-002](../issues/SOURCE-002-cloudflare-blocked-agendas.md)) |
| `state-lands` | `slc.ca.gov` meeting pages | Same as above |
| `california-state-parks` | Per-commission pages: `ohv.parks.ca.gov` (Off-Highway Motor Vehicle Recreation Commission) and `parks.ca.gov` (State Park and Recreation Commission) | Statewide commissions. Each page is a free-form accordion or file list whose headings and file names change per meeting ("Commission Meeting: August 6 and 7", "Meeting Agenda"). No dated index exists in the registry, and no existing discovery type fits two different sites with irregular layout. Only an occasional Tahoe item. Recommendation: keep the snapshot, or move to one-off meetings. The maintainer decides |
| `nevada-state-parks` | One public-hearing notice (`parks.nv.gov`) | A single regulation hearing, not a recurring meeting index. Keep as a one-off |

### Comment-period-only: `ltbmu`, `tahoe-usfs`, `usfws`

Checked October 20, 2026, one request each. All three newsrooms respond normally (HTTP 200) and none publishes a meeting series.

- `ltbmu` (Lake Tahoe Basin Management Unit): the newsroom lists closures, project updates, a memorial, and a newsletter. No meetings and no open comment periods on that page.
- `tahoe-usfs` (Tahoe National Forest): fire-restriction and prescribed-fire releases and feature stories. No meetings and no comment periods on that page. A kids' fishing day is an event, not a public meeting.
- `usfws` (Reno office): species listing news. Two items in about 20 months announced virtual public hearings (February 2026 Fish Lake Valley tui chub, March 2025 bleached sandhill skipper); several others were comment periods. Most are not in the Tahoe Basin.

These are sources of public comment periods, handled by `config/public-comment-periods.json`, not meeting bodies. Recommendation for the maintainer: remove `"meeting"` from `participationTypes` for `ltbmu` and `tahoe-usfs`; for `usfws` it is optional, since hearings are rare and usually outside the basin. The registry was not changed.

## Maintaining this page

When an adapter is added, move the entry to the automatic table and delete its snapshot row. When a source turns out to be blocked, add it to the blocked table with the evidence, create or extend a `docs/issues/SOURCE-NNN` file, and keep the entry followed so its snapshot meetings stay visible. Update the counts above and in [implementation status](../planning/implementation-status.md).
