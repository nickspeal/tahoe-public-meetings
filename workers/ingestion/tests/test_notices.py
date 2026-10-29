"""Meetings and previews read from tracked notices (IDEA-018 R1–R3): checks, hosts, records, revisions, reruns."""
import contextlib
import copy
import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest import notices
from tahoe_ingest.core import init_db
from tahoe_ingest.evaluate_topics import ModelReply

NOTICE = json.loads((Path(__file__).resolve().parents[3] / "tests/fixtures/notices/west-shore-trail-nop-2026-10.json").read_text())
TODAY = date(2026, 10, 29)
PERIOD_ID = "other:west-shore-trail-meeks-bay-dl-bliss-nop"
ONE_OFF_ID = "other:one-off-west-shore-trail-meeks-bay-dl-bliss-nop-2026-11-02"

OPEN_HOUSE = {
    "kind": "one-off", "title": "West Shore Trail – Meeks Bay to D.L. Bliss EA or EIS/EIR Scoping Meeting",
    "format": "Open House", "date": "2026-11-02", "startTime": "17:30", "endTime": "19:00",
    "locationLines": ["Tahoe City Public Utility District", "221 Fairway Drive", "Tahoe City, California 96145"],
    "remoteQuote": None, "hosts": ["TRPA", "El Dorado County"], "bodyName": None, "subject": None,
    "quote": ("West Shore Trail – Meeks Bay to D.L. Bliss EA or EIS/EIR Scoping Meeting November 2, 2026, "
              "Open House from 5:30-7:00 PM Tahoe City Public Utility District 221 Fairway Drive "
              "Tahoe City, California 96145"),
}
APC = {
    "kind": "regular-meeting", "title": "TRPA Advisory Planning Commission meeting", "format": None,
    "date": "2026-11-12", "startTime": "09:30", "endTime": None,
    "locationLines": ["128 Market Street", "Stateline, Nevada"],
    "remoteQuote": "The meeting will begin at 9:30 a.m. at 128 Market Street in Stateline, Nevada, with a virtual attendance option.",
    "hosts": ["TRPA"], "bodyName": "TRPA Advisory Planning Commission",
    "subject": "West Shore Trail – Meeks Bay to D.L. Bliss State Park",
    "quote": ("The project will also be presented at the TRPA Advisory Planning Commission meeting on November 12, 2026. "
              "The meeting will begin at 9:30 a.m. at 128 Market Street in Stateline, Nevada, with a virtual attendance option."),
}
# Invented: the notice states no such time, and the quote changes a word.
FABRICATED = {**OPEN_HOUSE, "title": "Public scoping meeting", "date": "2026-11-02", "startTime": "18:00", "endTime": None,
              "quote": "The meeting will be held online on November 2, 2026, as follows:"}


def period_row(**changes):
    row = {"id": PERIOD_ID, "agency_id": "other", "source_id": NOTICE["comment_period_source_id"],
           "title": "West Shore Trail – Meeks Bay to D.L. Bliss State Park Notice of Preparation",
           "opens_on": date.fromisoformat(NOTICE["opens_on"]), "closes_on": date.fromisoformat(NOTICE["closes_on"]),
           "official_url": NOTICE["official_url"], "topics": ["biking", "environment"], "source_text": NOTICE["text"],
           "source_sha256": NOTICE["sha256"], "project_title": "West Shore Trail – Meeks Bay to D.L. Bliss State Park",
           "checked_at": datetime(2026, 10, 27, 17, 0, tzinfo=timezone.utc), "host": "TRPA and El Dorado County"}
    row.update(changes)
    return row


class StubModel:
    def __init__(self, model, answer):
        self.model, self.answer, self.calls = model, answer, 0

    def complete(self, *, system, user, schema, max_tokens):
        self.calls += 1
        usage = {"input_tokens": 5000, "output_tokens": 500, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        return ModelReply(text=json.dumps(self.answer), stop_reason="end_turn", usage=usage, model=self.model)


@pytest.fixture
def db():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_notices_" + uuid.uuid4().hex
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


@pytest.fixture
def replaced(monkeypatch):
    """Stands in for previews.replace_source, which another module owns."""
    calls = []

    def replace(conn, source, previews, *, today):
        calls.append({"source": source, "previews": previews, "today": today})
        return {"inserted": len(previews), "current": True, "planned": [], "unchanged": False}
    monkeypatch.setattr(notices, "_replace_previews", replace)
    return calls


def store_period(conn, row):
    conn.execute("""INSERT INTO comment_periods(id,agency_id,source_id,title,opens_on,closes_on,official_url,topics,
            source_quote,source_text,source_sha256,checked_at,project_title,host)
        VALUES (%(id)s,%(agency_id)s,%(source_id)s,%(title)s,%(opens_on)s,%(closes_on)s,%(official_url)s,%(topics)s,
            'NOTICE OF PREPARATION AND SCOPING MEETING',%(source_text)s,%(source_sha256)s,%(checked_at)s,%(project_title)s,%(host)s)
        ON CONFLICT(id) DO UPDATE SET source_text=excluded.source_text,source_sha256=excluded.source_sha256""",
        {**row, "topics": json.dumps(row["topics"])})
    conn.commit()


@contextlib.contextmanager
def statement_count(monkeypatch):
    counter = {"n": 0}
    original = psycopg.Cursor.execute

    def execute(self, *args, **kwargs):
        counter["n"] += 1
        return original(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Cursor, "execute", execute)
        yield counter


# --- Model-free checks ------------------------------------------------------------

def test_validation_accepts_the_real_events_and_rejects_the_invented_one():
    accepted, rejected = notices.validate({"events": [OPEN_HOUSE, APC, FABRICATED]}, period_row(), today=TODAY)
    assert [e["kind"] for e in accepted] == ["one-off", "regular-meeting"]
    assert accepted[1]["agencyId"] == "trpa-advisory"
    assert len(rejected) == 1 and "quote" in rejected[0]["reason"]


@pytest.mark.parametrize("changes,reason", [
    ({"quote": OPEN_HOUSE["quote"].replace("Open House", "Open Meeting")}, "quote"),  # one word changed
    ({"startTime": "17:00", "endTime": None}, "start time"),  # a time the quote does not state
    ({"endTime": "20:00"}, "end time"),
    ({"date": "2026-11-03"}, "date"),
    ({"quote": "West Shore Trail – Meeks Bay to D.L. Bliss EA or EIS/EIR Scoping Meeting"}, "date"),  # no date in the quote
    ({"locationLines": ["Tahoe City Public Utility District", "231 Fairway Drive"]}, "location"),
    ({"hosts": ["TRPA", "Placer County"]}, "host"),
    ({"hosts": []}, "no host"),
])
def test_validation_rejects_what_the_notice_does_not_state(changes, reason):
    accepted, rejected = notices.validate({"events": [{**OPEN_HOUSE, **changes}]}, period_row(), today=TODAY)
    assert not accepted and reason in rejected[0]["reason"]


def test_validation_rejects_past_dates_unstated_years_and_unresolvable_bodies():
    _, rejected = notices.validate({"events": [OPEN_HOUSE]}, period_row(), today=date(2026, 11, 3))
    assert rejected[0]["reason"] == "already past"
    _, rejected = notices.validate({"events": [{**APC, "bodyName": "TRPA"}]}, period_row(), today=TODAY)
    assert "registry entries" in rejected[0]["reason"]
    _, rejected = notices.validate({"events": [{**APC, "remoteQuote": "Public comments on the project may be provided."}]},
                                   period_row(), today=TODAY)
    assert "remote" in rejected[0]["reason"]
    assert notices.quoted_dates("on November 2 as follows") == set()
    assert notices.quoted_times("begins at 5:30") == set()  # morning or evening is never guessed


def test_resolve_host():
    assert notices.resolve_host(["TRPA"]) == ("other", "TRPA")  # names both TRPA bodies
    assert notices.resolve_host(["Town of Truckee"]) == ("truckee", None)
    assert notices.resolve_host(["TRPA", "El Dorado County"]) == ("other", "TRPA and El Dorado County")
    assert notices.resolve_host(["Town of Truckee", "Truckee Donner Land Trust"]) == (
        "other", "Town of Truckee and Truckee Donner Land Trust")


def test_body_names_match_on_words_with_commission_and_committee_equivalent():
    assert notices.matching_agencies("TRPA Advisory Planning Commission") == {"trpa-advisory"}
    assert notices.matching_agencies("the APC") == {"trpa-advisory"}
    assert notices.matching_agencies("Tahoe Regional Planning Agency") == {"trpa-advisory", "trpa-governing"}


def test_records_for_the_west_shore_trail_notice():
    accepted, _ = notices.validate({"events": [OPEN_HOUSE, APC]}, period_row(), today=TODAY)
    records = notices.events_to_records(period_row(), accepted, previous=None)
    [row] = records.one_offs
    assert row["agency"] == "other" and row["id"] == ONE_OFF_ID.split(":", 1)[1]
    assert row["host"] == "TRPA and El Dorado County" and row["start_time"] == "17:30" and row["end_time"] == "19:00"
    assert row["location"] == "Tahoe City Public Utility District, 221 Fairway Drive, Tahoe City, California 96145"
    assert len(row["participation"]["instructions"]) <= notices.MAX_INSTRUCTIONS
    [preview] = records.previews
    assert preview["meeting_title"] == "TRPA Advisory Planning Commission Meeting"
    assert records.source == {"kind": "notice", "key": PERIOD_ID, "sha256": NOTICE["sha256"], "url": NOTICE["official_url"],
                              "title": "West Shore Trail – Meeks Bay to D.L. Bliss State Park notice", "published_on": date(2026, 10, 20), "pages": None}
    assert notices.published_on(period_row(opens_on=None)) == date(2026, 10, 27)


def test_same_day_events_get_numbered_ids():
    second = {**OPEN_HOUSE, "title": "Open House", "startTime": None, "endTime": None}
    rows = notices.events_to_records(period_row(), [
        {**OPEN_HOUSE}, {**second}], previous=None).one_offs
    assert [r["id"] for r in rows] == ["one-off-west-shore-trail-meeks-bay-dl-bliss-nop-2026-11-02",
                                       "one-off-west-shore-trail-meeks-bay-dl-bliss-nop-2026-11-02-2"]


def test_long_quotes_are_cut_at_a_sentence():
    text = "First sentence here. " * 30
    cut = notices.instructions_from(text)
    assert len(cut) <= notices.MAX_INSTRUCTIONS and cut.endswith("here.")


# --- Database: storage, reruns, and revisions ---------------------------------------

def test_notice_yields_one_off_and_preview_and_reruns_cost_nothing(db, replaced, monkeypatch):
    store_period(db, period_row())
    haiku = notices_client([OPEN_HOUSE, APC, FABRICATED])
    report = notices.extract_notices(db, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    assert report["periodsPending"] == 1 and report["extracted"] == {"claude-haiku-5-5": 1}
    assert report["eventsAccepted"] == 2 and report["eventsRejected"] == 1
    assert report["oneOffs"] == 1 and report["previews"] == 1 and report["costUsd"] > 0 and not report["failedPeriods"]

    meeting = db.execute("SELECT * FROM meetings WHERE id=%s", (ONE_OFF_ID,)).fetchone()
    assert meeting["agency_id"] == "other" and meeting["host"] == "TRPA and El Dorado County"
    assert meeting["local_date"] == date(2026, 11, 2)
    assert meeting["starts_at"].astimezone(notices.PACIFIC).strftime("%H:%M") == "17:30"
    assert meeting["observed_duration_minutes"] == 90 and meeting["duration_source"] == "published"
    assert sorted(meeting["declared_topics"]) == ["biking", "environment"]
    assert meeting["warnings"] == [] and meeting["official_url"] == NOTICE["official_url"]
    assert meeting["status"] == "scheduled" and "Tahoe City Public Utility District" in meeting["location"]

    [call] = replaced
    [preview] = call["previews"]
    assert call["source"]["kind"] == "notice" and call["source"]["key"] == PERIOD_ID and call["today"] == TODAY
    assert preview["agency_id"] == "trpa-advisory" and preview["local_date"] == date(2026, 11, 12)
    assert preview["start_time"] == "09:30" and preview["location"] == "128 Market Street, Stateline, Nevada"
    assert preview["remote_quote"] == APC["remoteQuote"] and preview["subject"] == APC["subject"]
    assert preview["topics"] == ["biking", "environment"]

    stored = db.execute("SELECT * FROM notice_extractions").fetchone()
    assert stored["source_sha256"] == NOTICE["sha256"] and len(stored["rejected"]) == 1
    usage = db.execute("SELECT purpose,count(*) AS n FROM ai_usage GROUP BY purpose").fetchall()
    assert usage == [{"purpose": "notice-extraction", "n": 1}]

    with statement_count(monkeypatch) as counter:
        again = notices.extract_notices(db, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    assert again["periodsPending"] == 0 and haiku.calls == 1 and counter["n"] == 1 and len(replaced) == 1


def test_revised_notice_keeps_ids_and_marks_moved_and_missing_events(db, replaced):
    store_period(db, period_row())
    workshop = {**OPEN_HOUSE, "title": "Open House", "startTime": None, "endTime": None,
                "quote": "The meeting will be held in person on November 2, 2026, as follows:"}
    notices.extract_notices(db, clients={"claude-haiku-5-5": notices_client([OPEN_HOUSE, workshop, APC])}, today=TODAY)
    second_id = ONE_OFF_ID + "-2"
    assert db.execute("SELECT count(*) AS n FROM meetings WHERE id = ANY(%s)", ([ONE_OFF_ID, second_id],)).fetchone()["n"] == 2

    # The revision moves the open house to November 3 and drops the second event.
    revised_text = NOTICE["text"].replace("November 2, 2026, Open House", "November 3, 2026, Open House")
    store_period(db, period_row(source_text=revised_text, source_sha256="b" * 64))
    moved = {**OPEN_HOUSE, "date": "2026-11-03", "quote": OPEN_HOUSE["quote"].replace("November 2", "November 3")}
    haiku = notices_client([moved, APC])
    report = notices.extract_notices(db, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    assert report["periodsPending"] == 1 and report["withdrawn"] == 1 and haiku.calls == 1

    meeting = db.execute("SELECT * FROM meetings WHERE id=%s", (ONE_OFF_ID,)).fetchone()
    assert meeting["local_date"] == date(2026, 11, 3) and meeting["status"] == "rescheduled"
    assert meeting["change"] == "rescheduled"
    dropped = db.execute("SELECT * FROM meetings WHERE id=%s", (second_id,)).fetchone()
    assert dropped["warnings"] == ["Not in the latest notice."]
    assert db.execute("SELECT count(*) AS n FROM meetings WHERE agency_id='other'").fetchone()["n"] == 2  # nothing deleted
    assert replaced[-1]["source"]["sha256"] == "b" * 64


def test_dry_run_and_missing_credentials_make_no_calls(db, replaced, monkeypatch):
    store_period(db, period_row())
    report = notices.extract_notices(db, dry_run=True, today=TODAY)
    assert report["dryRun"] and report["periodsPending"] == 1 and report["estimatedCostUsd"] > 0
    monkeypatch.setattr(notices, "has_credentials", lambda: False)
    assert "skipped" in notices.extract_notices(db, today=TODAY)
    assert db.execute("SELECT count(*) AS n FROM notice_extractions").fetchone()["n"] == 0 and not replaced


def test_haiku_failure_falls_back_to_sonnet(db, replaced):
    store_period(db, period_row())
    bad = StubModel("claude-haiku-5-5", None)
    bad.answer = "not json"
    good = notices_client([OPEN_HOUSE], model="claude-sonnet-5-5")
    report = notices.extract_notices(db, clients={"claude-haiku-5-5": bad, "claude-sonnet-5-5": good}, today=TODAY)
    assert bad.calls == notices.ATTEMPTS_PER_MODEL and report["extracted"] == {"claude-sonnet-5-5": 1}
    assert not replaced  # no regular meeting now or before: no preview source to replace


def notices_client(events, model="claude-haiku-5-5"):
    return StubModel(model, {"events": copy.deepcopy(events)})
