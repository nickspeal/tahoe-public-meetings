"""The `meeting-post-feed` discovery type, replayed on the State Lands Commission's saved feed and meetings page."""
import datetime as real
import re
from pathlib import Path

import pytest

from tahoe_ingest import source_discovery
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.source_discovery import discover_configured, validate_discovery

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/discovery"
FEED_URL = "https://www.slc.ca.gov/category/meeting/feed/"
MEETINGS_URL = "https://www.slc.ca.gov/meetings/"
FEED = (FIXTURES / "state-lands-meeting-feed.xml").read_text(encoding="utf-8")
MEETINGS = (FIXTURES / "state-lands-meetings.html").read_text(encoding="utf-8")


def setup(monkeypatch, today, feed=FEED):
    class Today(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return real.datetime(*today, 12, tzinfo=tz)
    monkeypatch.setattr(source_discovery, "datetime", Today)
    config = next(a for a in agency_catalog() if a["id"] == "state-lands")["discovery"]
    return config, {FEED_URL: feed, MEETINGS_URL: MEETINGS}


def test_posted_meetings_come_with_their_agenda_page_and_scheduled_start(monkeypatch):
    config, pages = setup(monkeypatch, (2026, 11, 2))
    requested = []

    def fetch(url):
        requested.append(url)
        return pages[url]
    rows = discover_configured("state-lands", config, limit=5, fetch=fetch)
    assert requested == [FEED_URL, MEETINGS_URL]  # the feed and the schedule; no meeting page is read
    assert [(r["source_id"], r["date"], r["title"], r["start_time"]) for r in rows] == [
        ("2026-10-21", "2026-10-21", "Commission Meeting", "13:00"),
        ("2026-08-25", "2026-08-25", "Commission Meeting", "13:00"),
        ("2026-07-28", "2026-07-28", "Commission Meeting", "10:00"),
        ("2026-06-23", "2026-06-23", "Commission Meeting", "12:00"),
        ("2026-04-07", "2026-04-07", "Commission Meeting", "14:00")]
    page = "https://www.slc.ca.gov/meeting/10-21-26-voting-record/"
    assert rows[0]["official_url"] == page
    assert rows[0]["documents"] == [{"id": "state-lands-agenda-html", "title": "Official agenda (HTML)", "url": page}]
    every = {r["source_id"]: r for r in discover_configured("state-lands", config, limit=None, fetch=fetch)}
    assert len(every) == 10
    assert every["2025-11-07-special"]["title"] == "Special Commission Meeting"
    assert every["2026-03-16"]["title"] == "Emergency Commission Meeting" and every["2026-03-16"]["start_time"] == "17:00"
    assert every["2025-12-16"]["start_time"] is None  # the 2026 schedule says nothing about 2025


def test_a_scheduled_meeting_gets_a_notice_until_its_agenda_is_posted(monkeypatch):
    unposted = re.sub(r"<item>\s*<title>October 21, 2026.*?</item>", "", FEED, count=1, flags=re.S)
    assert unposted != FEED
    config, pages = setup(monkeypatch, (2026, 10, 1), unposted)
    notice = discover_configured("state-lands", config, limit=3, fetch=pages.__getitem__)[0]
    assert (notice["source_id"], notice["title"], notice["start_time"], notice["official_url"], notice["documents"]) == (
        "2026-10-21", "Commission Meeting", "13:00", MEETINGS_URL, [])
    assert notice["warnings"] == []  # an empty document list is the state; the export says "no agenda yet"
    # A scheduled date that has passed without a post gets no notice.
    config, pages = setup(monkeypatch, (2026, 11, 2), unposted)
    found = discover_configured("state-lands", config, limit=None, fetch=pages.__getitem__)
    assert "2026-10-21" not in {r["source_id"] for r in found}


def test_posts_are_still_listed_when_the_schedule_page_fails(monkeypatch):
    config, pages = setup(monkeypatch, (2026, 11, 2))

    def fetch(url):
        if url == MEETINGS_URL:
            raise OSError("schedule unavailable")
        return pages[url]
    rows = discover_configured("state-lands", config, limit=2, fetch=fetch)
    assert [(r["source_id"], r["start_time"]) for r in rows] == [("2026-10-21", None), ("2026-08-25", None)]


def test_schedule_url_must_be_https():
    block = {"type": "meeting-post-feed", "indexUrl": FEED_URL, "scheduleUrl": "http://www.slc.ca.gov/meetings/"}
    with pytest.raises(ValueError, match="scheduleUrl"):
        validate_discovery({"id": "x", "discovery": block})
    with pytest.raises(ValueError, match="does not read"):
        validate_discovery({"id": "x", "discovery": {**block, "scheduleUrl": MEETINGS_URL, "idPrefix": "x"}})


def test_a_wordpress_rest_collection_of_posts_reads_like_a_feed():
    # The Olympic Valley PSD's "agenda" posts: a REST collection holds 100 posts where its RSS feed holds 10, and the
    # district has re-published 2023 agendas recently, so the feed alone would lose most of the year.
    posts = (FIXTURES / "ovpsd-agenda-posts-2026-11-13.json").read_text(encoding="utf-8")
    config = next(a for a in agency_catalog() if a["id"] == "ovpsd")["discovery"]
    requested = []

    def fetch(url):
        requested.append(url)
        return posts
    rows = discover_configured("ovpsd", config, limit=12, fetch=fetch)
    assert requested == [config["indexUrl"]]
    assert [(r["source_id"], r["title"]) for r in rows][:3] == [
        ("2026-10-27", "OVPSD Board of Directors Meeting"), ("2026-09-29", "OVPSD Board of Directors Meeting"),
        ("2026-08-25", "OVPSD Board of Directors Meeting")]
    assert sum(r["date"].startswith("2026") for r in rows) == 11
    special = next(r for r in rows if r["source_id"] == "2026-01-15-special")
    assert special["title"] == "Special OVPSD Board of Directors Meeting"  # "Special Board Meeting – Thursday, January 15, 2026"
    assert special["documents"] == [{"id": "ovpsd-agenda-html", "title": "Official agenda (HTML)", "url": special["official_url"]}]
    every = discover_configured("ovpsd", config, limit=None, fetch=fetch)
    assert not any("committee" in r["official_url"] for r in every)  # Finance Committee posts are left out
    assert min(r["date"] for r in every) < "2024-01-01"  # the re-published 2023 agendas are dated by their titles
