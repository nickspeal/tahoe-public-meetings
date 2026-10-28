"""The history backfill and the date-range adapter variants, with every network call mocked."""
import contextlib
import hashlib
import json
import os
import uuid
from datetime import date

import psycopg
import pytest

from tahoe_ingest import backfill, cli, discovery, scheduled, source_discovery

from test_scheduled import ROOT, Hook  # noqa: F401  (shared helpers)

SINCE, UNTIL = date(2026, 7, 1), date(2026, 12, 31)
AGENDA = "https://example.gov/planning/{}.pdf"
AGENDA_TEXT = "PLANNING COMMISSION AGENDA\n1. Call to order\n2. Housing element workshop"


@pytest.fixture
def db():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_backfill_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with psycopg.connect(url, options=f"-c search_path={schema}") as connection:
            connection.row_factory = psycopg.rows.dict_row
            yield connection
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def fake_fetch(url):
    if not url.startswith("https://example.gov/planning/") or url.endswith("broken.pdf"):
        raise ValueError("offline test: source unavailable")
    return {"sha256": hashlib.sha256(url.encode()).hexdigest(), "bytes": 1000, "page_count": 1, "warnings": [],
            "media_type": "application/pdf", "pages": [{"page_number": 1, "text": AGENDA_TEXT}]}


def meeting(sid, day, doc=None):
    return {"agency": "slt-planning", "source_id": sid, "title": "Planning Commission", "date": day,
            "official_url": f"https://example.gov/planning/{sid}", "warnings": [],
            "documents": [{"id": "slt-planning-agenda", "title": "Agenda", "url": AGENDA.format(doc or sid)}]}


def fake_discover(agency_id, config, *, since, until):
    assert (since, until) == (SINCE, UNTIL)
    if agency_id == "tcpud":
        raise RuntimeError("index down")
    if agency_id != "slt-planning":
        return {"candidates": [], "earliestListed": None, "queryByDate": False}
    return {"candidates": [meeting("7001", "2026-07-09"), meeting("7002", "2026-08-13"),
                           meeting("7003", "2026-09-10", doc="broken")],
            "earliestListed": "2015-03-12", "queryByDate": False}


def options(**overrides):
    return backfill.Options(**{"since": SINCE, "until": UNTIL, "root": ROOT, "delay": 0, "forecast_days": 0,
                               "lock_wait_seconds": 0, **overrides})


def test_backfill_captures_range_publishes_and_resumes(db, monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("VERCEL_DEPLOY_HOOK_URL", "https://hooks.example/secret-token")
    hook = Hook()
    summary = backfill.backfill(db, options(out=tmp_path / "public.json"), discover=fake_discover, fetch=fake_fetch, post=hook)
    json.dumps(summary)
    steps = {s["name"]: s for s in summary["steps"]}
    assert list(steps) == ["init-db", "backfill-discover", "backfill-capture", "quarantine", "reparse", "reclassify",
                           "meeting-records", "draft-minutes", "classify-topics", "export", "validate", "store", "deploy-hook"]
    assert summary["status"] == "degraded" and summary["exportId"] and summary["message"] == "backfill degraded"
    planning = summary["byAgency"]["slt-planning"]
    assert planning["found"] == 3 and planning["toCapture"] == 3 and planning["captured"] == 3
    assert planning["documentFailures"] == 1  # the notice is kept; its document is retried next time
    assert planning["earliestListed"] == "2015-03-12" and planning["reachesSince"] is True
    assert planning["earliestInRange"] == "2026-07-09"
    assert "index down" in summary["byAgency"]["tcpud"]["error"]
    assert summary["totals"]["agenciesWithErrors"] == ["tcpud"]
    assert summary["downloads"] == {"documents": 2, "bytes": 2000}
    assert summary["database"]["bytesAfter"] >= summary["database"]["bytesBefore"] > 0
    assert summary["database"]["storedAfter"]["meetings"] - summary["database"]["storedBefore"]["meetings"] == 3
    assert summary["exportBytes"] > 0 and set(summary["phaseSeconds"]) >= {"backfill-discover", "backfill-capture", "export"}
    assert hook.calls == ["https://hooks.example/secret-token"] and "secret-token" not in json.dumps(summary)
    assert db.execute("SELECT count(*) AS n FROM public_exports").fetchone()["n"] == 1

    fetched = []
    again = backfill.backfill(db, options(), discover=fake_discover, fetch=lambda u: fetched.append(u) or fake_fetch(u), post=hook)
    planning = again["byAgency"]["slt-planning"]
    # Stored meetings with documents are skipped; only the one whose document failed is retried.
    assert planning["alreadyStored"] == 2 and planning["toCapture"] == 1
    assert fetched == [AGENDA.format("broken")]
    assert again["database"]["storedAfter"]["meetings"] == summary["database"]["storedAfter"]["meetings"]


def test_dry_run_discovers_without_downloading_or_publishing(db):
    fetched, hook = [], Hook()
    summary = backfill.backfill(db, options(dry_run=True), discover=fake_discover,
                                fetch=lambda u: fetched.append(u), post=hook)
    assert [s["name"] for s in summary["steps"]] == ["init-db", "backfill-discover"]
    assert summary["byAgency"]["slt-planning"]["toCapture"] == 3 and summary["window"]["dryRun"] is True
    assert fetched == [] and hook.calls == []
    assert db.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"] == 0


def test_agency_selection(db):
    summary = backfill.backfill(db, options(agencies=["slt-planning"], dry_run=True), discover=fake_discover, fetch=fake_fetch)
    assert list(summary["byAgency"]) == ["slt-planning"]
    with pytest.raises(ValueError, match="not a followed agency"):
        backfill.backfill(db, options(agencies=["nowhere"]), discover=fake_discover, fetch=fake_fetch)
    with pytest.raises(ValueError):
        options(since=date(2026, 8, 1), until=date(2026, 7, 1))


def test_backfill_waits_for_or_skips_a_running_scheduled_run(db):
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    with psycopg.connect(url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (scheduled.RUN_LOCK,))
        summary = backfill.backfill(db, options(), discover=fake_discover, fetch=fake_fetch)
        assert summary["status"] == "skipped" and summary["steps"] == []

        waits = []

        def release(seconds):  # The scheduled run finishes while the backfill waits.
            waits.append(seconds)
            other.execute("SELECT pg_advisory_unlock(%s)", (scheduled.RUN_LOCK,))
        summary = backfill.backfill(db, options(lock_wait_seconds=600, dry_run=True, agencies=["slt-planning"]),
                                    discover=fake_discover,
                                    fetch=fake_fetch, sleep=release)
        assert waits == [backfill.LOCK_POLL_SECONDS] and summary["status"] == "ok"
    # The backfill released the lock, so a scheduled run can take it.
    assert db.execute("SELECT pg_try_advisory_lock(%s) AS ok", (scheduled.RUN_LOCK,)).fetchone()["ok"]
    db.execute("SELECT pg_advisory_unlock(%s)", (scheduled.RUN_LOCK,))


def test_cli_validates_and_prints_summary(db, monkeypatch, capsys):
    monkeypatch.setattr(cli, "connect", lambda _: contextlib.nullcontext(db))
    with pytest.raises(SystemExit):
        cli.main(["--database-url", "unused", "backfill", "--since", "2026-07-01", "--delay", "0.5"])
    with pytest.raises(SystemExit):
        cli.main(["--database-url", "unused", "backfill", "--since", "July"])
    monkeypatch.setattr(backfill, "discover_agency_range", fake_discover)
    seen = {}

    def run(conn, opts, **kwargs):
        seen.update(since=opts.since, until=opts.until, agencies=opts.agencies, dry=opts.dry_run, delay=opts.delay)
        return {"status": "ok", "steps": []}
    monkeypatch.setattr(backfill, "backfill", run)
    assert cli.main(["--database-url", "unused", "backfill", "--since", "2026-07-01", "--until", "2026-12-31",
                     "--agency", "slt", "--agency", "ttd", "--dry-run"]) == 0
    assert seen == {"since": SINCE, "until": UNTIL, "agencies": ["slt", "ttd"], "dry": True, "delay": 1.0}
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


# ---------------------------------------------------------------- date-range discovery types

class Pages:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, *args, **kwargs):
        self.calls.append(url)
        for key, value in self.pages.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected fetch {url}")


def test_wordpress_range_pages_until_posts_predate_since():
    def post(pid, title, posted):
        return {"id": pid, "link": f"https://tahoetransportation.org/meeting/{pid}/", "title": {"rendered": title},
                "date": posted + "T10:00:00", "modified": posted + "T10:00:00"}
    page1 = [post(1, "TTD Board of Directors Meeting &#8211; November 4, 2026", "2026-10-13"),
             post(2, "TTD Finance and Personnel Committee Meeting &#8211; November 4, 2026", "2026-10-13"),
             post(3, "TTD Board of Directors Meeting", "2026-10-05")]
    page1 += [post(100 + n, f"TTD Board of Directors Meeting &#8211; March {n % 28 + 1}, 2026", "2026-03-01") for n in range(97)]
    page2 = [post(4, "TTD Board of Directors Meeting &#8211; August 5, 2026", "2025-12-18"),
             post(5, "TTD Board of Directors Meeting &#8211; June 3, 2025", "2025-05-01")] + \
            [post(200 + n, "Board meeting", "2024-01-01") for n in range(98)]
    detail = '<a href="https://tahoetransportation.org/wp-content/uploads/agenda.pdf">Agenda</a>'
    fetch = Pages({"page=1&": json.dumps(page1), "page=2&": json.dumps(page2), "page=3&": AssertionError("stop"),
                   "/meeting/": detail})
    config = {"type": "wordpress-meetings", "indexUrl": "https://tahoetransportation.org/wp-json/wp/v2/meeting", "bodyPattern": "board"}
    found, earliest = discovery.wordpress_meetings_range("ttd", config, since=SINCE, until=UNTIL, fetch=fetch, delay=0)
    by_id = {c["source_id"]: c for c in found}
    # In range by title date, or undated but posted in range; committees and old meetings are skipped.
    assert set(by_id) == {"1", "3", "4"}
    assert by_id["1"]["date"] == "2026-11-04" and by_id["3"]["date"] is None
    assert by_id["1"]["documents"][0]["url"].endswith("/agenda.pdf")
    assert earliest == "2025-06-03"
    assert not any("page=3&" in url for url in fetch.calls)  # page 2 reached posts older than the margin


def test_granicus_row_ids_range_reads_whole_publisher_archive():
    def row(key, clip):
        return (f'<tr><td id="{key}">x</td><td><a href="https://slt.granicus.com/AgendaViewer.php?view_id=6&clip_id={clip}">Agenda</a>'
                f'<a href="https://slt.granicus.com/agenda{clip}.pdf">Agenda PDF</a></td></tr>')
    markup = row("10-06-2026-City-Council", 9) + row("10-01-2026-Planning-Commission", 8) + \
        row("07-14-2026-City-Council", 7) + row("06-02-2026-City-Council", 6) + row("03-02-2021-City-Council", 1)
    config = {"type": "granicus-row-ids", "indexUrl": "https://slt.granicus.com/ViewPublisher.php?view_id=6", "bodyPattern": "City Council"}
    found, earliest, by_date = source_discovery.discover_configured_range("slt", config, since=SINCE, until=UNTIL,
                                                                          fetch=lambda url: markup)
    assert sorted((c["source_id"], c["date"]) for c in found) == [("7", "2026-07-14"), ("9", "2026-10-06")]
    assert earliest == "2021-03-02" and by_date is False


def test_civicclerk_range_pages_until_older_than_since():
    def event(eid, day, name="Board of Trustees Meeting"):
        return {"id": eid, "eventName": name, "eventDate": day + "T17:00:00Z",
                "publishedFiles": [{"type": "Agenda", "fileId": eid * 10, "name": "Agenda"}]}
    pages = {"skip=0,": {"items": [event(1, "2026-11-10"), event(2, "2026-09-01", "One Time Event")], "hasMore": True},
             "skip=2,": {"items": [event(3, "2026-07-15"), event(4, "2026-06-01")], "hasMore": True},
             "skip=4,": AssertionError("paged past since")}
    fetch = Pages({k: v if isinstance(v, Exception) else json.dumps(v) for k, v in pages.items()})
    config = {"type": "civicclerk", "indexUrl": "https://ivgid.portal.civicclerk.com/", "bodyPattern": "Trustees",
              "apiUrl": "https://ivgid.api.civicclerk.com/v1"}
    found, earliest, by_date = source_discovery.discover_configured_range("ivgid", config, since=SINCE, until=UNTIL, fetch=fetch)
    assert [c["source_id"] for c in found] == ["1", "3"] and earliest == "2026-06-01" and by_date is False
    assert "fileId=10" in found[0]["documents"][0]["url"]


def test_legistar_range_uses_web_api_and_keeps_uncovered_page_rows():
    events = [{"EventId": 5219, "EventBodyName": "Planning Commission", "EventDate": "2026-07-09T00:00:00",
               "EventTime": "8:30 AM", "EventAgendaFile": "https://eldorado.legistar.com/View.ashx?M=A&ID=1",
               "EventInSiteURL": "https://eldorado.legistar.com/MeetingDetail.aspx?LEGID=5219"},
              {"EventId": 5220, "EventBodyName": "Board of Supervisors", "EventDate": "2026-07-10T00:00:00"}]
    department = ('<table><tr><td>Planning Commission</td><td>10/22/2026</td>'
                  '<td><a href="MeetingDetail.aspx?ID=1452289&GUID=X">Meeting details</a></td></tr>'
                  '<tr><td>Planning Commission</td><td>7/9/2026</td>'
                  '<td><a href="MeetingDetail.aspx?ID=1449000&GUID=Y">Meeting details</a></td></tr></table>')
    fetch = Pages({"webapi.legistar.com/v1/eldorado/events": json.dumps(events), "DepartmentDetail.aspx": department})
    config = {"type": "legistar", "bodyPattern": "Planning Commission",
              "indexUrl": "https://eldorado.legistar.com/DepartmentDetail.aspx?GUID=E&ID=4415"}
    found, earliest, by_date = source_discovery.discover_configured_range("el-dorado-planning", config,
                                                                          since=SINCE, until=UNTIL, fetch=fetch)
    # The API row covers July 9, so the department page's July 9 row is not a second meeting.
    assert sorted((c["source_id"], c["date"]) for c in found) == [("1452289", "2026-10-22"), ("5219", "2026-07-09")]
    api_row = next(c for c in found if c["source_id"] == "5219")
    assert api_row["start_time"] == "08:30" and api_row["documents"][0]["id"] == "el-dorado-planning-agenda"
    assert earliest == "2026-07-09" and by_date is True
    assert "datetime%272026-07-01%27" in fetch.calls[0]

    fallback = Pages({"webapi.legistar.com": RuntimeError("Public source download failed"), "DepartmentDetail.aspx": department})
    found, _, by_date = source_discovery.discover_configured_range("el-dorado-planning", config, since=SINCE, until=UNTIL,
                                                                   fetch=fallback)
    assert len(found) == 2 and by_date is False


def test_yearly_index_is_read_once_per_year():
    config = {"type": "dated-table", "bodyPattern": "Board", "title": "Lahontan Board Meeting",
              "indexUrl": "https://www.waterboards.ca.gov/lahontan/board_info/agenda/2026_schedule.html",
              "indexUrlTemplate": "https://www.waterboards.ca.gov/lahontan/board_info/agenda/{year}_schedule.html"}

    def table(day):
        return f'<table><tr><td>{day}</td><td><strong>Board</strong> <a href="/a.pdf">Agenda</a></td></tr></table>'
    fetch = Pages({"2026_schedule": table("December 9"), "2027_schedule": table("January 13")})
    found, earliest, by_date = source_discovery.discover_configured_range(
        "lahontan-board", config, since=date(2026, 12, 1), until=date(2027, 1, 31), fetch=fetch)
    assert sorted(c["date"] for c in found) == ["2026-12-09", "2027-01-13"] and by_date is True and earliest == "2026-12-09"


def test_municode_range_queries_the_date_range():
    sent = []

    def fetch_json(payload):
        sent.append(json.loads(payload))
        return [{"meeting": "Town Council Regular Meeting", "date": "7/14/2026", "meetingId": "m1", "time": "5:00 PM",
                 "agenda": "https://mccmeetings.blob.core.usgovcloudapi.net/a.pdf"},
                {"meeting": "Planning Commission", "date": "7/15/2026", "meetingId": "m2"}]
    config = {"type": "municode", "indexUrl": "https://meetings.municode.com/MeetingsPage/index?clientCode=TRUCKEECA",
              "bodyPattern": "Town Council"}
    found, earliest, by_date = source_discovery.discover_configured_range(
        "truckee", config, since=SINCE, until=UNTIL, fetch=lambda url: pytest.fail("no page is read"), municode_json=fetch_json)
    assert sent[0]["meetingDateFrom"] == "2026-07-01" and sent[0]["meetingDateTo"] == "2026-12-31"
    assert sent[0]["displayClientCode"] == "TRUCKEECA"
    assert [(c["source_id"], c["date"], c["start_time"]) for c in found] == [("m1", "2026-07-14", "17:00")]
    assert found[0]["official_url"].endswith("?clientCode=TRUCKEECA&meetingId=m1")
    assert by_date is True and earliest == "2026-07-14"


def test_full_listing_sources_are_unbounded_and_filtered_by_date():
    rows = "".join(f'<a href="https://example.org/agendas/Board-Meeting-Agenda-2026-{m:02}-10.pdf">Board Meeting Agenda {m}/10/2026</a>'
                   for m in range(1, 13))
    config = {"type": "link-index", "indexUrl": "https://example.org/board-meetings", "bodyPattern": "Board Meeting"}
    found, earliest, by_date = source_discovery.discover_configured_range("trcd", config, since=SINCE, until=UNTIL,
                                                                          fetch=lambda url: rows)
    assert len(found) == 6 and earliest == "2026-01-10" and by_date is False  # every listed row, not the newest few
    with pytest.raises(ValueError):
        source_discovery.discover_configured("trcd", config, limit=21, fetch=lambda url: rows)
