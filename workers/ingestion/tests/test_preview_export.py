"""Notice previews in the export (IDEA-018 R3, R7, R8): the one-off and planned meetings, comment period links,
and the "not on the posted agenda" note once the agenda is captured."""
from datetime import date, datetime, timezone

from tahoe_ingest import core, notices
from tahoe_ingest.core import export_public, ingest_bundle
from tahoe_ingest.export_check import validate_export

from test_notices import APC, NOTICE, ONE_OFF_ID, OPEN_HOUSE, PERIOD_ID, notices_client, period_row, store_period
from test_pipeline import db  # noqa: F401  (pytest fixture)

TODAY = date(2026, 10, 29)
PLANNED = "trpa-advisory:planned-2026-11-12"


def at(monkeypatch, day: date) -> None:
    monkeypatch.setattr(core, "now", lambda: datetime(day.year, day.month, day.day, 19, tzinfo=timezone.utc))


def read_notice(conn):
    store_period(conn, period_row())
    report = notices.extract_notices(conn, clients={"claude-haiku-5-5": notices_client([OPEN_HOUSE, APC])}, today=TODAY)
    assert report["oneOffs"] == 1 and report["previews"] == 1 and not report["failedPeriods"]


def test_notice_meetings_and_previews_are_exported_and_linked(db, monkeypatch):
    read_notice(db)
    at(monkeypatch, TODAY)
    payload = export_public(db)
    assert validate_export(payload)[0] == []
    meetings = {m["id"]: m for m in payload["meetings"]}
    one_off, planned = meetings[ONE_OFF_ID], meetings[PLANNED]
    assert one_off["host"] == "TRPA and El Dorado County" and set(one_off["topics"]) >= {"biking", "environment"}
    assert "planned" not in one_off and "previews" not in one_off
    assert planned["planned"] is True and planned["kind"] == "confirmed" and planned["startsAt"].startswith("2026-11-12T")
    assert "128 Market Street" in planned["location"] and planned["officialUrl"] == NOTICE["official_url"]
    [preview] = planned["previews"]
    assert preview["subject"] == APC["subject"] and preview["source"]["kind"] == "notice"
    assert preview["source"]["url"] == NOTICE["official_url"] and preview["source"]["publishedOn"] == NOTICE["opens_on"]
    assert "biking" in planned["topics"] and "biking" in preview["topics"]
    [period] = [p for p in payload["commentPeriods"] if p["id"] == PERIOD_ID]
    assert period["meetingIds"] == [ONE_OFF_ID, PLANNED]
    assert "other" in {a["id"] for a in payload["agencies"]}


def test_captured_agenda_replaces_the_preview_and_notes_a_missing_project(db, monkeypatch):
    read_notice(db)
    pages = "TRPA ADVISORY PLANNING COMMISSION AGENDA\nNovember 12, 2026\n1. Tahoe Valley Area Plan amendments"
    agenda = {"meetings": [{
        "agency": "trpa-advisory", "id": "2026-11-12", "title": "TRPA Advisory Planning Commission", "date": "2026-11-12",
        "start_time": "09:30", "status": "scheduled", "source_meeting_url": "https://www.trpa.gov/meeting/apc-2026-11-12/",
        "documents": [{"id": "agenda", "title": "November 12, 2026 APC Meeting Agenda", "url": "https://www.trpa.gov/apc.pdf",
                       "sha256": "e" * 64, "page_count": 1, "fetched_at": "2026-11-05T17:00:00Z"}],
        "pages": [{"document_id": "agenda", "page_number": 1, "text": pages}],
        "agenda_items": [{"source_number": "1", "order": 1, "section": None, "title": "Tahoe Valley Area Plan amendments",
                          "source_pages": [{"document_id": "agenda", "page_number": 1,
                                            "quote": "Tahoe Valley Area Plan amendments"}]}]}]}
    result = ingest_bundle(db, agenda, mode="fixture")
    assert result["failed"] == 0, result["errors"]
    at(monkeypatch, date(2026, 11, 6))
    payload = export_public(db)
    assert validate_export(payload)[0] == []
    meetings = {m["id"]: m for m in payload["meetings"]}
    captured = meetings["trpa-advisory:2026-11-12"]
    assert "previews" not in captured and captured["previewNotOnAgenda"] == [period_row()["project_title"]]
    assert PLANNED not in meetings  # the captured meeting carries the date now
    [period] = [p for p in payload["commentPeriods"] if p["id"] == PERIOD_ID]
    assert period["meetingIds"] == [ONE_OFF_ID, "trpa-advisory:2026-11-12"]
