# IDEA-018: Meetings and agenda previews from notices and planning calendars

Status: proposed; maintainer decisions recorded 2026-10-29
Stage: MVP polish

## Problem and audience
Residents following a topic learn about a meeting item only when its agenda is posted, usually a few days ahead. Agencies often say earlier what is coming. They do it in two kinds of document that the pipeline already captures and does not use:

1. **Notices.** The West Shore Trail Notice of Preparation, tracked as a public comment period since 2026-10-27, announces a scoping open house on November 2, 2026 (5:30–7:00 PM, Tahoe City Public Utility District, 221 Fairway Drive). It also says the project will be presented at the TRPA Advisory Planning Commission on November 12, 2026 at 9:30 a.m. Neither meeting is on the timeline. `trpa-advisory` has no schedule, so no forecast exists. Its past meetings fell on Wednesdays, so a learned schedule would have predicted November 11, not Thursday November 12.
2. **Agenda planning calendars.** South Lake Tahoe City Council attaches an "Agenda Planning Calendar" (for example `APC 09 30 2026.pdf`) to an agenda item of the same name on each regular agenda. It lists the items planned for each upcoming meeting date. The TTD board packet includes a "Tentative Agenda Calendar" under the Executive Director Report item, which lists planned items for the board and its committees month by month. A December 1 South Lake Tahoe item about prioritizing bikes at intersections is already visible in one of these documents, weeks before the agenda is posted.

Readers who filter by topic should see these meetings and planned subjects before the agenda is posted. Each one must be clearly marked as planned and subject to change.

## Maintainer decisions (2026-10-29)
1. Upcoming meetings that have a preview appear in the default timeline. They keep the "Expected · no agenda yet" badge.
2. One-off meetings can be hosted by any organization or combination; the scoping meeting is co-hosted by TRPA and El Dorado County. One-off meetings get a flexible host. The Agency filter gains an **Other** option.
3. Extracted meetings and previews publish automatically, with no review step.
4. Read only notices that are already tracked: today, the official notices of configured public comment periods. Finding new notices automatically comes later.
5. Agenda planning calendars (South Lake Tahoe City Council, TTD board) populate previews for that agency's future meetings. Each newer calendar replaces the previous one.

Everything else below is a proposed default unless it restates one of these decisions.

## Smallest useful version

### Terms
- **Preview:** a subject that an official source says is planned for a specific future meeting of a followed body, before that meeting's agenda is posted.
- **Preview source:** a notice, or one agency's planning calendar series. Each new version of a source replaces all of that source's previews.
- **Planned meeting:** a stored meeting record created because a preview names a meeting that is not stored yet.
- **One-off meeting:** an event that is not a regular meeting of a followed body. Examples are an open house, a workshop, a scoping meeting, or a staff-hosted hearing.

### R1. Hosts and the Other agency
- Add a registry entry `other` named "Other" to `packages/domain/agencies.json`, following the [Adding an agency](../../config/README.md#adding-an-agency) checklist. A registry property such as `"meetingHost": "per-record"` marks it, so code never tests for the ID ([decision 0027](../decisions/0027-agency-definitions-in-configuration.md)). It has no discovery, schedule, agenda, or staff report blocks, and it is excluded from [source coverage](../operations/source-coverage.md) counts.
- Meetings and comment periods gain an optional `host` text field. It is required under a per-record-host agency. Wherever the agency name appears today, the host replaces it: meeting cards, meeting pages, comment period pages, and calendar files.
- For extracted records, the host is built only from organization names that appear in the source text, for example "TRPA and El Dorado County". Manual entries in `config/one-off-meetings.json` and `config/public-comment-periods.json` may set `host` directly; `one_offs.py` and `ingest_comment_periods` accept it.
- A one-off goes under a followed body only when the source names exactly one registry entry as host, matched by name or alias. Otherwise it goes under Other. "TRPA" matches both TRPA bodies, so a TRPA staff open house goes under Other.
- The Agency filter lists **Other** last whenever an exported meeting uses it, and the bookmark is `?agency=other`. Its search terms include the hosts of its current meetings, so typing "El Dorado" offers both the El Dorado bodies and Other.
- The West Shore Trail comment period moves to Other with host "TRPA and El Dorado County". Its page URL contains the agency (`/comment-periods/<agency>/<id>/`), so the old URL must redirect to the new one.

### R2. Reading tracked notices
- Add a new `notices` step to `run-scheduled`. It runs after `comment-periods`, which already fetches each notice and stores its text and hash, and before `export`. It is also available as `tahoe-ingest extract-notices [--dry-run]`.
- **Input:** the stored text of each comment period notice that the current prompt has not yet read, tracked by hash. An unchanged notice costs no model call and no writes.
- **Model:** Claude Haiku 5.5 with Sonnet 5.5 as fallback, using a prompt in `prompts/notice_extraction.md`. It runs under the shared monthly AI budget, with its own per-run cap, and records usage in `ai_usage`. Notices are free prose that varies from one notice to the next, so a model reads them here, as with participation details ([decision 0021](../decisions/0021-participation-details-from-agendas.md)).
- **Output per event:**
  - `kind`: `one-off` or `regular-meeting`;
  - title, date, start time, end time, location lines, and any remote option;
  - hosts;
  - for a regular meeting, the followed body's registry ID and the subject to be presented;
  - a supporting quote.
- **Checks.** Each check is deterministic. A failure rejects only that event, which is stored with the reason and never shown:
  - The quote appears word for word in the text, ignoring line breaks and whitespace.
  - The date and times parse again from the quote under the ingestion rules (`starts_at`). The year must be stated, a time is never inferred, and the end must come after the start.
  - Each location line appears in the text. A remote option is kept only if the text states one.
  - Each host name, and the body's name or an alias, appears in the text.
  - The event date is not before the notice opens and is not past at extraction.
- **Where events go:** `one-off` events become stored meetings (R3), and `regular-meeting` events become previews (R5) whose source is the notice.
- **Revised notices.** A revised notice (new hash) is read again, and events are matched to earlier ones by normalized title:
  - A changed date or time updates the same meeting and marks it rescheduled.
  - An event missing from the revision stays published with the warning "No longer listed in the latest version of the notice." Removal never deletes, as with configured one-offs.

### R3. One-off meetings from notices
- The ID is stable: `one-off-<comment period id>-<YYYY-MM-DD>`, with `-2`, `-3` for further events on the same day. Records are stored through the same `ingest_bundle` path as configured one-offs. `declared_topics` are the comment period's topics.
- Fields:
  - the title as the notice writes it, for example "West Shore Trail – Meeks Bay to D.L. Bliss EA or EIS/EIR Scoping Meeting";
  - the format when stated ("Open house");
  - start and end times, and location;
  - host (R1);
  - official URL: the notice;
  - participation instructions from the quote.
- A stated end time sets the calendar download's end, replacing the estimated duration ([decision 0013](../decisions/0013-estimated-meeting-durations.md)).
- Each record carries the warning "Listed in <notice title>; check the official notice for changes."
- A one-off never gets an agenda, so the notice counts as its posted document. A new agenda-availability kind, `notice` ("Meeting notice posted"), shows it in the default timeline without the "Expected · no agenda yet" badge.
- An entry in `config/one-off-meetings.json` with the same ID overrides the extracted fields.

### R4. Planning calendars
- Each agency configures `planningCalendar: {format, ...parameters, bodies}` in `agencies.json`. Format rules register by name in `tahoe_ingest/planning_calendar_rules/`, the way staff report rules do, so code never names an agency ID.
- Two formats to start:
  - **`attachment` (South Lake Tahoe City Council).** The calendar is the attachment labeled `APC MM DD YYYY` on the agenda item titled "Agenda Planning Calendar". The SLT staff report rule already lists it in `item_attachments`. Store the stable Granicus download URL (`slt.granicus.com/services/legistar/download/pdf/<id>/...`), not the expiring S3 redirect. Download the file temporarily and keep its page text, hash, and URL ([decision 0004](../decisions/0004-source-first-storage.md)). Fetch only calendar URLs not yet seen.
  - **`packet-pages` (TTD).** The calendar is the run of consecutive pages headed "Tentative Agenda Calendar" in the meeting's current agenda packet. Those pages are already stored in `document_pages`, so nothing is downloaded. A date block can continue across a page break.
- `bodies` maps the calendar's body labels to followed registry entries; unmapped labels are skipped. TTD maps `TTD` → `ttd` and `RTCPC` → `ttd-rtcpc`; its TTC and Finance & Personnel columns are not followed bodies. South Lake Tahoe maps the whole calendar to `slt`.
- **Parsing is deterministic per format,** in line with [decision 0015](../decisions/0015-deterministic-agenda-layouts.md). Each format has one reviewed fixture from a real calendar in `tests/fixtures/planning-calendars/`.
  - **South Lake Tahoe:** an Excel-exported table with columns for meeting date and time, department, item, section, and requester initials.
    - A date cell starts a block, and rows without a date belong to the block above, including across pages.
    - Repeated page headers are ignored.
    - Month-only headings ("January") and "Unscheduled/Pending" start undated blocks.
  - **TTD:** two columns per block. A month-day heading starts the board block (TTD | TTC), and "RTCPC <month day>" starts the committee block (RTCPC | Fin & Pers).
    - Columns split at the whitespace gutter, and a bullet may wrap onto following lines in the same column.
    - "CANCELED", "CANCELLED", "No Meeting", and "N/A" describe a block's state; they are not items.
  - **Year:** taken from the calendar title ("2026 Agenda Planning Calendar", "2026 TTD/C Board/Committees Tentative Agenda Calendar"). Blocks run in date order, so a month earlier than the previous block's month rolls into the next year.
  - **Publication date:** the title's "(Updated 09 30 2026)" where present. Otherwise, the date of the meeting whose agenda carries the calendar.
- **The whole calendar validates or publishes nothing,** as with agendas:
  - the title matches;
  - every date heading parses;
  - every entry falls inside a block;
  - every subject is an exact quote from one stored page, with line wraps joined.
  On failure, the calendar is stored with the reason and the previous calendar's previews stay current. The run summary names it.
- **Each entry records:** body; date (or month, or pending); the start time if the heading states one ("Nov 3, 9:30 a.m."); the subject as written; the section where stated; and the page number.
  - South Lake Tahoe section codes map to labels: NB → New business, PH → Public hearing, UB → Unfinished business, Consent → Consent.
  - TTD's inline "(Consent)" and "Action Item" stay in the subject as published.
- **Not published:**
  - requester or sponsor initials;
  - motion numbers;
  - conditional notes, such as one that moves items if an election is not certified in time;
  - a trailing parenthetical that names a person.
- **Ignored or held back:** entries dated on or before the publication date are ignored; TTD's calendar repeats the year's earlier months. Month-only and pending entries are stored but not shown in this version.

### R5. Previews and replacement
- A new table, `meeting_previews`, has these columns:
  - body (`agency_id`), `local_date`, and the start time and location if stated;
  - subject, section, and topics;
  - `source_kind` (`notice` or `planning-calendar`) and `source_key` (the comment period ID, or `<agency>:planning-calendar`);
  - source URL, title, publication date, SHA-256, and page;
  - quote, and rejection reason (null when valid).
  Rows are keyed on body and date, not meeting ID, so they reattach when a planned meeting is adopted by the captured agenda (R6).
- **Replacement:** for each source key, the version with the latest publication date is current. Capture order does not matter, so re-capturing an older calendar during a backfill never displaces a newer one. The current version replaces all earlier previews of that key for dates after its publication date. Only the current and one previous version are kept, to bound storage. As with summaries, the public sees only the latest.
- Notice previews and calendar previews for the same meeting coexist; each source replaces only its own.
- **Topics:**
  - Calendar subjects are tagged by the same classifier as agenda item titles: catalog patterns and agency `topicHints`, then Claude with the `classify-topics` prompt. One editorial voice is kept ([decision 0010](../decisions/0010-single-editorial-voice.md)).
  - A subject already classified for the same agency and normalized text reuses its result, because each new calendar repeats most entries.
  - Notice previews take the comment period's topics plus the classifier's tags on the subject.

### R6. Planned meetings
- When a valid preview names a date with no stored meeting for that body, create a planned meeting:
  - **ID:** `<agency>:planned-<YYYY-MM-DD>`.
  - **Title:** from the format's per-body configuration (for example "City Council Meeting"). Add "Special" when the calendar says "Special Mtg", so the bridge's qualifiers agree.
  - **Start time and location:** only when the source states them for that date. Examples: "Nov 3, 9:30 a.m."; "9:30 a.m. at 128 Market Street in Stateline, Nevada, with a virtual attendance option".
  - **Official URL:** the source document.
  - **Warning:** "Planned in <source>; the date, time, and items can change before the agenda is posted."
- **Adoption:** when discovery later captures that body's meeting on the same date, the existing same-date bridge in `capture.source_identity` adopts the planned record and writes an alias. The meeting keeps its URL and its previews. South Lake Tahoe uses Granicus event IDs, which cannot be predicted, and TRPA uses dates, so test both.
- A planned meeting is a stored record, so it suppresses that body's forecast within three days, as today ([decision 0012](../decisions/0012-forecast-meetings-from-schedule-patterns.md)).
- **Withdrawal:** a planned meeting is withdrawn from the export, but kept in the database, once two things are both true:
  - no current preview supports it, for example because a newer calendar dropped that date;
  - nothing has been captured for it: no documents, items, or status change.
  A cancellation follows the normal path.
- One planned meeting per body and date. None is created for a date block with no valid items, such as the empty TTD December 2 block.

### R7. Display
- **Timeline (decision 1):**
  - An upcoming meeting with at least one current preview shows in the default view and keeps the "Expected · no agenda yet" pill.
  - It is not counted in the "Show N upcoming meetings without a posted agenda" button.
  - It matches a topic filter through its preview topics: `meetingTopicIds` includes them while previews are shown.
  - The card lists "Planned:" with the subjects that match the selected topic, or the first three and "and N more".
- **Meeting page:**
  - A "Planned for this agenda" section appears above the empty-agenda message. Each preview shows its subject, section, topic tags, and a source line linked to the official document, such as "From the agenda planning calendar updated September 30, 2026" or "From the West Shore Trail Notice of Preparation".
  - The section ends with "Planned items can move or be dropped before the agenda is posted."
- **Topic pages** list previews alongside agenda items, labeled as planned.
- **Once the agenda is posted** (items listed, or the official agenda captured), previews stop showing on that meeting and topics come from items only.
  - For a notice preview, the notice's project name is extracted and validated word for word. If no item title contains it, the page says "Announced in <notice> but not found on the posted agenda."
  - Calendar previews disappear quietly, because the next calendar carries a moved item forward.
- Forecasts never carry previews: a valid preview always produces a stored record. Past meetings never show previews.
- Calendar downloads for planned meetings with a stated time put the subject-to-change note in the event description.

### R8. Export and checks
- **New export fields:**
  - meetings gain `host` (string or null), `previews`, and `planned: true` for planned meetings;
  - each preview carries subject, section, topics, and `source` (`kind`, `title`, `url`, `publishedOn`, `quote`, `page`);
  - `agencies` includes Other.
  The fields are additive. Planned meetings keep `kind: 'confirmed'` because an official document states them, so a consumer that ignores previews still reads correct meetings and `schemaVersion` stays 2.
- **New `scripts/check_export.py` invariants:**
  - previews appear only on future meetings whose agenda is not posted;
  - every preview has a source URL and quote;
  - no preview is dated on or before its source's publication date;
  - every planned meeting has a preview or a captured document;
  - every per-record-host meeting has a host.
- The export reads current previews in one bulk query ([decision 0020](../decisions/0020-database-operation-budget.md)).

### R9. Cost and operations
- **Database:**
  - An unchanged notice or calendar causes no writes.
  - A changed calendar costs about one statement to replace its previews, one per new planned meeting, and one for new topic results.
  - Tests assert zero writes on unchanged input, and that the export's query count does not grow with previews.
- **Claude:** about one Haiku call per notice revision (a fraction of a cent). Topic classification runs only for new calendar subjects, about 10–20 per new calendar.
- **Downloads:** one South Lake Tahoe calendar PDF per new regular agenda, about twice a month, found through the staff report step's attachment list and bounded by its per-run meeting limit.

### Deferred
- Finding new notices automatically (agency notice pages, federal and state environmental review listings): decision 4.
- Showing month-only and pending calendar entries, for example "January: VHR permitting and enforcement status update".
- Planning calendars from other agencies. Stored agendas show that Lahontan, California Fish and Game, Washoe County Planning, and the Truckee Tahoe Airport district have "future agenda items" sections; each needs a format.
- Using a calendar's "CANCELED" block state to mark meetings canceled.
- Matching calendar previews to posted agenda items.
- Listing an Other meeting under the filters of the followed bodies that co-host it.

## Evidence and dependencies
- `config/public-comment-periods.json` holds one record (West Shore Trail, added 2026-10-27). Its instructions already restate both meetings by hand. `config/one-off-meetings.json` is empty.
- **South Lake Tahoe:**
  - An "Agenda Planning Calendar" item appears on the regular agendas of August 11, August 25, September 8, September 22, and October 6, 2026 (`slt:2295` through `slt:2339`). `item_attachments` already lists `APC 09 15 2026` and `APC 09 30 2026`.
  - The September 30 calendar is six pages. It has dated blocks for October 13 (special meeting), November 3 (9:30 a.m.), November 17 (5:30 p.m.), December 1 (9 a.m.), and December 15 (5:30 p.m.); month blocks for January through April; and an Unscheduled/Pending list.
  - Topic-relevant entries include "Pedestrian Only Zone Ordinance (First Reading)" (November 3), "Prioritization of Bikes at City Intersections and Bike Lanes" and "Bicycle Safety Regulation Discussion" (December 1), and "2027 Pavement Management Plan Update and Prioritization of all users" (January).
- **TTD:**
  - The agenda item "Executive Director Report and Agenda Calendar Check" (for example item VII on October 7, 2026) carries the calendar. Packet pages 191–192 of the October 7 packet list November 4 board items, such as "Ratification of Strategic Priorities – Action Item (Resolution)", and RTCPC items for November 2 ("Quarterly Parking Report", "Project Spotlight").
  - `ttd:7172` (November 4) is stored with no items, so previews attach to it directly.
  - The calendar repeats every earlier month of the year, and some subjects wrap across lines within a column.
- **TRPA:** discovery uses the meeting date as the source ID, so the APC meeting would be `trpa-advisory:2026-11-12`.
- **Depends on:**
  - the SLT attachment listing from [decision 0026](../decisions/0026-staff-reports-for-every-agency.md), implemented locally and not yet deployed;
  - the same-date bridge in `capture.py`;
  - the registry checklist (decision 0027);
  - the query budget tests (decision 0020).

## Acceptance criteria
1. The West Shore Trail notice yields a one-off meeting with these properties:
   - on 2026-11-02 from 5:30 to 7:00 PM, at the Tahoe City Public Utility District, 221 Fairway Drive, Tahoe City;
   - host "TRPA and El Dorado County", under Other, tagged Biking and Environment;
   - shown by default, with a calendar download that ends at 7:00 PM.
2. The same notice yields a planned `trpa-advisory` meeting on 2026-11-12 at 9:30 a.m., at 128 Market Street, Stateline, with a virtual option. It carries one West Shore Trail preview, shows by default with "Expected · no agenda yet", and matches the Biking filter.
3. When the TRPA adapter captures the November 12 APC agenda, it updates that same record and the preview stops showing. If the West Shore Trail is not on the agenda, the "not found" note appears.
4. The `APC 09 30 2026` fixture yields:
   - previews shown on November 3, November 17, December 1, and December 15;
   - none shown for October 13, whose agenda is already captured;
   - the two December 1 bike entries tagged Biking;
   - no requester initials anywhere;
   - January–April and pending entries stored but not shown.
5. Ingesting `APC 09 15 2026` after `APC 09 30 2026` leaves the September 30 previews current. A later calendar that moves an item from November 17 to December 15 moves its preview.
6. The October 7 TTD packet yields:
   - previews on `ttd:7172` (November 4) and a planned `ttd-rtcpc` meeting on November 2;
   - nothing for TTC or Finance & Personnel, nothing dated before October 7, and nothing for December 2.
7. A calendar that fails layout validation publishes nothing and leaves the previous previews in place, and the run summary names it.
8. Rerunning with unchanged inputs makes no database writes and no model calls.
9. The Agency filter offers Other, `?agency=other` works, and the old West Shore Trail comment period URL redirects.
10. `scripts/check_export.py` passes with the new invariants, and the export's query count stays constant.

## Open questions
- **Parser approach:** deterministic calendar parsing (proposed, per decision 0015) or Claude with validation (as in decision 0021)? Deterministic costs nothing but needs one layout per format, and previews stop when an agency redesigns its calendar until the layout is fixed.
- **Co-hosted filters:** should a co-hosted Other meeting also appear under the filters of its followed co-hosts? Proposed: not in this version; host search terms cover finding it.
- **Default visibility for one-offs:** they never get an agenda, so they are proposed to show by default (R3).
- **Calendar downloads for planned meetings** with an agency-stated time: proposed yes, with the subject-to-change note.
- **Moved items:** should readers see that a preview moved ("Moved from November 17")? Proposed no, because the public sees only the latest calendar.

## Links and updates
- Related decisions: [0009](../decisions/0009-configurable-coverage-and-one-off-meetings.md) (one-off notices), [0012](../decisions/0012-forecast-meetings-from-schedule-patterns.md) (forecasts), [0014](../decisions/0014-reviewed-sources-and-public-comment-periods.md) (comment periods), [0015](../decisions/0015-deterministic-agenda-layouts.md) (deterministic layouts), [0020](../decisions/0020-database-operation-budget.md) (operation budget), [0021](../decisions/0021-participation-details-from-agendas.md) (participation from agendas), [0022](../decisions/0022-timeline-default-view.md) (default view), [0027](../decisions/0027-agency-definitions-in-configuration.md) (registry). Related ideas: [IDEA-003](IDEA-003-calendar.md), [IDEA-012](IDEA-012-coverage-admin.md).
- 2026-10-29: proposed after the maintainer noticed that the meetings named in the West Shore Trail notice were missing from the timeline. The maintainer decided default visibility, flexible hosts with an Other filter, automatic publication, notice scope, and the use of planning calendars (above).
