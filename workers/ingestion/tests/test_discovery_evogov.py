"""The `evogov-meetings` discovery type, replayed on the Northstar Community Services District's saved responses.

The district's EvoGov meeting dashboard lists one calendar's year headings; each year's
meetings come from the site's ``/meetings/year_events/`` endpoint as table rows (saved for
2026 on November 13, 2026). Older agendas are on EvoGov's S3 bucket, newer ones under
``/meetingfiles/`` on the district's own host.
"""
import datetime as real
import json
import urllib.parse
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import file_discovery
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.source_discovery import discover_configured, discover_configured_range, validate_discovery

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/discovery"
DASHBOARD = (FIXTURES / "northstar-csd-meetingdashboard-2026-11-13.html").read_text()
YEAR_2026 = (FIXTURES / "northstar-csd-year-events-2026.json").read_text()
CONFIG = next(a for a in agency_catalog() if a["id"] == "northstar-csd")["discovery"]


def dashboard_fetch(requested):
    def fetch(url):
        requested.append(url)
        if url == CONFIG["indexUrl"]:
            return DASHBOARD
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert url.startswith("https://www.northstarcsd.org/meetings/year_events/?") and query["calendar_id"] == ["508"]
        return YEAR_2026 if query["year"] == ["2026"] else json.dumps({"html": ""})
    return fetch


@pytest.fixture
def november(monkeypatch):
    class Today(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return real.datetime(2026, 11, 13, 12, tzinfo=tz)
    monkeypatch.setattr(file_discovery, "datetime", Today)


def test_the_dashboard_lists_its_calendar_years():
    assert file_discovery.evogov_calendars(DASHBOARD) == {"508": list(range(2026, 2017, -1))}
    with pytest.raises(ValueError, match="lists no calendar years"):
        file_discovery.evogov_calendars("<html><body>Meeting Dashboard</body></html>")


def test_scheduled_discovery_reads_the_years_in_its_window(november):
    requested = []
    rows = discover_configured("northstar-csd", CONFIG, limit=20, fetch=dashboard_fetch(requested))
    # 60 days back to 120 ahead of November 13, 2026 reaches into 2027, which the dashboard does not list yet.
    assert requested == [CONFIG["indexUrl"], "https://www.northstarcsd.org/meetings/year_events/?calendar_id=508&year=2026"]
    assert [(r["source_id"], r["date"], r["start_time"], r["title"], len(r["documents"])) for r in rows] == [
        ("98336", "2026-12-16", "09:00", "December Board Meeting", 0),
        ("98335", "2026-11-18", "09:00", "November Board Meeting", 0),
        ("98334", "2026-10-21", "09:00", "October Board Meeting", 2),
        ("98333", "2026-09-16", "09:00", "September Board Meeting", 2)]
    october = rows[2]
    assert october["official_url"] == "https://www.northstarcsd.org/events/98334/"
    assert october["documents"] == [
        {"id": "northstar-csd-agenda", "title": "10_21_2026_Regular_Board_Meeting_Agenda.pdf",
         "url": "https://www.northstarcsd.org/meetingfiles/98334/agendas/c23d7dbc1c9e42ae9b85cf38ebfe01cd.pdf"},
        {"id": "northstar-csd-agenda-packet", "title": "Complete Board Packet 10-21-2026.pdf",
         "url": "https://www.northstarcsd.org/meetingfiles/98334/attachments/e43bf6c8365645618b4bfd80f180ecd9.pdf"}]


def test_a_year_of_meetings_keeps_specials_and_workshops_and_leaves_out_other_events():
    rows, earliest, by_date = discover_configured_range("northstar-csd", CONFIG, since=date(2026, 1, 1),
                                                        until=date(2026, 6, 30), fetch=dashboard_fetch([]))
    assert by_date and earliest == "2026-01-21"
    assert [(r["date"], r["start_time"], r["status"], r["title"]) for r in rows] == [
        ("2026-06-17", "09:00", "unknown", "June Board Meeting"),
        ("2026-06-05", "09:00", "unknown", "Strategic Plan Workshop-Retreat"),
        ("2026-05-29", "15:30", "unknown", "NCSD May Special Meeting"),
        ("2026-05-20", "09:00", "unknown", "May Board Meeting"),
        ("2026-04-15", "09:00", "unknown", "April Board Meeting"),
        ("2026-03-25", "09:00", "unknown", "March Special Meeting"),
        ("2026-03-18", "09:00", "canceled", "March Board Meeting"),
        ("2026-02-26", "09:00", "unknown", "February Special Meeting of the Board"),
        ("2026-02-18", "09:00", "canceled", "February Board Meeting"),
        ("2026-01-21", "09:00", "unknown", "January Board Meeting")]
    # The sort key is UTC: 17:00 in January and 16:00 in June are both 9:00 a.m. in Truckee.
    may = next(r for r in rows if r["date"] == "2026-05-20")
    # Labels drop the file size the listing prints ("(160 KB)").
    assert [d["title"] for d in may["documents"]] == ["05_20_2026_Regular_Board_Meeting_Agenda.pdf",
                                                      "Complete Board Packet 05-20-2026.pdf"]
    workshop = next(r for r in rows if r["date"] == "2026-06-05")
    assert [d["id"] for d in workshop["documents"]] == ["northstar-csd-agenda"]


def test_files_are_read_by_their_heading():
    row = ('<tr><td data-evo_sort_key="20261118170000">Wed, Nov 18 9:00 AM</td>'
           '<td><a class="evo_meeting_name_link" href="/events/98335/">November Board Meeting</a></td>'
           '<td><div class="event_details_heading">Agendas</div><ul><li><a href="/meetingfiles/98335/agendas/a.pdf">Agenda.pdf (1 MB)</a></li></ul>'
           '<div class="event_details_heading">Attachments</div><ul><li><a href="/meetingfiles/98335/attachments/b.pdf">Fire Engine Quote.pdf</a></li>'
           '<li><a href="/meetingfiles/98335/attachments/c.pdf">Complete Board Packet 11-18-2026.pdf</a></li></ul>'
           '<div class="event_details_heading">Minutes</div><ul><li><a href="/meetingfiles/98335/minutes/d.pdf">Minutes.pdf</a></li></ul></td></tr>')
    [meeting] = file_discovery.evogov_rows("northstar-csd", CONFIG, row, "https://www.northstarcsd.org/meetingdashboard")
    assert [(d["id"], d["url"].rsplit("/", 1)[1]) for d in meeting["documents"]] == [
        ("northstar-csd-agenda", "a.pdf"), ("northstar-csd-agenda-packet", "c.pdf")]
    assert meeting["minutes_url"] == "https://www.northstarcsd.org/meetingfiles/98335/minutes/d.pdf"


def test_the_type_reads_only_its_keys():
    with pytest.raises(ValueError, match="evogov-meetings does not read horizonDays"):
        validate_discovery({"id": "x", "discovery": {"type": "evogov-meetings", "indexUrl": "https://x.org/meetingdashboard",
                                                     "horizonDays": 30}})
