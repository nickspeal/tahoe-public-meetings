"""The `squarespace-blog` discovery type, replayed on the North Tahoe Fire Protection District's saved board posts.

The collection's JSON (``?format=json``) lists posts titled "Regular Meeting 10-2026" (a month,
not a meeting date), "Regular Meeting 11-2026 - Canceled", and in older years "Regular Meeting
10/27/2020"; each body links the agenda (or cancellation notice) and the approved minutes.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import blog_discovery
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.scheduled import UNDATED_INDEX_LIMIT, _index_limit
from tahoe_ingest.source_discovery import (UNDATED_TYPES, discover_configured, discover_configured_range,
                                           validate_discovery)

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/discovery"
FIRST = (FIXTURES / "north-tahoe-fire-board-minutes-2026-11-13.json").read_text(encoding="utf-8")
SECOND = (FIXTURES / "north-tahoe-fire-board-minutes-page-2-2026-11-13.json").read_text(encoding="utf-8")
INDEX = "https://www.ntfire.net/board-minutes"
FILES = "https://www.ntfire.net/s/"


def config():
    return next(a for a in agency_catalog() if a["id"] == "north-tahoe-fire")["discovery"]


def fetch(requested):
    def get(url):
        requested.append(url)
        if url == INDEX + "?format=json":
            return FIRST
        assert url == INDEX + "?format=json&offset=" + str(json.loads(FIRST)["pagination"]["nextPageOffset"])
        return SECOND
    return get


def test_posts_are_meetings_dated_later_from_their_agendas():
    requested = []
    rows = discover_configured("north-tahoe-fire", config(), limit=3, fetch=fetch(requested))
    assert requested == [INDEX + "?format=json"]  # one request: the JSON carries each post's body
    assert [(r["source_id"], r["date"], r["title"], r["status"]) for r in rows] == [
        ("6ae37ba3e082b1785e9ba0dc", None, "North Tahoe Fire Board of Directors Meeting", "canceled"),
        ("6ad94f679afb5f265256a341", None, "North Tahoe Fire Board of Directors Meeting", "unknown"),
        ("6aac24e224297719056fb9df", None, "North Tahoe Fire Board of Directors Meeting", "unknown")]
    canceled, october, september = rows
    # "Regular Meeting 10-2026" names a month; capture reads October 27 beside the agenda's start time.
    assert october["official_url"] == INDEX + "/regular-meeting-10-2024-eh4b6-h6edy-hnfwj-rljjp-eyhxn-h336c-66m9w-zpgg9-ndcd8-89ajj-k5nsj"
    assert october["documents"] == [{"id": "north-tahoe-fire-agenda", "title": "Agenda", "url": FILES + "AgendaOctober-2026LINKED.pdf"}]
    assert "minutes_url" not in october
    assert september["minutes_url"] == FILES + "3-MinutesSeptember-2026approved-20261027.pdf"
    assert canceled["documents"] == [{"id": "north-tahoe-fire-agenda", "title": "Notice of Cancelation",
                                      "url": FILES + "Notice-of-CancelationNovember-2026.pdf"}]


def test_scheduled_runs_bound_the_undated_posts_by_count():
    assert "squarespace-blog" in UNDATED_TYPES

    class Options:
        discovery_limit = 20
    assert _index_limit({"discovery": config()}, Options) == UNDATED_INDEX_LIMIT


def test_a_title_with_a_full_date_dates_the_meeting():
    post = {"id": "1", "title": "Special Meeting 7/23/2020", "fullUrl": "/board-minutes/special-meeting",
            "publishOn": 1595462400000, "body": '<a href="/s/Agenda-7-23-20.pdf">Agenda</a>'}
    row = blog_discovery._row("north-tahoe-fire", config(), post)
    assert (row["date"], row["title"]) == ("2020-07-23", "Special North Tahoe Fire Board of Directors Meeting")
    assert row["documents"][0]["url"] == FILES + "Agenda-7-23-20.pdf"


def test_a_backfill_pages_back_and_keeps_posts_published_in_its_range():
    requested = []
    rows, earliest, by_date = discover_configured_range("north-tahoe-fire", config(), since=date(2025, 3, 1),
                                                       until=date(2025, 6, 30), fetch=fetch(requested))
    assert len(requested) == 2  # the March post is on the second page, which also reaches posts older than the range
    assert [r["source_id"] for r in rows] == ["6853104204937f6feb247b51", "68265b543f6c160380e04dbb",
                                              "680135766ab82932eccd85af", "67dc51a29bdbc452bd3cba21"]  # June back to March
    assert all(r["date"] is None for r in rows)
    assert by_date is False and earliest < "2025-03-01"


def test_the_block_names_only_keys_the_type_reads():
    validate_discovery({"id": "x", "discovery": config()})
    with pytest.raises(ValueError, match="does not read"):
        validate_discovery({"id": "x", "discovery": {**config(), "idPrefix": "x-"}})
