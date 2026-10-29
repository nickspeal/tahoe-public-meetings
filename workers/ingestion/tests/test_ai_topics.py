"""Production Claude topic classification: fallback chain, storage, export precedence, and spend limits (no network)."""
import json
import os
import re
import time
import uuid
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest import ai_topics
from tahoe_ingest.core import export_public, ingest_bundle, init_db
from tahoe_ingest.evaluate_topics import ModelReply

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/meetings.json"


@pytest.fixture
def conn():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_ai_topics_" + uuid.uuid4().hex
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
    """Answers like Claude: misaligned refs when ``misalign`` is set, otherwise transit for fare items."""

    def __init__(self, model, *, misalign=False):
        self.model, self.misalign, self.calls = model, misalign, 0

    def complete(self, *, system, user, schema, max_tokens):
        self.calls += 1
        entries = []
        for ref, body in re.findall(r'<item ref="(i\d+)">\n(.*?)</item>', user, re.S):
            fare = bool(re.search(r"fare free", body, re.I))
            entries.append({"ref": "5.1" if self.misalign and not entries else ref, "reason": "stub",
                            "tahoeRelevant": "Roll Call" not in body, "topics": ["transit"] if fare else ["environment"]})
        usage = {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        return ModelReply(text=json.dumps({"items": entries}), stop_reason="end_turn", usage=usage, model=self.model)


def exported_topics(conn):
    return {i["id"]: set(i["topics"]) for m in export_public(conn)["meetings"] for i in m["items"]}


def test_haiku_failure_falls_back_to_sonnet_and_export_prefers_current_ai_tags(conn):
    rules = exported_topics(conn)
    haiku, sonnet = StubModel("claude-haiku-5-5", misalign=True), StubModel("claude-sonnet-5-5")
    clients = {"claude-haiku-5-5": haiku, "claude-sonnet-5-5": sonnet}
    report = ai_topics.classify_topics(conn, clients=clients)
    assert (haiku.calls, sonnet.calls) == (2, 1)  # one retry, then the fallback model
    assert report["classified"] == {"claude-sonnet-5-5": 16} and report["failedMeetings"] == []
    usage = list(conn.execute("SELECT model, ok FROM ai_usage ORDER BY id"))
    assert [(u["model"], u["ok"]) for u in usage] == [("claude-haiku-5-5", False)] * 2 + [("claude-sonnet-5-5", True)]
    assert report["costUsd"] == pytest.approx(2 * (1000 * 0.10 + 200 * 0.50) / 1e6 + (1000 * 2.00 + 200 * 10.00) / 1e6)

    topics = exported_topics(conn)
    assert topics["ttd:7110:IV.D"] == {"transit"}
    roll_call = conn.execute("SELECT id FROM agenda_items WHERE title ILIKE 'Roll Call%'").fetchone()["id"]
    assert topics[roll_call] == set()  # outside Tahoe in the stub, so no topics
    assert topics != rules

    # Unchanged items are not sent again.
    again = ai_topics.classify_topics(conn, clients=clients)
    assert again["meetingsPending"] == 0 and (haiku.calls, sonnet.calls) == (2, 1)

    # A revised item shows its rule tags until it is classified again.
    conn.execute("UPDATE agenda_items SET source_fingerprint='revised' WHERE id='ttd:7110:IV.D'")
    assert exported_topics(conn)["ttd:7110:IV.D"] == rules["ttd:7110:IV.D"]


def test_failed_meetings_keep_rule_tags(conn):
    rules = exported_topics(conn)
    clients = {m: StubModel(m, misalign=True) for m in ("claude-haiku-5-5", "claude-sonnet-5-5")}
    report = ai_topics.classify_topics(conn, clients=clients)
    assert report["failedMeetings"] == ["ttd:7110"] and report["classified"] == {}
    assert exported_topics(conn) == rules


def test_spend_limits_and_missing_key(conn, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    dry = ai_topics.classify_topics(conn, dry_run=True)
    assert dry["meetingsPending"] == 1 and dry["itemsPending"] == 16 and dry["estimatedCostUsd"] > 0
    assert "no Claude credentials" in ai_topics.classify_topics(conn)["skipped"]
    clients = {m: StubModel(m) for m in ("claude-haiku-5-5", "claude-sonnet-5-5")}
    with pytest.raises(SystemExit, match="exceeds --max-cost"):
        ai_topics.classify_topics(conn, clients=clients, max_cost=0)
    conn.execute("INSERT INTO ai_usage(purpose,model,ok,cost_usd) VALUES ('topic-classification','claude-haiku-5-5',true,5)")
    with pytest.raises(SystemExit, match="reached the \\$5.00 budget"):
        ai_topics.classify_topics(conn, clients=clients)


def test_storing_a_meetings_classifications_costs_one_statement_whatever_its_size(conn, monkeypatch):
    # On hosted Postgres each statement is a billed operation (decision 0020); a prompt change reclassifies every item.
    counter = {"n": 0}
    original = psycopg.Cursor.execute

    def execute(self, *args, **kwargs):
        counter["n"] += 1
        return original(self, *args, **kwargs)
    monkeypatch.setattr(psycopg.Cursor, "execute", execute)
    clients = {m: StubModel(m) for m in ("claude-haiku-5-5", "claude-sonnet-5-5")}
    report = ai_topics.classify_topics(conn, clients=clients)
    assert report["classified"] == {"claude-haiku-5-5": 16}
    assert counter["n"] == 6  # 4 reads, then per meeting one usage row and one statement for all its items
    stored = conn.execute("""SELECT count(*) AS n FROM ai_topic_classifications a
        JOIN agenda_items i ON i.id=a.item_id AND i.source_fingerprint=a.source_fingerprint""").fetchone()["n"]
    assert stored == 16 and exported_topics(conn)["ttd:7110:IV.D"] == {"transit"}


def test_a_passed_deadline_stops_before_the_first_meeting_and_none_means_no_budget(conn):
    rules = exported_topics(conn)
    clients = {m: StubModel(m) for m in ("claude-haiku-5-5", "claude-sonnet-5-5")}
    report = ai_topics.classify_topics(conn, clients=clients, deadline=time.monotonic() - 1)
    assert sum(c.calls for c in clients.values()) == 0 and report["classified"] == {}
    assert report["timeBudgetReached"] and report["meetingsDeferred"] == 1
    assert report["stoppedEarly"] == "time budget reached; remaining meetings keep rule tags and are classified on the next run"
    assert exported_topics(conn) == rules and not conn.execute("SELECT 1 FROM ai_usage").fetchone()
    # A deadline still ahead, or none at all, classifies as before.
    ahead = ai_topics.classify_topics(conn, clients=clients, deadline=time.monotonic() + 3600)
    assert ahead["classified"] == {"claude-haiku-5-5": 16} and "stoppedEarly" not in ahead
    assert not ai_topics.out_of_time(None) and ai_topics.out_of_time(time.monotonic() - 1)
