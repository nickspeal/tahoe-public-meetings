"""Topic evaluation harness: sampling, prompts, comparison, and a stubbed Claude client (no network)."""
import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from tahoe_ingest import evaluate_topics as ev
from tahoe_ingest.catalog import topic_catalog
from tahoe_ingest.cli import main
from tahoe_ingest.core import ingest_bundle, init_db

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/meetings.json"
TOPIC_IDS = [topic["id"] for topic in topic_catalog()]


def item(item_id, agency="slt", meeting="slt:1", order=1, title="Item", **extra):
    return ev.AgendaItem(id=item_id, meeting_id=meeting, agency_id=agency, agency_name=agency.upper(),
                         meeting_title="Council meeting", meeting_date="2026-10-06", number=str(order),
                         order=order, title=title, section=extra.pop("section", None), **extra)


def arguments(*argv):
    parser = argparse.ArgumentParser()
    ev.add_arguments(parser)
    return parser.parse_args(list(argv))


# --- Sampling --------------------------------------------------------------

def corpus():
    rows = []
    for agency, count in (("slt", 30), ("ttd", 15), ("truckee", 3)):
        for n in range(count):
            rows.append(item(f"{agency}:m{n // 10}:{n}", agency, f"{agency}:m{n // 10}", n, f"{agency} item {n}"))
    return rows


def test_sample_is_stable_stratified_and_in_agenda_order():
    items = corpus()
    first = ev.sample_items(items, limit=12, seed=7)
    assert [i.id for i in first] == [i.id for i in ev.sample_items(items, limit=12, seed=7)]
    assert len(first) == 12
    counts = {agency: sum(i.agency_id == agency for i in first) for agency in ("slt", "ttd", "truckee")}
    assert counts["truckee"] >= 1 and counts["slt"] > counts["ttd"] > 0  # proportional, every agency present
    positions = {i.id: n for n, i in enumerate(items)}
    assert [positions[i.id] for i in first] == sorted(positions[i.id] for i in first)
    assert {i.id for i in ev.sample_items(items, limit=12, seed=8)} != {i.id for i in first}
    assert ev.sample_items(items) == items and ev.sample_items(items, limit=500) == items
    with pytest.raises(ValueError):
        ev.sample_items(items, limit=0)


def test_larger_limit_extends_each_agency_sample_instead_of_reshuffling():
    # Each agency's shuffle uses its own seed, so other agencies' data and a larger quota do not reorder it.
    items = corpus() + [item(f"truckee:extra:{n}", "truckee", "truckee:extra", n) for n in range(40)]
    small = {i.id for i in ev.sample_items(items, limit=10, seed=3) if i.agency_id == "slt"}
    large = {i.id for i in ev.sample_items(items, limit=40, seed=3) if i.agency_id == "slt"}
    assert small and small < large


# --- Agenda excerpts --------------------------------------------------------

def test_excerpt_is_bounded_to_the_item_block_and_may_continue_onto_the_next_page():
    page2 = ("Consent Agenda\n\n3.   Fiscal Year Appropriation Limit\n     Requested Action: Pass a Resolution.\n\n"
             "4.   Bike lane   striping contract\n     Requested Action: Award the contract\n")
    page3 = "     for Pioneer Trail.\n\n5.   Adjournment\n     Posted by the City Clerk.\n"
    pages = {("doc", 2): page2, ("doc", 3): page3}
    rows = [item("a", order=1, title="Consent Agenda", citations=[{"documentId": "doc", "page": 2, "quote": "Consent Agenda"}]),
            item("b", order=2, title="Fiscal Year Appropriation Limit",
                 citations=[{"documentId": "doc", "page": 2, "quote": "Fiscal Year Appropriation Limit"}]),
            item("c", order=3, title="Bike lane striping contract",
                 citations=[{"documentId": "doc", "page": 2, "quote": "Bike lane   striping contract"}]),
            item("d", order=4, title="Adjournment", citations=[{"documentId": "doc", "page": 3, "quote": "Adjournment"}]),
            item("e", order=5, title="No citation")]
    ev.attach_excerpts(rows, pages, max_chars=500)
    assert rows[0].excerpt == "Consent Agenda"  # trailing "3." enumerator removed
    assert rows[1].excerpt == "Fiscal Year Appropriation Limit Requested Action: Pass a Resolution."
    assert rows[2].excerpt == "Bike lane striping contract Requested Action: Award the contract for Pioneer Trail."
    assert rows[2].excerpt_pages == [2, 3]
    assert rows[3].excerpt == "Adjournment"  # trailing posting boilerplate excluded
    assert rows[4].excerpt == "" and rows[4].excerpt_pages == []
    ev.attach_excerpts(rows, pages, max_chars=30)
    assert rows[2].excerpt == "Bike lane striping contract …"


def test_overlapping_fuzzy_quotes_are_split_between_neighbors():
    page = "Regular-Unfinished Business\n\n11. Housing Amendments Summary\n    Requested Action: Provide direction.\n\n12. Next"
    rows = [item("h", order=1, title="Regular-Unfinished Business",
                 citations=[{"documentId": "d", "page": 1, "quote": "Regular-Unfinished Business"}]),
            item("i", order=2, title="Housing Amendments Summary",
                 citations=[{"documentId": "d", "page": 1, "quote": "Regular-Unfinished Business\n\n11. Housing Amendments"}]),
            item("j", order=3, title="Next", citations=[{"documentId": "d", "page": 1, "quote": "Next"}])]
    ev.attach_excerpts(rows, {("d", 1): page}, max_chars=500)
    assert rows[0].excerpt == "Regular-Unfinished Business"
    assert rows[1].excerpt == "11. Housing Amendments Summary Requested Action: Provide direction."


# --- Prompt construction ----------------------------------------------------

def test_system_prompt_renders_the_catalog_rubric_and_is_deterministic():
    system = ev.render_system_prompt()
    assert system == ev.render_system_prompt()  # stable prefix keeps the prompt cache valid
    for topic in topic_catalog():
        assert f"- {topic['id']} ({topic['name']}): {topic['description'].rstrip('.')}." in system
        for example in topic.get("examples", []):
            assert example in system
        for example in topic.get("judgmentExamples", []):
            verdict = "Tag" if example["tag"] else "Do not tag"
            assert f"  - {verdict}: {example['item']} ({example['why']})" in system
    assert "$topics" not in system and ", ".join(TOPIC_IDS) in system
    assert ev.PROMPT_TEMPLATE.is_file()
    with pytest.raises(ValueError):
        ev.render_system_prompt("A template without the rubric placeholder")
    schema = ev.response_schema()
    entry = schema["properties"]["items"]["items"]
    assert entry["properties"]["topics"]["items"]["enum"] == TOPIC_IDS
    assert entry["additionalProperties"] is False and schema["additionalProperties"] is False


def test_user_message_lists_items_with_refs_and_adds_excerpts_only_in_context_mode():
    rows = [item("slt:1:1", order=1, title="Roll Call", section="Opening"),
            item("slt:1:2", order=2, title="Complete Streets grant", section="Consent Agenda",
                 excerpt="Complete Streets grant Requested Action: Apply.", excerpt_pages=[4])]
    title_only, refs = ev.render_user_message(rows, "title-only")
    context, context_refs = ev.render_user_message(rows, "agenda-context")
    assert refs == context_refs == {"i1": "slt:1:1", "i2": "slt:1:2"}
    assert '<item ref="i2">' in title_only and "Title: Complete Streets grant" in title_only
    assert "Section: Consent Agenda" in title_only and "Agenda excerpt" not in title_only
    assert "Agenda excerpt (page 4): Complete Streets grant Requested Action: Apply." in context
    assert "Council meeting (2026-10-06)" in context
    requests = ev.build_requests(rows, ["title-only", "agenda-context"], "SYSTEM")
    assert [(r.meeting_id, r.mode) for r in requests] == [("slt:1", "title-only"), ("slt:1", "agenda-context")]
    assert requests[0].max_tokens == 4096 + 200 * 2
    with pytest.raises(ValueError):
        ev.render_user_message(rows, "full-packet")


# --- Comparison and scoring -------------------------------------------------

def results(mapping, errors=()):
    return {k: {"topics": v, "reason": "", "error": "failed" if k in errors else None} for k, v in mapping.items()}


def test_agreement_matrix_counts_kappa_and_disagreements():
    rules = results({"a": ["transit"], "b": [], "c": ["biking"], "d": [], "e": ["housing"]})
    model = results({"a": ["transit"], "b": ["transit"], "c": [], "d": [], "e": ["housing"]}, errors={"e"})
    comparison = ev.compare("rules", rules, "claude", model, TOPIC_IDS)
    assert comparison["items"] == 4  # errored item excluded
    assert comparison["disagreements"] == ["b", "c"] and comparison["exactMatch"] == 2
    assert comparison["topics"]["transit"] == {"both": 1, "onlyA": 0, "onlyB": 1, "neither": 2,
                                               "agreement": 0.75, "kappa": 0.5}
    assert comparison["topics"]["biking"]["onlyA"] == 1
    assert comparison["topics"]["housing"]["kappa"] is None  # all "neither": agreement is uninformative
    assert ev.topic_counts(model, TOPIC_IDS)["transit"] == 2


def test_gold_scoring_and_formats(tmp_path):
    review = {"items": [{"id": "a", "gold": ["transit"]}, {"id": "b", "gold": []},
                        {"id": "c", "gold": ["short-term-rentals"]}, {"id": "d", "gold": None}]}
    path = tmp_path / "gold.json"
    path.write_text(json.dumps(review))
    gold = ev.load_gold(path)
    assert gold == {"a": {"transit"}, "b": set(), "c": {"short-term-rentals", "housing"}}  # STR implies Housing
    path.write_text(json.dumps({"a": ["transit"]}))
    assert ev.load_gold(path) == {"a": {"transit"}}
    path.write_text(json.dumps({"a": ["cycling"]}))
    with pytest.raises(ValueError):
        ev.load_gold(path)
    predicted = results({"a": ["transit"], "b": ["transit"], "c": ["housing"]})
    score = ev.score_against_gold(predicted, gold, TOPIC_IDS)
    assert score["items"] == 3 and score["exactMatch"] == 1
    assert score["topics"]["transit"] == {"tp": 1, "fp": 1, "fn": 0, "precision": 0.5, "recall": 1.0, "f1": 0.667}
    assert score["topics"]["short-term-rentals"]["recall"] == 0.0
    assert score["micro"]["tp"] == 2 and score["micro"]["fn"] == 1


def test_review_file_preserves_existing_annotations(tmp_path):
    rows = [item("a", title="Transit plan"), item("b", title="Roll Call")]
    methods = {"rules": results({"a": ["transit"], "b": []}), "claude:title-only": results({"a": [], "b": []})}
    comparison = ev.compare("rules", methods["rules"], "claude:title-only", methods["claude:title-only"], TOPIC_IDS)
    review = ev.build_review(rows, methods, [comparison])
    assert [r["id"] for r in review["items"]] == ["a"] and review["items"][0]["gold"] is None
    json_path, md_path = ev.write_review(review, tmp_path)
    saved = json.loads(json_path.read_text())
    saved["items"][0]["gold"], saved["items"][0]["notes"] = ["transit"], "Plan is about bus service"
    json_path.write_text(json.dumps(saved))
    # A later run where the item no longer disagrees still keeps its label.
    rerun = ev.build_review(rows, methods, [{**comparison, "disagreements": ["b"]}])
    ev.write_review(rerun, tmp_path)
    kept = {r["id"]: r for r in json.loads(json_path.read_text())["items"]}
    assert kept["a"]["gold"] == ["transit"] and kept["a"]["notes"] == "Plan is about bus service"
    assert kept["b"]["gold"] is None
    markdown = md_path.read_text()
    assert "| rules | transit |" in markdown and 'Gold: ["transit"]' in markdown


# --- Cost model -------------------------------------------------------------

def test_cost_model_applies_batch_cache_and_long_context_rates():
    haiku = "claude-haiku-5-5"
    assert ev.cost_usd(haiku, uncached_input=100_000, output=0) == pytest.approx(0.01)
    assert ev.cost_usd(haiku, uncached_input=0, output=1_000_000, batch=True) == pytest.approx(0.25)
    assert ev.cost_usd(haiku, uncached_input=0, cache_read=100_000, output=0) == pytest.approx(0.001)
    assert ev.cost_usd(haiku, uncached_input=0, cache_write=100_000, output=0) == pytest.approx(0.0125)
    # Prompts over 100K tokens use the long-context rate card.
    assert ev.cost_usd(haiku, uncached_input=200_000, output=0) == pytest.approx(200_000 * 0.50 / 1_000_000)
    # Totals across many short requests stay at standard rates.
    assert ev.cost_usd(haiku, uncached_input=200_000, output=0, requests=20) == pytest.approx(200_000 * 0.10 / 1_000_000)
    usage = {"title-only": {"requests": 2, "items": 20, "sharedPrefixTokens": 800, "itemInputTokens": 600,
                            "outputTokens": 1000, "source": "estimate (chars/4)"}}
    projection = ev.project_costs(usage, items_per_meeting=10)["title-only"]
    row = projection["rows"][0]
    assert row["meetingsPerMonth"] == 22 and projection["inputTokensPerMeeting"] == 800 + 300
    assert row["batch"] == pytest.approx(row["standard"] / 2, abs=1e-4)
    assert row["cachedRubric"] < row["standard"] < row["coldCacheWrite"]
    assert row["claude-sonnet-5-5"] > row["standard"]


# --- Stubbed Claude client --------------------------------------------------

class StubMessages:
    def __init__(self, respond):
        self.calls, self.respond = [], respond

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.respond(kwargs)


def tagging_reply(kwargs, *, refuse_context=False):
    user = kwargs["messages"][0]["content"]
    if refuse_context and "Agenda excerpt" in user:
        return SimpleNamespace(content=[], stop_reason="refusal", model="claude-haiku-5-5",
                               usage=SimpleNamespace(input_tokens=50, output_tokens=0))
    entries = []
    for ref, body in re.findall(r'<item ref="(i\d+)">\n(.*?)</item>', user, re.S):
        title = re.search(r"Title: (.*)", body).group(1)
        topics = ["transit"] if re.search(r"transit|ridership|bus", body, re.I) else []
        if re.search(r"rental", title, re.I):
            topics.append("short-term-rentals")
        entries.append({"ref": ref, "topics": topics, "reason": "stub"})
    text = json.dumps({"items": entries})
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           stop_reason="end_turn", model="claude-haiku-5-5",
                           usage=SimpleNamespace(input_tokens=400, output_tokens=120,
                                                 cache_creation_input_tokens=0, cache_read_input_tokens=900))


def test_model_wrapper_sends_cached_rubric_and_structured_output():
    messages = StubMessages(tagging_reply)
    model = ev.AnthropicTopicModel(client=SimpleNamespace(messages=messages))
    request = ev.ClassificationRequest("slt:1", "title-only", "RUBRIC", '<item ref="i1">\nTitle: Vacation rental rules\n</item>',
                                       {"i1": "slt:1:1"})
    reply = model.complete(system=request.system, user=request.user, schema=ev.response_schema(), max_tokens=request.max_tokens)
    call = messages.calls[0]
    assert call["model"] == "claude-haiku-5-5" == ev.MODEL_ID
    assert call["system"] == [{"type": "text", "text": "RUBRIC", "cache_control": {"type": "ephemeral"}}]
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in call and "thinking" not in call  # Haiku 5.5 rejects non-default sampling
    parsed, problems = ev.parse_reply(reply, request, TOPIC_IDS)
    assert problems == [] and reply.usage["cache_read_input_tokens"] == 900
    assert parsed["slt:1:1"]["topics"] == ["housing", "short-term-rentals"]  # catalog order; STR implies Housing


def test_parse_reply_records_errors_for_refusals_and_unmatched_items():
    request = ev.ClassificationRequest("m", "title-only", "S", "U", {"1": "x", "2": "y"})
    refused = ev.ModelReply(text="", stop_reason="refusal", usage={}, model="m")
    parsed, problems = ev.parse_reply(refused, request, TOPIC_IDS)
    assert problems and all(r["error"] for r in parsed.values())
    unknown_topic = ev.ModelReply(text=json.dumps({"items": [{"ref": "1", "topics": ["transit", "parking"], "reason": "bus"},
                                                             {"ref": "2", "topics": [], "reason": ""}]}),
                                  stop_reason="end_turn", usage={}, model="m")
    parsed, problems = ev.parse_reply(unknown_topic, request, TOPIC_IDS)
    assert parsed["x"] == {"topics": ["transit"], "reason": "bus", "tahoeRelevant": True, "error": None}
    assert parsed["y"]["error"] is None and any("parking" in p for p in problems)
    # An echoed agenda number ("5.1") instead of a ref can shift entries, so the whole reply is rejected.
    shifted = ev.ModelReply(text=json.dumps({"items": [{"ref": "1", "topics": ["transit"], "reason": "bus"},
                                                       {"ref": "5.1", "topics": [], "reason": ""}]}),
                            stop_reason="end_turn", usage={}, model="m")
    parsed, problems = ev.parse_reply(shifted, request, TOPIC_IDS)
    assert all(r["error"] for r in parsed.values()) and any("misaligned refs" in p for p in problems)
    garbled = ev.ModelReply(text="not json", stop_reason="end_turn", usage={}, model="m")
    assert all(r["error"] for r in ev.parse_reply(garbled, request, TOPIC_IDS)[0].values())


def test_parse_reply_drops_topics_for_items_outside_tahoe():
    request = ev.ClassificationRequest("m", "agenda-context", "S", "U", {"1": "x", "2": "y"})
    reply = ev.ModelReply(text=json.dumps({"items": [
        {"ref": "1", "reason": "Death Valley discharge permit", "tahoeRelevant": False, "topics": ["environment"]},
        {"ref": "2", "reason": "Tahoe stormwater", "tahoeRelevant": True, "topics": ["environment"]}]}),
        stop_reason="end_turn", usage={}, model="m")
    parsed, problems = ev.parse_reply(reply, request, TOPIC_IDS)
    assert not problems
    assert parsed["x"]["topics"] == [] and parsed["y"]["topics"] == ["environment"]


def test_real_sdk_request_shape_and_error_mapping_offline():
    anthropic = pytest.importorskip("anthropic")  # installed by the optional "ai" extra
    import httpx2
    seen = []
    statuses = iter([200, 400, 401])

    def handler(request):
        seen.append(json.loads(request.content))
        status = next(statuses)
        if status != 200:
            return httpx2.Response(status, json={"type": "error", "error": {"type": "invalid_request_error", "message": "no"}})
        body = {"items": [{"ref": "1", "topics": ["transit"], "reason": "bus"}]}
        return httpx2.Response(200, headers={"request-id": "req_test"}, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": ev.MODEL_ID, "stop_reason": "end_turn",
            "stop_sequence": None, "content": [{"type": "thinking", "thinking": "", "signature": "s"},
                                               {"type": "text", "text": json.dumps(body)}],
            "usage": {"input_tokens": 12, "output_tokens": 7, "cache_creation_input_tokens": 900, "cache_read_input_tokens": 0}})

    client = anthropic.Anthropic(api_key="offline-test", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    model = ev.AnthropicTopicModel(client=client)
    model._sdk = anthropic  # enable SDK error mapping for an injected client
    kwargs = {"system": "RUBRIC", "user": "U", "schema": ev.response_schema(), "max_tokens": 500}
    reply = model.complete(**kwargs)
    assert reply.request_id == "req_test" and reply.usage["cache_creation_input_tokens"] == 900
    assert seen[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert seen[0]["output_config"]["format"]["schema"] == ev.response_schema()
    with pytest.raises(ev.ModelError) as rejected:
        model.complete(**kwargs)
    assert not rejected.value.fatal
    with pytest.raises(ev.ModelError) as unauthorized:
        model.complete(**kwargs)
    assert unauthorized.value.fatal


def test_core_worker_does_not_import_the_optional_sdk():
    code = "import sys, tahoe_ingest.cli, tahoe_ingest.evaluate_topics; assert 'anthropic' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


# --- Database-backed runs (temporary schema) --------------------------------

@pytest.fixture
def db_url():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_evaluate_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}"
    try:
        with psycopg.connect(scoped, row_factory=psycopg.rows.dict_row) as conn:
            init_db(conn)
            bundle = json.loads(FIXTURE.read_text())
            bundle["meetings"] = [m for m in bundle["meetings"] if (m["agency"], m["id"]) in {("ttd", "7110"), ("slt", "2339")}]
            assert ingest_bundle(conn, bundle)["failed"] == 0
        yield scoped
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_load_items_reads_agenda_page_excerpts(db_url):
    with psycopg.connect(db_url, row_factory=psycopg.rows.dict_row) as conn:
        items = ev.load_items(conn)
        assert {i.agency_id for i in items} == {"ttd", "slt"} and len(items) == 16 + 28
        assert all(i.excerpt for i in items)
        consent = next(i for i in items if i.id == "ttd:7110:IV.D")
        assert "Fare Free Transit" in consent.excerpt and consent.excerpt_pages == [2]
        assert [i.id for i in ev.load_items(conn, agency="ttd")] == [i.id for i in items if i.agency_id == "ttd"]


def test_dry_run_cli_is_read_only_and_reports_rules_and_costs(db_url, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    out = tmp_path / "results.json"
    assert main(["--database-url", db_url, "evaluate-topics", "--limit", "20", "--out", str(out),
                 "--review-dir", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "Live comparison skipped: ANTHROPIC_API_KEY is not set." in printed
    assert "--- system ---" in printed and "--- user [agenda-context] ---" in printed
    assert "Monthly cost projection for claude-haiku-5-5" in printed
    report = json.loads(out.read_text())
    assert report["config"]["live"] is False and report["sample"]["items"] == 20
    assert set(report["sample"]["byAgency"]) == {"ttd", "slt"}
    assert set(report["topicCounts"]) == {"rules", "rules-on-agenda-context"}
    assert all(row["methods"]["rules"]["error"] is None for row in report["items"])
    assert {row["meetingsPerMonth"] for row in report["costProjection"]["agenda-context"]["rows"]} == {22, 100}
    assert (tmp_path / "disagreements.json").exists() and (tmp_path / "disagreements.md").exists()


def test_live_run_with_stub_client_compares_both_modes(db_url, tmp_path):
    messages = StubMessages(tagging_reply)
    client = ev.AnthropicTopicModel(client=SimpleNamespace(messages=messages))
    args = arguments("--agency", "ttd", "--out", str(tmp_path / "r.json"), "--review-dir", str(tmp_path))
    with psycopg.connect(db_url, row_factory=psycopg.rows.dict_row) as conn:
        report = ev.run(conn, args, model_client=client)
    assert len(messages.calls) == 2  # one TTD meeting x two modes
    assert report["config"]["live"] is True
    pairs = [(c["a"], c["b"]) for c in report["comparisons"]]
    assert ("rules", "claude:title-only") in pairs and ("claude:title-only", "claude:agenda-context") in pairs
    context = next(c for c in report["comparisons"] if c["b"] == "claude:agenda-context" and c["a"] == "rules")
    assert context["items"] == 16
    actual = report["usage"]["actual"]["title-only"]
    assert actual["requests"] == 1 and actual["cache_read_input_tokens"] == 900
    assert report["usage"]["actualCostUsd"]["title-only"] == pytest.approx((400 * 0.10 + 900 * 0.01 + 120 * 0.50) / 1e6)
    assert report["costProjection"]["title-only"]["source"] == "actual API usage"
    assert report["costProjection"]["title-only"]["sharedPrefixTokens"] == 900
    by_id = {row["id"]: row for row in report["items"]}
    assert "transit" in by_id["ttd:7110:IV.D"]["methods"]["claude:agenda-context"]["topics"]


def test_live_run_keeps_going_after_a_refusal_and_respects_the_cost_cap(db_url, tmp_path):
    client = ev.AnthropicTopicModel(client=SimpleNamespace(messages=StubMessages(
        lambda kwargs: tagging_reply(kwargs, refuse_context=True))))
    args = arguments("--agency", "slt", "--review-dir", str(tmp_path))
    with psycopg.connect(db_url, row_factory=psycopg.rows.dict_row) as conn:
        report = ev.run(conn, args, model_client=client)
        refused = next(c for c in report["comparisons"] if c["b"] == "claude:agenda-context" and c["a"] == "rules")
        assert refused["items"] == 0
        context_usage = report["usage"]["actual"]["agenda-context"]
        assert context_usage["retries"] == context_usage["requests"]  # each refused request was retried once
        assert next(c for c in report["comparisons"] if c["b"] == "claude:title-only")["items"] == 28
        with pytest.raises(SystemExit, match="exceeds --max-cost"):
            ev.run(conn, arguments("--max-cost", "0", "--review-dir", str(tmp_path)), model_client=client)


def test_live_client_times_out_instead_of_waiting_ten_minutes(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    created = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            raise anthropic.APITimeoutError(request=None)

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(ev, "uses_gcp_federation", lambda: False)
    model = ev.AnthropicTopicModel()
    assert created["timeout"].read == ev.REQUEST_TIMEOUT_SECONDS
    assert created["timeout"].connect == ev.CONNECT_TIMEOUT_SECONDS
    with pytest.raises(ev.ModelError, match="no reply within 180 seconds") as raised:
        model.complete(system="s", user="u", schema={}, max_tokens=10)
    assert not raised.value.fatal
