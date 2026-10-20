# Source coverage and blockers

Updated October 20, 2026. This is the per-agency answer to "how does each followed body get into the site?" The registry is [`packages/domain/agencies.json`](../../packages/domain/agencies.json): 44 entries, 38 followed. Six disabled entries ([source review](source-review.md)) are omitted below.

## How meetings arrive

The scheduled job (`tahoe-ingest run-scheduled`, [decision 0018](../decisions/0018-scheduled-publication.md)) has two inputs:

1. **Live discovery.** Each run checks the website of every followed entry that has a `discoveryAdapter`. Only these entries stay current on their own.
2. **Tracked config.** The job loads `config/verified-agency-meetings-*.json`, `config/one-off-meetings.json`, and `config/public-comment-periods.json`. These are snapshots found and checked by hand. Nothing refreshes them. An entry that has only these goes stale and misses new meetings.

An entry's coverage is therefore one of: **automatic**, **snapshot only**, or **blocked** (the official source refuses automated requests, so neither a better adapter nor a retry helps until the source or the agency changes). "Blocked" and "snapshot only" are different. A snapshot-only entry may just need an adapter written.

## Automatic: live discovery (19 followed entries)

| Registry ID | Platform / adapter |
| --- | --- |
| `ttd` | Built-in (WordPress meeting posts) |
| `slt` | Built-in (Granicus, City Council) |
| `truckee` | CivicPlus calendar, plus Municode agenda index |
| `slt-planning`, `slt-parks-recreation-commission` | Granicus, body filter |
| `douglas-transportation` | Granicus, body filter |
| `tcpud`, `tcpud-parks-recreation-committee` | Granicus, body filter |
| `el-dorado-planning`, `washoe-planning` | Legistar, body filter |
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
| `ctc` | California Tahoe Conservancy Board | Meeting pages at `tahoe.ca.gov`: Cloudflare block page | Cloudflare bot protection (WP Engine hosting). Agendas cannot be discovered or fetched | [SOURCE-002](../issues/SOURCE-002-cloudflare-blocked-agendas.md) |
| `state-lands` | California State Lands Commission | Meeting pages at `slc.ca.gov`: Cloudflare block page | Same as above | [SOURCE-002](../issues/SOURCE-002-cloudflare-blocked-agendas.md) |

Behavior while blocked: the meeting notice stays published with its official link, a failed document does not discard the notice, the meeting page says the agenda is on the agency website instead of claiming it has not been posted, and the run records the error and returns a partial-failure result.

`placer-transportation` (Placer County Transportation Planning Agency) has no source URL in the registry. If it turns out to be hosted on `placer.ca.gov`, expect it to share the Placer block; this has not been tested.

## Snapshot only, not known to be blocked (15 followed entries)

These have no adapter. Their meetings come only from tracked config snapshots, so they do not update. None is known to be blocked, but none has been tested for an adapter either. The adapter-expansion work for them is described in [adapter expansion](adapter-expansion.md).

| Registry ID | Official source (from the registry) | Likely platform |
| --- | --- | --- |
| `douglas-commissioners`, `douglas-planning` | Douglas County Granicus publisher | Granicus (an adapter already exists; SR-025/026) |
| `el-dorado-supervisors` | El Dorado County Legistar | Legistar (an adapter already exists; SR-027) |
| `washoe-commissioners` | Washoe County official site | Unknown (SR-028) |
| `placer-transportation` | None in the registry | Unknown |
| `south-shore-tma` | `ss-tma.org/board-meetings/` | Unknown |
| `ntpud` | `ntpud.org/category/meetings/` | Unknown (WordPress-style category) |
| `stpud` | Unknown | Unknown |
| `truckee-tahoe-airport` | Unknown | Unknown |
| `ndep` | Unknown | Unknown |
| `california-state-parks`, `nevada-state-parks` | Statewide bodies kept for occasional Tahoe-relevant meetings (SR-011/012) | Unknown |
| `ltbmu`, `tahoe-usfs`, `usfws` | Federal newsrooms, kept mainly for public comment periods (SR-006/007/009) | Not meeting bodies. These may belong only in the comment-periods file |

## Maintaining this page

When an adapter is added, move the entry to the automatic table and delete its snapshot row. When a source turns out to be blocked, add it to the blocked table with the evidence, create or extend a `docs/issues/SOURCE-NNN` file, and keep the entry followed so its snapshot meetings stay visible. Update the counts above and in [implementation status](../planning/implementation-status.md).
