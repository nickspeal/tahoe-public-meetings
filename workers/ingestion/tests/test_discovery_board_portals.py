"""The `simbli` discovery type and the Simbli agenda tree, replayed on Lake Tahoe Unified's saved portal answers.

The listing page (`SB_MeetingListing.aspx`) is an empty grid filled by a POST of the
page's own listing request; the meeting page (`ViewMeeting.aspx`) is a script shell
whose agenda the portal serves as a tree (`GetAgendaTree`). Fixtures were fetched on
2026-11-09 and trimmed to what the readers use.
"""
import hashlib
import io
import json
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import board_portal_discovery as portal
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.schedules import forecast_meetings, rule_dates
from tahoe_ingest.source_discovery import discover_configured, discover_configured_range, validate_discovery

ROOT = Path(__file__).resolve().parents[3]
DISCOVERY = ROOT / "tests/fixtures/discovery"
AGENDAS = ROOT / "tests/fixtures/html-agendas"
LISTING_URL = "https://simbli.eboardsolutions.com/SB_Meetings/SB_MeetingListing.aspx?S=36030287"
API_URL = "https://simbli.eboardsolutions.com/Services/api/GetMeetingListing"
MEETING_URL = "https://simbli.eboardsolutions.com/SB_Meetings/ViewMeeting.aspx?S=36030287&MID=91500"
LISTING = (DISCOVERY / "simbli-ltusd-meeting-listing.html").read_text(encoding="utf-8")
ROWS = json.loads((DISCOVERY / "simbli-ltusd-meeting-listing.json").read_text(encoding="utf-8"))
MEETING_PAGE = (AGENDAS / "simbli-ltusd--2026-11-12.html").read_text(encoding="utf-8")
TREE_BYTES = (AGENDAS / "simbli-ltusd--2026-11-12.tree.json").read_bytes()
TREE = json.loads(TREE_BYTES)
REVIEWED = json.loads((AGENDAS / "simbli-ltusd--2026-11-12.json").read_text(encoding="utf-8"))


def ltusd():
    return next(a for a in agency_catalog() if a["id"] == "ltusd")


class Portal:
    """The listing page by GET and the listing API by POST, recording each request body."""

    def __init__(self, rows=ROWS):
        self.rows, self.requests = rows, []

    def fetch(self, url):
        assert url == LISTING_URL
        return LISTING

    def post(self, url, body):
        assert url == API_URL
        self.requests.append(body)
        start = body["RecordStart"] - 1
        return [{**r, "MeetingsCount": len(self.rows)} for r in self.rows[start:start + body["RecordCount"]]]


def test_registry_entry_reads_the_board_from_the_simbli_listing():
    agency = ltusd()
    assert agency["discovery"] == {"type": "simbli", "indexUrl": LISTING_URL, "bodyPattern": "^Board of Education\\b"}
    assert "agenda" not in agency  # items come from the portal's agenda tree, not a layout
    assert agency["staffReports"]["published"] is True and agency["staffReports"]["note"]


def test_listing_gives_board_meetings_newest_first_with_start_place_and_meeting_page(monkeypatch):
    site = Portal()
    monkeypatch.setattr(portal, "post_json", site.post)
    found = discover_configured("ltusd", ltusd()["discovery"], limit=3, fetch=site.fetch)
    assert [r["source_id"] for r in found] == ["91500", "90187", "86186"]  # the bond oversight committee is left out
    first = found[0]
    assert first["title"] == "Board of Education Regular Meeting"
    assert (first["date"], first["start_time"]) == ("2026-11-12", "17:00")
    assert first["official_url"] == MEETING_URL
    assert first["documents"] == [{"id": "ltusd-agenda-html", "title": "Official agenda (Simbli)", "url": MEETING_URL}]
    assert first["location"] == ("Lake Tahoe Unified School District, District Office Boardroom, "
                                 "1021 Al Tahoe Blvd., South Lake Tahoe, CA 96150")
    assert first["status"] == "unknown" and "minutes_url" not in first
    # The page's own request: its public site token, the site, the first page of 50 rows.
    request = site.requests[0]
    assert request["SecurityToken"].startswith("ZekKE44z") and request["SchoolID"] == "36030287"
    assert (request["RecordStart"], request["RecordCount"], request["ListingType"]) == (1, 50, "0")
    assert request["TimeZone"] == "-180" and request["ConnectionString"] and request["IsUserLoggedIn"] is False


def test_published_minutes_link_the_meeting_page_minutes_view_and_titles_keep_special_meetings(monkeypatch):
    site = Portal()
    monkeypatch.setattr(portal, "post_json", site.post)
    found = {r["source_id"]: r for r in discover_configured("ltusd", ltusd()["discovery"], limit=None, fetch=site.fetch)}
    assert len(found) == 17 and not any("Bond" in r["title"] for r in found.values())
    assert found["86200"]["minutes_url"] == "https://simbli.eboardsolutions.com/SB_Meetings/ViewMeeting.aspx?S=36030287&MID=86200&T=1"
    assert found["86200"]["minutes_status"] == "approved"
    assert found["63551"]["title"] == "Board of Education Special Meeting"
    assert found["57576"]["title"] == "Board of Education Joint Special Meeting"


def test_a_whole_listing_is_read_page_by_page_until_the_count(monkeypatch):
    rows = [{**ROWS[0], "Master_MeetingID": 1000 + n, "MM_DateTime": f"2025-{1 + n % 12:02}-{1 + n % 28:02}T17:00:00"}
            for n in range(120)]
    site = Portal(rows)
    monkeypatch.setattr(portal, "post_json", site.post)
    found = discover_configured("ltusd", ltusd()["discovery"], limit=None, fetch=site.fetch)
    assert [r["RecordStart"] for r in site.requests] == [1, 51, 101]
    assert len(found) == 120


def test_backfill_range_keeps_the_listed_meetings_in_range(monkeypatch):
    site = Portal()
    monkeypatch.setattr(portal, "post_json", site.post)
    found, earliest, by_date = discover_configured_range("ltusd", ltusd()["discovery"], since=date(2026, 9, 1),
                                                         until=date(2026, 10, 31), fetch=site.fetch)
    assert [r["source_id"] for r in found] == ["90187", "86186", "86200", "82096"]
    assert by_date is False


def test_listing_page_without_its_request_is_an_error():
    with pytest.raises(ValueError, match="no meeting listing request"):
        portal.simbli("ltusd", ltusd()["discovery"], 3, lambda url: "<html><body>Access denied</body></html>",
                      post=lambda *a: [])


def test_a_simbli_block_takes_only_title():
    agency = {"id": "x", "discovery": {"type": "simbli", "indexUrl": LISTING_URL, "title": "Board Meeting"}}
    validate_discovery(agency)
    agency["discovery"]["idPrefix"] = "x-"
    with pytest.raises(ValueError, match="simbli does not read idPrefix"):
        validate_discovery(agency)


def test_agenda_tree_gives_every_numbered_part_and_lettered_item_with_exact_quotes():
    text, items, complete = portal.agenda_from_tree(TREE)
    assert complete and len(items) == 44
    assert [[i["source_number"], i["section"], i["title"]] for i in items] == REVIEWED["expected"]
    lines = text.split("\n")
    assert lines[:3] == ["1. CALL TO ORDER - 5:00 PM", "a. Pledge of Allegiance", "b. Roll Call"]
    for order, item in enumerate(items, 1):
        assert item["order"] == order
        assert item["source_pages"][0]["quote"] in lines and item["source_pages"][0]["page_number"] == 1
    assert "Revison to Board Policy 3350 Travel Expenses" in text  # runs of spaces in a title are collapsed


def test_unlabeled_or_misnumbered_trees_keep_their_text_without_items():
    unlabeled = [dict(TREE[0], Title="CALL TO ORDER")] + TREE[1:]
    text, items, complete = portal.agenda_from_tree(unlabeled)
    assert text.startswith("CALL TO ORDER\n") and items == [] and not complete
    skipped = [e for e in TREE if not e["Title"].startswith("2. ")]
    assert portal.agenda_from_tree(skipped)[1:] == ([], False)
    deleted = [dict(e, Deleted=e["Title"].startswith("16. ")) for e in TREE]
    _, items, complete = portal.agenda_from_tree(deleted)
    assert complete and items[-1]["source_number"] == "15"


def test_capture_reads_the_meeting_page_and_then_its_agenda_tree(monkeypatch):
    from tahoe_ingest import http, source
    requested = []

    class Response(io.BytesIO):
        def __init__(self, url, body, content_type):
            super().__init__(body)
            self.url, self.headers = url, {"Content-Type": content_type}

        def geturl(self):
            return self.url

    def urlopen(request, timeout=None):
        url = request.full_url
        requested.append(url)
        if url == MEETING_URL:
            return Response(url, MEETING_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        return Response(url, TREE_BYTES, "application/json; charset=utf-8")
    monkeypatch.setattr(http.urllib.request, "urlopen", urlopen)
    fetched = source.download_extract(MEETING_URL)
    assert requested[0] == MEETING_URL
    assert requested[1].startswith("https://simbli.eboardsolutions.com/Services/api/GetAgendaTree/?sct=ZekKE44z")
    assert "&endid=Lgtp60JeGP4jOSVFplusZv9qQ==&enmid=7Gb3Oq44rnBPqWEGA4hZog==&enuid=&v=" in requested[1]
    assert fetched["media_type"] == "text/html" and fetched["items_complete"] and len(fetched["agenda_items"]) == 44
    assert fetched["sha256"] == hashlib.sha256(TREE_BYTES).hexdigest()  # the tree's hash, not the page's per-request tokens
    assert fetched["pages"] == [{"page_number": 1, "text": portal.agenda_from_tree(TREE)[0]}]


def test_other_pages_are_not_portal_agendas_and_a_bare_simbli_page_is_an_error():
    assert portal.portal_agenda("https://example.org/SB_Meetings/ViewMeeting.aspx?S=1&MID=2", MEETING_PAGE) is None
    assert portal.portal_agenda(LISTING_URL, LISTING) is None
    with pytest.raises(ValueError, match="does not name its agenda"):
        portal.portal_agenda(MEETING_URL, "<html><title>Just a moment</title></html>", fetch_json=lambda url: b"[]")
    with pytest.raises(ValueError, match="no published agenda items"):
        portal.portal_agenda(MEETING_URL, MEETING_PAGE, fetch_json=lambda url: b"[]")


def test_schedule_follows_the_2026_calendar_and_skips_its_holiday_thursdays():
    rules = ltusd()["schedule"]["rules"]
    dates = sorted(d for rule in rules for d in rule_dates(rule, date(2026, 1, 1), date(2026, 12, 31)))
    published = [date(2026, m, d) for m, d in [(1, 22), (2, 12), (2, 26), (3, 12), (3, 26), (4, 23), (5, 14), (5, 28),
                                               (6, 11), (6, 25), (7, 23), (8, 13), (8, 27), (9, 10), (9, 24), (10, 8),
                                               (10, 22), (11, 12), (12, 10)]]
    assert dates == published
    expected = forecast_meetings("ltusd", ltusd()["schedule"], [date(2026, 11, 12)], today=date(2026, 11, 9),
                                 horizon_days=90, official_url=LISTING_URL, generated_at="2026-11-09T00:00:00Z")
    # Thanksgiving (November 26) and December 24 are not forecast; no time, since the listed start varies.
    assert [(e["date"], e["startsAt"]) for e in expected] == [("2026-12-10", None), ("2027-01-28", None)]
