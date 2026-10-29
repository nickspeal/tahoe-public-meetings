"""Staff report links and summaries end to end against PostgreSQL: storage, export, fallback, and reprocessing (no network)."""
import contextlib
import copy
import json
import os
import re
import time
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest import item_summaries, staff_reports
from tahoe_ingest.core import edit_item, export_public, ingest_bundle, init_db
from tahoe_ingest.evaluate_topics import ModelReply
from tahoe_ingest.export_check import check as check_export

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "tests/fixtures/meetings.json"
SLT = json.loads((ROOT / "tests/fixtures/staff-reports/slt.json").read_text())
TODAY = date(2026, 10, 1)


@pytest.fixture
def conn():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_summaries_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}"
    try:
        with psycopg.connect(scoped, row_factory=psycopg.rows.dict_row) as connection:
            init_db(connection)
            assert ingest_bundle(connection, slt_bundle())["failed"] == 0
            connection.commit()
            yield connection
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def slt_bundle():
    bundle = json.loads(BUNDLE.read_text())
    bundle["meetings"] = [m for m in bundle["meetings"] if (m["agency"], m["id"]) == ("slt", "2339")]
    return bundle


def reader(url, *, last_page=None, links=False):
    found = copy.deepcopy(SLT["downloads"].get(url))
    if found is None:  # Executive summaries the fixture leaves out
        raise staff_reports.Uncertain("not in fixture")
    found["pages"] = [p for p in found["pages"] if not last_page or p["page_number"] <= last_page]
    return found


BACKGROUND = {  # A sentence from each executive summary beyond its requested action
    "5": "The Finance Department requires specialized, as-needed support for complex governmental accounting and financial reporting matters.",
    "9": "The proposed agreement amount is within the approved project budget of $100,000.00.",
}
ACTIONS = {  # The requested action on each executive summary, trimmed
    "5": "Pass a Motion authorizing and directing the Mayor to execute a Professional Services Agreement with Eide Bailly Advisory LLC "
         "for Governmental Accounting Technical and Financial Reporting Consulting Services for an initial one-year term in an "
         "amount not to exceed $73,200.",
    "9": "Pass a Motion authorizing and directing the Mayor to execute a Professional Services Agreement with Zenith Economics for "
         "the City’s Economic Development Market Analysis and Business Attraction Strategy in an amount not to exceed $80,100.00.",
}


class StubModel:
    """Answers from the item's own page, or with a paraphrase when ``paraphrase`` is set."""

    def __init__(self, model, paraphrase=False):
        self.model, self.paraphrase, self.calls = model, paraphrase, 0

    def complete(self, *, system, user, schema, max_tokens):
        self.calls += 1
        number = re.search(r"^Agenda item (\S+):", user, re.M).group(1)
        if number in ACTIONS and not self.paraphrase:
            # The requested action again (dropped: the agenda's leads the summary), then background.
            answer = {"decision": "summarize", "skipReason": None,
                      "sentences": [{"page": 1, "source": ACTIONS[number], "sentence": ACTIONS[number]},
                                    {"page": 1, "source": BACKGROUND[number], "sentence": BACKGROUND[number]}]}
        elif number in ACTIONS:
            answer = {"decision": "summarize", "skipReason": None,
                      "sentences": [{"page": 1, "source": ACTIONS[number], "sentence": "The city wants to hire a consultant."}]}
        else:
            answer = {"decision": "skip", "skipReason": "nothing to summarize", "sentences": []}
        usage = {"input_tokens": 1500, "output_tokens": 400, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        return ModelReply(text=json.dumps(answer), stop_reason="end_turn", usage=usage, model=self.model)


def exported_action(conn, number):
    from tahoe_ingest.requested_actions import for_meeting
    rows = conn.execute("SELECT id,number,citations FROM agenda_items WHERE meeting_id='slt:2339' AND active").fetchall()
    pages = {(r["document_version_id"], r["page_number"]): r["text"] for r in conn.execute("SELECT * FROM document_pages")}
    return for_meeting(rows, pages)[next(r["id"] for r in rows if r["number"] == number)]["text"]


def items(conn):
    meeting = next(m for m in export_public(conn)["meetings"] if m["id"] == "slt:2339")
    return {i["number"]: i for i in meeting["items"]}


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


def test_links_and_summaries_publish_and_survive_reprocessing(conn, monkeypatch):
    before = items(conn)
    with statement_count(monkeypatch) as mapping:
        report = staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)
    assert report["itemsMapped"] == 6 and report["meetingsScanned"] == 1 and report["itemsWithAttachments"] >= 6
    assert mapping["n"] <= 5  # items, documents, scans, one stored-pages read, one write
    exported = items(conn)
    assert exported["5"]["staffReport"]["url"].endswith("Item_5-_Executive_Summary.pdf")
    assert exported["5"]["staffReport"]["page"] == 1 and exported["5"]["staffReport"]["pages"] == []
    assert "staffReport" not in exported["2"]  # minutes: procedural
    assert [f["label"] for f in exported["7"]["attachments"]] == [
        "Executive Summary", "Staff report: Paradise Ave Abandonment", "Resolution: Paradise Ave Abandonment", "Attachment 1: Abandonment Map"]
    assert exported["5"]["summary"] == before["5"]["summary"]  # links alone leave the excerpt in place

    haiku = StubModel("claude-haiku-5-5")
    with statement_count(monkeypatch) as summarizing:
        result = item_summaries.summarize_items(conn, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    assert result["published"] == {"claude-haiku-5-5": 2} and result["itemsSkipped"] == 4 and result["costUsd"] > 0
    assert summarizing["n"] <= 6  # selection, month-to-date spend, one write for the meeting
    exported = items(conn)
    summary = exported["9"]["summary"]
    # The agenda's requested action leads, cited to the agenda page; the report adds what the agenda does not say.
    assert summary["method"] == item_summaries.METHOD and summary["text"] == exported_action(conn, "9") + " " + BACKGROUND["9"]
    assert summary["citations"][0]["documentId"].startswith("slt:2339:slt-agenda") and summary["citations"][0]["page"] == 4
    assert summary["citations"][1]["documentId"] == exported["9"]["staffReport"]["id"]
    assert [p["number"] for p in exported["9"]["staffReport"]["pages"]] == [1]
    assert exported["11"]["summary"] == before["11"]["summary"]  # skipped by the model: excerpt stays
    errors, _ = check_export(export_public(conn))
    assert errors == []
    usage = conn.execute("SELECT purpose,ok FROM ai_usage").fetchall()
    assert len(usage) == 6 and {u["purpose"] for u in usage} == {item_summaries.PURPOSE}
    assert conn.execute("SELECT count(*) AS n FROM summary_versions WHERE method=%s", (item_summaries.METHOD,)).fetchone()["n"] == 2

    # Nothing changed, so nothing is sent again.
    again = item_summaries.summarize_items(conn, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    assert again["itemsPending"] == 0 and haiku.calls == 6
    assert staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)["meetingsPending"] == 0

    # An operator correction replaces the displayed text and survives a forced new summary.
    edit_item(conn, "slt:2339:9", correction=("Corrected summary.", "wording", "operator"))
    item_summaries.summarize_items(conn, force=True, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    assert items(conn)["9"]["summary"]["text"] == "Corrected summary."

    # A revised agenda item invalidates its link and summary until the next run maps and summarizes it again.
    revised = slt_bundle()
    item = next(i for i in revised["meetings"][0]["agenda_items"] if i["source_number"] == "5")
    item["title"] += " (Revised)"
    assert ingest_bundle(conn, revised)["failed"] == 0
    exported = items(conn)
    assert "staffReport" not in exported["5"] and exported["5"]["summary"]["method"] == "agenda-requested-action-v1"
    assert exported["9"]["summary"]["text"] == "Corrected summary."  # untouched items keep theirs
    assert staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)["meetingsPending"] == 1


def test_a_report_that_is_not_a_pdf_exports_its_media_type(conn, monkeypatch):
    import dataclasses
    rule = staff_reports.RULES["slt"]
    monkeypatch.setitem(staff_reports.RULES, "slt",
                        lambda ctx: [dataclasses.replace(m, media_type="text/html") if m.url.endswith("Item_5-_Executive_Summary.pdf")
                                     else m for m in rule(ctx)])
    staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)
    exported = items(conn)
    assert exported["5"]["staffReport"]["mediaType"] == "text/html" and exported["9"]["staffReport"]["mediaType"] == "application/pdf"
    assert check_export(export_public(conn))[0] == []


def test_a_replaced_staff_report_falls_back_to_the_excerpt(conn):
    staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)
    item_summaries.summarize_items(conn, clients={"claude-haiku-5-5": StubModel("claude-haiku-5-5")}, today=TODAY)
    assert items(conn)["9"]["summary"]["method"] == item_summaries.METHOD
    conn.execute("UPDATE item_staff_reports SET sha256=%s WHERE item_id='slt:2339:9'", ("f" * 64,))
    assert items(conn)["9"]["summary"]["method"] == "agenda-requested-action-v1"  # the agenda's own words remain
    assert item_summaries.summarize_items(conn, dry_run=True, today=TODAY)["itemsPending"] == 1


def test_paraphrases_never_publish_and_are_not_retried_every_run(conn):
    staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)
    before = items(conn)
    haiku, sonnet = StubModel("claude-haiku-5-5", paraphrase=True), StubModel("claude-sonnet-5-5", paraphrase=True)
    clients = {"claude-haiku-5-5": haiku, "claude-sonnet-5-5": sonnet}
    result = item_summaries.summarize_items(conn, clients=clients, today=TODAY)
    assert result["validationFailed"] == 2 and not result["published"]
    assert haiku.calls == 2 * 2 + 4 and sonnet.calls == 2  # Haiku twice per failing item, Sonnet once; the rest skip
    assert items(conn)["9"]["summary"] == before["9"]["summary"]
    failed = conn.execute("SELECT status,error FROM ai_item_summaries WHERE item_id='slt:2339:9'").fetchone()
    assert failed["status"] == "validation_failed" and "adds words" in failed["error"]
    assert item_summaries.summarize_items(conn, clients=clients, today=TODAY)["itemsPending"] == 0


def test_a_new_validator_retries_only_the_items_that_failed_validation(conn, monkeypatch):
    staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)
    clients = {"claude-haiku-5-5": StubModel("claude-haiku-5-5", paraphrase=True),
               "claude-sonnet-5-5": StubModel("claude-sonnet-5-5", paraphrase=True)}
    item_summaries.summarize_items(conn, clients=clients, today=TODAY)
    failed = {r["item_id"] for r in conn.execute("SELECT item_id FROM ai_item_summaries WHERE status='validation_failed'")}
    assert failed and {r["validator_version"] for r in conn.execute("SELECT validator_version FROM ai_item_summaries")} == {1}
    monkeypatch.setattr(item_summaries, "VALIDATOR_VERSION", 2)
    pending = item_summaries.summarize_items(conn, dry_run=True, today=TODAY)
    assert pending["itemsPending"] == len(failed) and pending["itemsPendingByReason"] == {"validator": len(failed)}
    item_summaries.summarize_items(conn, clients=clients, today=TODAY)
    assert item_summaries.summarize_items(conn, dry_run=True, today=TODAY)["itemsPending"] == 0  # tried once, not every run


def test_pending_items_say_why(conn):
    staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)
    assert item_summaries.summarize_items(conn, dry_run=True, today=TODAY)["itemsPendingByReason"] == {"new": 6}
    item_summaries.summarize_items(conn, clients={"claude-haiku-5-5": StubModel("claude-haiku-5-5")}, today=TODAY)
    conn.execute("UPDATE item_staff_reports SET sha256=%s WHERE item_id='slt:2339:9'", ("f" * 64,))
    conn.execute("UPDATE item_staff_reports SET title='Renamed report' WHERE item_id='slt:2339:5'")
    conn.execute("UPDATE ai_item_summaries SET prompt_sha256='old',input_sha256='old' WHERE item_id='slt:2339:11'")
    reasons = item_summaries.summarize_items(conn, dry_run=True, today=TODAY)["itemsPendingByReason"]
    assert reasons == {"report": 1, "other": 1, "prompt": 1}
    forced = item_summaries.summarize_items(conn, dry_run=True, force=True, today=TODAY)["itemsPendingByReason"]
    assert forced == {"report": 1, "other": 1, "prompt": 1, "forced": 3}


def test_run_cost_limit_defers_items(conn):
    staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)
    result = item_summaries.summarize_items(conn, max_cost=0.0001, dry_run=True, today=TODAY)
    assert result["itemsPending"] == 6 and result["itemsSelected"] < 6 and result["deferred"] >= 1


def test_time_budget_stops_before_the_next_item_and_keeps_finished_ones(conn, monkeypatch):
    staff_reports.map_staff_reports(conn, reader=reader, delay=0, today=TODAY)
    haiku = StubModel("claude-haiku-5-5")
    clients = {"claude-haiku-5-5": haiku}
    stored = lambda: conn.execute("SELECT count(*) AS n FROM ai_item_summaries").fetchone()["n"]

    expired = item_summaries.summarize_items(conn, clients=clients, today=TODAY, deadline=time.monotonic() - 1)
    assert haiku.calls == 0 and stored() == 0 and expired["costUsd"] == 0
    assert expired["timeBudgetReached"] and expired["itemsDeferred"] == expired["itemsSelected"] == 6
    assert expired["stoppedEarly"] == "time budget reached; remaining items are summarized on the next run"

    # The budget runs out after two items: both are stored, the other four wait for the next run.
    monkeypatch.setattr(item_summaries, "out_of_time", lambda deadline: deadline is not None and haiku.calls >= 2)
    partial = item_summaries.summarize_items(conn, clients=clients, today=TODAY, deadline=0.0)
    assert haiku.calls == 2 and stored() == 2 and partial["itemsDeferred"] == 4 and partial["timeBudgetReached"]

    rest = item_summaries.summarize_items(conn, clients=clients, today=TODAY)
    assert rest["itemsPending"] == 4 and haiku.calls == 6 and stored() == 6 and "stoppedEarly" not in rest
