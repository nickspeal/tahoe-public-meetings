"""The `wordpress-media`, `file-links`, and `tribe-events` discovery types, replayed on saved official responses.

`wordpress-media`: the Tahoe Douglas Visitors Authority's media library searched for
"agenda" (the board) and "stakeholder" (the Tahoe Blue Event Center stakeholder meetings),
with the meetings page's cancellation notice. `file-links`: the South Lake Tahoe Tourism
Improvement District's GoDaddy page of Word and PDF downloads, and (read by link label) the
Tahoe Forest Hospital District's minutes and agendas page. `tribe-events`: the Lake
Tahoe Visitors Authority's events calendar API (the board and its marketing committee).
"""
import datetime as real
import json
import re
import urllib.parse
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import file_discovery
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.file_discovery import canceled_dates, file_name
from tahoe_ingest.source_discovery import date_in, discover_configured, discover_configured_range, validate_discovery

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/discovery"
MEDIA = {"agenda": json.loads((FIXTURES / "tdva-media-agenda-2026-11-09.json").read_text()),
         "stakeholder": json.loads((FIXTURES / "tdva-media-stakeholder-2026-11-09.json").read_text())}
TDVA_PAGE = (FIXTURES / "tdva-meetings-2026-11-09.html").read_text()
SLTTID_PAGE = (FIXTURES / "slttid-agendas-minutes-notices-2026-11-09.html").read_text()
TFHD_PAGE = (FIXTURES / "tfhd-minutes-agendas-2026-11-13.html").read_text()
EVENTS = json.loads((FIXTURES / "ltva-tribe-events-2026.json").read_text())
TDVA_UPLOADS = "https://tahoedouglasva.org/wp-content/uploads/"


def agency(aid):
    return next(a for a in agency_catalog() if a["id"] == aid)


def media_fetch(requested):
    def fetch(url):
        requested.append(url)
        if url == "https://tahoedouglasva.org/meetings/":
            return TDVA_PAGE
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        term = query["search"][0]
        page = int(query["page"][0])
        found = MEDIA[term] if page == 1 else []
        return json.dumps(found)
    return fetch


def test_media_library_files_become_meetings_by_the_date_in_their_names():
    requested = []
    rows = discover_configured("tdva", agency("tdva")["discovery"], limit=4, fetch=media_fetch(requested))
    assert [(r["source_id"], r["title"], r["status"]) for r in rows] == [
        ("2026-11-11", "TDVA Board of Directors Meeting", "canceled"),  # the meetings page's cancellation notice
        ("2026-10-14", "TDVA Board of Directors Meeting", "unknown"),
        ("2026-09-21", "TDVA Board of Directors Meeting", "unknown"),
        ("2026-09-11", "TDVA Board of Directors Meeting", "unknown")]
    assert rows[1]["documents"] == [
        {"id": "tdva-agenda", "title": "TDVA Agenda – October 14, 2026",
         "url": TDVA_UPLOADS + "2026/10/TDVA-Agenda-October-14-2026.pdf"},
        {"id": "tdva-agenda-packet", "title": "TDVA Agenda – October 14, 2026 Packet",
         "url": TDVA_UPLOADS + "2026/10/TDVA-Agenda-October-14-2026-Packet.pdf"}]
    assert rows[1]["official_url"] == rows[1]["documents"][0]["url"]
    assert requested[0].startswith("https://tahoedouglasva.org/wp-json/wp/v2/media?search=agenda&per_page=100&page=1")
    assert requested[1:] == ["https://tahoedouglasva.org/meetings/"]


def test_media_library_leaves_out_other_bodies_and_month_only_files():
    rows = discover_configured("tdva", agency("tdva")["discovery"], limit=20, fetch=media_fetch([]))
    names = [d["url"].rsplit("/", 1)[-1] for r in rows for d in r["documents"]]
    assert not any("Joint" in n or "Stakeholder" in n for n in names)  # the joint workshop and TBEC are not the board's
    assert "TDVA-Agenda-April-2026.pdf" not in names  # a month without a day is no meeting date
    special = next(r for r in rows if r["date"] == "2026-04-28")
    assert special["source_id"] == "2026-04-28-special" and special["title"] == "Special TDVA Board of Directors Meeting"
    assert [d["id"] for d in special["documents"]] == ["tdva-agenda-" + d["id"].rsplit("-", 1)[-1] for d in special["documents"]]


def test_a_sibling_body_searches_the_same_library_for_its_own_files():
    rows = discover_configured("tbec-stakeholders", agency("tbec-stakeholders")["discovery"], limit=3, fetch=media_fetch([]))
    assert [(r["source_id"], r["title"]) for r in rows] == [
        ("2026-10-13", "Tahoe Blue Event Center Stakeholder Meeting"),
        ("2026-07-14", "Tahoe Blue Event Center Stakeholder Meeting"),
        ("2026-04-14", "Tahoe Blue Event Center Stakeholder Meeting")]
    assert all(d["id"].startswith("tbec-stakeholders-agenda-packet") for d in rows[0]["documents"])


def test_media_library_range_pages_until_the_results_run_out():
    requested = []
    rows, earliest, by_date = discover_configured_range("tdva", agency("tdva")["discovery"], since=date(2026, 1, 1),
                                                        until=date(2026, 12, 31), fetch=media_fetch(requested))
    assert not by_date and earliest == "2026-04-28"  # earlier 2026 agendas name only their month
    assert [r["date"] for r in rows][-3:] == ["2026-06-10", "2026-05-20", "2026-04-28"]
    assert all("2026-01-01" <= r["date"] <= "2026-12-31" for r in rows)
    assert sum("wp-json" in u for u in requested) == 1  # one short page is the last


def test_cancellation_notices_name_their_meeting_date():
    assert canceled_dates(TDVA_PAGE) == {"2026-11-11"}
    assert canceled_dates("<p>All meeting dates are subject to change or cancellation.</p>") == set()


def test_file_names_read_as_text_with_their_dates():
    assert file_name("https://x.org/d/TID%20Special%20meeting%209-24-26%20(002).docx?ver=1") == "TID Special meeting 9.24.26 (002)"
    assert date_in(file_name("https://x.org/d/TID%20Special%20meeting%209-24-26%20(002).docx")) == "2026-09-24"
    assert date_in(file_name("https://x.org/d/TID%20Agenda%2010.29.2026.docx")) == "2026-10-29"
    assert date_in(file_name("https://x.org/u/1_TDVA-Agenda-November-11-2026-Packet.pdf")) == "2026-11-11"
    assert date_in("TDVA Agenda April 2026") is None


def test_download_links_on_a_page_become_meetings_and_word_files_qualify():
    url = agency("slttid")["discovery"]["indexUrl"]
    rows = discover_configured("slttid", agency("slttid")["discovery"], limit=5, fetch=lambda u: SLTTID_PAGE if u == url else 1 / 0)
    assert [(r["source_id"], r["title"]) for r in rows] == [
        ("2026-10-29", "SLTTID Board of Directors Meeting"),
        ("2026-09-24-special", "Special SLTTID Board of Directors Meeting"),
        ("2026-08-27", "SLTTID Board of Directors Meeting"),
        ("2026-06-25", "SLTTID Board of Directors Meeting"),
        ("2026-03-26", "SLTTID Board of Directors Meeting")]
    first = rows[0]
    assert first["official_url"] == url
    assert first["documents"] == [{"id": "slttid-agenda", "title": "TID Agenda 10.29.2026", "url": (
        "https://img1.wsimg.com/blobby/go/d2b08c94-0165-485f-9ec5-f832cd1047fc/downloads/"
        "4faa4df5-28b2-425d-85d7-1d4857f47ab7/TID%20Agenda%2010.29.2026.docx?ver=1793223943790")}]
    every = discover_configured("slttid", agency("slttid")["discovery"], limit=None, fetch=lambda u: SLTTID_PAGE)
    names = [d["title"] for r in every for d in r["documents"]]
    assert not any("Minutes" in n or "SLTLA" in n or "Joint" in n or "Board" in n for n in names)  # minutes and other bodies stay out


def test_link_labels_name_the_meeting_when_file_names_are_loose():
    # Tahoe Forest Hospital District: "2026-4-23-...pdf" and "Thursday-Nov-21-agendas.pdf" name no readable date,
    # but every link is labeled "Regular Meeting of the Board of Directors – 04/23/2026 – Agenda".
    url = agency("tfhd")["discovery"]["indexUrl"]
    rows = discover_configured("tfhd", agency("tfhd")["discovery"], limit=None, fetch=lambda u: TFHD_PAGE if u == url else 1 / 0)
    assert [(r["source_id"], r["title"]) for r in rows][:5] == [
        ("2026-10-22", "TFHD Board of Directors Meeting"),
        ("2026-09-24-special", "Special TFHD Board of Directors Meeting"),
        ("2026-09-24", "TFHD Board of Directors Meeting"),
        ("2026-08-27", "TFHD Board of Directors Meeting"),
        ("2026-07-23", "TFHD Board of Directors Meeting")]
    assert len(rows) == 14 and all(r["official_url"] == url for r in rows)
    april = next(r for r in rows if r["source_id"] == "2026-04-23")
    assert [(d["id"], d["url"].rsplit("/", 1)[1]) for d in april["documents"]] == [
        ("tfhd-agenda", "2026-4-23-Regular-Meeting-of-the-Board-of-Directors_FINAL-Agenda.pdf"),
        ("tfhd-agenda-packet", "2026-04-23-Regular-Meeting-of-the-Board-of-Directors_Agenda-Packet-rev.pdf")]
    names = [d["url"] for r in rows for d in r["documents"]]
    # Minutes, Spanish agendas, committees, the joint meeting with the Town, and the surgery center stay out.
    assert not any(re.search(r"Minutes|SPANISH|Committee|Joint|Surgery", n) for n in names)
    by_name = discover_configured("tfhd", {**agency("tfhd")["discovery"], "matchLabels": False}, limit=None,
                                  fetch=lambda u: TFHD_PAGE)
    assert by_name == []  # "2026 10 22 Regular Meeting ..." states no date the reader takes
    with pytest.raises(ValueError, match="matchLabels is true or false"):
        validate_discovery({"id": "x", "discovery": {"type": "file-links", "indexUrl": url, "matchLabels": "yes"}})


def events_fetch(requested):
    def fetch(url):
        requested.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        lo, hi = query["start_date"][0], query["end_date"][0]
        found = [e for e in EVENTS["events"] if lo <= e["start_date"][:10] <= hi]
        return json.dumps({"events": found, "total": len(found), "total_pages": 1})
    return fetch


@pytest.fixture
def november(monkeypatch):
    class Today(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return real.datetime(2026, 11, 9, 12, tzinfo=tz)
    monkeypatch.setattr(file_discovery, "datetime", Today)


def test_calendar_events_of_the_body_are_its_meetings(november):
    requested = []
    rows = discover_configured("ltva", agency("ltva")["discovery"], limit=5, fetch=events_fetch(requested))
    assert requested == ["https://ltva.org/wp-json/tribe/events/v1/events?start_date=2026-09-10&end_date=2027-03-09&per_page=50&page=1"]
    assert [(r["source_id"], r["date"], r["start_time"], r["status"]) for r in rows] == [
        ("6261", "2026-12-10", "15:00", "unknown"), ("6260", "2026-11-12", "15:00", "unknown"),
        ("6259", "2026-10-08", "15:00", "unknown"), ("6257", "2026-09-10", "15:00", "unknown")]
    november_meeting = rows[1]
    assert november_meeting["title"] == "LTVA Board of Directors Meeting"
    assert november_meeting["official_url"] == "https://ltva.org/event/lake-tahoe-visitors-authority-board-of-directors-32/"
    assert [d["id"] for d in november_meeting["documents"]] == ["ltva-agenda", "ltva-agenda-packet"]
    assert november_meeting["documents"][0]["url"] == "https://ltva.org/wp-content/uploads/2026/11/11_LTVA-Agenda-November-12-2026.pdf"
    assert november_meeting["location"] == "Tahoe Blue Event Center, 75 Hwy 50, Stateline"
    assert rows[0]["documents"] == []  # December: no agenda yet


def test_a_challenge_page_instead_of_json_is_named_in_the_error(november):
    page = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body></body></html>"
    with pytest.raises(ValueError, match=r"ltva\.org answered with non-JSON content: 'Just a moment\.\.\.'"):
        discover_configured("ltva", agency("ltva")["discovery"], limit=5, fetch=lambda url: page)


def test_a_canceled_event_is_a_canceled_meeting_and_the_committee_is_kept_apart():
    rows, earliest, by_date = discover_configured_range("ltva", agency("ltva")["discovery"], since=date(2026, 8, 1),
                                                        until=date(2026, 8, 31), fetch=events_fetch([]))
    assert by_date and [(r["date"], r["status"], r["title"]) for r in rows] == [
        ("2026-08-13", "canceled", "LTVA Board of Directors Meeting")]
    committee, _, _ = discover_configured_range("ltva-msc", agency("ltva-msc")["discovery"], since=date(2026, 1, 1),
                                                until=date(2026, 12, 31), fetch=events_fetch([]))
    assert [r["date"] for r in committee][:3] == ["2026-11-04", "2026-10-07", "2026-08-05"]
    assert all(r["title"] == "LTVA Marketing Services Committee Meeting" for r in committee)
    # The calendar's joint workshop, its other boards' meetings, and community events belong to neither body.
    every = rows + committee
    assert not any("Joint" in d["url"] for r in every for d in r["documents"])


@pytest.mark.parametrize("config, problem", [
    ({"type": "wordpress-media", "indexUrl": "https://x.org/wp-json/wp/v2/media", "noticeUrl": "http://x.org/"}, "noticeUrl must be https"),
    ({"type": "file-links", "indexUrl": "https://x.org/", "search": "agenda"}, "file-links does not read search"),
    ({"type": "tribe-events", "indexUrl": "https://x.org/", "noticeUrl": "https://x.org/"}, "tribe-events does not read noticeUrl"),
])
def test_the_new_types_validate_their_keys(config, problem):
    with pytest.raises(ValueError, match=problem):
        validate_discovery({"id": "x", "discovery": config})
