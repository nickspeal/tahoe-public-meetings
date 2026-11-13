"""Scheduled CivicClerk discovery, replayed on the Tahoe-Truckee Sanitation Agency's saved event list.

The agency's portal lists a year of placeholder board meetings and office holidays ahead, so an
unwindowed list (newest first) would start in 2027. Scheduled discovery asks for the window of
60 days back to 120 ahead and pages through it; a recording hosted on YouTube is linked directly.
"""
import json
import re
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import source_discovery
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.source_discovery import discover_configured

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/discovery"
EVENTS = json.loads((FIXTURES / "ttsa-civicclerk-events-2026-11-13.json").read_text())
CONFIG = next(a for a in agency_catalog() if a["id"] == "ttsa")["discovery"]


@pytest.fixture
def november(monkeypatch):
    monkeypatch.setattr(source_discovery, "_local_today", lambda: date(2026, 11, 13))


def portal_fetch(requested):
    def fetch(url):
        requested.append(url)
        if url == CONFIG["indexUrl"]:
            return "<!doctype html><html><body><div id='root'></div></body></html>"  # the portal is a script shell
        skip = int(re.search(r"skip=(\d+),", url)[1])
        return json.dumps(EVENTS["pages"][skip // 15])
    return fetch


def test_scheduled_discovery_pages_through_the_window(november):
    requested = []
    rows = discover_configured("ttsa", CONFIG, limit=20, fetch=portal_fetch(requested))
    api = "https://ttsa.api.civicclerk.com/v1/Events/GetEventsList(fromDate='2026-09-14',toDate='2027-03-13',categoryIds=[],"
    assert requested[1:] == [api + "skip=0,sortDescending=true,cursorId=0)", api + "skip=15,sortDescending=true,cursorId=0)"]
    # The Finance Committee and the office holidays share the calendar; only board meetings qualify.
    assert [(r["source_id"], r["date"], r["start_time"], r["title"]) for r in rows] == [
        ("290", "2027-02-17", "09:00", "Board of Directors Meeting"),
        ("289", "2027-01-20", "09:00", "Board of Directors Meeting"),
        ("288", "2026-12-16", "09:00", "Board of Directors Meeting"),
        ("287", "2026-11-18", "09:00", "Board of Directors Meeting"),
        ("286", "2026-10-21", "09:00", "Board of Directors Meeting"),
        ("1300", "2026-09-24", "09:00", "Special Board of Directors Meeting - Board Workshop 09/24/26"),
        ("285", "2026-09-16", "09:00", "Board of Directors Meeting")]
    november_meeting = rows[3]
    assert [(d["id"], d["title"]) for d in november_meeting["documents"]] == [
        ("ttsa-agenda", "11-18-26 Agenda"), ("ttsa-agenda-packet", "11-18-26 Board Packet")]
    assert november_meeting["official_url"] == "https://ttsa.portal.civicclerk.com/event/287/overview"


def test_a_youtube_recording_is_linked_directly(november):
    rows = {r["source_id"]: r for r in discover_configured("ttsa", CONFIG, limit=20, fetch=portal_fetch([]))}
    assert rows["286"]["recording_url"] == "https://www.youtube.com/watch?v=w2-ydb1D1tw"
    assert "recording_url" not in rows["287"]  # not held yet


def test_portal_media_page_without_an_external_recording():
    data = {"items": [{"id": 745, "eventName": "Board of Trustees Meeting", "eventDate": "2026-09-09T16:00:00Z",
                       "hasMedia": True, "externalMediaUrl": None, "publishedFiles": []}]}
    config = {"type": "civicclerk", "indexUrl": "https://ivgid.portal.civicclerk.com/", "bodyPattern": "Trustees"}
    [row] = discover_configured("ivgid", config, fetch=lambda url: json.dumps(data))
    assert row["recording_url"] == "https://ivgid.portal.civicclerk.com/event/745/media"
