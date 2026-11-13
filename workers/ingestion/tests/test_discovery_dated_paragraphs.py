"""The `dated-paragraphs` discovery type, replayed on the Truckee Fire Protection District's saved board meeting archive.

The page (Squarespace) prints one paragraph per meeting: "October 20, 2026 • Agenda • Minutes",
with stray empty links left in most paragraphs, a "(Special Session)" on the same day as a regular
meeting, and canceled meetings as a paragraph that links the cancellation notice.
"""
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.source_discovery import discover_configured, discover_configured_range, validate_discovery

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/discovery"
URL = "https://www.truckeefire.org/board-meeting-archive"
PAGE = (FIXTURES / "truckee-fire-board-meeting-archive-2026-11-13.html").read_text(encoding="utf-8")
FILES = "https://www.truckeefire.org/s/"


def config():
    return next(a for a in agency_catalog() if a["id"] == "truckee-fire")["discovery"]


def fetch(requested=None):
    def get(url):
        if requested is not None:
            requested.append(url)
        assert url == URL
        return PAGE
    return get


def test_each_dated_paragraph_is_a_meeting_with_its_agenda_and_minutes():
    requested = []
    rows = discover_configured("truckee-fire", config(), limit=4, fetch=fetch(requested))
    assert requested == [URL]  # the agenda files are documents, read by capture
    assert [(r["source_id"], r["date"], r["title"], r["status"]) for r in rows] == [
        ("2026-10-20", "2026-10-20", "Truckee Fire Board of Directors Meeting", "unknown"),
        ("2026-09-15", "2026-09-15", "Truckee Fire Board of Directors Meeting", "unknown"),
        ("2026-08-18", "2026-08-18", "Truckee Fire Board of Directors Meeting", "unknown"),
        ("2026-07-21", "2026-07-21", "Truckee Fire Board of Directors Meeting", "unknown")]
    october, september, _, july = rows
    # Only the link labeled "Agenda" is a document; the empty links to other meetings' packets are not.
    assert october["documents"] == [{"id": "truckee-fire-agenda-packet", "title": "Agenda",
                                     "url": FILES + "TFPD_-Regular-Meeting-Agenda-Packet-10-20-26.pdf"}]
    assert october["official_url"] == URL and october["start_time"] is None
    assert "minutes_url" not in october  # "• Minutes" is not linked until the board approves them
    assert september["minutes_url"] == FILES + "2A-TFPD_BOD_10-20-26_TFPD-Board-Minutes-9-15-26.pdf"
    assert september["minutes_status"] == "approved"
    assert july["documents"][0]["id"] == "truckee-fire-agenda"  # an agenda without the packet


def test_a_canceled_meeting_links_its_notice():
    rows = discover_configured("truckee-fire", config(), limit=None, fetch=fetch())
    canceled = {r["date"]: r for r in rows if r["status"] == "canceled"}
    assert sorted(canceled) == ["2026-02-17", "2026-05-19"]
    assert canceled["2026-05-19"]["official_url"] == FILES + "Notice-of-Cancellation-51926.pdf"
    assert canceled["2026-05-19"]["documents"] == []


def test_a_special_session_on_a_regular_meeting_day_keeps_its_own_id():
    rows = {r["source_id"]: r for r in discover_configured("truckee-fire", config(), limit=None, fetch=fetch())}
    assert rows["2025-11-18"]["title"] == "Truckee Fire Board of Directors Meeting"
    assert rows["2025-11-18-special"]["title"] == "Special Truckee Fire Board of Directors Meeting"
    assert rows["2025-11-18-special"]["documents"][0]["url"] == FILES + "11-18-2025-TFPD-Special-Meeting-Agenda.pdf"
    assert rows["2026-02-20-special"]["documents"][0]["url"] == FILES + "TFPD_Special-Meeting-Agenda-Packet-2-20-26.pdf"


def test_the_footer_and_year_links_are_not_meetings():
    rows = discover_configured("truckee-fire", config(), limit=None, fetch=fetch())
    urls = [d["url"] for r in rows for d in r["documents"]]
    assert not any("COC" in u for u in urls)  # the Measure T oversight committee's current agenda
    assert len(urls) == len(set(urls)) and len(rows) == 16  # 11 in 2026 (two canceled), 5 from September 2025


def test_a_backfill_keeps_meetings_inside_its_range():
    rows, earliest, by_date = discover_configured_range("truckee-fire", config(), since=date(2026, 1, 1),
                                                       until=date(2026, 3, 31), fetch=fetch())
    assert [r["source_id"] for r in rows] == ["2026-03-17", "2026-02-20-special", "2026-02-17", "2026-01-20"]
    assert earliest == "2025-09-16" and by_date is False


def test_the_block_names_only_keys_the_type_reads():
    validate_discovery({"id": "x", "discovery": {**config(), "documentPattern": "Agenda|Packet", "idPrefix": "x-"}})
    with pytest.raises(ValueError, match="does not read"):
        validate_discovery({"id": "x", "discovery": {**config(), "horizonDays": 30}})
