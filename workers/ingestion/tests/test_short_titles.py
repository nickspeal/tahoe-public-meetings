"""Short headlines for long agenda titles (decision 0035): the validator, caching by agenda text, overrides, and the export."""
import contextlib
import json
import os
import time
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest import short_titles
from tahoe_ingest.core import export_public, ingest_bundle, init_db
from tahoe_ingest.evaluate_topics import ModelReply
from tahoe_ingest.export_check import check as check_export
from tahoe_ingest.short_titles import Rejected, validate

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "tests/fixtures/meetings.json"
TODAY = date(2026, 10, 1)

HEARING = ("HEARING - Planning and Building Department recommending the Board deny the appeal of the Planning "
           "Commission’s denial of Tentative Parcel Map P25-0004 for a three-lot subdivision on Lake Tahoe Boulevard, "
           "in an amount not to exceed $2,200,007.50. (No discussion among Commissioners will take place on this item.)")
BUDGET = ("Chief Administrative Office recommending the Board: 1) Receive a presentation and provide direction on "
          "recommended changes to the County of El Dorado Budget for Fiscal Year (FY) 2026-27; 2) Direct staff to return "
          "on September 29, 2026, with a Budget Resolution, Budget Amendment, and corresponding Personnel Allocation "
          "Changes Resolution based on discussion and direction received during this item.")


# --- Validator (no database) -----------------------------------------------------

@pytest.mark.parametrize("headline", [
    "Hearing to deny appeal of Planning Commission denial of three-lot subdivision parcel map",
    "Deny appeal of subdivision denial on Lake Tahoe Boulevard for $2,200,007.50",  # the amount as printed
    "Presentation and direction on recommended changes to FY 2026-27 budget",
    "Receives presentation on recommended budget changes for Fiscal Year 2026-27",  # inflections of source words
    "Direction on the County of El Dorado’s budget for FY 2026-27",  # possessive, connecting words
])
def test_faithful_headlines_pass(headline):
    source = HEARING if "deny" in headline.lower() or "denial" in headline.lower() else BUDGET
    assert validate(headline, source) == headline[0].upper() + headline[1:]


@pytest.mark.parametrize("headline,kind", [
    ("County leaders weigh new budget plan for FY 2026-27", "adds"),            # words the agenda never uses
    ("Hearing on $2.2 million subdivision appeal and denial", "number"),         # a rounded amount
    ("Review budget changes and return on September 30, 2026", "number"),       # a date not printed
    ("Hearing on appeal of Planning Commission denial of subdivision map", "negation"),  # drops "deny"
    ("Hearing to approve appeal of subdivision denial", "adds"),               # "approve" is not in the hearing text
    ("Budget", "length"),
    ("Receive a presentation and provide direction on recommended changes to the County of El Dorado Budget for "
     "Fiscal Year 2026-27 and return", "length"),
])
def test_unfaithful_headlines_are_rejected(headline, kind):
    source = HEARING if any(w in headline.lower() for w in ("hearing", "subdivision")) else BUDGET
    with pytest.raises(Rejected) as rejected:
        validate(headline, source)
    assert rejected.value.kind == kind


def test_benign_negations_and_procedural_notes_need_not_be_kept():
    # "not to exceed" limits nothing, and the parenthetical "(No discussion ...)" is a procedural note.
    source = ("Approve an agreement with CCY Architects for the Snowflake Lodge Replacement Project in the amount of "
              "$811,332, and authorize change orders not to exceed $81,000. (No discussion will take place.) "
              "Case No. 1234.")
    assert validate("Approve agreement with CCY Architects for Snowflake Lodge Replacement Project", source)


def test_a_negation_in_a_clause_the_headline_leaves_out_need_not_be_kept():
    # El Dorado's delegation clause, a routine list of options, and a limit on something the headline does not name.
    source = ("Health and Human Services Agency recommending the Board: 1) Approve and authorize the Chair to sign Amendment I "
              "to Agreement for Services 9109 with Alice Rodriguez, increasing the amount of the Agreement by $71,284, with no "
              "change to the term; 2) Authorize the Purchasing Agent to execute amendments relating to Agreement 9109 which do "
              "not increase the maximum dollar amount or term of the Agreement; and 3) Find that specialty skills are not "
              "expressly identified in County classifications. The Board may approve, approve with conditions, or deny.")
    assert validate("Approve Amendment I to Agreement 9109 with Alice Rodriguez, increasing the amount by $71,284", source)


def test_a_real_negation_must_be_kept():
    source = ("Discussion and possible action to not renew the agreement with the Tahoe Douglas Visitors Authority "
              "for marketing services for Fiscal Year 2026-27, and direct staff to issue a request for proposals.")
    with pytest.raises(Rejected) as rejected:
        validate("Renew agreement with Tahoe Douglas Visitors Authority for marketing services", source)
    assert rejected.value.kind == "negation"
    assert validate("Not renew agreement with Tahoe Douglas Visitors Authority for marketing services", source)


def test_only_long_titles_qualify_and_the_whole_text_is_the_source():
    short = "Approve the minutes of the October 6 meeting"
    assert not short_titles.qualifies(short)
    cut = "Recommendation to approve an agreement with the Regional Transportation Commission (RTC) for"
    assert not short_titles.qualifies(cut) and short_titles.qualifies(cut, cut + " " + "x" * 200)
    assert short_titles.source_text(cut, "  whole\n text  ") == "whole text"


# --- Database: caching and export ------------------------------------------------

@pytest.fixture
def conn():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_short_titles_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with psycopg.connect(url, options=f"-c search_path={schema}", row_factory=psycopg.rows.dict_row) as connection:
            init_db(connection)
            assert ingest_bundle(connection, bundle())["failed"] == 0
            connection.commit()
            yield connection
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def bundle():
    data = json.loads(BUNDLE.read_text())
    data["meetings"] = [m for m in data["meetings"] if (m["agency"], m["id"]) == ("slt", "2307")]
    return data


class StubModel:
    """Answers with the first eight words of the agenda text, which always validates."""

    def __init__(self, model="claude-haiku-5-5"):
        self.model, self.calls = model, 0

    def complete(self, *, system, user, schema, max_tokens):
        self.calls += 1
        text = user.split("<agenda_text>\n", 1)[1].split("\n</agenda_text>", 1)[0]
        answer = {"decision": "shorten", "skipReason": None, "title": " ".join(text.split()[:8]).rstrip(",;:")}
        usage = {"input_tokens": 900, "output_tokens": 120, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        return ModelReply(text=json.dumps(answer), stop_reason="end_turn", usage=usage, model=self.model)


class NoCallModel:
    def complete(self, **kwargs):
        raise AssertionError("no request expected")


NO_CALLS = {"claude-haiku-5-5": NoCallModel(), "claude-sonnet-5-5": NoCallModel()}


def exported(conn):
    meeting = next(m for m in export_public(conn)["meetings"] if m["id"] == "slt:2307")
    return meeting["items"]


def long_items(conn):
    return [i for i in exported(conn) if short_titles.qualifies(i["title"], i.get("fullTitle"))]


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


def test_headlines_publish_once_and_export_beside_the_whole_title(conn, monkeypatch):
    before = long_items(conn)
    assert len(before) >= 4 and not any("shortTitle" in i for i in exported(conn))
    haiku = StubModel()
    with statement_count(monkeypatch) as writes:
        report = short_titles.shorten_titles(conn, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    assert report["published"] == {"claude-haiku-5-5": haiku.calls} and haiku.calls == len({i["title"] for i in before})
    assert writes["n"] <= 6  # stored ids, the item read, month-to-date spend, and one write for the meeting
    usage = conn.execute("SELECT count(*) AS n, min(purpose) AS purpose FROM ai_usage").fetchone()
    assert usage["n"] == haiku.calls and usage["purpose"] == "short-title"
    items = exported(conn)
    for item in items:
        if short_titles.qualifies(item["title"], item.get("fullTitle")):
            assert item["shortTitle"] and len(item["shortTitle"]) < len(item["title"])
        else:
            assert "shortTitle" not in item  # short titles are left alone
    assert check_export(export_public(conn))[0] == []
    # Nothing the model sees has changed: no request.
    again = short_titles.shorten_titles(conn, clients=NO_CALLS, today=TODAY)
    assert again["titlesPending"] == 0 and again["costUsd"] == 0


def test_an_unchanged_agenda_reposted_as_a_new_pdf_keeps_its_headlines_without_a_request(conn):
    short_titles.shorten_titles(conn, clients={"claude-haiku-5-5": StubModel()}, today=TODAY)
    before = {i["number"]: i["shortTitle"] for i in exported(conn) if "shortTitle" in i}
    reposted = bundle()
    agenda = next(d for d in reposted["meetings"][0]["documents"] if d["id"] == "slt-agenda")
    agenda["sha256"] = "0" * 64
    assert ingest_bundle(conn, reposted)["failed"] == 0
    items = exported(conn)
    assert items[0]["citations"][0]["documentId"].endswith("0" * 16)  # a new document version and fingerprints
    assert {i["number"]: i["shortTitle"] for i in items if "shortTitle" in i} == before  # still shown, no rebind
    assert short_titles.shorten_titles(conn, clients=NO_CALLS, today=TODAY)["titlesPending"] == 0


def test_a_revised_title_hides_its_headline_until_the_next_run(conn):
    short_titles.shorten_titles(conn, clients={"claude-haiku-5-5": StubModel()}, today=TODAY)
    target = long_items(conn)[0]
    conn.execute("UPDATE agenda_items SET title=title || ' (revised)' WHERE id=%s", (target["id"],))
    conn.commit()
    assert "shortTitle" not in next(i for i in exported(conn) if i["id"] == target["id"])
    haiku = StubModel()
    report = short_titles.shorten_titles(conn, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    assert report["titlesPending"] == 1 and report["titlesPendingByReason"] == {"new": 1} and haiku.calls == 1
    assert next(i for i in exported(conn) if i["id"] == target["id"])["shortTitle"]


def test_the_same_long_title_at_another_item_is_shortened_once(conn):
    first, second = long_items(conn)[:2]
    conn.execute("UPDATE agenda_items SET title=%s WHERE id=%s", (first["title"], second["id"]))
    conn.commit()
    haiku = StubModel()
    short_titles.shorten_titles(conn, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    items = {i["id"]: i for i in exported(conn)}
    assert items[first["id"]]["shortTitle"] == items[second["id"]]["shortTitle"]
    assert haiku.calls == len({i["title"] for i in long_items(conn)})


class Unfaithful:
    """Haiku adds a word twice; Sonnet gets it right."""

    def __init__(self, model, good):
        self.model, self.good, self.calls, self.users = model, good, 0, []

    def complete(self, *, system, user, schema, max_tokens):
        self.calls += 1
        self.users.append(user)
        text = user.split("<agenda_text>\n", 1)[1].split("\n</agenda_text>", 1)[0]
        title = " ".join(text.split()[:6]) if self.good else "Controversial plan " + " ".join(text.split()[:5])
        usage = {"input_tokens": 900, "output_tokens": 120, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        return ModelReply(text=json.dumps({"decision": "shorten", "skipReason": None, "title": title}),
                          stop_reason="end_turn", usage=usage, model=self.model)


def test_a_rejected_headline_is_retried_with_the_reason_then_falls_back_to_sonnet(conn):
    haiku, sonnet = Unfaithful("claude-haiku-5-5", False), Unfaithful("claude-sonnet-5-5", True)
    report = short_titles.shorten_titles(conn, clients={"claude-haiku-5-5": haiku, "claude-sonnet-5-5": sonnet},
                                         max_items=1, today=TODAY)
    assert haiku.calls == 2 and sonnet.calls == 1 and report["published"] == {"claude-sonnet-5-5": 1}
    assert "words not in the agenda text: controversial, plan" in haiku.users[1]
    assert report["failures"] == {"validation: adds": 2}
    errors = [r["error"] for r in conn.execute("SELECT error FROM ai_usage WHERE NOT ok ORDER BY id")]
    assert len(errors) == 2 and all(e.startswith("validation: words not in") for e in errors)


def test_failures_are_not_retried_until_the_validator_changes(conn, monkeypatch):
    bad = {"claude-haiku-5-5": Unfaithful("claude-haiku-5-5", False), "claude-sonnet-5-5": Unfaithful("claude-sonnet-5-5", False)}
    report = short_titles.shorten_titles(conn, clients=bad, max_items=1, today=TODAY)
    assert report["validationFailed"] == 1
    assert short_titles.shorten_titles(conn, clients=NO_CALLS, dry_run=True, today=TODAY)["titlesPendingByReason"].get("validator") is None
    monkeypatch.setattr(short_titles, "VALIDATOR_VERSION", short_titles.VALIDATOR_VERSION + 1)
    assert short_titles.shorten_titles(conn, dry_run=True, today=TODAY)["titlesPendingByReason"]["validator"] == 1


def test_cost_and_time_limits_defer_titles(conn, monkeypatch):
    dry = short_titles.shorten_titles(conn, dry_run=True, max_cost=0.00001, today=TODAY)
    assert dry["titlesSelected"] == 0 and dry["deferred"] == dry["titlesPending"] >= 4
    expired = short_titles.shorten_titles(conn, clients=NO_CALLS, today=TODAY, deadline=time.monotonic() - 1)
    assert expired["timeBudgetReached"] and expired["titlesDeferred"] == expired["titlesSelected"]
    assert conn.execute("SELECT count(*) AS n FROM ai_short_titles").fetchone()["n"] == 0


# --- Operator overrides ------------------------------------------------------------

def test_the_operator_can_suppress_replace_and_clear_a_headline(conn):
    short_titles.shorten_titles(conn, clients={"claude-haiku-5-5": StubModel()}, today=TODAY)
    target = long_items(conn)[0]
    claude = next(i for i in exported(conn) if i["id"] == target["id"])["shortTitle"]

    result = short_titles.set_override(conn, target["id"], suppress=True, reason="misreads the appeal", author="operator")
    assert result["override"] == "suppressed"
    item = next(i for i in exported(conn) if i["id"] == target["id"])
    assert "shortTitle" not in item and item["title"] == target["title"]  # the item's own title (and fullTitle) stay
    assert check_export(export_public(conn))[0] == []

    short_titles.set_override(conn, target["id"], text="  Hearing on the  subdivision appeal ", reason="clearer", author="operator")
    assert next(i for i in exported(conn) if i["id"] == target["id"])["shortTitle"] == "Hearing on the subdivision appeal"
    stored = conn.execute("SELECT short_title FROM editorial WHERE item_id=%s", (target["id"],)).fetchone()["short_title"]
    assert stored["reason"] == "clearer" and stored["author"] == "operator" and stored["text"] == "Hearing on the subdivision appeal"
    assert "reason" not in json.dumps(next(i for i in exported(conn) if i["id"] == target["id"]))  # nothing attributed publicly

    short_titles.set_override(conn, target["id"], clear=True)
    assert next(i for i in exported(conn) if i["id"] == target["id"])["shortTitle"] == claude
    # Overridden texts cost no request; cleared ones are already cached.
    assert short_titles.shorten_titles(conn, clients=NO_CALLS, today=TODAY)["titlesPending"] == 0


def test_an_override_follows_the_agenda_text_not_the_item(conn):
    first, second = long_items(conn)[:2]
    conn.execute("UPDATE agenda_items SET title=%s WHERE id=%s", (first["title"], second["id"]))
    conn.commit()
    short_titles.set_override(conn, first["id"], suppress=True, reason="misleading", author="operator")
    # No request for the suppressed text, though it was never shortened.
    haiku = StubModel()
    short_titles.shorten_titles(conn, clients={"claude-haiku-5-5": haiku}, today=TODAY)
    assert haiku.calls == len({i["title"] for i in long_items(conn)}) - 1
    items = {i["id"]: i for i in exported(conn)}
    assert "shortTitle" not in items[first["id"]] and "shortTitle" not in items[second["id"]]
    # A replacement made on the other item supersedes it: one override per text.
    short_titles.set_override(conn, second["id"], text="Short and right", reason="clearer", author="operator")
    items = {i["id"]: i for i in exported(conn)}
    assert items[first["id"]]["shortTitle"] == items[second["id"]]["shortTitle"] == "Short and right"
    assert conn.execute("SELECT count(*) AS n FROM editorial WHERE short_title IS NOT NULL").fetchone()["n"] == 1
    # A revised title leaves the override behind and is shortened on the next run.
    conn.execute("UPDATE agenda_items SET title=title || ' (revised)' WHERE id=%s", (first["id"],))
    conn.commit()
    assert "shortTitle" not in {i["id"]: i for i in exported(conn)}[first["id"]]
    assert short_titles.shorten_titles(conn, clients={"claude-haiku-5-5": StubModel()}, today=TODAY)["titlesPendingByReason"] == {"new": 1}


def test_overrides_are_refused_without_a_reason_or_for_short_titles(conn):
    target = long_items(conn)[0]
    short = next(i for i in exported(conn) if not short_titles.qualifies(i["title"], i.get("fullTitle")))
    with pytest.raises(ValueError, match="reason and an author"):
        short_titles.set_override(conn, target["id"], suppress=True)
    with pytest.raises(ValueError, match="gets no short title"):
        short_titles.set_override(conn, short["id"], suppress=True, reason="r", author="operator")
    with pytest.raises(ValueError, match="1 to 120 characters"):
        short_titles.set_override(conn, target["id"], text="x" * 121, reason="r", author="operator")
    with pytest.raises(ValueError, match="exactly one"):
        short_titles.set_override(conn, target["id"], text="A", suppress=True, reason="r", author="operator")
    with pytest.raises(ValueError, match="unknown item"):
        short_titles.set_override(conn, "nope", suppress=True, reason="r", author="operator")


def test_short_title_command_arguments_and_the_scheduled_default():
    from tahoe_ingest import cli
    args = cli._parser().parse_args(["short-title", "slt:2307:1", "--suppress", "--reason", "r", "--author", "operator"])
    assert args.suppress and not args.clear and args.text is None
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["short-title", "slt:2307:1", "--suppress", "--clear"])
    assert cli._parser().parse_args(["run-scheduled"]).short_title_max_cost == short_titles.DEFAULT_MAX_RUN_COST == 0.05
