# Topics, agencies, and one-off meetings

These are local, version-controlled operator settings. No login or admin page is needed. Run commands from the repository root. Keep IDs stable: changing an ID creates a different agency, topic, or meeting.

## Topics

Edit [`packages/domain/topics.json`](../packages/domain/topics.json). This ordered catalog controls every public topic selector, Python classification, exports, and operator tags. The seven current topics are Biking, Transit, Housing, Short Term Rentals, Environment, Elections, and Hot Topics. Elections covers elections, ballot measures, and an agency's support of or opposition to a measure; implementing an already-approved tax is tagged by its subject instead. Biking includes pedestrian and road repair items. Short Term Rentals includes Housing, even when a separate override excludes Housing.

`titlePatterns` contains named Python regular expressions, matched case-insensitively against each item's title. `includes` lists other topic IDs implied by a tag. Hot Topics started with Glyphosate, Incline Village Mobility Hub / 771 Southwood Blvd, Vacancy Tax, and Palisades redevelopment; license plate readers and other surveillance cameras or technology were added on October 27, 2026 (the `surveillance` pattern skips disease and mosquito surveillance). Adjust these patterns over time. This is a watchlist, not a claim that attendance was measured or predicted. A generic mobility hub is Transit; only the named Incline hub or Southwood address is on the initial watchlist.

After editing:

```sh
python3 scripts/project.py worker reclassify --dry-run
python3 scripts/project.py worker reclassify
python3 scripts/project.py refresh
```

Rule classification uses each item's title, never neighboring page excerpts. Supporting-report subjects can be missed. An agency entry may add `topicHints`: title patterns that tag a topic only for that agency's items, where a word means more than it does elsewhere. TTD and its committee tag Transit from "fleet", an operating or service budget, or a service change or route number: `"topicHints": {"transit": [{"rule": "ttd-fleet", "pattern": "\\bfleet\\b"}, ...]}`. A topic's hints apply only when the catalog's own patterns did not tag it, the first matching hint counts, and its `rule` name is recorded as the tag's provenance. Hints must name catalog topics; `reclassify` applies an edit wherever it changes an item's topics.

### Claude classification

With `ANTHROPIC_API_KEY` set, Claude tags items from their title and agenda excerpt ([decision 0011](../docs/decisions/0011-claude-for-automated-analysis.md)). Haiku runs first; Sonnet takes over a meeting when Haiku's reply fails twice; rule tags remain when both fail. A current Claude result replaces the rule tags in the export, including an empty result for an item outside the Tahoe area. Overrides still apply on top.

```sh
python3 scripts/project.py worker classify-topics --dry-run   # pending meetings and estimated cost
python3 scripts/project.py worker classify-topics             # only new or changed meetings
python3 scripts/project.py refresh
```

Editing the catalog or [the prompt](../workers/ingestion/tahoe_ingest/prompts/topic_classification.md) changes the prompt hash, so the next run reclassifies everything (about $0.02 for the current 325 items). To calibrate a judgment call, add an entry to a topic's `judgmentExamples`, such as `{"item": "Emerald Bay Resort fiscal report", "tag": false, "why": "small development"}` under Hot Topics. Record your labels in [`tests/fixtures/topic-gold.json`](../tests/fixtures/topic-gold.json) and check a prompt change with `worker evaluate-topics --gold tests/fixtures/topic-gold.json`. Use `worker tag ITEM_ID TOPIC_ID --author the maintainer` for a manual assignment. Removing a topic from the catalog hides it publicly; its human overrides remain stored for possible restoration. Generated tags are replaced by reclassification. Adding a topic without patterns is supported for manual tagging.

## Agencies and bodies

Edit [`packages/domain/agencies.json`](../packages/domain/agencies.json). Each entry has a stable `id`, display names, optional `aliases` (other names readers may type into the website's agency filter, such as acronyms and full official names), `follow`, optional operational URLs, an optional `discovery` block (see [below](#reviewed-source-configuration-and-repeatable-acquisition)), and an optional regular meeting `schedule` (see [below](#meeting-schedules-and-expected-meetings)).

The registry preserves **all 41 CSV rows**, plus the existing Town of Truckee entry and two explicitly requested committee entries, for **44 entries**. Six entries are disabled by the maintainer's source-review decisions with `follow: false`; they remain in the registry for provenance. Rows include distinct boards, offices, newsletters, and organizations; these are not necessarily 41 separate jurisdictions or meeting bodies. No rows were merged or dropped. `legacy` preserves all six original CSV cells, original row number, and the supplied February 1, 2023 update date. All imported metadata is explicitly unverified. It is research input only: the software never reads `legacy.meetingSchedule`. Regular meeting patterns live in the separate, validated `schedule` field. Original website and notification cells may contain broken or misplaced links.

`follow: true` selects an entry for ingestion/publication. Set it to `false` (or remove the entry) to stop accepting new records and hide its meetings on the next export, while keeping historical database records and editorial edits. Apply name/URL changes and new entries with:

```sh
python3 scripts/project.py worker init-db
python3 scripts/project.py refresh
python3 scripts/project.py worker agencies
```

An entry has live discovery when it carries a `discovery` block: its `type` names one of the source formats [below](#reviewed-source-configuration-and-repeatable-acquisition), and the rest of the block says where and how to read it. Adding a body whose source uses a supported format needs only that block, a fixture test, and a source-review record; code never branches on an agency ID to discover meetings. Setting `follow` alone does not implement discovery: entries without a `discovery` block remain manual sources. Body-specific filters keep unrelated commissions out. Operational URLs were verified during the source review where possible; `legacy` cells remain unverified and are never fetched automatically.

An entry can also carry a standing `participation` block for details the agency publishes on its website rather than in each agenda: `watchUrl` (livestream), `url` (comment form or participation page), `email`, and `instructions`, plus a required official `sourceUrl` and `checkedOn` date (`YYYY-MM-DD`). The export uses these only when neither the source adapter nor the agenda supplies the field ([decision 0021](../docs/decisions/0021-participation-details-from-agendas.md)). Recheck them when the agency changes its process.

An entry's optional `staffReports` block chooses how `map-staff-reports` links its items to their own staff reports ([decision 0026](../docs/decisions/0026-staff-reports-for-every-agency.md)): `rule` names a rule in [`staff_report_rules/`](../workers/ingestion/tahoe_ingest/staff_report_rules/README.md), and the other keys are that rule's parameters, for example `{"rule": "legistar", "client": "washoe-nv", "body": "Board of County Commissioners", "report": "staff-report"}` or `{"rule": "ttd-packet", "footer": "(?:RTCPC\\s+)?AGENDA ITEM"}`. An agency on a format that already has a rule needs only this block (plus a fixture and a source review record). Without a rule, a `note` says why, with `"published": false` when the agency publishes no reports a rule could use (`{"published": false, "note": "A one-page agenda with unnumbered items and no staff reports."}`) or `true` when reports exist but cannot be reached or attached yet. A malformed block, an unknown rule, or a missing, unknown, or invalid parameter stops the registry from loading.

Public agency filters and coverage lists include followed agencies with actual stored meetings. A manually added notice's official URL is used as a source fallback when registry URLs are absent. The registry itself is not a claim of comprehensive live coverage. Existing recurring-cost constraints remain under $20/month; expanding the watchlist does not create cloud jobs or approve a larger budget.

## Source review flags

[`source-reviews.json`](source-reviews.json) tracks source questions independently of `follow`. The readable [source review queue](../docs/operations/source-review.md) has stable SR IDs, official links, observed problems, and the input needed to resolve each. A pending flag does not disable a source, hide its meetings, or imply that download failure means no meetings exist. Record the maintainer's decision and supplied URLs in `resolution` when resolving an entry; keep the review ID stable. Recovered access failures and missing item parsers are distinguished from unresolved download problems.
## Meeting schedules and expected meetings

An entry's optional `schedule` describes its regular meeting pattern. The public export uses it to add **expected meetings**: dates in the next 90 days when the agency usually meets but has not yet published an agenda or notice. Each export derives them again. They are never stored as meetings. The timeline hides them unless a reader selects **Show expected meetings**; expected cards have a dashed outline and an "Expected · no agenda yet" label.

```json
"schedule": {
  "source": "configured",
  "note": "Why this schedule is trusted",
  "rules": [
    {"ordinal": 1, "weekday": "wednesday", "time": "15:00", "timezone": "America/Los_Angeles", "label": "TTD Board of Directors Meeting"}
  ]
}
```

Each rule has:

- `weekday`: a lowercase English weekday.
- `ordinal`: `1`–`4` or `"last"`, as in "the first Wednesday of the month". Use two rules for a pattern such as the second and fourth Tuesday.
- `every` with `anchor` instead of `ordinal`: a meeting every N weeks (`2` = every other week), counted from a real meeting date (`YYYY-MM-DD`, on that weekday).
- `months` (optional): month numbers 1–12 for quarterly or seasonal bodies. Omit it for every month.
- `time`: the 24-hour local start, `HH:MM`. `timezone` must be `America/Los_Angeles`.
- `label`: the expected meeting's title.

`source` is `configured` (verified by an operator, such as the maintainer's statement that TTD meets on the first Wednesday at 3 PM, or an official calendar) or `learned`. Learned schedules also record `confidence` (match rate) and `learnedFrom` (meetings considered and matched, date span, and learning date). `note` is optional. A malformed schedule stops the registry from loading.

### Learning schedules from stored meetings

```sh
python3 scripts/project.py worker learn-schedules --dry-run   # report only; the default
python3 scripts/project.py worker learn-schedules --agency truckee
python3 scripts/project.py worker learn-schedules --write     # store proposals
python3 scripts/project.py refresh
```

The learner uses stored, non-canceled meetings with a verified start time. It excludes `one-off-` notices and titles naming special, joint, workshop, hearing, retreat, or study session meetings. It compares monthly ordinals, the 1st/3rd and 2nd/4th pairs, and every-other-week cadences at the most common start time. Each predicted date in the observed span that has no stored record counts against a pattern, so one stray third-week meeting does not create a twice-monthly schedule. The report lists support, exceptions (off pattern or a different start time), and pattern dates with no stored record.

A schedule is proposed only with **at least 3 matching meetings and a match rate of at least 60%**; otherwise the result is "insufficient data". `--write` fills or updates `source: "learned"` schedules in `packages/domain/agencies.json` (or `--registry PATH`). It never replaces `source: "configured"`, and it keeps an existing learned schedule when newer data is insufficient. Review the Git diff before committing; `--json` prints the full report. The [October 20 findings](../docs/research/meeting-schedule-patterns-2026-10-20.md) list every agency's result.

To correct a pattern, edit its rules and set `source` to `configured`. Remove `schedule` to stop expected meetings for that agency.

### Forecast horizon and replacement

`refresh` and `worker export` derive expected meetings from tomorrow through **90 days** ahead. Set `TAHOE_FORECAST_DAYS` (for example in `.env`) or pass `worker export --out PATH --forecast-days N` to change it; `0` disables expected meetings, and the maximum is 366. Any stored record from the same agency within three days of an expected date replaces it. That includes a published agenda, a date-only notice, a one-off notice, or a cancellation. Expected meetings appear only for agencies with at least one stored meeting, and never for past dates. They have no documents, agenda items, topics, meeting page, or calendar download, so topic filters never match them. Each card links to the agency's official page. Holidays and summer recesses are not modeled.

## One-off workshops, hearings, and public meetings

Edit [`one-off-meetings.json`](one-off-meetings.json). The initial file is empty: no hypothetical event is published. Add a meeting under `meetings` using this shape, replacing all example values with a real official notice:

```json
{
  "meetings": [
    {
      "agency": "slt",
      "id": "one-off-housing-workshop-YYYY-MM-DD",
      "title": "Housing and VHR policy public workshop",
      "source_meeting_url": "https://OFFICIAL-AGENCY-SITE/PATH-TO-NOTICE",
      "date": "YYYY-MM-DD",
      "timezone": "America/Los_Angeles",
      "status": "scheduled",
      "location": null,
      "topics": ["housing", "short-term-rentals"],
      "participation": {"instructions": null, "url": null, "remoteUrl": null, "deadline": null},
      "items_complete": false,
      "documents_complete": false
    }
  ]
}
```

The owning agency must be followed. Add a new agency entry first if needed. `topics` tags the meeting notice so it matches timeline filters before an agenda is posted; it does not create agenda items or summaries. Area-plan workshops can be tagged according to their actual subject, such as Housing or Environment. Only use Hot Topics when it belongs on the watchlist.

Omit `start_time` until confirmed. When verified, add e.g. `"start_time": "18:00"`; this enables the existing calendar download. A date alone never invents a time. An offset-aware `starts_at` is also supported; ambiguous daylight-saving local times require an explicit offset or `fold`.

Preview, import, and refresh:

```sh
python3 scripts/project.py worker ingest-one-offs --dry-run
python3 scripts/project.py worker ingest-one-offs
python3 scripts/project.py refresh
```

An alternative config path can follow `ingest-one-offs`. The default path is relative to the working directory; installed/container workers need a mounted manifest and explicit path. Regular `refresh` only exports stored data; it does not import edits to this file.

Reimporting the same agency and ID updates the record. Keep that ID when the meeting is rescheduled; edit the date/time and use `status: "rescheduled"`. For cancellation set `status: "canceled"` and import again. Removing an entry from the file does not delete an already published meeting; this prevents accidental loss of cancellation history. Removing a topic from a notice requires changing its `topics` list and reimporting.

Manual imports do not claim a fresh automatic source check. The notice links to the official page, and the default warning asks readers to check it for updates. One-off notices are never evidence for a regular schedule, but a notice within three days of an expected date replaces that expected meeting. Nothing is generated from the legacy CSV schedule cells. If an agenda arrives, use the existing `ingest-manifest`/`refresh-document` pipeline with the same agency/meeting ID and official PDF URLs. PDFs remain temporary; extracted text, citations, and provenance persist. Keep `items_complete` and `documents_complete` false for a notice-only update so existing extracted material is preserved.

A future authenticated admin page could edit these same records if this becomes frequent; it is deferred, along with login and remote write endpoints.

## Meeting durations

Edit [`meeting-durations.json`](meeting-durations.json). Every exported meeting gets a length so calendar events have an end time. Observed or published lengths always win; otherwise the model estimates, always rounding up and biased long, and the export and calendar label it as an estimate.

`defaults` apply to all agencies; an entry under `agencies` (by agency ID) overrides any setting, and its `sectionWeights` merge with the defaults. Settings:

- `mode`: `fixed` (the body keeps a roughly constant length; `fixedMinutes`) or `estimated` (`baseMinutes` plus per-item minutes).
- `sectionWeights`: regular expression -> minutes per matching agenda item, matched case-insensitively against the item's section and title together. The heaviest match wins; items matching nothing use `perItemMinutes`. Edit these to say which kinds of items take long.
- `longBiasFactor` multiplies the result; `minMinutes`/`maxMinutes` clamp it; `roundUpToMinutes` rounds up. `notes` is free text.

Tune with data:

```sh
python3 scripts/project.py worker set-duration MEETING_ID --minutes 150 --source minutes   # or recording|manual
python3 scripts/project.py worker detect-durations            # dry run; --apply records minutes-text times
python3 scripts/project.py worker duration-report             # per-agency observed vs. estimated; --json available
python3 scripts/project.py refresh
```

See [the pacing research](../docs/research/meeting-duration-pacing-2026-10-20.md) for the initial values. Installed or container workers bundle this file; rebuild after editing.

## Reviewed source configuration and repeatable acquisition

The maintainer's source review is recorded in [decision 0014](../docs/decisions/0014-reviewed-sources-and-public-comment-periods.md). The registry now has 44 entries, of which 38 are followed. Disabled entries stay in the file with `follow: false`. Sources with live discovery carry a `discovery` object with a verified `type`, an `https` `indexUrl`, and an exact `bodyPattern` (a case-insensitive regular expression; without one every row qualifies). For example, the TTD committee:

```json
"discovery": {
  "type": "wordpress-meetings",
  "indexUrl": "https://tahoetransportation.org/wp-json/wp/v2/meeting",
  "bodyPattern": "Regional (?:and )?Transit (?:and |& )?Capital Programs",
  "documentIdPrefix": "ttd"
}
```

Loading the registry checks each block against its type (`TYPES` in `tahoe_ingest/source_discovery.py`) and rejects keys the type does not read. The retired `discoveryAdapter` key is rejected too.

| Type | `indexUrl` and what is read | Also reads |
| --- | --- | --- |
| `granicus` | Granicus `ViewPublisher.php`; rows are matched by their text and keep the HTML agenda | `title`, `titlePrefix` |
| `granicus-row-ids` | Granicus `ViewPublisher.php` whose rows carry IDs such as `10-06-2026-City-Council`; the ID gives the date and title (`bodyPattern` matches "10 06 2026 City Council"), and only the row's PDFs are documents | |
| `legistar` | A Legistar department page; minutes and video come from the public web API | `title`, `legistarClient` |
| `civicplus` | A CivicPlus Agenda Center category | `title`, `idPrefix` |
| `municode` | The Municode meetings page, `https://meetings.municode.com/MeetingsPage/index?clientCode=…`; its public API is queried 90 days either side of today | |
| `civicclerk` | A CivicClerk portal; its public event API supplies current agenda file IDs | `apiUrl`, `portalUrl` |
| `link-index` | Rolling dated agenda links, or a published Google Sheets meeting library | `title`, `idPrefix`, `qualifySpecial`, `minutesLinks` |
| `trpa` | TRPA meeting materials; each meeting page is read | `idPrefix`, `documentPattern` |
| `dated-table` | An annual table with dates in its first column | `title`, `datePattern` |
| `meeting-files` | A dated table or "next meeting" block whose links open per-meeting file lists | `title`, `filesLabel` |
| `rss` | A category feed, filtered by body | `titlePrefix` |
| `wordpress-meetings` | A WordPress REST collection of meeting posts, which several bodies may share | `documentIdPrefix` |

Every type except `granicus-row-ids`, `municode`, `civicclerk`, and `wordpress-meetings` also takes `indexUrlTemplate` and `horizonDays`. Optional keys: `title`, `titlePrefix` (Granicus, RSS), `idPrefix` (a prefix for date-based source IDs), `qualifySpecial` (link-index: give special meetings and workshops their own ID and title), `minutesLinks` (link-index: attach minutes PDFs on the index to the agenda row of the same date, read from the link's aria-label, label, or file name), `datePattern` and `horizonDays` (dated-table: restrict to one table and ignore far-future placeholders), `filesLabel` (meeting-files), `documentPattern` (trpa: how the meeting page names the body's documents when that differs from `bodyPattern`, for example "APC"), and `documentIdPrefix` (wordpress-meetings: stored document IDs start with this instead of the agency ID; the TTD committee keeps `ttd`, the prefix of its first captures). WordPress posts state no meeting date, so a scheduled run reads at most 6 of them and capture takes the date from the post title or agenda. A TRPA meeting page links committee agendas, staff reports, attachments, and public comments beside the board's own documents; discovery keeps only the body's agenda and its full packet, in that order, as `<agency>-agenda-<date>` and `<agency>-agenda-packet-<date>`, and marks them the meeting's complete document set. Elsewhere, when a meeting offers a short agenda and a packet, the packet is ordered second so the default two captured documents are the agenda and the packet. The generic link index supports rolling dated agenda links and published Google Sheets meeting libraries. Annual-table sources (Lahontan and Fish and Game) use `indexUrlTemplate` with `{year}` for the current annual index, retaining dated notices and cancellations before agendas are posted. A multi-day date uses the first day and directs readers to the agenda for subsequent sessions. Truckee's official agenda page embeds its public Municode meeting index, which discovery reads directly.

For example, from the repository root:

```sh
python3 scripts/project.py worker discover tcpud-parks-recreation-committee --limit 3 --out data/live/tcpud-parks-next.json
python3 scripts/project.py worker capture data/live/tcpud-parks-next.json --max-meetings 3 --max-documents 1
python3 scripts/project.py refresh
```

The source collector checks the index again on each invocation; `refresh` alone exports stored data and forecasts, and does not fetch new agendas. A future agenda-less notice can be rechecked with the same commands once documents are posted. No cloud scheduling was provisioned here. The SLT RSS index exposes an All feed rather than a verified commission-specific feed, so the body-filtered Granicus source is used. Placer RSS/download failures are tracked in [SOURCE-001](../docs/issues/SOURCE-001-placer-downloads.md).

Acquisition prefers small agendas over full packets, supports HTML/plain text as well as PDFs, and stores the detected format. HTML citations refer to source sections and link to the webpage without invented PDF page numbers. A complete supported structured HTML agenda can publish exact cited headings; other layouts retain text for later parsing. Bounded captures keep existing unrelated documents unless the candidate explicitly declares `documents_complete: true`. A failed document still leaves a verified meeting notice available with a retry warning. Verified TLS stays enabled for the native curl fallback; install `curl` and system CA certificates on worker hosts (included in the Dockerfile).

## Skipped endpoints

[`skipped-endpoints.json`](skipped-endpoints.json) lists URL prefixes the worker never requests, so a source known to answer HTTP 403 is not retried on every scheduled run. Each entry has a `prefix`, the observed `status`, the `since` date, and a `reason` (optionally an `issue` path). The skip applies to discovery indexes and document downloads alike. A skipped agency's index is reported as `skipped` in the run summary, not as a failure. Stored meetings whose documents are all skipped are not re-ingested, and a newly captured notice carries a warning that its documents are read at the official link.

To try an endpoint again, delete its entry (or set `skipped` to `[]`) and rebuild the container image, since the file is bundled with it. If the fetch fails again, add the entry back. Locally the file is read from `TAHOE_SOURCE_ROOT` (default: the working directory).

## Public comment periods

Edit [`public-comment-periods.json`](public-comment-periods.json) when an official comment window is verified. It starts empty because no current Tahoe-related deadline was verified during this source review. A record looks like:

```json
{
  "comment_periods": [{
    "agency": "ltbmu",
    "id": "stable-project-notice-id",
    "title": "Project name — public comment period",
    "opens_on": "YYYY-MM-DD",
    "closes_on": "YYYY-MM-DD",
    "official_url": "https://OFFICIAL-AGENCY-SITE/NOTICE",
    "participation_url": "https://OFFICIAL-AGENCY-SITE/COMMENT-INSTRUCTIONS",
    "instructions": "Submission methods stated in the notice",
    "topics": ["environment"],
    "source_quote": "Exact passage from the fetched notice establishing the comment window and deadline"
  }]
}
```

Run `python3 scripts/project.py worker ingest-comment-periods` and then `refresh`. The importer fetches the source, checks the exact quote, stores its text/hash/provenance, and upserts a stable agency/notice ID. Dates and instructions must come from that notice. `opens_on` may be omitted if unknown. `closes_on` is required; optionally include an offset-aware `closes_at` when the exact deadline/timezone is verified. Never infer a closing hour. The separate `/comment-periods/` page lists current and past windows; they are not board meetings or calendar events with fabricated durations. No comment is submitted by the app.

## Minutes and recording sources

`meeting-records.json` lists YouTube channels or playlists and Laserfiche minutes folders that `tahoe-ingest` matches to stored meetings each scheduled run ([decision 0025](../docs/decisions/0025-minutes-and-recording-links.md)). Listing links (Granicus, Legistar, CivicClerk, dated tables, TRPA pages, and link indexes with `minutesLinks`) need no entry here.

- `youtube`: `agency`, exactly one of `channelId` (`UC…`) or `playlistId`, an `include` title pattern, and optional `exclude` and `meetingPattern` (only meetings whose title matches). A video matches the agency's meeting on the date written in its title, once that date has passed. Use a channel feed unless a playlist is known to be current: several agencies' playlists stopped years ago, and their feeds return the oldest videos. Pin the channel that actually carries meetings; several agencies' obvious channels are empty or unrelated (see the [research](../docs/research/minutes-and-recordings-sources-2026-10-27.md)).
- `laserfiche`: `agency`, `repo`, and `folderId` of a folder of year folders whose documents are named `YYYY-MM-DD - Regular`, `- Special and Joint`, and so on.

`tahoe-ingest meeting-records --dry-run` reports matches without storing them.
