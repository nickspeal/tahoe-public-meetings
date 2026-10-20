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
                           "quarantine", "reparse", "reclassify", "classify-topics", "export", "validate", "store", "deploy-hook"]
    # Source failures degrade the run but never stop publication.
    assert summary["status"] == "degraded" and summary["exportId"]
    assert steps["classify-topics"]["status"] == "skipped"
    assert steps["seed-fixtures"]["detail"]["processed"] == 19
    assert steps["discover"]["detail"]["agenciesWithFailures"] == ["tcpud"]
    assert steps["discover"]["detail"]["byAgency"]["slt-planning"]["captured"] == 1  # the 400-day-old row is outside the window
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
    assert fetched == [AGENDA_URL]  # only the upcoming discovered agenda is checked again
    assert db.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"] == meetings
    assert db.execute("SELECT count(*) AS n FROM public_exports").fetchone()["n"] == 2
    assert len(hook.calls) == 2


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
