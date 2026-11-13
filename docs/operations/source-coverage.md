# Source coverage and blockers

This is the per-agency answer to "how does each followed body get into the site?" The tables between `generated` markers restate the registry, [`packages/domain/agencies.json`](../../packages/domain/agencies.json), and [`config/skipped-endpoints.json`](../../config/skipped-endpoints.json). `python3 scripts/project.py worker coverage-doc` rewrites them, and a test fails when they are out of date, so edit the configuration rather than the tables. The notes around them are written by hand.

<!-- generated: summary -->
The registry has **56 entries**. **49 are followed**; the 6 entries disabled by the [source review](source-review.md) (`cal-epa`, `douglas-newsletter`, `fows`, `lahontan-office`, `tahoe-fund`, `usace`) stay in the file for provenance and are left out below. Of the followed entries, **40** have live discovery, **3** are blocked by a skipped endpoint, and **6** have no live discovery. 4 entries extend a sibling body's entry: `ttd-rtcpc` (`ttd`), `trpa-advisory` (`trpa-governing`), `douglas-planning` (`douglas-commissioners`), `tbec-stakeholders` (`tdva`). 1 entry (`other`) is not a body: it holds one-off meetings whose host is named per record.
<!-- /generated: summary -->

## How meetings arrive

The scheduled job (`tahoe-ingest run-scheduled`, [decision 0018](../decisions/0018-scheduled-publication.md)) gets meetings from **live discovery** only ([decision 0033](../decisions/0033-production-data-only-from-live-pipeline.md)): each run reads the index named by the `discovery` block of every followed entry that has one ([configuration guide](../../config/README.md#reviewed-source-configuration-and-repeatable-acquisition)). Besides meetings, it reads `config/public-comment-periods.json` (each notice fetched and verified) and the one-off notices in `config/one-off-meetings.json` (currently none).

Until November 2026 the job also loaded hand-built meeting records: a proof-of-concept bundle and the dated candidate lists of the October 20, 2026 source review (now [`docs/research/verified-agency-meetings/`](../research/verified-agency-meetings/)). Meetings they created stay stored until `clean-hand-built` removes the empty ones, but nothing adds to them.

An entry's coverage is therefore one of: **automatic**, **no live discovery**, or **blocked** (the official source refuses automated requests, so neither a better adapter nor a retry helps until the source or the agency changes). "Blocked" and "no live discovery" are different. An entry without live discovery may just need a discovery block, or a new discovery type.

## Automatic: live discovery

<!-- generated: discovery -->
| Registry ID | Type | Index | Body filter | Agenda layouts |
| --- | --- | --- | --- | --- |
| `ivgid` | `civicclerk` | [ivgid.portal.civicclerk.com](https://ivgid.portal.civicclerk.com/) | `Trustees` | `ivgid-board` |
| `tdrpd` | `civicplus` | [www.tdrpd.org](https://www.tdrpd.org/AgendaCenter/Board-Meetings-Agendas-2) | `^(?!.*(?:Committee\|\bFCA\b)).*(?:\bBoard\b\|\bWorkshop\b\|\bSpecial Meeting\b)` | `tdrpd-board` |
| `ctc` | `dated-page-links` | [tahoe.ca.gov](https://tahoe.ca.gov/board-meetings-agendas/) | every row | `ctc-board` |
| `california-state-parks` | `dated-sections` | [ohv.parks.ca.gov](https://ohv.parks.ca.gov/?page_id=27160) | `Commission Meeting` | `ohmvr-commission`, `state-park-commission` |
| `california-fish-game` | `dated-table` | [fgc.ca.gov](https://fgc.ca.gov/Meetings/2026) | `Commission\|Committee` | `fish-game-commission` |
| `lahontan-board` | `dated-table` | [www.waterboards.ca.gov](https://www.waterboards.ca.gov/lahontan/board_info/agenda/2026_schedule.html) | `Board` | `lahontan-board` |
| `ndep` | `dated-table` | [ndep.nv.gov](https://ndep.nv.gov/water/financing-infrastructure/board-for-financing-water-projects/meeting-agendas-minutes/) | `.` | `ndep-board` |
| `slttid` | `file-links` | [southtahoetourism.com](https://southtahoetourism.com/agendas%2Fminutes%2Fnotices) | `^TID (?:Agenda\|Special meeting)\b` | `slttid-special`, `slttid-board` |
| `douglas-commissioners` | `granicus` | [douglascountynv.granicus.com](https://douglascountynv.granicus.com/ViewPublisher.php?view_id=1) | `(?s)^(?!.*Supplement)\s*Board of County Commissioners` | `douglas-county-board` |
| `douglas-planning` (extends `douglas-commissioners`) | `granicus` | [douglascountynv.granicus.com](https://douglascountynv.granicus.com/ViewPublisher.php?view_id=1) | `(?s)^(?!.*Supplement)\s*Planning Commission` | `douglas-county-planning` |
| `douglas-transportation` | `granicus` | [douglascountynv.granicus.com](https://douglascountynv.granicus.com/ViewPublisher.php?view_id=1) | `Regional Transportation Commission` | `douglas-county-joint-board-rtc` |
| `slt-parks-recreation-commission` | `granicus` | [slt.granicus.com](https://slt.granicus.com/ViewPublisher.php?view_id=6) | `Parks and Recreation Commission` | `slt-parks-commission` |
| `slt-planning` | `granicus` | [slt.granicus.com](https://slt.granicus.com/ViewPublisher.php?view_id=6) | `Planning Commission` | `slt-planning-commission` |
| `slt-transit-jpa` | `granicus` | [slt.granicus.com](https://slt.granicus.com/ViewPublisher.php?view_id=6) | `South Tahoe Transit` | `slt-transit-jpa` |
| `tcpud` | `granicus` | [tahoecitypud.granicus.com](https://tahoecitypud.granicus.com/ViewPublisher.php?view_id=2) | `Board of Directors` | `tcpud-board` |
| `tcpud-parks-recreation-committee` | `granicus` | [tahoecitypud.granicus.com](https://tahoecitypud.granicus.com/ViewPublisher.php?view_id=2) | `Parks and Recreation Committee` | — |
| `slt` | `granicus-row-ids` | [slt.granicus.com](https://slt.granicus.com/ViewPublisher.php?view_id=6) | `City Council` | `slt-council` |
| `el-dorado-planning` | `legistar` | [eldorado.legistar.com](https://eldorado.legistar.com/DepartmentDetail.aspx?GUID=E85584F9-B2BA-464D-8D34-A85046210F50&ID=4415) | `Planning Commission` | `el-dorado-legistar-planning` |
| `el-dorado-supervisors` | `legistar` | [eldorado.legistar.com](https://eldorado.legistar.com/DepartmentDetail.aspx?GUID=8D6AEF28-D72E-4E85-9BCE-93169FD91B4F&ID=4317) | `Board of Supervisors` | `el-dorado-legistar-board` |
| `washoe-commissioners` | `legistar` | [washoe-nv.legistar.com](https://washoe-nv.legistar.com/DepartmentDetail.aspx?ID=30765&GUID=4B8B322D-C2C5-48F3-899F-45F45D87C758) | `Board of County Commissioners` | `washoe-county-board` |
| `washoe-planning` | `legistar` | [washoe-nv.legistar.com](https://washoe-nv.legistar.com/DepartmentDetail.aspx?ID=54400&GUID=37861DCF-5BF7-4D16-BE71-F860591E1ADC) | `Planning Commission` | `washoe-county-planning` |
| `ntcd` | `link-index` | [docs.google.com](https://docs.google.com/spreadsheets/d/e/2PACX-1vS4aTYmjstsH_N06j4NGelCESd-ONVcib4QUp2i4gUn_7nROGirLdlGbSI5WwMcNjHIxY0t-WCTeU7e/pubhtml/sheet?headers=false&gid=0) | `Board Meeting Library` | `ntcd-board` |
| `placer-transportation` | `link-index` | [www.pctpa.net](https://www.pctpa.net/pctpa-board) | `PCTPA` | `pctpa-board` |
| `south-shore-tma` | `link-index` | [ss-tma.org](https://ss-tma.org/board-meetings/) | `Board` | `south-shore-tma-board` |
| `stpud` | `link-index` | [www.stpud.us](https://www.stpud.us/board-meetings) | `Board` | `stpud-board`, `stpud-workshop` |
| `trcd` | `link-index` | [tahoercd.specialdistrict.org](https://tahoercd.specialdistrict.org/board-meetings) | `Board Meeting` | `tahoe-rcd-board` |
| `truckee-north-shore-tma` | `link-index` | [tnttma.com](https://tnttma.com/tma-board-agenda/) | `Board Meeting\|TNT.?TMA` | `tnt-tma-board` |
| `truckee-tahoe-airport` | `meeting-files` | [truckeetahoeairport.com](https://truckeetahoeairport.com/administration/board/meetings) | every row | `truckee-tahoe-airport-board` |
| `state-lands` | `meeting-post-feed` | [www.slc.ca.gov](https://www.slc.ca.gov/category/meeting/feed/) | `Commission Meeting` | — |
| `truckee` | `municode` | [meetings.municode.com](https://meetings.municode.com/MeetingsPage/index?clientCode=TRUCKEECA) | `Town Council` | `truckee-council` |
| `ntpud` | `rss` | [ntpud.org](https://ntpud.org/category/meetings/feed/) | `Board of Directors` | `ntpud-board` |
| `ltusd` | `simbli` | [simbli.eboardsolutions.com](https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx?S=36030287) | `^Board of Education\b` | — |
| `ltva` | `tribe-events` | [ltva.org](https://ltva.org/wp-json/tribe/events/v1/events) | `^(?:CANCELL?ED\b.*?[–—-]\s*)?Lake Tahoe Visitors Authority Board of Directors\b` | `ltva-board` |
| `ltva-msc` | `tribe-events` | [ltva.org](https://ltva.org/wp-json/tribe/events/v1/events) | `^(?:CANCELL?ED\b.*?[–—-]\s*)?Lake Tahoe Visitors Authority Marketing Services Committee\b` | — |
| `trpa-advisory` (extends `trpa-governing`) | `trpa` | [www.trpa.gov](https://www.trpa.gov/meeting-materials/) | `Advisory Planning Commission` | `trpa-apc` |
| `trpa-governing` | `trpa` | [www.trpa.gov](https://www.trpa.gov/meeting-materials/) | `Governing Board` | `trpa-governing-board` |
| `tbec-stakeholders` (extends `tdva`) | `wordpress-media` | [tahoedouglasva.org](https://tahoedouglasva.org/wp-json/wp/v2/media) | `^(?:TBEC )?Stakeholder (?:Coordination )?(?:Agenda\|Meeting)\b` | `tbec-stakeholder` |
| `tdva` | `wordpress-media` | [tahoedouglasva.org](https://tahoedouglasva.org/wp-json/wp/v2/media) | `^(?:\d+ )?TDVA (?:BOD )?Agenda\b` | `tdva-board` |
| `ttd` | `wordpress-meetings` | [tahoetransportation.org](https://tahoetransportation.org/wp-json/wp/v2/meeting) | `board` | `ttd` |
| `ttd-rtcpc` (extends `ttd`) | `wordpress-meetings` | [tahoetransportation.org](https://tahoetransportation.org/wp-json/wp/v2/meeting) | `Regional (?:and )?Transit (?:and \|& )?Capital Programs` | `ttd` |
<!-- /generated: discovery -->

Notes on particular sources:

- `ttd`, `ttd-rtcpc`: one WordPress feed of meeting posts serves the board and its committee, told apart by title. Posts state no meeting date, so a scheduled run reads at most six.
- `slt`: Granicus row IDs give the date and title, and only the row's PDFs are documents. The commissions on the same publisher are matched by their row text.
- `truckee`: the Town's agenda page embeds its public Municode meeting index, which discovery reads directly.
- `state-lands`: the State Lands Commission's WordPress "Meeting" category feed lists each meeting post ("October 21, 2026 Commission Meeting"), and the post's page is the HTML agenda, read for items from its "Order of Business" markup (no agenda layout; see `html_order_of_business.py`). After the meeting the same page, at a `-voting-record` address, adds the voting record above the agenda. The meetings page's "2026 Meeting Schedule" gives start times and a notice for each scheduled date before its post appears. Each item row links its staff report, which the `order-of-business-item-files` rule maps when the report's "Staff Report 11" heading, title, and meeting date agree. Staff reports, public comment letters, transcripts and the archive are on `slcprdwordpressstorage.blob.core.windows.net`, which was not reachable from the sandbox where this was built; the rule's fixture uses a State Lands report that TRPA attached to its own packet, so the first scheduled scan is the first real download. The live webcast is on CAL-SPAN; the agency pages link only its general site and a CSLC folder, not per-meeting recordings.
- Douglas County: one county Granicus publisher serves all three bodies. The Board and Planning Commission patterns skip the duplicate "Supplemental" rows. Rows with a video link are archive recordings, so their listed time is not used as a start time.
- Legistar (El Dorado, Washoe): only meetings the county has made public appear (unpublished future rows are "not viewable"). Cancellations are read from the calendar.
- Link indexes (`south-shore-tma`, `stpud`, `placer-transportation`, `truckee-north-shore-tma`, `trcd`, `ntcd`): cancelled meetings and meetings without a posted agenda are not discovered. NTCD's index is a published Google Sheet.
- `ntpud`: the category RSS feed, filtered to the Board of Directors. Special meetings get their own IDs.
- `tdrpd`: one CivicPlus Agenda Center category holds the Truckee-Donner Recreation and Park District board and its Budget & Finance and Facility Condition Assessment ("FCA") committees; the body filter keeps rows whose title names the board, a workshop, or a special meeting and no committee. Each row's title link and its "Agenda" download link open the same file, which counts once. Special meetings and workshops get their own IDs (`qualifySpecial`, read from the row title). Regular meetings post the whole board packet as the agenda; the agenda's printed start time (4:00 PM in September and October 2026, though the board page says 6:00 PM) is used at export.
- `ndep`: Board for Financing Water Projects meetings in a dated table (two-digit years). Dates are listed before agendas; placeholders more than 120 days ahead are ignored.
- `truckee-tahoe-airport`: a dated table plus the "next meeting" block, each linking to a per-meeting file list from which the agenda is read.
- `lahontan-board`, `california-fish-game`: annual dated tables (`indexUrlTemplate` with `{year}`).
- `ctc`: the Tahoe Conservancy's board meetings page lists the year's meetings under "Conservancy Meetings 2026", each date linking the meeting's own page, which is the HTML agenda (items read by the `ctc-board` layout; the start time comes from the agenda heading). The next meeting is listed as a bare date before its page exists ("December 10, 2026"), which becomes a notice. The meeting pages are WordPress pages with no feed, and the site's REST API requires a login, so the index page is the source. The Board meets "as needed, four to six times a year", so the learned quarterly schedule was removed: it forecast the third Thursday (December 17) while the Conservancy lists December 10. Staff recommendations are on a separate "meeting documents" page per meeting; no rule reads it yet.
- `california-state-parks`: two statewide commission pages in the same State Parks layout, one accordion panel per meeting. The Off-Highway Motor Vehicle Recreation Commission (`indexUrl`) and the State Park and Recreation Commission (`pages`) keep the `ohmvr-` and `sprc-` source IDs of the earlier records. Within a panel, the "Commission Meeting" line's "Date:", "Time:", "Location:", and "Address:" lines give the meeting (not the tour or workshop on the neighbouring day), and the "Meeting Agenda" link is its document. A panel appears only once a meeting's details are posted, so the schedule's later dates ("November 18 and 19") are not shown until then.

Item parsing is separate from discovery. An entry without agenda layouts (`—` above) keeps its meetings' text and official links, without itemized, topic-tagged agenda items ([agenda layouts](../../config/README.md#agenda-layouts)).

## Blocked

These sources refuse automated requests. The project does not evade bot protection or disable TLS verification, and it does not request in bursts. The worker never requests a [skipped endpoint](../../config/README.md#skipped-endpoints); detailed evidence and next steps are in the linked issue.

<!-- generated: blocked -->
- `https://www.placer.ca.gov/` (HTTP 403 since 2026-10-20; [SOURCE-001](../issues/SOURCE-001-placer-downloads.md)): Every request, including the home page, returns HTTP 403 to this worker. Cause not established; see SOURCE-001. Affects `placer-supervisors` (source page), `placer-planning` (source page), `placer-tahoe-design-review` (discovery index and source page).
<!-- /generated: blocked -->

The Placer County cause is not established: Python and curl with normal TLS get the same result, and DocAccess or automated-request filtering is a hypothesis only. On November 3, 2026 the 403 body was a Cloudflare "Attention Required" page, and no alternative public index was found (see SOURCE-001). The official `rss.aspx` index also returns 403, so no feed is configured. Before the skip, the Tahoe Basin Design Review Committee's Agenda Center archive listed notices, so its stored meetings keep their dates and times.

Behavior while blocked: the meeting notice stays published with its official link, a failed document does not discard the notice, and the meeting page says the agenda is on the agency website instead of claiming it has not been posted. A skipped index is reported as `skipped` in the run summary, not as a failure. The public export gives a blocked entry `status: "blocked"` with the skip reason, so the website's coverage page says the source is blocked instead of showing its last good check.

`placer-transportation` is not part of the Placer County site. It is published by the agency itself at `pctpa.net`, which is not blocked, and it has live discovery.

**Update on `ctc` and `state-lands`.** These were listed as blocked (SOURCE-002). On October 20, 2026, after the fetcher began identifying itself as `TahoePublicMeetings/0.1 (+https://tahoepublicmeetings.org)` instead of `(+local research prototype)`, one request each to the Tahoe Conservancy and State Lands meeting pages returned real pages (HTTP 200), not the Cloudflare block page. A User-Agent rule is a plausible explanation, but it is a hypothesis. `www.placer.ca.gov` still returned 403 with the new identification. State Lands has had live discovery since November 2, 2026, and `ctc` since November 3, 2026 (see the notes above).

## No live discovery, not blocked

These have no live discovery, so no new meetings arrive for them. Meetings stored from the former hand-built records stay until cleaned up.

<!-- generated: no-discovery -->
| Registry ID | Name | Records | Official page |
| --- | --- | --- | --- |
| `ltbmu` | LTBMU — Main office | comment periods only | [www.fs.usda.gov](https://www.fs.usda.gov/r05/laketahoebasin/newsroom) |
| `usfws` | USFWS | comment periods and meetings | [www.fws.gov](https://www.fws.gov/office/reno-fish-and-wildlife/news) |
| `nevada-state-parks` | Nevada State Parks | meetings | [parks.nv.gov](https://parks.nv.gov/news) |
| `tahoe-usfs` | Tahoe USFS | comment periods only | [www.fs.usda.gov](https://www.fs.usda.gov/r05/tahoe/newsroom) |
| `ttusd` | TTUSD — Board of Education | meetings | [ttusdpub.ic-board.com](https://ttusdpub.ic-board.com/) |
| `tdpud` | TDPUD — Board of Directors | meetings | [www.tdpud.org](https://www.tdpud.org/board-meetings) |
<!-- /generated: no-discovery -->

Why there is no discovery:

- `nevada-state-parks`: the Division holds no regular public meetings. Its only record was one statewide regulation hearing (October 28, 2026, Carson City), whose notice page now answers 404. Workshops and hearings appear as posts in the "Public Comment Workshops" category of the [news page](https://parks.nv.gov/news), which is filtered by a form post and has no feed, and the posts carry a publication date rather than a meeting date, so no discovery type reads them. Checked November 3, 2026. Recommendation: keep following for occasional hearings (the coverage note says they are not collected), or stop following; the maintainer decides.
- `tdpud`: the district's "current agenda" link redirects to its CivicClerk portal (`tdpud.portal.civicclerk.com`), the format IVGID's `civicclerk` discovery already reads. The portal and its event API were not reachable from the sandbox where the entry was added on November 13, 2026 (its network proxy refused both hosts; the district did not block anything), so no discovery block was added without checking the board's event names. Next: from a network that reaches CivicClerk, try `{"type": "civicclerk", "indexUrl": "https://tdpud.portal.civicclerk.com/", "apiUrl": "https://tdpud.api.civicclerk.com/v1", "portalUrl": "https://tdpud.portal.civicclerk.com"}` with `worker discover tdpud`, set `bodyPattern` from the event names, and add a layout ([SR-047](source-review.md)). Its YouTube recordings feed is already configured.

`ctc` and `california-state-parks` gained live discovery on November 3, 2026 (see the notes above); their earlier records were the October 20 snapshots.

### Comment-period-only: `ltbmu`, `tahoe-usfs`, `usfws`

Checked October 20, 2026, one request each. All three newsrooms respond normally (HTTP 200) and none publishes a meeting series.

- `ltbmu` (Lake Tahoe Basin Management Unit): the newsroom lists closures, project updates, a memorial, and a newsletter. No meetings and no open comment periods on that page.
- `tahoe-usfs` (Tahoe National Forest): fire-restriction and prescribed-fire releases and feature stories. No meetings and no comment periods on that page. A kids' fishing day is an event, not a public meeting.
- `usfws` (Reno office): species listing news. Two items in about 20 months announced virtual public hearings (February 2026 Fish Lake Valley tui chub, March 2025 bleached sandhill skipper); several others were comment periods. Most are not in the Tahoe Basin.

These are sources of public comment periods, handled by `config/public-comment-periods.json`, not meeting bodies. Per the maintainer's decision, `ltbmu` and `tahoe-usfs` have `participationTypes` of `public-comment-period` only. `usfws` keeps `"meeting"` for its occasional virtual hearings.

## Staff reports and attachments

Items get a "Read the staff report" link and a verbatim summary only where the entry's `staffReports` names a rule ([decision 0026](../decisions/0026-staff-reports-for-every-agency.md)). The [rules README](../../workers/ingestion/tahoe_ingest/staff_report_rules/README.md) says what each rule reads: the item's own report file, pages inside the packet, or Legistar's legislation text.

<!-- generated: staff-reports -->
| Rule | Registry IDs |
| --- | --- |
| `airport-tab-files` | `truckee-tahoe-airport` |
| `civicclerk-packet` | `ivgid` |
| `cover-sheet-packet` | `tdrpd` |
| `douglas-packet` | `douglas-commissioners`, `douglas-planning` (extends `douglas-commissioners`), `douglas-transportation` |
| `fish-game-staff-summaries` | `california-fish-game` |
| `lahontan-item-files` | `lahontan-board` |
| `legistar` | `el-dorado-supervisors`, `el-dorado-planning`, `washoe-commissioners`, `washoe-planning` |
| `ndep-material-packet` | `ndep` |
| `ntpud-packet` | `ntpud` |
| `order-of-business-item-files` | `state-lands` |
| `pctpa-packet` | `placer-transportation` |
| `slt-commission-report` | `slt-planning`, `slt-parks-recreation-commission`, `slt-transit-jpa` |
| `slt-executive-summary` | `slt` |
| `state-parks-cover-pages` | `california-state-parks` (State Park and Recreation Commission only: the OHMVR Commission names its staff reports by topic and prints no item number, so a title would be the only signal.) |
| `stpud-packet` | `stpud` |
| `tcpud-memo` | `tcpud`, `tcpud-parks-recreation-committee` |
| `trpa-meeting-materials` | `trpa-advisory` (extends `trpa-governing`), `trpa-governing` |
| `truckee-packet` | `truckee` |
| `ttd-packet` | `ttd`, `ttd-rtcpc` (extends `ttd`) |

| Registry ID | Staff reports | Why there is no rule |
| --- | --- | --- |
| `ltva-msc` | none published | The committee posts discussion topics and presentation slides, not staff reports. |
| `nevada-state-parks` | none published | A public hearing notice, not an agenda with staff reports. |
| `ntcd` | none published | Publishes one-page agreement summaries, not staff reports. |
| `slttid` | none published | A one-page Word agenda; attachments such as the previous minutes are posted separately, not as staff reports. |
| `south-shore-tma` | none published | A one-page agenda with unnumbered items and no staff reports. |
| `tbec-stakeholders` | none published | A one-page agenda of lettered discussion items; the packet adds the previous meeting's minutes and monitoring reports, not staff reports. |
| `trcd` | none published | The agenda's page column refers to a packet the district does not publish. |
| `truckee-north-shore-tma` | none published | A two-page agenda whose item numbers restart in each section, and no staff reports. |
| `placer-planning` | not known | Blocked: the county site answers HTTP 403 (SOURCE-001). |
| `placer-supervisors` | not known | Blocked: the county site answers HTTP 403 (SOURCE-001). |
| `placer-tahoe-design-review` | not known | Blocked: the county site answers HTTP 403 (SOURCE-001). |
| `tdpud` | not known | Not checked: the district's agendas are on its CivicClerk portal, which was not reachable when this entry was added. |
| `ttusd` | not known | Blocked: the district's agenda site answers HTTP 403 to the project's user agent. |
| `ctc` | published, not attached yet | Staff recommendations are listed on a separate meeting documents page, not beside the agenda items, and no rule reads that page yet. |
| `ltusd` | published, not attached yet | Supporting documents are attached to items inside Simbli and open through the portal's own viewer; no rule reads them yet. |
| `ltva` | published, not attached yet | The board packet reprints financial statements and presentations without numbered staff reports; no rule reads it yet. |
| `tdva` | published, not attached yet | The board packet reprints financial statements and presentations without numbered staff reports; no rule reads it yet. |
| `ltbmu` | — | No `staffReports` entry. |
| `tahoe-usfs` | — | No `staffReports` entry. |
| `usfws` | — | No `staffReports` entry. |
<!-- /generated: staff-reports -->

## Maintaining this page

Edit the registry or `config/skipped-endpoints.json`, then run `python3 scripts/project.py worker coverage-doc` to rewrite the generated tables (`--check` only reports). Keep the notes beside them in step: when an entry gains a `discovery` block, remove its "why there is no discovery" note and add a source note if its index has quirks. When a source turns out to be blocked, add a skipped endpoint with the evidence, create or extend a `docs/issues/SOURCE-NNN` file, and keep the entry followed so its snapshot meetings stay visible. Other documents link here instead of repeating counts.
