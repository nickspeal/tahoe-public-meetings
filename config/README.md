# Topics, agencies, and one-off meetings

These are local, version-controlled operator settings. No login or admin page is needed. Run commands from the repository root. Keep IDs stable: changing an ID creates a different agency, topic, or meeting.

## Topics

Edit [`packages/domain/topics.json`](../packages/domain/topics.json). This ordered catalog controls every public topic selector, Python classification, exports, and operator tags. The eight current topics are Biking, Transit, Housing, Short Term Rentals, Environment, Wildlife, Elections, and Hot Topics. Wildlife (added November 13, 2026) covers bears and other wild animals, human-wildlife conflict, wildlife crossings, and keeping trash away from wildlife; trash collection contracts, pickup fees, and recycling are not Wildlife, nor are pets or animal control. Its title patterns skip place and business names such as Elk Point, Coyote Crossing, Green Bear Construction, and the Wildlife Conservation Board, which funds habitat projects that are Environment. Elections covers elections, ballot measures, and an agency's support of or opposition to a measure; implementing an already-approved tax is tagged by its subject instead. Biking includes pedestrian and road repair items. Short Term Rentals includes Housing, even when a separate override excludes Housing.

`titlePatterns` contains named Python regular expressions, matched case-insensitively against each item's title. `includes` lists other topic IDs implied by a tag. Hot Topics started with Glyphosate, Incline Village Mobility Hub / 771 Southwood Blvd, Vacancy Tax, and Palisades redevelopment; license plate readers and other surveillance cameras or technology were added on October 27, 2026 (the `surveillance` pattern skips disease and mosquito surveillance). Adjust these patterns over time. This is a watchlist, not a claim that attendance was measured or predicted. A generic mobility hub is Transit; only the named Incline hub or Southwood address is on the initial watchlist.

After editing:

```sh
python3 scripts/project.py worker reclassify --dry-run
python3 scripts/project.py worker reclassify
python3 scripts/project.py refresh
```

Rule classification uses each item's title, never neighboring page excerpts. Supporting-report subjects can be missed. An agency entry may add `topicHints`: title patterns that tag a topic only for that agency's items, where a word means more than it does elsewhere. TTD and its committee tag Transit from "fleet", an operating or service budget, or a service change or route number: `"topicHints": {"transit": [{"rule": "ttd-fleet", "pattern": "\\bfleet\\b"}, ...]}`. A topic's hints apply only when the catalog's own patterns did not tag it, the first matching hint counts, and its `rule` name is recorded as the tag's provenance. Hints must name catalog topics. Unlike a catalog edit, a hint edit does not change the classifier version, which would rewrite every item's tags: `reclassify` compares each item's stored tags and evidence with the rules, so it rewrites only the items whose tags or provenance the edit changes. The scheduled run reclassifies every run, so production applies a hint edit on the first run of a worker image that bundles it; run `reclassify --dry-run` locally to see how many items it touches (`itemsChanged`, `itemsEvidenceChanged`).

### Claude classification

With `ANTHROPIC_API_KEY` set, Claude tags items from their title and agenda excerpt ([decision 0011](../docs/decisions/0011-claude-for-automated-analysis.md)). Haiku runs first; Sonnet takes over a meeting when Haiku's reply fails twice; rule tags remain when both fail. A current Claude result replaces the rule tags in the export, including an empty result for an item outside the Tahoe area. Overrides still apply on top.

```sh
python3 scripts/project.py worker classify-topics --dry-run   # pending meetings and estimated cost
python3 scripts/project.py worker classify-topics             # only new or changed meetings
python3 scripts/project.py refresh
```

Editing the catalog or [the prompt](../workers/ingestion/tahoe_ingest/prompts/topic_classification.md) changes the prompt hash, so the next run reclassifies everything (about $0.02 for the current 325 items). To calibrate a judgment call, add an entry to a topic's `judgmentExamples`, such as `{"item": "Emerald Bay Resort fiscal report", "tag": false, "why": "small development"}` under Hot Topics. Record your labels in [`tests/fixtures/topic-gold.json`](../tests/fixtures/topic-gold.json) and check a prompt change with `worker evaluate-topics --gold tests/fixtures/topic-gold.json`. Use `worker tag ITEM_ID TOPIC_ID --author the maintainer` for a manual assignment. Removing a topic from the catalog hides it publicly; its human overrides remain stored for possible restoration. Generated tags are replaced by reclassification. Adding a topic without patterns is supported for manual tagging.

## Agencies and bodies

Edit [`packages/domain/agencies.json`](../packages/domain/agencies.json). It is the one place an agency is defined ([decision 0027](../docs/decisions/0027-agency-definitions-in-configuration.md)): code registers reusable discovery types, agenda layouts, and staff report rules by format name and never branches on an agency ID. Each entry has a stable `id`, display names, optional `aliases` (other names readers may type into the website's agency filter, such as acronyms and full official names), optionally `extends` (see [below](#sibling-bodies-extends)), `follow`, optional operational URLs, an optional `discovery` block (see [below](#reviewed-source-configuration-and-repeatable-acquisition)), `agenda`, `staffReports`, and `topicHints` blocks, an optional `itemScope` (see below), an optional regular meeting `schedule` (see [below](#meeting-schedules-and-expected-meetings)), and an optional meeting `duration` (see [below](#meeting-durations)). An unknown key stops the registry from loading.

The registry preserves **all 41 CSV rows**, plus entries added since (the Town of Truckee, the parks bodies, committees, and the South Tahoe Transit JPA); [source coverage](../docs/operations/source-coverage.md) gives the current counts. Six entries are disabled by the maintainer's source-review decisions with `follow: false`; they remain in the registry for provenance. Rows include distinct boards, offices, newsletters, and organizations; these are not necessarily 41 separate jurisdictions or meeting bodies. No rows were merged or dropped. `legacy` preserves all six original CSV cells, original row number, and the supplied February 1, 2023 update date. All imported metadata is explicitly unverified. It is research input only: the software never reads `legacy.meetingSchedule`. Regular meeting patterns live in the separate, validated `schedule` field. Original website and notification cells may contain broken or misplaced links.

`follow: true` selects an entry for ingestion/publication. Set it to `false` (or remove the entry) to stop accepting new records and hide its meetings on the next export, while keeping historical database records and editorial edits. Apply name/URL changes and new entries with:

```sh
python3 scripts/project.py worker init-db
python3 scripts/project.py refresh
python3 scripts/project.py worker agencies
```

`worker agencies` prints followed entries as the worker reads them, with `extends` applied. Keep the file in canonical form: two-space JSON whose entry keys follow `AGENCY_KEYS` in `tahoe_ingest/catalog.py` (`id`, `name`, `shortName`, `page`, `aliases`, `extends`, `follow`, `meetingHost`, `officialUrl`, `sourceUrl`, `coverageNote`, `participation`, `participationTypes`, `discovery`, `agenda`, `staffReports`, `itemScope`, `planningCalendar`, `topicHints`, `schedule`, `duration`, `legacy`). A test checks it, and `learn-schedules --write` writes it that way.

An entry has live discovery when it carries a `discovery` block: its `type` names one of the source formats [below](#reviewed-source-configuration-and-repeatable-acquisition), and the rest of the block says where and how to read it. Adding a body whose source uses a supported format needs only that block, a fixture test, and a source-review record; code never branches on an agency ID to discover meetings. Setting `follow` alone does not implement discovery: entries without a `discovery` block remain manual sources. Body-specific filters keep unrelated commissions out. Operational URLs were verified during the source review where possible; `legacy` cells remain unverified and are never fetched automatically.

An entry can also carry a standing `participation` block for details the agency publishes on its website rather than in each agenda: `watchUrl` (livestream), `url` (comment form or participation page), `email`, and `instructions`, plus a required official `sourceUrl` and `checkedOn` date (`YYYY-MM-DD`). The export uses these only when neither the source adapter nor the agenda supplies the field ([decision 0021](../docs/decisions/0021-participation-details-from-agendas.md)). Recheck them when the agency changes its process.

An optional `coverageNote` is one or two short, plain sentences on a lasting limitation of this website for that body, such as recordings the agency does not publish, documents the worker cannot collect, or meetings it cannot forecast. The export copies it to that body's `agencies[]` entry, and the Coverage page shows it under the body's source status. It must be non-empty text when present, and a sibling body does not inherit it. Keep it to what stays true from run to run (a failed check already shows as the source status), leave participation guidance to meeting pages, and remove or reword it when the gap closes.

An entry's optional `staffReports` block chooses how `map-staff-reports` links its items to their own staff reports ([decision 0026](../docs/decisions/0026-staff-reports-for-every-agency.md)): `rule` names a rule in [`staff_report_rules/`](../workers/ingestion/tahoe_ingest/staff_report_rules/README.md), and the other keys are that rule's parameters, for example `{"rule": "legistar", "client": "washoe-nv", "body": "Board of County Commissioners", "report": "staff-report"}` or `{"rule": "ttd-packet", "footer": "(?:RTCPC\\s+)?AGENDA ITEM"}`. A numeric parameter is a JSON number: `douglas-packet` takes optional packet download limits, `{"rule": "douglas-packet", "maxMegabytes": 300, "maxPages": 2000}` (its defaults, sized to the largest 2026 packets). An agency on a format that already has a rule needs only this block (plus a fixture and a source review record). Without a rule, a `note` says why, with `"published": false` when the agency publishes no reports a rule could use (`{"published": false, "note": "A one-page agenda with unnumbered items and no staff reports."}`) or `true` when reports exist but cannot be reached or attached yet. A malformed block, an unknown rule, or a missing, unknown, or invalid parameter stops the registry from loading.

An optional `itemScope` block limits staff report mapping and Claude summaries to an agency's Tahoe items, for statewide bodies whose agendas are mostly about other places ([decision 0026](../docs/decisions/0026-staff-reports-for-every-agency.md#amendment-2026-11-02-tahoe-items-only-for-statewide-agencies)). `{"mode": "tahoe-only", "terms": ["Tahoe", "Truckee", ...], "note": "..."}` keeps an item when one of the terms appears, as whole words and ignoring case and line breaks, in its title or the agenda text it cites; `map-staff-reports` gives its rule only those items (a meeting with none downloads nothing), and `summarize-items` summarizes only those. Other items keep their agenda excerpt, with no warning; the scan report counts them as `outOfScope`. Without the block, or with `"mode": "whole-agenda"`, every item is processed, which is right for every Tahoe body. Topic classification still reads every item: its `tahoeRelevant` answer is what keeps far-away items out of the topic filters, and it costs well under a cent a meeting. Choose terms from how the agenda prints locations, and say in the `note` why counties or other broad names are in or out. The State Lands Commission uses `"Tahoe"` (every Lake Tahoe row names the lake) plus Tahoe and Truckee place names printed without it; El Dorado, Placer, and Nevada County alone do not qualify, since those counties reach Folsom Lake and the American River. Changing the terms changes which items a meeting maps, so the next run rescans it and drops mappings for items now out of scope. A malformed block stops the registry from loading.

Public agency filters and coverage lists include followed agencies with actual stored meetings. A manually added notice's official URL is used as a source fallback when registry URLs are absent. The registry itself is not a claim of comprehensive live coverage. Existing recurring-cost constraints remain under $20/month; expanding the watchlist does not create cloud jobs or approve a larger budget.

### Sibling bodies (`extends`)

Bodies that publish through the same website in the same formats, such as a board and its committee, can share configuration. `"extends": "ttd"` starts an entry from the `ttd` entry, as resolved, and the entry then gives only what differs:

```json
{
  "id": "ttd-rtcpc",
  "name": "TTD — Regional Transit and Capital Programs Committee",
  "aliases": ["Tahoe Transportation District", "RTCPC", "Regional and Transit Capital Programs Committee"],
  "extends": "ttd",
  "follow": true,
  "discovery": {"bodyPattern": "Regional (?:and )?Transit (?:and |& )?Capital Programs", "documentIdPrefix": "ttd"},
  "staffReports": {"footer": "(?:RTCPC\\s+)?AGENDA ITEM"}
}
```

- Inherited: `shortName`, the URLs, `participationTypes`, `discovery`, `agenda`, `staffReports`, `itemScope`, and `topicHints`, which describe the publisher's website, formats, and subject area.
- Never inherited: `id`, `name`, `page`, `aliases`, `follow`, `coverageNote`, `participation`, `schedule`, `duration`, and `legacy`. They describe the body itself: who it is and its page on this website, whether the maintainer follows it, what this website cannot show for it, how readers take part, when and how long it meets, and its research record. A committee does not meet when its board does, and disabling a board does not disable its committees.
- Objects merge key by key, recursively, and the child's value wins: the committee above keeps the board's discovery `type` and `indexUrl` and replaces `bodyPattern`. Lists and other values replace the parent's (a child's `agenda.layouts` is its whole list).
- A child cannot delete an inherited key. When a body's blocks differ in shape and not just in values (a `discovery.title` the parent has and the child must not), give it a full entry instead.
- The parent must be another entry; a chain resolves parent first, and a cycle stops the registry from loading. The resolved entry is validated like any other, so a parameter the inherited rule does not take is an error.

The worker resolves `extends` when it loads the registry, so every consumer sees complete entries. The website reads only `id` and `aliases` from the file, and `aliases` is never inherited. Use `extends` where it removes real duplication: today the TTD committee, the TRPA Advisory Planning Commission, and the Douglas County Planning Commission extend their boards. Bodies whose blocks differ in shape (El Dorado and Washoe planning commissions lack their boards' discovery `title`; the TCPUD parks committee has no agenda layouts) keep full entries.

### Other and per-record hosts

An entry with `"meetingHost": "per-record"` is not a body. `other` (named "Other") is the only one today: it holds one-off meetings and comment periods that no single followed body holds, such as an open house co-hosted by TRPA and El Dorado County. Every meeting and comment period under such an entry must name its `host`, which the website shows instead of the agency name. The loader rejects `meetingHost` with any other value, or on an entry with `discovery`, `agenda`, or `schedule`; `meetingHost` is never inherited through `extends`. The entry is the whole definition of Other: code asks `per_record_host_agencies()` for the entries that carry the property and never tests for the ID, and source coverage leaves these entries out of its counts.

### Planning calendars

Some agencies publish a tentative calendar of the subjects planned for their next meetings. An entry's optional `planningCalendar` block turns that calendar into previews: subjects shown on a future meeting, marked subject to change, before its agenda is posted (IDEA-018). Dates with no stored meeting get a planned meeting (`<agency>:planned-<YYYY-MM-DD>`), which the captured agenda later adopts.

```json
"planningCalendar": {"format": "slt-apc-attachment", "itemTitle": "Agenda Planning Calendar",
                     "attachmentPattern": "^APC \\d{2} \\d{2} \\d{4}\\.pdf$", "title": "Agenda Planning Calendar",
                     "bodies": {"*": {"id": "slt", "meetingTitle": "City Council Meeting"}}}
```

- `format` names a format in [`planning_calendar_rules/`](../workers/ingestion/tahoe_ingest/planning_calendar_rules/README.md); the other keys are its parameters. `slt-apc-attachment` reads the PDF attached to an agenda item (South Lake Tahoe), downloading each new calendar once. `ttd-packet-pages` reads the pages of the stored agenda packet headed `heading` (TTD), with no download.
- `title` is text the calendar's title must contain.
- `bodies` maps each body label the calendar prints to a followed entry and the title its planned meetings get; `"*"` maps a calendar that covers one body. Labels not listed are skipped, so TTD's TTC and Finance & Personnel columns are never read.
- The block is never inherited through `extends`: it belongs to the body whose agenda carries the calendar, and its `bodies` map covers the siblings.

Each calendar is parsed whole by deterministic rules. If any heading, column, or quote fails, nothing of it is published, the previous calendar's previews stay current, and the run summary names the calendar. A calendar redesign therefore stops that agency's previews until its format is updated. To add an agency on a known format, add the block, a reviewed fixture under `tests/fixtures/planning-calendars/`, and a test; then run `python3 scripts/project.py worker read-planning-calendars --agency ID --dry-run`, then without `--dry-run`. A new layout needs a format module (see the rules README). After fixing a format, `--force` reads stored calendars again from their stored text (see "Fixing a format after a bad calendar" in the rules README).

### Adding an agency

For a body whose website and documents use formats the worker already reads:

1. Add an entry to `agencies.json` in canonical key order, with `"extends"` if it is a sibling of an existing entry. Give it a `page` with a permanent URL `slug` and the `name` residents search for ("South Lake Tahoe City Council"); the website serves the body at `/meetings/<slug>/` and its meetings under it, so never change a published slug. Give it `discovery` (a type from [the table below](#reviewed-source-configuration-and-repeatable-acquisition)), `agenda.layouts` (from [`agenda-layouts.json`](agenda-layouts.json)), and `staffReports` (a rule from the [rules README](../workers/ingestion/tahoe_ingest/staff_report_rules/README.md), or a note on why there is none). For a statewide body whose agendas are mostly about other places, add an `itemScope` with its Tahoe terms. Add `schedule`, `duration`, `topicHints`, or a [`planningCalendar`](#planning-calendars) only when known, and a `coverageNote` when the body has a lasting gap readers should know about.
2. Check discovery against the live index: `worker discover ID --limit 3 --out data/live/ID.json`, then `worker capture` on a few results.
3. Add fixtures: a saved index page under `tests/fixtures/discovery/`, an agenda under `tests/fixtures/agenda-layouts/` with the reviewed items, and, with a rule, a staff report fixture under `tests/fixtures/staff-reports/`.
4. Record the review in [`source-reviews.json`](source-reviews.json) and, for recordings or minutes on YouTube or Laserfiche, add a feed to [`meeting-records.json`](meeting-records.json).
5. Run `python3 scripts/project.py worker coverage-doc` to regenerate [source coverage](../docs/operations/source-coverage.md), then the tests (`python3 scripts/project.py test`), then `init-db` and `refresh` as above.

Code changes are needed only for a format the worker does not read yet: a new discovery type in `source_discovery.py`, a layout the outline engine cannot describe (a parser in `agenda_custom.py`), or a new staff report rule module. Each is registered by format name, never by agency ID.

## Agenda layouts

An agency entry's optional `agenda` block names the agenda layouts that read its documents' items, tried in order ([decision 0015](../docs/decisions/0015-deterministic-agenda-layouts.md)):

```json
"agenda": {"layouts": ["trpa-governing-board"], "agendaBeforePacket": true}
```

`agendaBeforePacket` is for sources that link the agenda and a full packet that reprints it (TRPA): items come from the agenda, and the packet is read only when no agenda was captured, so an older packet can never void a revised agenda. An agency without `agenda` keeps its meetings' text and official links, with no items.

The layouts live in [`agenda-layouts.json`](agenda-layouts.json), keyed by format name. A layout describes a document format, not an agency: bodies whose agendas look alike share one (the TTD board and its Regional Transit and Capital Programs Committee both use `ttd`), so a new body that publishes in a supported format needs only its registry entry, a fixture test, and a source review record. The cover is what tells a body's agenda from an attachment or a sibling body's agenda in the same packet, so sibling bodies with different covers (Douglas County's three bodies, TRPA's two, the SLT commissions) keep their own layouts. A layout's keys (patterns are Python regular expressions, case-sensitive unless they start with `(?i)`):

- `label` (required): names the format in review notes ("NTPUD Board agenda numbering is not sequential…"; decision 0041). Changing it changes stored notes.
- `cover` (required): patterns that must all occur in the first `coverPages` pages (default 2). A document whose cover does not match is ignored as an attachment.
- `recognize`: patterns that identify the publisher's agenda when `cover` fails, for example after a cover redesign. Such a document blocks publication with a "cover was not recognized" review note instead of being ignored.
- `parser`: a hand-written parser for a format the outline engine cannot describe (`ntcd-table`, `south-shore-tma-bullets`, `ttd-sections`, `slt-council`, `truckee-council` in `agenda_custom.py`). It takes only `label`, `cover`, `coverPages`, and `recognize` besides.
- `levels` (required otherwise): outline levels from the top. Use a named level (`NUM` "1.", `NUM_LOOSE`, `NUM_TIGHT`, `UPPER` "A.", `UPPER_ANY`, `UPPER_BARE`, `LOWER` "a.", `ROMAN_LEVEL` "IV.", `LOWER_ROMAN`, `LEGISTAR_FILE` "12. 26-1714", `DOUGLAS_CHILD`); `{"level": "LOWER", "item": false}` for one tracked for numbering only, such as recommended actions; or an inline `{"kind": "num", "pattern": "...", "item": false}` whose pattern captures the label as group `v`. Kinds are `num`, `upper`, `lower`, `roman`, `lroman`, and `free` (a compound label such as `8.E.2` that captures its parent's label as `p`).
- `firstPages` (default 8): pages read. `start`: ignore lines before the first match. `end` (default: a line starting "Adjourn"): the ending marker; `endItem: false` leaves it out of the items. `minItems` (default 3).
- Page furniture: `skip` blanks matching lines (running headers, page numbers); `prefix` and `suffix` strip column text from the start or end of each line; `columnHeader` (group `col`) and `columnGap` end titles at a table column.
- Sections: `headings` (unnumbered section headings), `headingItems` (unnumbered lines published as items, such as "Call to Order"), `sectionRestart` and `restart` (numbering restarts under each heading, or when a repeated agenda starts again at 1).
- Titles: `stop` (a line that ends a wrapped title), `wrap` (`indent` by default, `lowercase`, `any`, `none`), `maxTitleLines` (default 3; a title cut at the limit keeps its whole text, read by the same rules, as the item's `full_title`, which the site shows with an ellipsis on the title), `titleEnd` (a title that matches ends there), `titleCleanup` (`[pattern, replacement]` pairs), `capsColon`, `capsLead`.
- Numbering: `maxGap` (default 0) skipped numbers allowed when the missing label appears nowhere in the agenda; `indentTolerance` (default 3) columns a sibling label may shift. Beyond these, the agenda is still published with a "review required" warning ([decision 0032](../docs/decisions/0032-publish-irregular-agendas.md)): a longer gap of up to three missing labels gets "Not Found In Agenda" placeholder items, and a numbered line that cannot be placed is passed over. Fewer than `minItems` readable items still publishes nothing.
- After the ending marker: `afterEnd` lists separately printed children of one entry, such as a consent calendar (`{"heading": ..., "level": ..., "parent": ..., "end": ...}`); `addendum` is a heading that adds numbered items.

`"@NAME"` uses a shared pattern from `workers/ingestion/tahoe_ingest/agenda_layouts.py`: `@CAPS_HEADING`, `@STAFF_LINE`, `@SLT_HEADINGS`, and the cleanup pairs `@FOR_ACTION`, `@ACTION_TAGS`, and `@PAREN_DESCRIPTION`. A list in a single-pattern key matches any of its patterns, so `"stop": ["@STAFF_LINE", "^-"]` extends a shared one. The registry does not load if a key, layout, level, pattern name, or parser is unknown or a pattern does not compile. Every layout needs a fixture in `tests/fixtures/agenda-layouts/` with the reviewed items; after a change, run the agenda tests and re-parse stored text without fetching sources:

```sh
python3 scripts/project.py worker reparse --agency AGENCY --dry-run
python3 scripts/project.py worker reparse --agency AGENCY
```

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
- `time` (optional): the 24-hour local start, `HH:MM`. Omit it when the body's start time varies (as for the TRPA Governing Board); its forecasts then carry a date and no `startsAt`, and the website shows them as date-only. `timezone` must be `America/Los_Angeles`. The learner always sets a time.
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
      "host": "Only under an agency with meetingHost per-record (Other): who holds the meeting",
      "topics": ["housing", "short-term-rentals"],
      "participation": {"instructions": null, "url": null, "remoteUrl": null, "deadline": null},
      "items_complete": false,
      "documents_complete": false
    }
  ]
}
```

The owning agency must be followed. Add a new agency entry first if needed. Put a meeting under `other` when it is not a regular meeting of one followed body (a co-hosted open house, for example); there `host` is required, written only from organization names in the notice. `host` is optional under any other agency. `topics` tags the meeting notice so it matches timeline filters before an agenda is posted; it does not create agenda items or summaries. Area-plan workshops can be tagged according to their actual subject, such as Housing or Environment. Only use Hot Topics when it belongs on the watchlist.

Omit `start_time` until confirmed. When verified, add e.g. `"start_time": "18:00"`; this enables the existing calendar download. A date alone never invents a time. An offset-aware `starts_at` is also supported; ambiguous daylight-saving local times require an explicit offset or `fold`.

Preview, import, and refresh:

```sh
python3 scripts/project.py worker ingest-one-offs --dry-run
python3 scripts/project.py worker ingest-one-offs
python3 scripts/project.py refresh
```

An alternative config path can follow `ingest-one-offs`. The default path is relative to the working directory; installed/container workers need a mounted manifest and explicit path. Regular `refresh` only exports stored data; it does not import edits to this file.

Reimporting the same agency and ID updates the record. Keep that ID when the meeting is rescheduled; edit the date/time and use `status: "rescheduled"`. For cancellation set `status: "canceled"` and import again. Removing an entry from the file does not delete an already published meeting; this prevents accidental loss of cancellation history. Removing a topic from a notice requires changing its `topics` list and reimporting.

Manual imports do not claim a fresh automatic source check. The notice links to the official page. It carries no default warning; add `warnings` only for a known issue with the notice. One-off notices are never evidence for a regular schedule, but a notice within three days of an expected date replaces that expected meeting. Nothing is generated from the legacy CSV schedule cells. If an agenda arrives, use the existing `ingest-manifest`/`refresh-document` pipeline with the same agency/meeting ID and official PDF URLs. PDFs remain temporary; extracted text, citations, and provenance persist. Keep `items_complete` and `documents_complete` false for a notice-only update so existing extracted material is preserved.

A future authenticated admin page could edit these same records if this becomes frequent; it is deferred, along with login and remote write endpoints.

## Meeting durations

Every exported meeting gets a length so calendar events have an end time. Observed or published lengths always win; otherwise the model estimates, always rounding up and biased long, and the export and calendar label it as an estimate.

[`meeting-durations.json`](meeting-durations.json) holds the `defaults` for all agencies. An agency entry's own `duration` in `agencies.json` overrides any setting, and its `sectionWeights` merge with the defaults, for example `"duration": {"mode": "fixed", "fixedMinutes": 150, "notes": "..."}` on TTD. A sibling body never inherits it. Settings:

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

See [the pacing research](../docs/research/meeting-duration-pacing-2026-10-20.md) for the initial values. Installed or container workers bundle both files; rebuild after editing.

## Reviewed source configuration and repeatable acquisition

The maintainer's source review is recorded in [decision 0014](../docs/decisions/0014-reviewed-sources-and-public-comment-periods.md). Disabled entries stay in the file with `follow: false`; [source coverage](../docs/operations/source-coverage.md) lists each entry's discovery type and body filter. Sources with live discovery carry a `discovery` object with a verified `type`, an `https` `indexUrl`, and an exact `bodyPattern` (a case-insensitive regular expression; without one every row qualifies). For example, the TTD committee:

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
| `granicus-row-ids` | Granicus `ViewPublisher.php` whose rows carry IDs such as `10-06-2026-City-Council`; the ID gives the date and title (`bodyPattern` matches "10 06 2026 City Council"), and the row's agenda viewer link (the official URL, whose `clip_id` is the source ID) is the first document, `<agency>-agenda`, ahead of the row's PDFs (the packet); rows without a `clip_id` are skipped | |
| `legistar` | A Legistar department page, which lists only the current calendar month; the public web API (`legistarClient`, default the page's subdomain) adds upcoming meetings whose agenda has posted on dates the page does not show (up to `horizonDays`, default 120, ahead) and supplies minutes and video. A row's location comes from the page's Location column or the API's `EventLocation`, without join links, meeting IDs, or notes such as "OR Live Streamed" | `title`, `legistarClient` |
| `civicplus` | A CivicPlus Agenda Center category; each row's title link is a meeting, and `bodyPattern` matches that title, so a category shared by a board and its committees can be split | `title`, `idPrefix`, `qualifySpecial` |
| `municode` | The Municode meetings page, `https://meetings.municode.com/MeetingsPage/index?clientCode=…`; its public API is queried 90 days either side of today | |
| `civicclerk` | A CivicClerk portal; its public event API supplies current agenda file IDs. Scheduled runs ask the API for events dated 60 days back to 120 ahead (a portal can list a year of placeholder meetings and office holidays ahead), and a YouTube recording the event links (`externalMediaUrl`) is linked directly, else the portal's media page | `apiUrl`, `portalUrl` |
| `link-index` | Rolling dated agenda links, or a published Google Sheets meeting library | `title`, `idPrefix`, `qualifySpecial`, `minutesLinks` |
| `trpa` | TRPA meeting materials; each meeting page is read | `idPrefix`, `documentPattern` |
| `dated-table` | An annual table with dates in its first column | `title`, `datePattern`, `sectionTitles` |
| `meeting-files` | A dated table, "next meeting" block, or calendar whose entries open per-meeting file lists, either directly (a link labeled `filesLabel`, default "View Files") or, with `eventLabel`, through an event page whose `filesLabel` link is the file list; the file list's number in its path (`/board_meetings/411/attachments`) is the source ID and the file list is the official URL, from which the agenda is read. An entry is the innermost element holding one date and one such link; a 24-hour time ("16:30") is read when no a.m./p.m. time is printed, and an event page without a file list yet is skipped | `title`, `filesLabel`, `eventLabel` |
| `rss` | A category feed, filtered by body | `titlePrefix` |
| `wordpress-meetings` | A WordPress REST collection of meeting posts, which several bodies may share | `documentIdPrefix` |
| `meeting-post-feed` | A category RSS feed, or a WordPress REST collection (`.../wp-json/wp/v2/<post type>?per_page=100`, for a feed that holds too few posts), of meeting posts dated in their titles ("October 21, 2026 Commission Meeting"), each post's page being the meeting's HTML agenda, which is its one document (`<agency>-agenda-html`); no post page is read during discovery | `title`, `scheduleUrl` |
| `dated-page-links` | A page whose list items are only a meeting date ("June 18, 2026", "March 19 and 20, 2026"); a linked date opens the meeting's own page, which is its HTML agenda and one document (`<agency>-agenda-html`), and an unlinked date from today on is a notice with no documents; no meeting page is read during discovery | `title`, `headingPattern` |
| `dated-sections` | A page with one section per meeting under its own heading (an accordion panel); the line naming the session (`bodyPattern`, matched against the whole line) is followed by `Date:`, `Time:`, `Location:`, and `Address:` lines, and the section's link labeled `documentPattern` (default `Meeting Agenda`) is the agenda; the page stays the official URL | `title`, `idPrefix`, `documentPattern`, `pages` |
| `simbli` | A Simbli (eBOARDsolutions) meeting listing, `SB_MeetingListing.aspx?S=<site>`; discovery repeats the page's own listing request (the public site token it prints) to the portal's `GetMeetingListing` API, newest first, and `bodyPattern` matches the meeting title. Each meeting page, `ViewMeeting.aspx?S=<site>&MID=<id>`, is the official URL and its one document (`<agency>-agenda-html`); published minutes link its `&T=1` view. The page draws its agenda from the portal's agenda tree, which capture reads instead of the page's script shell: one text line per item and the items from the tree's numbering (`tahoe_ingest/board_portal_discovery.py`), with no `agenda.layouts` entry. It takes no `indexUrlTemplate` or `horizonDays` | `title` |
| `wordpress-media` | A WordPress media library, `.../wp-json/wp/v2/media`, searched for `search` (default `agenda`); each uploaded PDF or Word file whose name (dashes and underscores read as spaces) matches `bodyPattern` and states a full date ("TDVA-Agenda-November-11-2026-Packet.pdf") is a document of the meeting on that date, the packet when its name says "Packet". A file naming only a month is skipped. The source ID is the date (with `-special`, `-workshop`, or `-joint` when the name says so), the official URL the agenda file. Scheduled runs read the newest 100 matches; a backfill pages through all of them. Optional `noticeUrl`: a page whose paragraphs announce cancellations; a meeting on a date such a paragraph names (with "canceled" or "cancellation") is canceled | `title`, `idPrefix`, `search`, `noticeUrl` |
| `file-links` | A page of download links whose file names carry the body and date ("TID Agenda 10.29.2026.docx", "TID Special meeting 9-24-26.docx"), read like `wordpress-media`; PDF and Word (.docx) links qualify, link labels such as "Download" are ignored, and the page is each meeting's official URL. With `"matchLabels": true` the link labels are read instead of the file names, for a page that names its files loosely but labels each link with the body and date ("Regular Meeting of the Board of Directors – 10/22/2026 – Agenda Packet", Tahoe Forest Hospital District) | `title`, `idPrefix`, `matchLabels` |
| `tribe-events` | The Events Calendar's REST API, `.../wp-json/tribe/events/v1/events`, read by date range (scheduled runs: 60 days back to `horizonDays`, default 120, ahead); each event whose title matches `bodyPattern` is a meeting with the event's date, start time, venue, and page, and the PDF and Word links in its description are its documents. A title starting "CANCELLED" is a canceled meeting. The source ID is the event ID | `title`, `horizonDays` |
| `dated-paragraphs` | A page with one paragraph per meeting that opens with its date ("October 20, 2026 • Agenda • Minutes"); the paragraph's links labeled `documentPattern` (default `Agenda`, matched against the whole label) are its documents and a link labeled "Minutes" its minutes, so stray or empty links in a paragraph are ignored. "(Special Session)", workshop, or joint in the paragraph gives the meeting a `<date>-special` (and so on) ID and title; a paragraph saying the meeting was canceled is a canceled meeting whose official URL is the linked notice. The source ID is the date; the page is the official URL | `title`, `idPrefix`, `documentPattern` |
| `squarespace-blog` | A Squarespace blog collection's page (`https://www.ntfire.net/board-minutes`), read as JSON (`?format=json`, 20 posts a page, older pages by the `offset` it names); each post whose title matches `bodyPattern` is a meeting whose source ID is the post ID and official URL the post's page, with its body's links labeled `documentPattern` (default `Agenda`) as documents and "Minutes" as minutes. A title saying canceled marks it canceled. A title with a full date dates the meeting; one naming only the month ("Regular Meeting 10-2026") leaves the date to capture, which reads it beside the start time on the agenda's first page, so scheduled runs read at most 6 such posts (as for `wordpress-meetings`), and a backfill keeps posts published inside its range | `title`, `documentPattern` |
| `evogov-meetings` | An EvoGov site's meeting dashboard, `.../meetingdashboard`; its year headings name each calendar and year, whose meetings the site's `/meetings/year_events/` endpoint returns as table rows (scheduled runs read the listed years that meet the window of 60 days back to 120 ahead). Each event whose name matches `bodyPattern` is a meeting: the row's UTC sort key gives the local date and start time, the event page (`/events/<id>/`) is the official URL, the event ID the source ID, files under "Agendas" are agendas, a "packet" under "Attachments" is the packet, and a file under "Minutes" is the minutes. A name ending "- Cancelled" is a canceled meeting | `title` |

Every type except `granicus-row-ids`, `municode`, `civicclerk`, `wordpress-meetings`, `meeting-post-feed`, `dated-page-links`, `dated-sections`, `simbli`, `wordpress-media`, `file-links`, `tribe-events`, `dated-paragraphs`, `squarespace-blog`, and `evogov-meetings` also takes `indexUrlTemplate` and `horizonDays` (`tribe-events` takes `horizonDays` alone). Optional keys: `title`, `titlePrefix` (Granicus, RSS), `idPrefix` (a prefix for date-based source IDs), `qualifySpecial` (link-index: give special meetings and workshops their own ID and title), `minutesLinks` (link-index: attach minutes PDFs on the index to the agenda row of the same date, read from the link's aria-label, label, or file name), `datePattern` and `horizonDays` (dated-table: restrict to one table and ignore far-future placeholders), `sectionTitles` (dated-table: a list of `{headingPattern, title, idSuffix}` rules for a page whose later table lists another kind of meeting under its own heading, for example a "Board Workshop" table below the board meetings; a row takes the `title` of the first rule whose case-insensitive `headingPattern` matches the last text before its table, in any heading or paragraph, so anchor the pattern to the whole heading. The meeting ID stays the date, so a stored meeting is retitled by the next run; only when a regular row and a titled row fall on the same date does the titled row's ID take `-<idSuffix>`; a name printed in the row itself still wins), `filesLabel` and `eventLabel` (meeting-files), `documentPattern` (trpa: how the meeting page names the body's documents when that differs from `bodyPattern`, for example "APC"), and `documentIdPrefix` (wordpress-meetings: stored document IDs start with this instead of the agency ID; the TTD committee keeps `ttd`, the prefix of its first captures). WordPress posts state no meeting date, so a scheduled run reads at most 6 of them and capture takes the date from the post title or agenda. A TRPA meeting page links committee agendas, staff reports, attachments, and public comments beside the board's own documents; discovery keeps only the body's agenda and its full packet, in that order, as `<agency>-agenda-<date>` and `<agency>-agenda-packet-<date>`, and marks them the meeting's complete document set. Elsewhere, when a meeting offers a short agenda and a packet, the packet is ordered second so the default two captured documents are the agenda and the packet. The generic link index supports rolling dated agenda links and published Google Sheets meeting libraries. Annual-table sources (Lahontan and Fish and Game) use `indexUrlTemplate` with `{year}` for the current annual index, retaining dated notices and cancellations before agendas are posted. While the scheduled window (60 days back, 120 ahead) reaches into the neighbouring year, that year's page is read too, for its rows inside the window: next year's from early September (Fish and Game posts January and February meetings there), last year's until the end of February; a neighbouring page that is not posted yet is skipped. A multi-day date ("October 13-14") uses the first day as the meeting's date and adds a note stating the span ("Meets October 13–14, 2026."), except on a canceled meeting. Truckee's official agenda page embeds its public Municode meeting index, which discovery reads directly. A `meeting-post-feed` titles a meeting from its post with the date removed ("Commission Meeting", "Special Commission Meeting"; a special meeting, workshop, or joint meeting gets its own `<date>-special` ID), unless `title` is given; its optional `scheduleUrl` is a page with a "YYYY Meeting Schedule" heading followed by one paragraph per meeting, which gives each meeting's start time (a line's own time, else the "All meetings will be held at …" line) and a document-less notice for each scheduled date from today on that has no post yet (State Lands). A `dated-page-links` index (the Tahoe Conservancy) is read only in list items under a heading that matches `headingPattern` (default: any), such as `^Conservancy Meetings \d{4}$`; a multi-day date ("March 19 and 20, 2026") uses the first day and gets the same span note ("Meets March 19–20, 2026."), the source ID is the date, and the start time comes from the agenda heading at export. A `dated-sections` page (the California State Parks commissions) gives one meeting per section, the source ID `idPrefix` plus the date, its start time and location from the section's lines, and an agenda ID of `idPrefix` (else `<agency>-`) plus `agenda-<date>`; a tour or workshop on a neighbouring day, under another session line, is not the meeting, and a section with no `Date:` line yet is skipped. Its optional `pages` lists further pages of the same layout for the same agency, each an object with its own `indexUrl` and, to replace the block's, `bodyPattern`, `title`, `idPrefix`, and `documentPattern` (California State Parks reads its two commissions' pages, OHMVR first and the State Park and Recreation Commission under `pages`); only the first page is shown in source coverage and decides an agency's "blocked" status, though a skipped prefix stops requests to any of them.

For example, from the repository root:

```sh
python3 scripts/project.py worker discover tcpud-parks-recreation-committee --limit 3 --out data/live/tcpud-parks-next.json
python3 scripts/project.py worker capture data/live/tcpud-parks-next.json --max-meetings 3 --max-documents 1
python3 scripts/project.py refresh
```

The source collector checks the index again on each invocation; `refresh` alone exports stored data and forecasts, and does not fetch new agendas. A future agenda-less notice can be rechecked with the same commands once documents are posted. No cloud scheduling was provisioned here. The SLT RSS index exposes an All feed rather than a verified commission-specific feed, so the body-filtered Granicus source is used. Placer RSS/download failures are tracked in [SOURCE-001](../docs/issues/SOURCE-001-placer-downloads.md).

Acquisition prefers small agendas over full packets, supports HTML/plain text and Word (.docx) documents as well as PDFs, and stores the detected format. A Word file is recognized by its type, never by agency ([decision 0041](../docs/decisions/0040-private-groups-and-word-agendas.md)): `tahoe_ingest/docx_source.py` reads its body with the standard library and prints each automatically numbered paragraph's label ("A.", "ii.", "3.1") from the document's list definitions, indented four spaces per list level, so agenda layouts read it like a PDF's text; hard page breaks separate its source sections. Legacy binary .doc agendas are not read. HTML citations refer to source sections and link to the webpage without invented PDF page numbers. A complete supported structured HTML agenda can publish exact cited headings: Granicus agenda tables, Municode accessible agendas, "Order of Business" meeting pages (Roman-numbered parts and numbered item tables, the State Lands Commission format, in `tahoe_ingest/html_order_of_business.py`), and agendas written as one ordered list from "Call to Order" to "Adjourn" (lettered by the page's style, with nested items that print labels such as "D-12", the Olympic Valley PSD format, in `tahoe_ingest/html_ordered_list.py`) are read from their markup in `html_agenda.py`, with no `agenda.layouts` entry, and reviewed items for them live in `tests/fixtures/html-agendas/`; other layouts retain text for later parsing. Bounded captures keep existing unrelated documents unless the candidate explicitly declares `documents_complete: true`. A failed document still leaves a verified meeting notice available with a retry warning. Verified TLS stays enabled for the native curl fallback; install `curl` and system CA certificates on worker hosts (included in the Dockerfile).

## Skipped endpoints

[`skipped-endpoints.json`](skipped-endpoints.json) lists URL prefixes the worker never requests, so a source known to answer HTTP 403 is not retried on every scheduled run. Each entry has a `prefix`, the observed `status`, the `since` date, and a `reason` (optionally an `issue` path). The skip applies to discovery indexes and document downloads alike. A skipped agency's index is reported as `skipped` in the run summary, not as a failure. Stored meetings whose documents are all skipped are not re-ingested, and a newly captured notice carries a warning that its documents are read at the official link. In the public export, an agency whose discovery index or source page a prefix covers has `status: "blocked"` with the skip reason as its `error`, whatever its stored check result, and the coverage page shows it as blocked. The stored result is untouched, so deleting the entry restores it.

To try an endpoint again, delete its entry (or set `skipped` to `[]`) and rebuild the container image, since the file is bundled with it. If the fetch fails again, add the entry back. Locally the file is read from `TAHOE_SOURCE_ROOT` (default: the working directory).

## Public comment periods

Edit [`public-comment-periods.json`](public-comment-periods.json) when an official comment window is verified. It starts empty because no current Tahoe-related deadline was verified during this source review. A record looks like:

```json
{
  "comment_periods": [{
    "agency": "ltbmu",
    "id": "stable-project-notice-id",
    "title": "Project name — public comment period",
    "project_title": "Project title as the notice states it (optional)",
    "project_proponent": "Project proponent as the notice states it (optional)",
    "summary": "Passage from the notice describing the project (optional)",
    "host": "Who runs the period (required under other, optional elsewhere)",
    "previous_ids": ["agency:earlier-id (only when the period moved)"],
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

Run `python3 scripts/project.py worker ingest-comment-periods` and then `refresh`. The importer fetches the source, checks the exact quote, stores its text/hash/provenance, and upserts a stable agency/notice ID. Dates and instructions must come from that notice. `opens_on` may be omitted if unknown. `closes_on` is required; optionally include an offset-aware `closes_at` when the exact deadline/timezone is verified. Never infer a closing hour. `project_title`, `project_proponent`, and `summary` are optional and must also be copied from the notice; the importer checks each against the fetched text, ignoring only line breaks and spacing. They lead the period's page. The `/comment-periods/` page lists open and upcoming windows, then past ones, and each window has its own page at `/comment-periods/<agency>/<id>/` that stays up after it closes, so its `id` should not change once published; they are not board meetings or calendar events with fabricated durations. No comment is submitted by the app.

`host` names who runs the period when that is not the followed body itself; it replaces the agency name on the period's card and page, and it is required under `other` (see [Other and per-record hosts](#other-and-per-record-hosts)). To move a period to another agency, change `agency` and list the old full IDs (`agency:id`) in `previous_ids`: the importer deletes those rows after the upsert, and the website redirects each old page to the new one. Keep the entry in `previous_ids` for as long as old links may exist.

## Minutes and recording sources

`meeting-records.json` lists YouTube channels or playlists and Laserfiche minutes folders that `tahoe-ingest` matches to stored meetings each scheduled run ([decision 0025](../docs/decisions/0025-minutes-and-recording-links.md)). Listing links (Granicus, Legistar, CivicClerk, dated tables, TRPA pages, and link indexes with `minutesLinks`) need no entry here.

- `youtube`: `agency`, exactly one of `channelId` (`UC…`) or `playlistId`, an `include` title pattern, and optional `exclude` and `meetingPattern` (only meetings whose title matches). A video matches the agency's meeting on the date written in its title, once that date has passed. Use a channel feed unless a playlist is known to be current: several agencies' playlists stopped years ago, and their feeds return the oldest videos. Pin the channel that actually carries meetings; several agencies' obvious channels are empty or unrelated (see the [research](../docs/research/minutes-and-recordings-sources-2026-10-27.md)).
- `laserfiche`: `agency`, `repo`, and `folderId` of a folder of year folders whose documents are named `YYYY-MM-DD - Regular`, `- Special and Joint`, and so on.

`tahoe-ingest meeting-records --dry-run` reports matches without storing them. Every entry must name a followed agency; the file is checked when the step loads it, and a test checks that every agency named anywhere in `config/` exists in the registry. It stays a separate file because it is read from the source root at run time, entry by entry in file order, and several bodies share one channel's feed.
