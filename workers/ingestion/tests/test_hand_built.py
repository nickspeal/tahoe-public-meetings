"""Warning ownership after decision 0033, and the one-time cleanup of hand-built records (`clean-hand-built`)."""
import hashlib
import json
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest import capture, hand_built
from tahoe_ingest.core import ingest_bundle, init_db

TODAY = date(2026, 11, 3)
DATED = "No agenda document is linked from the official event page as of 2026-10-20. This is a logistics-only event record."
VERIFIED = "Official archive lists the meeting date; agenda text not captured."


@pytest.fixture
def db():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_hand_built_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with psycopg.connect(url, options=f"-c search_path={schema}") as connection:
            connection.row_factory = psycopg.rows.dict_row
            init_db(connection)
            yield connection
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def page_text(n):
    return f"TOWN COUNCIL AGENDA page {n}"


def sha(pages):
    return hashlib.sha256(str(pages).encode()).hexdigest()


def meeting(source_id, day, warnings=(), documents=(), pages=()):
    return {"agency": "truckee", "id": source_id, "title": "Town Council", "date": day, "status": "scheduled",
            "source_meeting_url": f"https://example.gov/{source_id}", "warnings": list(warnings),
            "documents": list(documents), "pages": list(pages), "agenda_items": []}


PACKET = {"id": "truckee-agenda", "title": "Agenda packet", "url": "https://example.gov/packet.pdf",
          "sha256": sha(10), "page_count": 10}


def write_sources(root: Path, seed: list[dict], verified: list[dict]):
    (root / "tests/fixtures").mkdir(parents=True)
    (root / "tests/fixtures/meetings.json").write_text(json.dumps({"captured_at": "2026-10-19T12:00:00Z", "meetings": seed}))
    (root / "docs/research/verified-agency-meetings").mkdir(parents=True)
    (root / "docs/research/verified-agency-meetings/batch.json").write_text(json.dumps({"candidates": verified}))


def candidate(source_id, day, warnings):
    return {"agency": "truckee", "source_id": source_id, "title": "Town Council", "date": day,
            "official_url": f"https://example.gov/{source_id}", "documents": [], "warnings": list(warnings)}


def test_a_capture_without_documents_stores_no_availability_warning():
    row = capture._capture_row(candidate("1", "2026-11-10", []), 2, fetch=None, failures=[], failure_records=[], delay=0)
    assert row["warnings"] == [] and row["documents"] == []


def test_a_capture_replaces_warnings_it_did_not_write(db):
    ingest_bundle(db, {"meetings": [meeting("1772", "2026-11-10", [DATED])]}, mode="fixture")
    capture.capture_candidates(db, [candidate("1772", "2026-11-10", [])], fetch=None)
    assert db.execute("SELECT warnings FROM meetings WHERE id='truckee:1772'").fetchone()["warnings"] == []


def test_the_same_file_with_more_pages_takes_the_new_warnings(db):
    three = [{"document_id": "truckee-agenda", "page_number": n, "text": page_text(n)} for n in (1, 2, 3)]
    ingest_bundle(db, {"captured_at": "2026-10-19T12:00:00Z", "meetings": [meeting("1", "2026-09-08", documents=[PACKET], pages=three)]})
    first = db.execute("SELECT warnings FROM document_versions").fetchone()["warnings"]
    assert first == ["Text retained for 3 of 10 source pages; remaining pages are not represented in this export.",
                     hand_built.UNKNOWN_FETCH]
    every = [{"document_id": "truckee-agenda", "page_number": n, "text": page_text(n)} for n in range(1, 11)]
    ingest_bundle(db, {"meetings": [meeting("1", "2026-09-08", documents=[{**PACKET, "fetched_at": "2026-10-27T19:00:00Z"}],
                                            pages=every)]}, mode="live")
    assert db.execute("SELECT warnings FROM document_versions").fetchone()["warnings"] == []


def test_clean_hand_built(db, tmp_path):
    upcoming = (TODAY + timedelta(days=7)).isoformat()
    three = [{"document_id": "truckee-agenda", "page_number": n, "text": page_text(n)} for n in (1, 2, 3)]
    seed = [meeting("1772", upcoming, [DATED]),                                   # seed only, empty: deleted
            meeting("1770", "2026-09-08", [DATED], documents=[PACKET], pages=three)]  # seed with text: kept
    verified = [candidate("2026-05-05", "2026-05-05", [VERIFIED]),   # still as typed, empty: deleted
                candidate("2026-06-02", "2026-06-02", [VERIFIED]),   # a discovery capture replaced its warnings: kept
                candidate("2026-07-07", "2026-07-07", [])]           # nothing shows whether discovery saw it: kept
    write_sources(tmp_path, seed, verified)
    ingest_bundle(db, {"captured_at": "2026-10-19T12:00:00Z", "meetings": seed}, mode="fixture")
    capture.capture_candidates(db, verified, fetch=None)
    capture.capture_candidates(db, [candidate("2026-06-02", "2026-06-02", [])], fetch=None)
    # A live capture of 1770's file stored all of its pages; a live step added its own warning.
    every = [{"document_id": "truckee-agenda", "page_number": n, "text": page_text(n)} for n in range(1, 11)]
    ingest_bundle(db, {"meetings": [{**meeting("1770", "2026-09-08", [DATED, "A source PDF changed; affected agenda items await reprocessing."],
                                               documents=[{**PACKET, "fetched_at": "2026-10-27T19:00:00Z"}], pages=every)}]}, mode="live")
    # The state production holds: the version's warnings as the bundle wrote them (before the core fix).
    db.execute("UPDATE document_versions SET warnings=%s", (json.dumps(
        ["Text retained for 3 of 10 source pages; remaining pages are not represented in this export.", hand_built.UNKNOWN_FETCH]),))
    ingest_bundle(db, {"meetings": [meeting("live-1", upcoming, ["Confirm the scheduled start in the agenda."])]}, mode="live")
    db.commit()

    report = hand_built.clean_hand_built(db, tmp_path, today=TODAY)
    assert report["summary"]["dryRun"] and db.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"] == 6
    assert sorted(d["meeting"] for d in report["delete"]) == ["truckee:1772", "truckee:2026-05-05"]
    assert {k["meeting"]: k["reason"] for k in report["keep"]} == {
        "truckee:1770": "seen by live discovery", "truckee:2026-06-02": "seen by live discovery",
        "truckee:2026-07-07": "cannot tell whether discovery has seen it"}
    assert report["summary"]["warnings"] == {"meetings": 3, "removed": 3}

    report = hand_built.clean_hand_built(db, tmp_path, dry_run=False, today=TODAY)
    db.commit()
    warnings = {r["id"]: r["warnings"] for r in db.execute("SELECT id, warnings FROM meetings")}
    assert warnings == {"truckee:1770": ["A source PDF changed; affected agenda items await reprocessing."],
                        "truckee:2026-06-02": [], "truckee:2026-07-07": [],
                        "truckee:live-1": ["Confirm the scheduled start in the agenda."]}
    # The file a live capture downloaded again: complete text, and that capture's time as the fetch time.
    version = db.execute("SELECT warnings, fetched_at FROM document_versions").fetchone()
    first_live = db.execute("""SELECT min(started_at) AS t FROM processing_runs WHERE meeting_id='truckee:1770'
        AND detail->>'mode'='live'""").fetchone()["t"]
    assert version["warnings"] == [] and version["fetched_at"] == first_live
    # Run logs stay, without the deleted meeting.
    assert db.execute("SELECT count(*) AS n FROM processing_runs WHERE meeting_id IS NULL AND stage='ingest'").fetchone()["n"] >= 2

    again = hand_built.clean_hand_built(db, tmp_path, dry_run=False, today=TODAY)["summary"]
    assert again["warnings"]["meetings"] == 0 and again["documentVersions"] == 0 and again["delete"]["count"] == 0
