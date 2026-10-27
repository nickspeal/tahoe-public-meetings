"""The scheduled pipeline, export storage, and deploy hook, with every network call mocked."""
import contextlib
import hashlib
import json
import os
import urllib.error
import uuid
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest import cli, scheduled
from tahoe_ingest.core import init_db

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def db():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_scheduled_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with psycopg.connect(url, options=f"-c search_path={schema}") as connection:
            connection.row_factory = psycopg.rows.dict_row
            yield connection
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


AGENDA_URL = "https://example.gov/planning/agenda.pdf"
AGENDA_TEXT = "PLANNING COMMISSION AGENDA\n1. Call to order\n2. Housing element workshop"


def fake_fetch(url):
    if url != AGENDA_URL:
        raise ValueError("offline test: source unavailable")
    return {"sha256": hashlib.sha256(AGENDA_TEXT.encode()).hexdigest(), "page_count": 1, "warnings": [],
            "media_type": "application/pdf", "pages": [{"page_number": 1, "text": AGENDA_TEXT}]}


def fake_discover(agency_id, config, *, limit):
    if agency_id == "tcpud":
        raise RuntimeError("index down")
    if agency_id != "slt-planning":
        return []
    upcoming = (date.today() + timedelta(days=10)).isoformat()
    old = (date.today() - timedelta(days=400)).isoformat()
    return [{"agency": agency_id, "source_id": "9001", "title": "Planning Commission", "date": upcoming,
             "official_url": "https://example.gov/planning/9001", "documents": [
                 {"id": "slt-planning-agenda", "title": "Agenda", "url": AGENDA_URL}], "warnings": []},
            {"agency": agency_id, "source_id": "1", "title": "Planning Commission", "date": old,
             "official_url": "https://example.gov/planning/1", "documents": [], "warnings": []}][:limit]


class Hook:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, url):
        self.calls.append(url)
        if self.fail:
            raise RuntimeError("deploy hook returned HTTP 500")
        return {"httpStatus": 201}


def options(**overrides):
    return scheduled.Options(**{"root": ROOT, "delay": 0, "forecast_days": 30, **overrides})


def test_run_scheduled_populates_empty_database_and_publishes(db, monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("VERCEL_DEPLOY_HOOK_URL", "https://hooks.example/secret-token")
    hook = Hook()
    summary = scheduled.run_scheduled(db, options(out=tmp_path / "public.json"), discover=fake_discover, fetch=fake_fetch, post=hook)
    json.dumps(summary)  # One JSON line for Cloud Logging.
    steps = {s["name"]: s for s in summary["steps"]}
    assert list(steps) == ["init-db", "seed-fixtures", "one-offs", "comment-periods", "verified-sources", "discover",
                           "quarantine", "reparse", "reclassify", "meeting-records", "draft-minutes", "classify-topics", "participation",
                           "export", "validate", "store", "deploy-hook"]
    # Source failures degrade the run but never stop publication.
    assert summary["status"] == "degraded" and summary["exportId"]
    assert steps["classify-topics"]["status"] == "skipped"
    assert steps["seed-fixtures"]["detail"]["processed"] == 19
    assert steps["discover"]["detail"]["agenciesWithFailures"] == ["tcpud"]
    assert steps["discover"]["detail"]["byAgency"]["slt-planning"]["captured"] == 1  # the 400-day-old row predates the coverage floor
    assert any(f.startswith("discover:tcpud: index down") for f in summary["failures"])
    assert hook.calls == ["https://hooks.example/secret-token"]
    assert "secret-token" not in json.dumps(summary)
    assert summary["counts"]["meetings"] >= 19 and summary["counts"]["agenciesWithMeetings"] > 3
    stored = db.execute("SELECT schema_version, payload FROM public_exports").fetchall()
    assert len(stored) == 1 and stored[0]["schema_version"] == 2
    assert stored[0]["payload"] == json.loads((tmp_path / "public.json").read_text())
    assert db.execute("SELECT status FROM agencies WHERE id='tcpud'").fetchone()["status"] == "error"
    assert db.execute("SELECT status FROM agencies WHERE id='slt-planning'").fetchone()["status"] == "ok"
    assert db.execute("SELECT 1 FROM meetings WHERE id='slt-planning:9001'").fetchone()
    assert db.execute("SELECT count(*) AS n FROM processing_runs WHERE stage='discover' AND status='failed'").fetchone()["n"] == 1
    # Quarantined candidates from tracked config are never ingested.
    assert not db.execute("SELECT 1 FROM meetings WHERE id='placer-tahoe-design-review:2026-10-01' AND quarantine_reason IS NULL").fetchone()

    meetings = db.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"]
    fetched = []
    again = scheduled.run_scheduled(db, options(), discover=fake_discover, fetch=lambda url: fetched.append(url) or fake_fetch(url), post=hook)
    steps = {s["name"]: s for s in again["steps"]}
    # A rerun neither reapplies the fixture bundle nor retries documents that failed within the day.
    assert steps["seed-fixtures"]["detail"]["processed"] == 0
    assert steps["verified-sources"]["detail"].get("captured", 0) == 0
    # Only the upcoming discovered agenda is checked again; comment-period notices are re-verified every run by design.
    notices = {p["official_url"] for p in json.loads((ROOT / scheduled.COMMENT_PERIODS).read_text())["comment_periods"]}
    assert [url for url in fetched if url not in notices] == [AGENDA_URL]
    assert db.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"] == meetings
    assert db.execute("SELECT count(*) AS n FROM public_exports").fetchone()["n"] == 2
    assert len(hook.calls) == 2


TODAY = date(2026, 3, 10)


def meeting(source_id, day, documents=()):
    return {"agency": "slt-planning", "source_id": source_id, "title": "Planning Commission", "date": day.isoformat(),
            "official_url": f"https://example.gov/planning/{source_id}", "warnings": [],
            "documents": [{"id": f"slt-planning-{source_id}-{n}", "title": "Agenda", "url": url} for n, url in enumerate(documents)]}


def discover_one(rows, seen_limits=None):
    def discover(agency_id, config, *, limit):
        if seen_limits is not None:
            seen_limits[agency_id] = limit
        return [dict(r) for r in rows][:limit] if agency_id == "slt-planning" else []
    return discover


def discover_report(db, rows, **overrides):
    run = scheduled.Run(db)
    fetched = []
    def fetch(url):
        fetched.append(url)
        text = f"{AGENDA_TEXT}\n3. Source {url}"
        return {**fake_fetch(AGENDA_URL), "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "pages": [{"page_number": 1, "text": text}]}
    status, detail = scheduled.discover_sources(db, ROOT, run, options(**overrides), discover_one(rows), fetch, TODAY)
    return detail["byAgency"]["slt-planning"], fetched


def test_window_is_applied_before_the_per_agency_cap(db):
    init_db(db)
    # Newest first, as the indexes list them: far-future postings would fill a 6-row cap.
    far = [meeting(f"far{n}", TODAY + timedelta(days=200 + n)) for n in range(12)]
    near = [meeting("next", TODAY + timedelta(days=5), [AGENDA_URL]), meeting("later", TODAY + timedelta(days=60)),
            meeting("recent", TODAY - timedelta(days=12), [AGENDA_URL]), meeting("older", TODAY - timedelta(days=50)),
            meeting("stale", TODAY - timedelta(days=61)), meeting("dec", date(2025, 12, 31)),
            meeting("jan", date(2026, 1, 1))]
    rows = sorted(far + near, key=lambda r: r["date"], reverse=True)
    assert len(rows) <= scheduled.INDEX_LIMIT
    report, fetched = discover_report(db, rows, max_captures_per_agency=3)
    assert report["discovered"] == len(rows)
    # The window is 2026-01-09..2026-07-08: far-future, "stale", and "jan" fall outside; "dec" predates coverage.
    assert report["outsideWindow"] == 12 + 2 and report["beforeCoverage"] == 1
    # Four are in the window; the nearest three are captured and "later" waits for the next run.
    assert report["captured"] == 3 and report["deferred"] == 1
    stored = {r["source_id"] for r in db.execute("SELECT source_id FROM meetings WHERE agency_id='slt-planning'")}
    assert stored == {"next", "recent", "older"}
    assert fetched == [AGENDA_URL, AGENDA_URL]
    # The next run reaches the deferred meetings; nothing stored is removed by the window.
    report, _ = discover_report(db, rows, max_captures_per_agency=10, past_days=2, future_days=2)
    assert report["outsideWindow"] == len(rows) - 1 and report.get("captured", 0) == 0
    assert db.execute("SELECT count(*) AS n FROM meetings WHERE agency_id='slt-planning'").fetchone()["n"] == 3


def test_settled_meetings_are_skipped_until_a_new_document_is_linked(db):
    init_db(db)
    minutes = "https://example.gov/planning/minutes.pdf"
    rows = [meeting("old", TODAY - timedelta(days=20), [AGENDA_URL]), meeting("week", TODAY - timedelta(days=3), [AGENDA_URL])]
    report, fetched = discover_report(db, rows)
    assert report["captured"] == 2 and len(fetched) == 2
    # Within the 7-day recapture window a meeting is fetched again; older ones are settled.
    report, fetched = discover_report(db, rows)
    assert report["settled"] == 1 and report["captured"] == 1 and len(fetched) == 1
    # A document at a new URL (minutes) on a settled meeting is captured once, then it settles again.
    rows[0]["documents"].append({"id": "slt-planning-old-minutes", "title": "Minutes", "url": minutes})
    report, fetched = discover_report(db, rows)
    assert "settled" not in report and report["captured"] == 2 and fetched.count(minutes) == 1
    report, fetched = discover_report(db, rows)
    assert report["settled"] == 1 and minutes not in fetched
    # Documents beyond the per-meeting cap are never fetched, so they cannot unsettle a meeting.
    rows[0]["documents"].append({"id": "slt-planning-old-extra", "title": "Extra", "url": "https://example.gov/x.pdf"})
    assert discover_report(db, rows)[0]["settled"] == 1


def test_failed_downloads_of_settled_age_meetings_retry_daily(db):
    init_db(db)
    rows = [meeting("old", TODAY - timedelta(days=20), ["https://example.gov/missing.pdf"])]
    run = scheduled.Run(db)
    scheduled.discover_sources(db, ROOT, run, options(), discover_one(rows), fake_fetch, TODAY)
    report, fetched = discover_report(db, rows)
    assert report["retryLater"] == 1 and fetched == []


def test_index_limits():
    opts = options()
    assert opts.discovery_limit == scheduled.INDEX_LIMIT == 20
    assert scheduled._index_limit({"discoveryAdapter": "ttd"}, opts) == scheduled.UNDATED_INDEX_LIMIT
    assert scheduled._index_limit({"discoveryAdapter": "configured"}, opts) == 20
    undated = {"source_id": "t", "date": None}
    assert scheduled._window_reason(undated, TODAY, opts) is None  # TTD posts are bounded by count instead
    assert scheduled._window_reason({"date": "2025-12-31"}, date(2026, 1, 20), opts) == "beforeCoverage"
    assert [c["source_id"] for c in scheduled._nearest_first(
        [{"source_id": "a", "date": "2026-06-01"}, {"source_id": "b", "date": "2026-03-01"}, undated], TODAY)] == ["t", "b", "a"]


def test_truckee_calendar_months(monkeypatch):
    from tahoe_ingest import capture
    assert capture.truckee_months(date(2026, 1, 5)) == [(2025, 12), (2026, 1), (2026, 2)]
    assert capture.truckee_months(date(2026, 12, 20)) == [(2026, 12), (2027, 1)]
    calls = []

    def fake_truckee(*, year, month, limit):
        calls.append((year, month, limit))
        return [{"source_id": f"{month}", "title": "Town Council"}, {"source_id": "shared", "title": "Town Council"}]
    monkeypatch.setattr(capture, "discover_truckee", fake_truckee)
    found = capture.discover_agency("truckee", {"discoveryAdapter": "truckee"}, limit=5)
    assert len(calls) in (2, 3) and all(c[2] == 5 for c in calls)
    assert [c["source_id"] for c in found].count("shared") == 1 and all(c["agency"] == "truckee" for c in found)
    calls.clear()
    capture.discover_agency("truckee", {"discoveryAdapter": "truckee"}, limit=5, year=2026, month=4)
    assert calls == [(2026, 4, 5)]


def test_invalid_or_empty_export_is_not_stored_or_deployed(db, monkeypatch, tmp_path):
    monkeypatch.setenv("VERCEL_DEPLOY_HOOK_URL", "https://hooks.example/secret-token")
    hook = Hook()
    summary = scheduled.run_scheduled(db, options(root=tmp_path, skip_discovery=True), discover=fake_discover,
                                      fetch=fake_fetch, post=hook)
    assert summary["status"] == "failed" and summary["exportId"] is None
    assert next(s for s in summary["steps"] if s["name"] == "validate")["status"] == "failed"
    assert "at least 1 required" in " ".join(summary["failures"])
    assert hook.calls == []
    assert db.execute("SELECT count(*) AS n FROM public_exports").fetchone()["n"] == 0

    run = scheduled.Run(db)
    scheduled.publish(db, {"schemaVersion": 1, "meetings": []}, run, min_meetings=0, hook_url="https://hooks.example/x", post=hook)
    assert run.fatal and hook.calls == []


def test_failed_deploy_hook_fails_the_run_after_storing(db, monkeypatch):
    monkeypatch.setenv("VERCEL_DEPLOY_HOOK_URL", "https://hooks.example/secret-token")
    summary = scheduled.run_scheduled(db, options(skip_discovery=True), fetch=fake_fetch, post=Hook(fail=True))
    assert summary["status"] == "failed" and summary["exportId"]
    assert next(s for s in summary["steps"] if s["name"] == "deploy-hook")["status"] == "failed"


def test_export_retention_keeps_latest_few(db):
    init_db(db)
    payload = {"schemaVersion": 2, "generatedAt": "2026-10-20T00:00:00Z", "meetings": []}
    ids = [scheduled.store_export(db, payload) for _ in range(scheduled.RETAINED_EXPORTS + 3)]
    kept = [r["id"] for r in db.execute("SELECT id FROM public_exports ORDER BY id")]
    assert kept == ids[-scheduled.RETAINED_EXPORTS:]


def test_concurrent_run_is_skipped(db):
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    with psycopg.connect(url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (scheduled.RUN_LOCK,))
        summary = scheduled.run_scheduled(db, options(), discover=fake_discover, fetch=fake_fetch, post=Hook())
    assert summary["status"] == "skipped"


def test_deploy_hook_post_never_reveals_url():
    class Response:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False
    seen = []
    assert scheduled.post_deploy_hook("https://hooks.example/abc", opener=lambda req, timeout: seen.append((req, timeout)) or Response()) == {"httpStatus": 201}
    assert seen[0][0].get_method() == "POST" and seen[0][1] == 30.0

    def refuse(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    with pytest.raises(RuntimeError) as error:
        scheduled.post_deploy_hook("https://hooks.example/abc", opener=refuse)
    assert "404" in str(error.value) and "abc" not in str(error.value)
    with pytest.raises(ValueError):
        scheduled.post_deploy_hook("http://hooks.example/abc")


def test_publish_export_cli(db, monkeypatch, tmp_path):
    monkeypatch.delenv("VERCEL_DEPLOY_HOOK_URL", raising=False)
    monkeypatch.setattr(cli, "connect", lambda _: contextlib.nullcontext(db))
    init_db(db)
    from tahoe_ingest.core import export_public, ingest_bundle
    ingest_bundle(db, json.loads((ROOT / "tests/fixtures/meetings.json").read_text()))
    path = tmp_path / "public.json"
    path.write_text(json.dumps(export_public(db, mode="live", forecast_days=30)))
    assert cli.main(["--database-url", "unused", "publish-export", "--file", str(path)]) == 0
    assert db.execute("SELECT count(*) AS n FROM public_exports").fetchone()["n"] == 1
    broken = json.loads(path.read_text())
    broken["meetings"][0]["items"] = [{**broken["meetings"][0]["items"][0], "citations": [{"documentId": "missing", "page": 1, "quote": "x"}]}] \
        if broken["meetings"][0]["items"] else [{"id": "x", "citations": [{"documentId": "missing", "page": 1, "quote": "x"}]}]
    path.write_text(json.dumps(broken))
    assert cli.main(["--database-url", "unused", "publish-export", "--file", str(path)]) == 1
    assert db.execute("SELECT count(*) AS n FROM public_exports").fetchone()["n"] == 1
