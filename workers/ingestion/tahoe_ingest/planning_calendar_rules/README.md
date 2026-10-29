# Planning calendar formats

Some agencies publish a tentative calendar of the subjects they plan to bring to future meetings. IDEA-018 (R4) turns
those calendars into previews: subjects shown on a future meeting before its agenda is posted. Each module in this
package parses one publishing format, registered under a format name, and each agency's entry in
[`packages/domain/agencies.json`](../../../../packages/domain/agencies.json) picks a format and gives its parameters.
Parsing is deterministic ([decision 0015](../../../../docs/decisions/0015-deterministic-agenda-layouts.md)). No model
reads a calendar, and nothing here touches the network or the database.

## Pointing an agency at a format

Add a `planningCalendar` entry to the agency whose agenda carries the calendar:

```json
"planningCalendar": {"format": "ttd-packet-pages", "heading": "Tentative Agenda Calendar",
                     "title": "TTD/C Board/Committees Tentative Agenda Calendar",
                     "bodies": {"TTD": {"id": "ttd", "meetingTitle": "TTD Board of Directors Meeting"},
                                "RTCPC": {"id": "ttd-rtcpc", "meetingTitle": "TTD Regional Transit and Capital Programs Committee Meeting"}}}
```

- `format` names a format below.
- `title` is text the calendar's title must contain, or the whole calendar is rejected.
- `bodies` maps the body labels the calendar prints to followed registry entries, with the title a planned meeting
  gets (R6). Labels that are not mapped are skipped. `"*"` maps a calendar that covers one body.
- The other keys are the format's parameters.
- `note` is optional.

`planningCalendar` is an own key, so sibling bodies never inherit it through `extends`: the entry belongs to the body
whose agenda carries the calendar, and its `bodies` map covers the siblings. The registry checks every entry when it
loads (`check_config`), and loading stops on any of these: an unknown format, a missing `title` or `bodies`, a body
that is not a followed registry entry, a missing or unknown parameter, or a regular expression that does not compile.
`agency_rules()` returns `{agency ID: BoundCalendar}`, with each format's parameters bound.

| Format | Calendar | Parameters |
| --- | --- | --- |
| `slt-apc-attachment` | South Lake Tahoe City Council: the "APC MM DD YYYY.pdf" attachment of the "Agenda Planning Calendar" item, an Excel table | `itemTitle`, `attachmentPattern` |
| `ttd-packet-pages` | TTD: the run of packet pages headed "Tentative Agenda Calendar", two columns per block | `heading` |

## What a format provides

A module exposes `RULES = {"format-name": CalendarFormat(locate, parse)}`. Modules are discovered when the package
loads, and two modules that register the same name raise an error. Modules whose names start with `_` are skipped.

- `locate(source, *, params...) -> list[dict]` says where a meeting's calendar is. `slt-apc-attachment` takes the
  agenda's items with their attachments, as the staff report rule lists them
  (`[{"title", "attachments": [{"url", "name", "label"}]}]`). It returns `[{"url", "name"}]`, the stable Granicus
  download URLs, which the caller downloads once per new URL. `ttd-packet-pages` takes the stored packet pages
  (`[{"page_number", "text"}]`) and returns the calendar's pages.
- The caller (`tahoe_ingest/planning_calendars.py`, the `planning-calendars` step) tells the two sources apart by
  `locate`'s first parameter name, which must be `items` or `pages`.
- `parse(pages, *, config, carried_by) -> dict` reads the whole calendar and returns
  `{"title", "published_on", "year", "blocks", "entries"}`.
  - `config` is the agency's entry. `carried_by` is the date of the meeting whose agenda carries the calendar, and it
    is the publication date when the title has no "(Updated MM DD YYYY)".
  - Each entry has exactly the `previews.Preview` field names (`FIELDS`). `blocks` summarises each heading for each
    followed label: its date, month, or pending flag, the start time, "Special", its state, and the entry count.
  - The parser returns every entry, including those dated on or before the publication date. The storage layer
    ignores those.

Parameters are the keyword-only arguments of `locate` and `parse` other than `config` and `carried_by`. Agencies set
them in camelCase (`item_title` -> `itemTitle`). The annotations are those staff report rules use: `str`,
`staff_reports.Regex`, or `Literal[...]`. A parameter with a default is optional.

## The whole calendar validates or publishes nothing

`parse` raises `CalendarInvalid(reason)` on any of these failures:

- the title does not contain `title`, or states no year;
- a date heading does not parse;
- text falls outside every block, or outside the format's columns;
- a subject is not an exact quote of its page.

On failure the caller stores the version with the reason, and the previous calendar's previews stay current. The
quote check (`check_quotes`) joins line wraps. Every printed line of a subject (the entry's `quote`, one line per
wrap) must appear verbatim in its page's text, with whitespace collapsed. The next page is also allowed, for a bullet
that wraps across a page break.

Shared rules (`__init__.py`):

- **Year.** The year comes from the title. Headings run in date order, so a heading more than a month before the
  latest one so far rolls into the next year. A strict month comparison would move TTD's committee block to the next
  year, because the committee meets a few days before the board block it follows ("April 1", then "RTCPC March 30").
- **Not published:** requester or sponsor initials, motion numbers, conditional notes, and a trailing parenthetical
  that names a person. A person is one capitalised word, such as "(Kira)", that is not an agenda term or a month, so
  "(Consent)" and "(Nov)" stay. Formats drop initials and notes by column. `entry()` removes the parenthetical.
- **Sections.** A section is only what the calendar states: South Lake Tahoe's NB/PH/UB/Consent codes. TTD's inline
  "(Consent)" and "Action Item" stay in the subject as published.

## Adding a format

1. Create `planning_calendar_rules/<format>.py`. Give it a module docstring that describes the layout in a few
   sentences, and give `RULES` a format name, never an agency ID.
2. Work from character columns in `pdftotext -layout` text (`runs(line)` gives `[(column, text)]` split at runs of
   two or more spaces). Infer column positions from each page's own rows, because they shift between pages.
3. Build entries with `entry(...)` and return `calendar(...)`, which runs the shared checks.
4. Add a reviewed fixture from a real calendar to `tests/fixtures/planning-calendars/`. Cover its expectations and
   its failure modes (a broken heading, a changed title, text outside the columns) in
   `tests/test_planning_calendars.py`. Fixtures are public, so take them from public agency documents only.

Run the tests with `cd workers/ingestion && uv run pytest tests/test_planning_calendars.py -q`.
