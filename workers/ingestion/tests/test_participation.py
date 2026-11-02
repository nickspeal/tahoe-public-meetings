"""Participation details read from agendas: source validation, storage, export precedence, and staleness (no network)."""
import json
import os
import time
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest import participation
from tahoe_ingest.core import export_public, ingest_bundle, init_db
from tahoe_ingest.evaluate_topics import ModelReply

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/meetings.json"

SOURCE = """Tuesday, September 22, 2026     9:00 AM     https://edcgov-us.zoom.us/j/
88991557512
                330 Fair Lane, Building A
                Placerville, CA
PUBLIC PARTICIPATION INFORMATION
Zoom: Join the meeting at https://us06web.zoom.us/j/88991557512
By Phone: Call 530-621-7603 or 530-621-7610 - Press *9 to indicate your desire to comment.
Written Comments: Please email your comments to edc.cob@edcgov.us by 4:00 PM the day before the meeting.
Alternative formats ... available upon request to the Clerk at 530-621-5390."""


def reply(**fields):
    base = {name: None for name in participation.FIELDS}
    base.update(fields)
    return base


def test_validation_keeps_only_fields_the_source_supports():
    kept, rejected = participation.validate(reply(
        location="330 Fair Lane, Building A, Placerville, CA",
        remoteUrl="https://edcgov-us.zoom.us/j/88991557512",  # broken across PDF lines in the source
        watchUrl="https://www.youtube.com/@edcgov",  # invented
        phone="530-621-7603 or 530-621-7610",
        commentEmail="edc.cob@edcgov.us",
        instructions="Join by Zoom and raise your hand, call 530-621-7603 and press *9, or email edc.cob@edcgov.us.",
        instructionsQuote="Press *9 to indicate your desire to comment.",
        deadline="by 4:00 PM the day before the meeting",
    ), SOURCE)
    assert kept["remoteUrl"] == "https://edcgov-us.zoom.us/j/88991557512"
    assert kept["location"] == "330 Fair Lane, Building A, Placerville, CA"
    assert {"phone", "commentEmail", "instructions", "deadline"} <= set(kept)
    assert [r["field"] for r in rejected] == ["watchUrl"]


@pytest.mark.parametrize("field,value", [
    ("phone", "530-621-9999"),
    ("commentEmail", "clerk@edcgov.us"),
    ("deadline", "by 5:00 PM the day before the meeting"),
    ("location", "330 Fair Lane, Building C, Placerville, CA"),
    ("remoteUrl", "Click here to join the Zoom meeting"),
])
def test_validation_rejects_values_not_in_the_source(field, value):
    kept, rejected = participation.validate(reply(**{field: value}), SOURCE)
    assert field not in kept and rejected[0]["field"] == field


def test_instructions_need_a_quote_and_only_source_numbers():
    kept, rejected = participation.validate(reply(instructions="Email edc.cob@edcgov.us."), SOURCE)
    assert "instructions" not in kept and rejected[0]["reason"] == "no supporting quote in the agenda text"
    kept, rejected = participation.validate(reply(instructions="Call 530-555-0100.", instructionsQuote="By Phone: Call 530-621-7603"), SOURCE)
    assert "instructions" not in kept and rejected[0]["field"] == "instructions"


def test_bare_domain_links_get_a_scheme():
    kept, _ = participation.validate(reply(remoteUrl="trpa.gov/public-meeting-zoom"), "Join at trpa.gov/public-meeting-zoom.")
    assert kept["remoteUrl"] == "https://trpa.gov/public-meeting-zoom"


def test_merge_prefers_source_then_agenda_then_agency():
    location, merged = participation.merged(None, {"url": "https://agency.example/participate"},
                                            {"location": "Town Hall", "commentUrl": "https://form.example", "deadline": "noon"},
                                            {"url": "https://agency.example/form", "watchUrl": "https://video.example", "sourceUrl": "x"})
    assert location == "Town Hall" and merged["url"] == "https://agency.example/participate"
    assert merged["watchUrl"] == "https://video.example" and merged["deadline"] == "noon" and merged["fromAgenda"]
    _, merged = participation.merged("Chambers", {}, None, None)
    assert merged["fromAgenda"] is False


@pytest.fixture
def conn():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_participation_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}"
    try:
        with psycopg.connect(scoped, row_factory=psycopg.rows.dict_row) as connection:
            init_db(connection)
            bundle = json.loads(FIXTURE.read_text())
            bundle["meetings"] = [m for m in bundle["meetings"] if (m["agency"], m["id"]) == ("ttd", "7110")]
            assert ingest_bundle(connection, bundle)["failed"] == 0
            connection.commit()
            yield connection
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


class StubModel:
    def __init__(self, model, answer):
        self.model, self.answer, self.calls = model, answer, 0

    def complete(self, *, system, user, schema, max_tokens):
        self.calls += 1
        usage = {"input_tokens": 3000, "output_tokens": 300, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        return ModelReply(text=json.dumps(self.answer), stop_reason="end_turn", usage=usage, model=self.model)


def ttd(conn):
    return next(m for m in export_public(conn)["meetings"] if m["id"] == "ttd:7110")


def test_extraction_is_stored_exported_and_not_repeated(conn):
    before = ttd(conn)
    answer = reply(location="Somewhere else", remoteUrl="https://us06web.zoom.us/webinar/register/WN_x4BFkKUvRe-QKhqeAZBCiw",
                   commentEmail="lrao@tahoetransportation.org", watchUrl="https://youtube.com/invented")
    haiku = StubModel("claude-haiku-5-5", answer)
    report = participation.extract_participation(conn, clients={"claude-haiku-5-5": haiku}, today=date(2026, 10, 1))
    assert report["extracted"] == {"claude-haiku-5-5": 1} and report["fieldsRejected"] == 2 and report["costUsd"] > 0
    after = ttd(conn)
    assert after["location"] == before["location"]  # the source adapter's value wins
    assert after["participation"]["remoteUrl"] == answer["remoteUrl"]
    assert after["participation"]["email"] == "lrao@tahoetransportation.org"
    assert before["participation"]["url"] == answer["remoteUrl"]  # the source feed files the Zoom link as the comment URL
    assert after["participation"]["url"] is None  # a join link is never the comment link
    assert after["participation"]["watchUrl"] is None and after["participation"]["fromAgenda"]
    usage = conn.execute("SELECT purpose,ok FROM ai_usage").fetchall()
    assert [(u["purpose"], u["ok"]) for u in usage] == [("participation-extraction", True)]

    again = participation.extract_participation(conn, clients={"claude-haiku-5-5": haiku}, today=date(2026, 10, 1))
    assert again["meetingsPending"] == 0 and haiku.calls == 1

    # A replaced agenda hides the old extraction until the next run reads the new one.
    conn.execute("UPDATE ai_participation SET version_ids='[\"gone\"]'")
    assert ttd(conn)["participation"]["remoteUrl"] is None


def test_malformed_replies_fall_back_to_sonnet(conn):
    bad = StubModel("claude-haiku-5-5", {"unexpected": True})
    good = StubModel("claude-sonnet-5-5", reply(commentEmail="lrao@tahoetransportation.org"))
    report = participation.extract_participation(conn, clients={"claude-haiku-5-5": bad, "claude-sonnet-5-5": good},
                                                 today=date(2026, 10, 1))
    assert bad.calls == participation.ATTEMPTS_PER_MODEL and report["extracted"] == {"claude-sonnet-5-5": 1}


def test_past_meetings_are_not_pending(conn):
    report = participation.extract_participation(conn, dry_run=True, today=date(2027, 1, 1))
    assert report["meetingsPending"] == 0


def test_a_passed_deadline_stops_before_the_first_meeting(conn):
    haiku = StubModel("claude-haiku-5-5", reply())
    report = participation.extract_participation(conn, clients={"claude-haiku-5-5": haiku}, today=date(2026, 10, 1),
                                                 deadline=time.monotonic() - 1)
    assert haiku.calls == 0 and report["timeBudgetReached"] and report["meetingsDeferred"] == 1
    assert report["stoppedEarly"] == "time budget reached; remaining meetings are extracted on the next run"
    again = participation.extract_participation(conn, clients={"claude-haiku-5-5": haiku}, today=date(2026, 10, 1))
    assert haiku.calls == 1 and again["extracted"] == {"claude-haiku-5-5": 1}


# --- Ordering, join-link normalization, and remote details without a link (text from the production export) ----------

def test_pending_order_is_upcoming_soonest_then_undated_then_past_recent_first():
    today = date(2026, 11, 2)
    meetings = {"a": date(2026, 10, 30), "b": date(2026, 12, 1), "c": None, "d": date(2026, 11, 3), "e": date(2026, 11, 2), "f": date(2026, 10, 21)}
    ordered = sorted(meetings, key=lambda mid: participation._priority(meetings[mid], today, mid))
    assert ordered == ["e", "d", "b", "c", "a", "f"]


TTD_PAGE = """Tahoe Regional Planning Agency                                                       October 7, 2026
                    To attend the TTD Board Meeting remotely, use the following link:
              https://us06web.zoom.us/webinar/register/WN_x4BFkKUvRe-QKhqeAZBCiw

 email at lrao@tahoetransportation.org. Please note which agenda item the comment pertains to."""
TTD_LINK = "https://us06web.zoom.us/webinar/register/WN_x4BFkKUvRe-QKhqeAZBCiw"


def test_a_join_link_in_the_comment_url_moves_to_the_remote_url():
    kept, rejected = participation.validate(reply(commentUrl=TTD_LINK, commentEmail="lrao@tahoetransportation.org"), TTD_PAGE)
    assert kept == {"remoteUrl": TTD_LINK, "commentEmail": "lrao@tahoetransportation.org"} and not rejected
    kept, _ = participation.validate(reply(commentUrl=TTD_LINK, remoteUrl=TTD_LINK), TTD_PAGE)
    assert kept == {"remoteUrl": TTD_LINK}
    form = "Submit comments at https://forms.example.gov/comment"
    kept, _ = participation.validate(reply(commentUrl="https://forms.example.gov/comment"), form)
    assert kept == {"commentUrl": "https://forms.example.gov/comment"}


@pytest.mark.parametrize("url,expected", [
    ("https://us06web.zoom.us/j/88991557512", True), (TTD_LINK, True), ("https://zoom.us/j/1?pwd=x", True),
    ("https://teams.microsoft.com/l/meetup-join/19%3ameeting", True), ("https://zoom.us/", False),
    ("https://forms.example.gov/comment", False), (None, False)])
def test_join_url_detection(url, expected):
    assert participation.is_join_url(url) is expected


def test_export_merge_files_a_join_link_as_the_remote_url_whoever_supplied_it():
    # The TTD feed names the Zoom link its participation URL (Oct 7, 2026 export), and no extraction had run.
    _, merged = participation.merged("TRPA", {"url": TTD_LINK}, None, None)
    assert merged["remoteUrl"] == TTD_LINK and merged["url"] is None
    _, merged = participation.merged("TRPA", {"url": TTD_LINK}, {"commentUrl": "https://form.example/c"}, None)
    assert merged["remoteUrl"] == TTD_LINK and merged["url"] == "https://form.example/c"
    _, merged = participation.merged("TRPA", {"remoteUrl": "https://zoom.us/j/9"}, None, {"url": TTD_LINK})
    assert merged["remoteUrl"] == "https://zoom.us/j/9" and merged["url"] is None


NDEP_PAGE = """                                     Join the Meeting virtually at:

                                          Microsoft Teams:
                                    Meeting ID: 217 707 950 084 88
                                         Passcode: hN2VS7fz

                                    Call in by Phone (audio only)
                                    United States: +1 (775) 321-6111
                                    Meeting extension: 610 223 794#
"""


def test_teams_meeting_id_and_passcode_survive_in_the_instructions():
    answer = reply(instructions="Join Microsoft Teams with Meeting ID 217 707 950 084 88 and Passcode hN2VS7fz.",
                   instructionsQuote="Microsoft Teams: Meeting ID: 217 707 950 084 88 Passcode: hN2VS7fz")
    kept, rejected = participation.validate(answer, NDEP_PAGE)
    assert kept["instructions"] == answer["instructions"] and not rejected
    answer["instructions"] = "Join Microsoft Teams with Meeting ID 217 707 950 084 88 and Passcode zzzzzzzz."
    kept, rejected = participation.validate(answer, NDEP_PAGE)
    assert "instructions" not in kept and rejected[0]["reason"] == "mentions a passcode not in the agenda text"
    assert participation.response_schema()["required"] == list(participation.FIELDS)


SSTMA_PAGE = """Board of Directors Agenda
                                       Friday, September 18, 2026
                                             8:30-10:00 am
              In-person option: Lisa Maloff University Center | Lake Tahoe Community College
                             1 College Drive
                             South Lake Tahoe, CA 96150
              Remote option:        Zoom Link
"""


def test_remote_option_without_a_readable_link_is_accepted_and_the_prompt_forbids_blame():
    answer = reply(instructions="The agenda offers a Zoom remote option; the link is on the official agenda.",
                   instructionsQuote="Remote option: Zoom Link")
    kept, rejected = participation.validate(answer, SSTMA_PAGE)
    assert kept["instructions"] == answer["instructions"] and not rejected
    assert "remoteUrl" not in kept
    prompt = participation.PROMPT.read_text()
    assert 'Remote option: Zoom Link' in prompt and "the link is on the official agenda" in prompt
    assert "Never say or imply that the agency or the agenda failed to give" in prompt


class _FakeConn:
    def execute(self, *args, **kwargs):
        return self

    def commit(self):
        pass


def _run_with_fake_backlog(monkeypatch, max_cost, count=3):
    docs = {f"m{i}": [{"id": f"v{i}", "title": "Agenda", "agency_name": "A", "meeting_title": "T", "local_date": None}] for i in range(count)}
    monkeypatch.setattr(participation, "_pending", lambda *a, **k: docs)
    monkeypatch.setattr(participation, "_pages", lambda conn, pending: {f"v{i}": [(1, "Zoom: https://zoom.us/j/123456789")] for i in range(count)})
    monkeypatch.setattr(participation, "month_to_date_spend", lambda conn: 0.0)
    monkeypatch.setattr(participation, "_store", lambda *a, **k: None)
    monkeypatch.setattr(participation, "_record_usage", lambda *a, **k: 0.001)
    haiku = StubModel("claude-haiku-5-5", reply())
    report = participation.extract_participation(_FakeConn(), clients={"claude-haiku-5-5": haiku}, max_cost=max_cost, today=date(2026, 10, 1))
    return report, haiku


def test_an_estimate_over_the_cost_limit_processes_the_largest_fitting_prefix(monkeypatch):
    _, one = _run_with_fake_backlog(monkeypatch, 10.0, count=1)
    assert one.calls == 1
    per_meeting = participation.cost_usd(participation.CHAIN[0][0], uncached_input=1, output=participation.EST_OUTPUT_TOKENS)
    report, haiku = _run_with_fake_backlog(monkeypatch, 0.02, count=3)
    assert haiku.calls == 3 and "meetingsDeferredForCost" not in report  # fits entirely
    # Find a limit between one and two meetings' estimates.
    one_cost = report["estimatedCostUsd"] / 3
    report, haiku = _run_with_fake_backlog(monkeypatch, one_cost * 1.5, count=3)
    assert haiku.calls == 1 and report["meetingsDeferredForCost"] == 2
    assert report["stoppedEarly"].startswith("cost limit reached") and per_meeting > 0
    with pytest.raises(SystemExit):
        _run_with_fake_backlog(monkeypatch, one_cost / 2, count=3)
