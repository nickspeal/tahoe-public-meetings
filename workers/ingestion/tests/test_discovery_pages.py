"""The `dated-page-links` and `dated-sections` discovery types, replayed on saved official pages.

`dated-page-links`: the California Tahoe Conservancy's board meetings page, whose
"Conservancy Meetings 2026" list links each meeting's own agenda page and names the
next meeting before its page exists. `dated-sections`: the California State Parks
commission pages (OHMVR Commission and State Park and Recreation Commission), one
accordion panel per meeting with "Date:", "Time:", and "Location:" lines.
"""
import datetime as real
from pathlib import Path

import pytest

from tahoe_ingest import page_discovery
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.source_discovery import discover_configured, discover_configured_range, validate_discovery

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/discovery"
CTC_URL = "https://tahoe.ca.gov/board-meetings-agendas/"
OHMVR_URL = "https://ohv.parks.ca.gov/?page_id=27160"
SPRC_URL = "https://www.parks.ca.gov/?page_id=31042"
PAGES = {CTC_URL: (FIXTURES / "ctc-board-meetings-agendas.html").read_text(encoding="utf-8"),
         OHMVR_URL: (FIXTURES / "ohmvr-commission-meetings.html").read_text(encoding="utf-8"),
         SPRC_URL: (FIXTURES / "sprc-commission-meetings.html").read_text(encoding="utf-8")}


def config(agency):
    return next(a for a in agency_catalog() if a["id"] == agency)["discovery"]


def today(monkeypatch, *day):
    class Today(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return real.datetime(*day, 12, tzinfo=tz)
    monkeypatch.setattr(page_discovery, "datetime", Today)


def test_dated_links_open_agenda_pages_and_an_unlinked_date_is_a_notice(monkeypatch):
    today(monkeypatch, 2026, 11, 3)
    requested = []

    def fetch(url):
        requested.append(url)
        return PAGES[url]
    rows = discover_configured("ctc", config("ctc"), limit=5, fetch=fetch)
    assert requested == [CTC_URL]  # meeting pages are documents, read by capture
    assert [(r["source_id"], r["title"], r["official_url"]) for r in rows] == [
        ("2026-12-10", "California Tahoe Conservancy Board Meeting", CTC_URL),
        ("2026-09-17", "California Tahoe Conservancy Board Meeting",
         "https://tahoe.ca.gov/california-tahoe-conservancy-board-meeting-september-17-2026/"),
        ("2026-06-18", "California Tahoe Conservancy Board Meeting",
         "https://tahoe.ca.gov/california-tahoe-conservancy-board-meeting-june-18-2026/"),
        ("2026-03-19", "California Tahoe Conservancy Board Meeting",
         "https://tahoe.ca.gov/california-tahoe-conservancy-board-meeting-march-19-20-2026/")]
    notice, september, _, march = rows
    assert notice["documents"] == [] and notice["warnings"] == []  # no agenda yet: the export says so
    assert september["documents"] == [{"id": "ctc-agenda-html", "title": "Official agenda (HTML)",
                                       "url": september["official_url"]}]
    assert september["start_time"] is None  # read from the agenda heading at export
    assert march["warnings"] == ["Meets March 19–20, 2026."]
    # The archive link and the procedures PDF on the same page are not meetings.
    assert all(r["date"].startswith("2026") for r in rows)


def test_an_unlinked_date_that_has_passed_is_not_a_meeting(monkeypatch):
    today(monkeypatch, 2026, 12, 11)
    rows = discover_configured("ctc", config("ctc"), limit=None, fetch=PAGES.__getitem__)
    assert "2026-12-10" not in {r["source_id"] for r in rows}


def test_a_list_under_another_heading_is_ignored(monkeypatch):
    today(monkeypatch, 2026, 11, 3)
    block = {**config("ctc"), "headingPattern": "^Board Meeting Materials$"}
    assert discover_configured("ctc", block, limit=None, fetch=PAGES.__getitem__) == []


def test_sections_give_each_commission_meeting_its_agenda_time_and_place():
    rows = discover_configured("california-state-parks", config("california-state-parks"), limit=None, fetch=PAGES.__getitem__)
    assert [(r["source_id"], r["date"], r["start_time"], r["title"]) for r in rows] == [
        ("ohmvr-2026-08-06", "2026-08-06", "09:00", "California Off-Highway Motor Vehicle Recreation Commission Meeting"),
        ("ohmvr-2026-06-17", "2026-06-17", "09:00", "California Off-Highway Motor Vehicle Recreation Commission Meeting"),
        ("sprc-2026-03-05", "2026-03-05", "09:00", "California State Park and Recreation Commission Meeting"),
        ("ohmvr-2026-02-26", "2026-02-26", "09:00", "California Off-Highway Motor Vehicle Recreation Commission Meeting")]
    by = {r["source_id"]: r for r in rows}
    # The meeting's own date, not the tour's (August 7, after it; March 4, before it) or the Safety Summit's (June 16).
    august = by["ohmvr-2026-08-06"]
    assert august["official_url"] == OHMVR_URL  # staff reports are listed on the page beside the agenda
    assert august["location"] == "North Lake Tahoe Event Center, 8318 North Lake Blvd. Kings Beach, CA 96143"
    assert august["documents"] == [{"id": "ohmvr-agenda-2026-08-06", "title": "Meeting Agenda",
                                    "url": "https://ohv.parks.ca.gov/pages/1140/files/OHVC%20Meeting%20Agenda%200826Final.pdf"}]
    # Two agendas in one panel: the commission's, not the Safety Summit's.
    assert [d["url"] for d in by["ohmvr-2026-06-17"]["documents"]] == [
        "https://ohv.parks.ca.gov/pages/1140/files/02-OHVC-Meeting-Agenda-20260617.pdf"]
    # A map pin in the location line is dropped.
    assert by["ohmvr-2026-02-26"]["location"] == "Canyon Country Community Center, 18410 Sierra Hwy, Santa Clarita, CA 91351"
    # The second page (`pages`) keeps its own title, ID prefix, and agenda link; the tour agenda is not the meeting's.
    sprc = by["sprc-2026-03-05"]
    assert (sprc["official_url"], sprc["documents"]) == (SPRC_URL, [
        {"id": "sprc-agenda-2026-03-05", "title": "Meeting Agenda",
         "url": "https://www.parks.ca.gov/pages/29247/files/SPRC%20March%20Meeting%20Agenda.pdf"}])
    # The November panel has no date yet, so it is not a meeting.
    assert all(r["date"] < "2026-11-01" for r in rows)


def test_the_backfill_reads_the_same_pages():
    found, earliest, by_date = discover_configured_range(
        "california-state-parks", config("california-state-parks"), since=real.date(2026, 3, 1), until=real.date(2026, 7, 1),
        fetch=PAGES.__getitem__)
    assert [r["source_id"] for r in found] == ["ohmvr-2026-06-17", "sprc-2026-03-05"]
    assert (earliest, by_date) == ("2026-02-26", False)


@pytest.mark.parametrize("pages", [[], [{"indexUrl": "http://www.parks.ca.gov/"}], [{"indexUrl": SPRC_URL, "scheduleUrl": SPRC_URL}], "x"])
def test_pages_are_validated(pages):
    with pytest.raises(ValueError):
        validate_discovery({"id": "x", "discovery": {"type": "dated-sections", "indexUrl": OHMVR_URL, "pages": pages}})


def test_pages_belong_to_dated_sections_only():
    with pytest.raises(ValueError, match="does not read pages"):
        validate_discovery({"id": "x", "discovery": {"type": "dated-page-links", "indexUrl": CTC_URL, "pages": [{"indexUrl": SPRC_URL}]}})
