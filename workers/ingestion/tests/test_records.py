"""Minutes and recording links: precedence, title dates, feed matching, adapters, and draft minutes in later agendas."""
import json
import os
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest import records
from tahoe_ingest.capture import StoredMeetings
from tahoe_ingest.core import ingest_bundle, init_db
from tahoe_ingest.discovery import granicus_row_ids
from tahoe_ingest.draft_minutes import following_text, link_draft_minutes, minutes_heading, minutes_targets
from tahoe_ingest.source_discovery import discover_configured, event_links

ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- precedence


def test_better_links_replace_and_missing_links_never_remove():
    approved = {"minutes_url": "https://a.gov/m.pdf", "minutes_status": "approved", "minutes_note": None}
    draft_listing = {"minutes_url": "https://a.gov/d.pdf", "minutes_status": "draft", "minutes_note": None}
    later_agenda = {"minutes_url": "https://a.gov/p.pdf#page=5", "minutes_status": "draft", "minutes_note": "Item 3 on the May 6, 2026 agenda"}
    assert records.merged(later_agenda, draft_listing)["minutes_url"] == "https://a.gov/d.pdf"
    assert records.merged(draft_listing, approved)["minutes_status"] == "approved"
    assert records.merged(approved, later_agenda) == {**approved, "recording_url": None}
    assert records.merged(approved, {}) == {**approved, "recording_url": None}
    granicus = {"recording_url": "https://slt.granicus.com/MediaPlayer.php?view_id=6&clip_id=2339"}
    youtube = {"recording_url": "https://www.youtube.com/watch?v=2J7EcPKNfw8"}
    assert records.merged(granicus, youtube)["recording_url"] == youtube["recording_url"]
    assert records.merged(youtube, granicus)["recording_url"] == youtube["recording_url"]
    # A re-offered URL without a status (a reparse) keeps the stored status.
    assert records.merged(later_agenda, {"minutes_url": later_agenda["minutes_url"]})["minutes_note"] == later_agenda["minutes_note"]


def test_listing_minutes_status_and_recording_links():
    assert records.listing_minutes("https://ndep.nv.gov/08.19.2026_DRAFT-BoardMeetingMinutes_.pdf", "Draft Minutes")["minutes_status"] == "draft"
    assert records.listing_minutes("https://ndep.nv.gov/06_03_26_Board_Meeting_Minutes.pdf", "Minutes")["minutes_status"] == "approved"
    assert records.recording_link("https://youtu.be/uBryaYaUW5E") == "https://www.youtube.com/watch?v=uBryaYaUW5E"
    safelink = ("https://gcc02.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DZqAHeQnD9II&data=05")
    assert records.recording_link(safelink) == "https://www.youtube.com/watch?v=ZqAHeQnD9II"
    assert records.recording_link("https://cal-span.org/meeting/rwqcb-laho_20260325/") == "https://cal-span.org/meeting/rwqcb-laho_20260325/"
    assert records.recording_link("https://cal-span.org/meetings.php?folder[]=CFG&year=2026") is None  # a folder of many meetings
    assert records.recording_link("https://www.youtube.com/@TahoeRegionalPlanningAgency") is None


# ---------------------------------------------------------------- dates and matching


@pytest.mark.parametrize("title, expected", [
    ("Truckee Town Council meeting,  Sept. 22,  2026", date(2026, 9, 22)),
    ("South Lake Tahoe City Council | Tuesday, October 6, 2026", date(2026, 10, 6)),
    ("EDC Board Meeting 9-29-2026", date(2026, 9, 29)),
    ("Planning Commission - 10/15/26", date(2026, 10, 15)),
    ("8.11.26  -  Board of Supervisors", date(2026, 8, 11)),
    ("Minutes attachment for 2026-09-03 Board Meeting", date(2026, 9, 3)),
    ("BOARD OF COUNTY COMMISSIONERS | December4, 2025", date(2025, 12, 4)),
    ("TTAD Special Board Meeting September  23, 2026", date(2026, 9, 23)),
    ("A summary 4, 2026 of nothing", None),
])
def test_title_dates(title, expected):
    assert records.title_date(title) == expected


def test_a_list_of_dates_shares_the_year_of_its_last_date():
    assert records.dates_in("REGULAR BOARD MEETING MINUTES: September 17, September 23, and October 1, 2026") == [
        date(2026, 9, 17), date(2026, 9, 23), date(2026, 10, 1)]


def stored(mid, day, title, status="unknown"):
    return {"id": mid, "agency_id": mid.split(":")[0], "title": title, "local_date": day, "status": status,
            "minutes_url": None, "minutes_status": None, "minutes_note": None, "recording_url": None}


def test_match_meeting_tells_same_day_meetings_apart():
    day = date(2026, 10, 13)
    regular = stored("truckee:1771", day, "Town Council Regular Meeting")
    joint = stored("truckee:1895", day, "Town Council and Tahoe Forest Hospital District Joint Meeting")
    meetings = [regular, joint]
    assert records.match_meeting(meetings, day, {"joint"}) is joint
    # The source relabelled the regular meeting as special; with one non-joint meeting that day it still matches.
    assert records.match_meeting(meetings, day, {"special"}) is regular
    closed = stored("slt:2329", date(2026, 9, 22), "09 22 2026 Special Closed Session City Council")
    council = stored("slt:2330", date(2026, 9, 22), "09 22 2026 City Council")
    assert records.match_meeting([closed, council], date(2026, 9, 22), set()) is council
    assert records.match_meeting([closed, council], date(2026, 9, 22), {"special", "closed"}) is closed
    assert records.match_meeting([stored("x:1", day, "Board", status="canceled")], day, set()) is None


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <entry><yt:videoId>2J7EcPKNfw8</yt:videoId><title>South Lake Tahoe City Council | Tuesday, October 6, 2026</title><published>2026-10-06T16:51:12+00:00</published></entry>
 <entry><yt:videoId>AAAAAAAAAAA</yt:videoId><title>South Lake Tahoe City Council | Tuesday, October 6, 2026 original</title><published>2026-10-07T16:51:12+00:00</published></entry>
 <entry><yt:videoId>tNYrO8LL2kc</yt:videoId><title>Planning Commission | Thursday, September 17, 2026</title><published>2026-09-17T16:00:00+00:00</published></entry>
 <entry><yt:videoId>BBBBBBBBBBB</yt:videoId><title>Special Closed Session City Council | Monday, October 26, 2026</title><published>2026-10-26T23:00:00+00:00</published></entry>
 <entry><yt:videoId>CCCCCCCCCCC</yt:videoId><title>South Lake Tahoe City Council | Tuesday, November 10, 2026</title><published>2026-10-27T23:00:00+00:00</published></entry>
</feed>"""


def test_youtube_matches_by_body_and_title_date():
    videos = records.parse_feed(FEED)
    meetings = [stored("slt:2339", date(2026, 10, 6), "10 06 2026 City Council"),
                stored("slt:2400", date(2026, 11, 10), "11 10 2026 City Council")]
    entry = {"agency": "slt", "channelId": "UC3LYne8Ih-kTPLnnuyJW9SQ", "include": r"\bcity council\b"}
    # The "original" upload loses; the scheduled stream for a future meeting is ignored.
    assert records.youtube_matches(entry, videos, meetings, today=date(2026, 10, 27)) == {
        "slt:2339": "https://www.youtube.com/watch?v=2J7EcPKNfw8"}
    planning = {"agency": "slt-planning", "channelId": "UC3LYne8Ih-kTPLnnuyJW9SQ", "include": r"\bplanning commission\b"}
    assert records.youtube_matches(planning, videos, [stored("slt-planning:2906", date(2026, 9, 17), "Planning Commission")],
                                   today=date(2026, 10, 27)) == {"slt-planning:2906": "https://www.youtube.com/watch?v=tNYrO8LL2kc"}


def test_data_api_pages_uploads_until_since_and_hides_the_key():
    pages = {"": {"items": [{"snippet": {"title": "City Council | October 6, 2026", "publishedAt": "2026-10-06T16:00:00Z",
                                         "resourceId": {"videoId": "2J7EcPKNfw8"}}}], "nextPageToken": "B"},
             "B": {"items": [{"snippet": {"title": "City Council | December 9, 2025", "publishedAt": "2025-12-09T16:00:00Z",
                                          "resourceId": {"videoId": "AAAAAAAAAAA"}}}], "nextPageToken": "C"}}
    requested = []

    def fetch(url):
        query = dict(p.split("=", 1) for p in url.split("?", 1)[1].split("&"))
        requested.append(query)
        return json.dumps(pages[query.get("pageToken", "")])
    videos = records.api_videos({"channelId": "UC3LYne8Ih-kTPLnnuyJW9SQ"}, "secret-key", since=date(2026, 1, 1), fetch=fetch)
    assert [v["id"] for v in videos] == ["2J7EcPKNfw8", "AAAAAAAAAAA"]
    assert [q["playlistId"] for q in requested] == ["UU3LYne8Ih-kTPLnnuyJW9SQ"] * 2  # stops once uploads predate --since

    def failing(url):
        raise OSError(f"request to {url} failed")
    with pytest.raises(RuntimeError) as error:
        records.api_videos({"channelId": "UC3LYne8Ih-kTPLnnuyJW9SQ"}, "secret-key", since=date(2026, 1, 1), fetch=failing)
    assert "secret-key" not in str(error.value)


def test_laserfiche_minutes_by_document_name():
    listings = {59909164: [{"name": "2025", "entryId": 1, "type": 0}, {"name": "2026", "entryId": 2, "type": 0}],
                2: [{"name": "2026-09-22 - Regular", "entryId": 59988011, "type": -2},
                    {"name": "2026-05-12 - Closed", "entryId": 3, "type": -2},
                    {"name": "2026-05-12 - Regular and Closed", "entryId": 4, "type": -2}]}

    def fetch_json(url, payload):
        assert payload["repoName"] == "r-6a91ddbc"
        return {"data": {"results": listings.get(payload["folderId"], [])}}
    meetings = [stored("truckee:1795", date(2026, 9, 22), "Town Council Regular Meeting"),
                stored("truckee:1500", date(2026, 5, 12), "Town Council Regular Meeting")]
    found = records.laserfiche_minutes({"agency": "truckee", "repo": "r-6a91ddbc", "folderId": 59909164}, meetings,
                                       today=date(2026, 10, 27), fetch_json=fetch_json)
    assert found == {"truckee:1795": "https://portal.laserfiche.com/Portal/DocView.aspx?id=59988011&repo=r-6a91ddbc",
                     "truckee:1500": "https://portal.laserfiche.com/Portal/DocView.aspx?id=4&repo=r-6a91ddbc"}


def test_tracked_meeting_records_config_is_valid():
    config = records.load_config(ROOT)
    assert config["youtube"] and config["laserfiche"]
    assert {e["agency"] for e in config["youtube"]} >= {"slt", "truckee", "trpa-governing"}


# ---------------------------------------------------------------- adapters

GRANICUS_ROWS = """<table>
<tr class="listingRow"><td class="listItem" headers="Name" id="Board-of-County-Commissioners-" scope="row"> Board of County Commissioners </td>
<td class="listItem"> October&nbsp;15,&nbsp;2026 - 10:00&nbsp;AM </td>
<td class="listItem"><a href="//douglascountynv.granicus.com/AgendaViewer.php?view_id=1&clip_id=1907">Agenda</a></td>
<td class="listItem"><a href="//douglascountynv.granicus.com/MinutesViewer.php?view_id=1&clip_id=1907&doc_id=eefade95">Action Summary</a></td>
<td class="listItem"><a href="javascript:void(0);" onClick="window.open('//douglascountynv.granicus.com/MediaPlayer.php?view_id=1&clip_id=1907','player')"> Video </a></td></tr>
<tr class="listingRow"><td class="listItem" headers="Name" id="Board-of-County-Commissioners-" scope="row"> Board of County Commissioners </td>
<td class="listItem"> September&nbsp;17,&nbsp;2026 - 10:00&nbsp;AM </td>
<td class="listItem"><a href="//douglascountynv.granicus.com/AgendaViewer.php?view_id=1&clip_id=1880">Agenda</a></td>
<td class="listItem"><a href="//douglascountynv.granicus.com/MinutesViewer.php?view_id=1&clip_id=1880&doc_id=781e3534">Minutes</a></td>
<td class="listItem"><a href="javascript:void(0);" onClick="window.open('//douglascountynv.granicus.com/MediaPlayer.php?view_id=1&clip_id=1880','player')"> Video </a></td></tr>
<tr class="listingRow"><td class="listItem" headers="Name" id="Board-of-County-Commissioners-Workshop" scope="row"> Board of County Commissioners Workshop (No Video)</td>
<td class="listItem"> July&nbsp;31,&nbsp;2026 </td><td class="listItem">00h&nbsp;00m</td>
<td class="listItem"><a href="//douglascountynv.granicus.com/AgendaViewer.php?view_id=1&clip_id=1801">Agenda</a></td>
<td class="listItem"><a href="javascript:void(0);" onClick="window.open('//douglascountynv.granicus.com/MediaPlayer.php?view_id=1&clip_id=1801','player')"> Video </a></td></tr>
</table>"""


def test_granicus_rows_give_minutes_and_video_but_not_action_summaries_or_empty_clips():
    rows = discover_configured("douglas-commissioners", {"type": "granicus", "indexUrl": "https://douglascountynv.granicus.com/ViewPublisher.php?view_id=1",
                                                         "bodyPattern": "Board of County Commissioners"}, fetch=lambda url: GRANICUS_ROWS)
    by_id = {r["source_id"]: r for r in rows}
    assert "minutes_url" not in by_id["1907"]
    assert by_id["1907"]["recording_url"] == "https://douglascountynv.granicus.com/MediaPlayer.php?view_id=1&clip_id=1907"
    assert by_id["1880"]["minutes_url"] == "https://douglascountynv.granicus.com/MinutesViewer.php?view_id=1&clip_id=1880&doc_id=781e3534"
    assert by_id["1880"]["minutes_status"] == "approved"
    assert "recording_url" not in by_id["1801"]


def test_slt_publisher_row_links():
    row = """<table><tr class="listingRow"><td class="listItem" headers="Name" id="09-22-2026-City-Council" scope="row">09 22 2026 City Council</td>
    <td class="listItem">02h&nbsp;10m</td>
    <td class="listItem"><a href="//slt.granicus.com/AgendaViewer.php?view_id=6&clip_id=2330">Agenda</a></td>
    <td class="listItem"><a href="//slt.granicus.com/MinutesViewer.php?view_id=6&clip_id=2330&doc_id=57df807f">Minutes</a></td>
    <td class="listItem"><a href="javascript:void(0);" onClick="window.open('//slt.granicus.com/MediaPlayer.php?view_id=6&amp;clip_id=2330','player')">Video</a></td>
    </tr></table>"""
    config = {"type": "granicus-row-ids", "indexUrl": "https://slt.granicus.com/ViewPublisher.php?view_id=6", "bodyPattern": "City Council"}
    [found] = granicus_row_ids("slt", config, limit=1, fetch=lambda url: row)
    assert found["minutes_url"] == "https://slt.granicus.com/MinutesViewer.php?view_id=6&clip_id=2330&doc_id=57df807f"
    assert found["recording_url"] == "https://slt.granicus.com/MediaPlayer.php?view_id=6&clip_id=2330"


LEGISTAR = """<table><tr class="rgRow"><td>10/22/2026</td><td>
<a href="MeetingDetail.aspx?ID=1452289&amp;GUID=9404">Meeting&nbsp;details</a></td>
<td><a href="View.ashx?M=A&amp;ID=1452289&amp;GUID=9404">Agenda</a></td>
<td><a href="View.ashx?M=M&amp;ID=1452289&amp;GUID=9404">Minutes</a></td>
<td><a class="videolink" onclick="window.open(&#39;Video.aspx?Mode=Granicus&amp;ID1=2690&amp;Mode2=Video&#39;,&#39;video&#39;);return false;" href="#">Video</a></td>
</tr></table>"""


def test_legistar_minutes_come_from_the_api_status_not_the_department_page():
    config = {"type": "legistar", "indexUrl": "https://eldorado.legistar.com/DepartmentDetail.aspx?ID=4415", "bodyPattern": "Planning Commission"}
    events = [{"EventId": 5313, "EventBodyName": "Planning Commission", "EventDate": "2026-10-22T00:00:00",
               "EventMinutesFile": "https://eldorado.legistar1.com/eldorado/meetings/2026/10/5313_M.pdf",
               "EventMinutesStatusName": "Draft", "EventMedia": 2690}]
    fetch = lambda url: json.dumps(events) if url.startswith("https://webapi.legistar.com/") else LEGISTAR
    [row] = discover_configured("el-dorado-planning", config, fetch=fetch)
    assert row["recording_url"] == "https://eldorado.legistar.com/Video.aspx?Mode=Granicus&ID1=2690&Mode2=Video"
    assert row["minutes_status"] == "draft" and row["minutes_url"].endswith("5313_M.pdf")
    events[0]["EventMinutesStatusName"] = "Final"
    assert event_links("eldorado", events[0])["minutes_status"] == "approved"
    # Without the API, the department page's minutes link (posted before approval) is not used.
    [row] = discover_configured("el-dorado-planning", config, fetch=lambda url: LEGISTAR if "legistar.com/Dep" in url else (_ for _ in ()).throw(OSError()))
    assert "minutes_url" not in row


def test_dated_table_rows_give_minutes_and_video():
    markup = """<table><tr><td>February 11</td><td>Barstow, CA</td><td><a href="2026/feb/feb2026agenda_eng.pdf">Agenda</a></td>
    <td><a href="2026/feb/february-11-2026-meeting-minutes.pdf">Minutes</a></td><td><a href="https://youtu.be/uBryaYaUW5E">Meeting Video</a></td></tr>
    <tr><td>March 25</td><td>Bishop, CA</td><td><a href="2026/mar/mar2026agenda_eng.pdf">Agenda</a></td><td></td>
    <td><a href="https://cal-span.org/meeting/rwqcb-laho_20260325/">Meeting Video</a></td></tr></table>"""
    config = {"type": "dated-table", "indexUrl": "https://www.waterboards.ca.gov/lahontan/board_info/agenda/2026_schedule.html", "bodyPattern": "Board"}
    rows = {r["date"]: r for r in discover_configured("lahontan-board", config, fetch=lambda url: markup)}
    assert rows["2026-02-11"]["minutes_url"].endswith("/2026/feb/february-11-2026-meeting-minutes.pdf")
    assert rows["2026-02-11"]["recording_url"] == "https://www.youtube.com/watch?v=uBryaYaUW5E"
    assert rows["2026-03-25"]["recording_url"] == "https://cal-span.org/meeting/rwqcb-laho_20260325/"
    assert "minutes_url" not in rows["2026-03-25"]


def test_link_index_minutes_match_agenda_rows_by_aria_label_date():
    markup = """<article><a href="/2026-09-03-board-meeting"><time datetime="2026-09-03">Sep 3 2026</time><h3>Board Meeting</h3></a>
    <a href="/files/c2/2026.09.03+District+Board+Agenda+Packet.pdf" aria-label="Agenda attachment for 2026-09-03 Board Meeting">Agenda</a>
    <a href="/files/b6/2026.09.03+Minutes.pdf" aria-label="Minutes attachment for 2026-09-03 Board Meeting">Minutes</a></article>
    <a href="/files/aa/07-16-2026+Minutes.pdf" aria-label="Minutes attachment for 2026-07-16 Board Meeting">Minutes</a>
    <a href="/board-meetings-minutes-and-agendas">Board Meetings Minutes and Agendas</a>"""
    config = {"type": "link-index", "indexUrl": "https://www.stpud.us/board-meetings", "bodyPattern": "Board",
              "title": "South Tahoe Public Utility District Board Meeting", "qualifySpecial": True, "minutesLinks": True}
    rows = discover_configured("stpud", config, fetch=lambda url: markup)
    [meeting] = [r for r in rows if not r.get("records_only")]
    assert meeting["minutes_url"] == "https://www.stpud.us/files/b6/2026.09.03+Minutes.pdf"
    [earlier] = [r for r in rows if r.get("records_only")]
    assert earlier["date"] == "2026-07-16" and earlier["minutes_url"].endswith("07-16-2026+Minutes.pdf")


def test_trpa_meeting_page_links():
    index = '<a href="https://www.trpa.gov/governing-board-documents-october-28-2026-hybrid-meeting/">Governing Board Documents October 28, 2026-Hybrid Meeting</a>'
    page = """<a href="https://www.trpa.gov/wp-content/uploads/October-28-Governing-Board-Agenda.pdf">Agenda</a>
    <a href="https://www.trpa.gov/wp-content/uploads/Draft-September-23-Governing-Board-Minutes.pdf">Draft September 23, 2026 Governing Board Minutes</a>
    <a href="https://www.trpa.gov/wp-content/uploads/Draft-April-22-2026-EIP-Committee-Minutes.pdf">Draft April 22, 2026 Environmental Improvement Program Committee Minutes</a>
    <a href="https://youtu.be/prPCJgSrnd4">October 28, 2026 TRPA Governing Board Meeting Video Recording</a>"""
    config = {"type": "trpa", "indexUrl": "https://www.trpa.gov/meeting-materials/", "bodyPattern": "Governing Board"}
    rows = discover_configured("trpa-governing", config, fetch=lambda url: index if url == config["indexUrl"] else page)
    [meeting] = [r for r in rows if not r.get("records_only")]
    assert meeting["recording_url"] == "https://www.youtube.com/watch?v=prPCJgSrnd4" and "minutes_url" not in meeting
    [earlier] = [r for r in rows if r.get("records_only")]
    assert earlier["date"] == "2026-09-23" and earlier["minutes_status"] == "draft"


def test_civicclerk_minutes_and_media():
    events = {"items": [{"id": 745, "eventName": "Board of Trustees Meeting", "eventDate": "2026-09-09T16:00:00Z", "hasMedia": True,
                         "publishedFiles": [{"type": "Agenda", "name": "Agenda", "fileId": 2838},
                                            {"type": "Minutes", "name": "2026-Sept 09 BOT Meeting Minutes Approved 2026-Oct 14", "fileId": 2873}]}]}
    config = {"type": "civicclerk", "indexUrl": "https://ivgid.portal.civicclerk.com/", "bodyPattern": "Trustees",
              "apiUrl": "https://ivgid.api.civicclerk.com/v1", "portalUrl": "https://ivgid.portal.civicclerk.com"}
    [row] = discover_configured("ivgid", config, fetch=lambda url: json.dumps(events))
    assert row["minutes_url"] == "https://ivgid.api.civicclerk.com/v1/Meetings/GetMeetingFileStream(fileId=2873,plainText=false)"
    assert row["minutes_status"] == "approved"
    assert row["recording_url"] == "https://ivgid.portal.civicclerk.com/event/745/media"


# ---------------------------------------------------------------- draft minutes in later agendas


def item(title, later, meeting_title="Board Meeting", quote=None, following=""):
    return {"title": title, "local_date": later, "meeting_title": meeting_title, "following": following,
            "citations": [{"quote": quote or title, "page": 1, "documentId": "v"}]}


def test_minutes_targets_dated_undated_and_other_bodies():
    meetings = [stored("ttad:1", date(2026, 5, 20), "Truckee Tahoe Airport District Regular Board Meeting"),
                stored("ttad:2", date(2026, 5, 20), "Truckee Tahoe Airport District Special Board Meeting"),
                stored("ttad:3", date(2026, 6, 24), "Truckee Tahoe Airport District Regular Board Meeting")]
    later = date(2026, 6, 24)
    assert [m["id"] for m in minutes_targets(item("Minutes: May 20, 2026, Special Meeting", later), meetings)] == ["ttad:2"]
    assert [m["id"] for m in minutes_targets(item("Minutes: May 20, 2026, Regular Meeting", later), meetings)] == ["ttad:1"]
    assert minutes_targets(item("Public comment, three minutes per speaker", later), meetings) == []
    council = [stored("slt:1", date(2026, 9, 22), "09 22 2026 City Council"),
               stored("slt:2", date(2026, 9, 22), "09 22 2026 Special Closed Session City Council")]
    october = date(2026, 10, 6)
    assert [m["id"] for m in minutes_targets(item("City Council Meeting Minutes", october, "10 06 2026 City Council"), council)] == ["slt:1"]
    # An undated item naming another body is never this body's minutes.
    assert minutes_targets(item("Technical Advisory Committee Minutes", october, "10 06 2026 City Council"), council) == []
    # Dates in the agenda text after the item: only the body's own meetings match.
    board = [stored("tcpud:1", date(2026, 9, 18), "Board of Directors Meeting"), stored("tcpud:2", date(2026, 10, 16), "Board of Directors Meeting")]
    text = following_text("D. CONSENT 1. Minutes a. Parks and Recreation Committee – September 8, 2026 b. Finance Committee – "
                          "September 10, 2026 c. Board of Directors Meeting – September 18, 2026 2. Treasurer's report for September",
                          "Minutes", "Treasurer's report for September")
    assert [m["id"] for m in minutes_targets(item("Minutes", date(2026, 10, 23), following=text), board)] == ["tcpud:1"]


def test_minutes_heading_rejects_agenda_pages():
    day = date(2026, 8, 11)
    assert minutes_heading("TAHOE TRANSPORTATION DISTRICT BOARD MEETING MINUTES August 11, 2026 Board Members in Attendance", day)
    assert minutes_heading("Approved: June 3, 2026 TAHOE TRANSPORTATION DISTRICT BOARD MEETING MINUTES August 11, 2026", day)
    assert not minutes_heading("1. Approve Accounts Paid 2. Approve the Special and Regular Meeting Minutes of August 11, 2026", day)
    assert not minutes_heading("BOARD OF DIRECTORS MEETING AGENDA Page 2 E. Approval of Minutes: August 11, 2026", day)


@pytest.fixture
def db():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_records_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with psycopg.connect(url, options=f"-c search_path={schema}") as connection:
            connection.row_factory = psycopg.rows.dict_row
            init_db(connection)
            yield connection
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def packet_meeting(source_id, day, pages, items, **links):
    return {"agency": "ttd", "id": source_id, "title": "TTD Board of Directors Meeting", "date": day,
            "source_meeting_url": f"https://tahoetransportation.org/meetings/{source_id}", "status": "scheduled",
            "documents": [{"id": "ttd-agenda-packet", "title": "Agenda packet", "url": f"https://tahoetransportation.org/{source_id}.pdf",
                           "sha256": f"{int(source_id):064x}", "fetched_at": "2026-10-01T00:00:00Z", "page_count": len(pages), "warnings": []}],
            "pages": [{"document_id": "ttd-agenda-packet", "page_number": n, "text": t} for n, t in enumerate(pages, 1)],
            "agenda_items": [{**i, "source_number": i["number"], "order": n} for n, i in enumerate(items, 1)],
            "items_complete": True, "documents_complete": True, **links}


def test_draft_minutes_link_the_packet_page_and_never_replace_listing_minutes(db):
    agenda = "AGENDA I. CALL TO ORDER C. Approval of Minutes of August 5, 2026 Page 1 II. BUSINESS A. Budget"
    later = packet_meeting("7070", "2026-10-07", [agenda, "Staff memo", "TAHOE TRANSPORTATION DISTRICT BOARD MEETING MINUTES August 5, 2026 Members"], [
        {"number": "I.C", "title": "Approval of Minutes of August 5, 2026", "source_pages": [
            {"document_id": "ttd-agenda-packet", "page_number": 1, "quote": "Approval of Minutes of August 5, 2026"}]},
        {"number": "II.A", "title": "Budget", "source_pages": [{"document_id": "ttd-agenda-packet", "page_number": 1, "quote": "Budget"}]}])
    earlier = packet_meeting("7144", "2026-08-05", ["AGENDA I. CALL TO ORDER"], [
        {"number": "I.A", "title": "Call to order", "source_pages": [{"document_id": "ttd-agenda-packet", "page_number": 1, "quote": "CALL TO ORDER"}]}])
    assert ingest_bundle(db, {"meetings": [earlier, later]})["failed"] == 0
    assert link_draft_minutes(db, since=date(2026, 1, 1)) == {"matched": 1, "updated": 1}
    row = db.execute("SELECT minutes_url,minutes_status,minutes_note FROM meetings WHERE id='ttd:7144'").fetchone()
    assert row == {"minutes_url": "https://tahoetransportation.org/7070.pdf#page=3", "minutes_status": "draft",
                   "minutes_note": "In the October 7, 2026 agenda packet, page 3"}
    assert link_draft_minutes(db, since=date(2026, 1, 1))["updated"] == 0  # idempotent
    # Approved minutes from a listing replace the draft; a later draft never replaces them.
    approved = {**earlier, "minutes_url": "https://tahoetransportation.org/approved.pdf", "minutes_status": "approved", "partial_logistics": True}
    assert ingest_bundle(db, {"meetings": [approved]})["failed"] == 0
    assert link_draft_minutes(db, since=date(2026, 1, 1))["updated"] == 0
    row = db.execute("SELECT minutes_url,minutes_status,minutes_note FROM meetings WHERE id='ttd:7144'").fetchone()
    assert row == {"minutes_url": "https://tahoetransportation.org/approved.pdf", "minutes_status": "approved", "minutes_note": None}


def test_stored_meetings_apply_links_for_settled_and_records_only_candidates(db):
    meeting = packet_meeting("7144", "2026-08-05", ["AGENDA I. CALL TO ORDER"], [
        {"number": "I.A", "title": "Call to order", "source_pages": [{"document_id": "ttd-agenda-packet", "page_number": 1, "quote": "CALL TO ORDER"}]}])
    assert ingest_bundle(db, {"meetings": [meeting]})["failed"] == 0
    snapshot = StoredMeetings(db, failure_hours=24)
    candidates = [{"agency": "ttd", "source_id": "7144", "title": meeting["title"], "date": "2026-08-05",
                   "official_url": meeting["source_meeting_url"], "recording_url": "https://www.youtube.com/watch?v=2J7EcPKNfw8"},
                  records.records_entry("ttd", "2026-08-05", "Minutes", records.listing_minutes("https://tahoetransportation.org/m.pdf", "Minutes"))]
    assert snapshot.apply_links(candidates, coverage_start=date(2026, 1, 1)) == 2
    assert snapshot.apply_links(candidates, coverage_start=date(2026, 1, 1)) == 0
    row = db.execute("SELECT minutes_url,recording_url FROM meetings WHERE id='ttd:7144'").fetchone()
    assert row == {"minutes_url": "https://tahoetransportation.org/m.pdf", "recording_url": "https://www.youtube.com/watch?v=2J7EcPKNfw8"}
