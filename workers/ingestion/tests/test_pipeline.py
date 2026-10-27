import copy
import contextlib
import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest.core import edit_item, export_public, ingest_bundle, init_db, reclassify_topics
from tahoe_ingest.export_check import check as check_export
from tahoe_ingest.topics import CLASSIFIER_VERSION, classify
from tahoe_ingest.catalog import public_topics, topic_ids


FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/meetings.json"
TOPIC_EXPECTATIONS = FIXTURE.with_name("topic-expectations.json")


@pytest.fixture
def db():
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL or DATABASE_URL is required")
    schema = "test_ingest_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    try:
        with psycopg.connect(url, options=f"-c search_path={schema}") as connection:
            # Match the worker's dict-row connection helper.
            connection.row_factory = psycopg.rows.dict_row
            init_db(connection)
            yield connection
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def fixture_bundle():
    return json.loads(FIXTURE.read_text())


def one_meeting(agency="ttd"):
    source = fixture_bundle()
    preferred = {"ttd": "7110", "slt": "2339", "truckee": "1895"}[agency]
    source["meetings"] = [next(m for m in source["meetings"] if m["agency"] == agency and m["id"] == preferred)]
    return source


def test_replay_is_idempotent_and_public_export_valid(db):
    bundle = fixture_bundle()
    assert ingest_bundle(db, bundle) == {"processed": len(bundle["meetings"]), "failed": 0, "errors": []}
    first = export_public(db)
    count = db.execute("SELECT count(*) AS n FROM document_versions").fetchone()["n"]
    times = {r["item_id"]: r["generated_at"] for r in db.execute("SELECT item_id,generated_at FROM summaries")}
    assert ingest_bundle(db, bundle)["failed"] == 0
    assert db.execute("SELECT count(*) AS n FROM document_versions").fetchone()["n"] == count
    assert {r["item_id"]: r["generated_at"] for r in db.execute("SELECT item_id,generated_at FROM summaries")} == times
    assert len(first["meetings"]) == len(bundle["meetings"])
    assert {a["id"] for a in first["agencies"]} == {"ttd", "slt", "truckee"}
    for meeting in first["meetings"]:
        for item in meeting["items"]:
            if item["summary"] and item["summary"]["status"] == "published":
                assert item["summary"]["method"] == "deterministic-source-excerpt-v1"


def test_revision_regenerates_summary_and_preserves_editorial(db):
    original = one_meeting()
    assert ingest_bundle(db, original)["failed"] == 0
    item_id = export_public(db)["meetings"][0]["items"][0]["id"]
    edit_item(db, item_id, correction=("Corrected context", "Clerical error", "operator"))
    edit_item(db, item_id, topic=("biking", True, "operator"))
    revised = copy.deepcopy(original)
    meeting = revised["meetings"][0]
    meeting["documents"][0]["sha256"] = "a" * 64
    meeting["pages"][0]["text"] += "\nRevised agenda text."
    assert ingest_bundle(db, revised)["failed"] == 0
    assert db.execute("SELECT count(*) AS n FROM document_versions").fetchone()["n"] == 2
    item = next(i for i in export_public(db)["meetings"][0]["items"] if i["id"] == item_id)
    assert item["change"] == "revised"
    assert item["summary"]["status"] == "published"
    assert item["summary"]["citations"][0]["documentId"].endswith("a" * 16)
    assert item["summary"]["text"] == "Corrected context"
    assert "correction" not in item and "perspective" not in item
    assert "humanReviewed" not in item["summary"]
    assert "biking" in item["topics"]


def test_failed_meeting_keeps_previous_publication_and_marks_source_error(db):
    original = one_meeting()
    assert ingest_bundle(db, original)["failed"] == 0
    before = export_public(db)["meetings"][0]
    broken = copy.deepcopy(original)
    broken["meetings"][0]["documents"][0]["sha256"] = "invalid"
    result = ingest_bundle(db, broken)
    assert result["failed"] == 1
    after = export_public(db)
    assert after["meetings"][0]["documents"] == before["documents"]
    assert after["meetings"][0]["items"] == before["items"]
    assert next(a for a in after["agencies"] if a["id"] == "ttd")["status"] == "error"
    assert db.execute("SELECT count(*) AS n FROM processing_runs WHERE status='failed'").fetchone()["n"] == 1


def test_invalid_quote_blocks_summary_but_keeps_logistics(db):
    bundle = one_meeting()
    item = bundle["meetings"][0]["agenda_items"][0]
    item["source_pages"][0]["quote"] = "This sentence does not occur in the PDF"
    assert ingest_bundle(db, bundle)["failed"] == 0
    meeting = export_public(db)["meetings"][0]
    assert meeting["officialUrl"]
    first = meeting["items"][0]
    assert first["summary"]["status"] == "validation_failed"
    assert first["summary"]["text"] == ""
    assert first["citations"] == []


def test_document_refresh_without_reparse_invalidates_affected_summary(db):
    original = one_meeting()
    assert ingest_bundle(db, original)["failed"] == 0
    revised = copy.deepcopy(original)
    m = revised["meetings"][0]
    m["documents"][0]["sha256"] = "b" * 64
    m["agenda_items"] = []
    m["items_complete"] = False
    assert ingest_bundle(db, revised)["failed"] == 0
    first = export_public(db)["meetings"][0]["items"][0]
    assert first["summary"]["status"] == "pending"
    assert first["citations"] == []
    assert first["change"] == "revised"


def test_partial_capture_with_new_document_id_preserves_logistics_and_editorial(db):
    original = one_meeting("truckee")
    assert ingest_bundle(db, original)["failed"] == 0
    existing = export_public(db)["meetings"][0]
    item_id = existing["items"][0]["id"]
    fresh = copy.deepcopy(original)
    row = fresh["meetings"][0]
    row["documents"][0]["id"] = "truckee-new-agenda-url"
    for page in row["pages"]:
        page["document_id"] = "truckee-new-agenda-url"
    row["agenda_items"] = []
    row["items_complete"] = False
    row["documents_complete"] = True
    row["partial_logistics"] = True
    row["status"] = "unknown"
    row["date"] = None
    row["start_time"] = None
    row["location"] = None
    row["participation_url"] = None
    assert ingest_bundle(db, fresh, mode="live")["failed"] == 0
    after = export_public(db, mode="live")["meetings"][0]
    assert after["startsAt"] == existing["startsAt"]
    assert after["status"] == existing["status"]
    assert after["location"] == existing["location"]
    assert after["participation"] == existing["participation"]
    assert after["items"][0]["summary"]["status"] == "pending"


def test_full_revision_withdraws_missing_item_without_erasing_edit(db):
    original = one_meeting()
    assert ingest_bundle(db, original)["failed"] == 0
    item_id = export_public(db)["meetings"][0]["items"][-1]["id"]
    edit_item(db, item_id, correction=("Kept context", "Clerical error", "operator"))
    revised = copy.deepcopy(original)
    revised["meetings"][0]["agenda_items"].pop()
    assert ingest_bundle(db, revised)["failed"] == 0
    withdrawn_at = export_public(db)["meetings"][0]["updatedAt"]
    assert item_id not in {i["id"] for i in export_public(db)["meetings"][0]["items"]}
    assert db.execute("SELECT correction IS NOT NULL AS kept FROM editorial WHERE item_id=%s", (item_id,)).fetchone()["kept"]
    assert ingest_bundle(db, revised)["failed"] == 0
    assert export_public(db)["meetings"][0]["updatedAt"] == withdrawn_at
    assert ingest_bundle(db, original)["failed"] == 0
    assert item_id in {i["id"] for i in export_public(db)["meetings"][0]["items"]}


def test_one_failed_agency_does_not_block_other_two(db):
    bundle = fixture_bundle()
    bundle["meetings"][0]["documents"][0]["sha256"] = "invalid"
    failed_id = f"{bundle['meetings'][0]['agency']}:{bundle['meetings'][0]['id']}"
    result = ingest_bundle(db, bundle)
    assert result["processed"] == len(bundle["meetings"]) - 1 and result["failed"] == 1
    data = export_public(db)
    assert len(data["meetings"]) == len(bundle["meetings"]) - 1
    assert failed_id not in {m["id"] for m in data["meetings"]}
    assert {m["agencyId"] for m in data["meetings"]} == {"ttd", "slt", "truckee"}
    assert next(a for a in data["agencies"] if a["id"] == "ttd")["status"] == "error"


def test_one_failed_truckee_event_keeps_agency_error_after_later_success(db):
    bundle = fixture_bundle()
    event = next(m for m in bundle["meetings"] if m["agency"] == "truckee" and m["documents"])
    event["documents"][0]["sha256"] = "invalid"
    assert ingest_bundle(db, bundle)["failed"] == 1
    truckee = next(a for a in export_public(db)["agencies"] if a["id"] == "truckee")
    assert truckee["status"] == "error"
    assert truckee["error"]


def test_empty_source_and_ambiguous_clock_time_do_not_publish(db):
    empty = one_meeting()
    empty["meetings"][0]["pages"] = [{**p, "text": "   "} for p in empty["meetings"][0]["pages"]]
    assert ingest_bundle(db, empty)["failed"] == 1
    ambiguous = one_meeting()
    ambiguous["meetings"][0]["date"] = "2026-11-01"
    ambiguous["meetings"][0]["start_time"] = "01:30"
    assert ingest_bundle(db, ambiguous)["failed"] == 1
    ambiguous["meetings"][0]["fold"] = 1
    assert ingest_bundle(db, ambiguous)["failed"] == 0


def test_summary_versions_append_only_on_real_source_change(db):
    original = one_meeting()
    assert ingest_bundle(db, original)["failed"] == 0
    before = db.execute("SELECT count(*) AS n FROM summary_versions").fetchone()["n"]
    assert ingest_bundle(db, original)["failed"] == 0
    assert db.execute("SELECT count(*) AS n FROM summary_versions").fetchone()["n"] == before
    revised = copy.deepcopy(original)
    revised["meetings"][0]["documents"][0]["sha256"] = "c" * 64
    assert ingest_bundle(db, revised)["failed"] == 0
    assert db.execute("SELECT count(*) AS n FROM summary_versions").fetchone()["n"] == 2 * before


def test_curated_topic_examples(db):
    bundle = fixture_bundle()
    assert ingest_bundle(db, bundle)["failed"] == 0
    data = export_public(db)
    meetings = {m["id"]: m for m in data["meetings"]}
    source_meetings = {f"{m['agency']}:{m['id']}": m for m in bundle["meetings"]}
    examples = json.loads(TOPIC_EXPECTATIONS.read_text())["examples"]
    for example in examples:
        meeting = meetings[f"{example['agency']}:{example['meeting_id']}"]
        item = next(i for i in meeting["items"] if i["number"] == example["source_number"])
        assert set(item["topics"]) == set(example["expected_topics"]), example
        source = source_meetings[meeting["id"]]
        source_item = next(i for i in source["agenda_items"] if i["source_number"] == example["source_number"])
        assert all(ref in source_item["source_pages"] for ref in example["source_pages"]), example
        for ref in example["source_pages"]:
            assert any(p["document_id"] == ref["document_id"] and p["page_number"] == ref["page_number"]
                       and p["text"].strip() for p in source["pages"]), example


def test_topic_rules_use_item_title_not_neighboring_page_excerpt():
    neighboring = [{"documentId": "ttd:7110:agenda:abc", "page": 2,
                    "quote": "Bicycle trail action from a neighboring agenda row"}]
    result = classify("ttd", "Low Carbon Transit Operations Program Cycle B fare free service", neighboring)
    assert set(result) == {"transit"}
    assert result["transit"]["matchedText"].lower() == "transit"
    assert result["transit"]["citation"] == neighboring[0]
    assert set(classify("ttd", "TTD, TART and Lake Link ridership report", [])) == {"transit"}
    assert set(classify("ttd", "District financial statement of operations", neighboring)) == set()
    assert set(classify("ttd", "Route 50 transit service changes and bicycle trail safety", [])) == {"transit", "biking"}


def test_catalog_is_exported_in_order_and_accepts_new_operator_topic(db):
    assert ingest_bundle(db, one_meeting("slt"))["failed"] == 0
    exported = export_public(db)
    assert exported["topics"] == public_topics()
    assert len(exported["topics"]) == 7
    item_id = exported["meetings"][0]["items"][0]["id"]
    edit_item(db, item_id, topic=("hot-topics", True, "operator"))
    assert "hot-topics" in export_public(db)["meetings"][0]["items"][0]["topics"]
    with pytest.raises(ValueError, match="known topic"):
        edit_item(db, item_id, topic=("not-a-topic", True, "operator"))


def test_classifier_allows_catalog_topic_without_rule_but_rejects_orphan_rule(monkeypatch):
    from tahoe_ingest import topics

    ids = topic_ids()
    monkeypatch.setattr(topics, "topic_ids", lambda: ids | {"future-topic"})
    assert set(classify("ttd", "Transit service report", [])) == {"transit"}
    monkeypatch.setattr(topics, "topic_ids", lambda: ids - {"transit"})
    with pytest.raises(ValueError, match="absent from the canonical catalog"):
        classify("ttd", "Transit service report", [])


def test_topics_do_not_depend_on_published_summary(db):
    bundle = one_meeting("ttd")
    for item in bundle["meetings"][0]["agenda_items"]:
        if item["source_number"] == "IV.D":
            item["source_pages"][0]["quote"] = "A fabricated quote absent from the source page"
    assert ingest_bundle(db, bundle)["failed"] == 0
    item = next(i for i in export_public(db)["meetings"][0]["items"] if i["number"] == "IV.D")
    assert item["summary"]["status"] == "validation_failed"
    assert item["topics"] == ["transit"]


def test_reclassify_updates_old_generated_tags_without_touching_source_history_or_overrides(db):
    bundle = one_meeting("ttd")
    assert ingest_bundle(db, bundle)["failed"] == 0
    meeting = export_public(db)["meetings"][0]
    cycle = next(i for i in meeting["items"] if i["number"] == "IV.D")
    ridership = next(i for i in meeting["items"] if i["number"] == "VI")
    edit_item(db, ridership["id"], topic=("biking", True, "operator"))
    db.execute("INSERT INTO item_topics(item_id,topic_id,source) VALUES (%s,'biking','keyword-v1')", (cycle["id"],))
    db.execute("DELETE FROM item_topics WHERE item_id=%s", (ridership["id"],))
    db.execute("UPDATE agenda_items SET classifier_version='keyword-v1' WHERE meeting_id=%s", (meeting["id"],))
    versions = db.execute("SELECT count(*) AS n FROM summary_versions").fetchone()["n"]
    documents = db.execute("SELECT count(*) AS n FROM document_versions").fetchone()["n"]
    before = export_public(db)["meetings"][0]["updatedAt"]
    preview = reclassify_topics(db, dry_run=True)
    assert preview["itemsChanged"] == 2
    assert db.execute("SELECT classifier_version FROM agenda_items WHERE id=%s", (cycle["id"],)).fetchone()["classifier_version"] == "keyword-v1"
    result = reclassify_topics(db)
    assert result["itemsChanged"] == 2
    after = export_public(db)["meetings"][0]
    assert after["updatedAt"] > before
    assert next(i for i in after["items"] if i["number"] == "IV.D")["topics"] == ["transit"]
    assert next(i for i in after["items"] if i["number"] == "VI")["topics"] == ["biking", "transit"]
    assert db.execute("SELECT count(*) AS n FROM summary_versions").fetchone()["n"] == versions
    assert db.execute("SELECT count(*) AS n FROM document_versions").fetchone()["n"] == documents
    assert db.execute("SELECT count(*) AS n FROM agenda_items WHERE classifier_version=%s", (CLASSIFIER_VERSION,)).fetchone()["n"] == len(after["items"])
    assert reclassify_topics(db)["itemsChanged"] == 0
    assert export_public(db)["meetings"][0]["updatedAt"] == after["updatedAt"]


def test_capture_parses_known_agenda_and_publishes_validated_excerpts(db, tmp_path, monkeypatch):
    from tahoe_ingest import cli

    source = one_meeting("truckee")["meetings"][0]
    document = source["documents"][0]
    candidate = {"agency": "truckee", "source_id": source["id"], "title": source["title"],
                 "official_url": source["source_meeting_url"], "date": source["date"],
                 "start_time": source["start_time"], "status": "scheduled", "documents": [
                     {"id": document["id"], "title": "Agenda Packet", "url": document["url"]}], "warnings": []}
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps({"candidates": [candidate]}))
    monkeypatch.setattr(cli, "connect", lambda _: contextlib.nullcontext(db))
    monkeypatch.setattr(cli, "download_extract", lambda _: {
        "sha256": document["sha256"], "page_count": document["page_count"],
        "warnings": [], "pages": [{"page_number": p["page_number"], "text": p["text"]}
                                 for p in source["pages"]]})
    assert cli.main(["--database-url", "local-test", "capture", str(path)]) == 0
    meeting = export_public(db, mode="live")["meetings"][0]
    assert len(meeting["items"]) == len(source["agenda_items"])
    assert all(i["summary"]["status"] == "published" for i in meeting["items"])
    assert meeting["warnings"] == []


def test_capture_limit_does_not_publish_partial_document_set(db, tmp_path, monkeypatch):
    from tahoe_ingest import cli

    source = one_meeting("truckee")["meetings"][0]
    document = source["documents"][0]
    candidate = {"agency": "truckee", "source_id": source["id"], "title": source["title"],
                 "official_url": source["source_meeting_url"], "documents": [
                     {"id": document["id"], "title": "Agenda Packet", "url": document["url"]},
                     {"id": "additional-agenda", "title": "Revised Agenda", "url": "https://example.org/revised.pdf"}],
                 "warnings": []}
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps({"candidates": [candidate]}))
    monkeypatch.setattr(cli, "connect", lambda _: contextlib.nullcontext(db))
    monkeypatch.setattr(cli, "download_extract", lambda _: {
        "sha256": document["sha256"], "page_count": document["page_count"],
        "warnings": [], "pages": [{"page_number": p["page_number"], "text": p["text"]}
                                 for p in source["pages"]]})
    assert cli.main(["--database-url", "local-test", "capture", str(path), "--max-documents", "1"]) == 0
    meeting = export_public(db, mode="live")["meetings"][0]
    assert meeting["items"] == []
    assert any("limit omitted" in warning for warning in meeting["warnings"])


def test_meeting_freshness_advances_for_revision_and_operator_edit_but_not_replay(db):
    original = one_meeting("ttd")
    assert ingest_bundle(db, original)["failed"] == 0
    first = export_public(db)["meetings"][0]
    assert ingest_bundle(db, original)["failed"] == 0
    replay = export_public(db)["meetings"][0]
    assert replay["updatedAt"] == first["updatedAt"]
    revised = copy.deepcopy(original)
    revised["meetings"][0]["documents"][0]["sha256"] = "d" * 64
    assert ingest_bundle(db, revised)["failed"] == 0
    changed = export_public(db)["meetings"][0]
    assert changed["change"] == "revised"
    assert changed["updatedAt"] > replay["updatedAt"]
    assert ingest_bundle(db, revised)["failed"] == 0
    assert export_public(db)["meetings"][0]["updatedAt"] == changed["updatedAt"]
    item_id = changed["items"][0]["id"]
    before_item = changed["items"][0]["updatedAt"]
    edit_item(db, item_id, correction=("Clerk correction", "Incorrect title", "operator"))
    edited = export_public(db)["meetings"][0]
    assert edited["updatedAt"] > changed["updatedAt"]
    assert edited["items"][0]["updatedAt"] > before_item
    edit_item(db, item_id, topic=("biking", True, "operator"))
    tagged = export_public(db)["meetings"][0]
    assert tagged["updatedAt"] > edited["updatedAt"]


def test_history_records_each_changed_agenda_but_not_identical_text(db):
    original = one_meeting("ttd")
    original["meetings"][0]["documents"][0]["fetched_at"] = "2026-10-01T16:00:00Z"
    assert ingest_bundle(db, original)["failed"] == 0
    meeting = export_public(db)["meetings"][0]
    assert meeting["history"] == [{"at": "2026-10-01T16:00:00Z", "change": "added", "document": meeting["documents"][0]["title"]}]
    # New bytes with the same extracted text, such as a re-rendered page, are not a change.
    rerendered = copy.deepcopy(original)
    rerendered["meetings"][0]["documents"][0].update(sha256="e" * 64, fetched_at="2026-10-02T16:00:00Z")
    assert ingest_bundle(db, rerendered)["failed"] == 0
    assert len(export_public(db)["meetings"][0]["history"]) == 1
    revised = copy.deepcopy(rerendered)
    revised["meetings"][0]["documents"][0].update(sha256="f" * 64, fetched_at="2026-10-03T16:00:00Z")
    revised["meetings"][0]["pages"][0]["text"] += "\nRevised agenda."
    assert ingest_bundle(db, revised)["failed"] == 0
    exported = export_public(db)
    assert [(e["at"], e["change"]) for e in exported["meetings"][0]["history"]] == [
        ("2026-10-01T16:00:00Z", "added"), ("2026-10-03T16:00:00Z", "revised")]
    assert check_export(exported)[0] == []


def test_canceled_and_rescheduled_labels_survive_operator_edit(db):
    source = one_meeting("ttd")
    assert ingest_bundle(db, source)["failed"] == 0
    item_id = export_public(db)["meetings"][0]["items"][0]["id"]
    canceled = copy.deepcopy(source)
    canceled["meetings"][0]["status"] = "canceled"
    assert ingest_bundle(db, canceled)["failed"] == 0
    edit_item(db, item_id, suppressed=True)
    assert export_public(db)["meetings"][0]["change"] == "canceled"
    rescheduled = copy.deepcopy(source)
    rescheduled["meetings"][0]["status"] = "rescheduled"
    rescheduled["meetings"][0]["date"] = "2026-10-08"
    assert ingest_bundle(db, rescheduled)["failed"] == 0
    assert export_public(db)["meetings"][0]["change"] == "rescheduled"


def test_date_only_meeting_is_exported_and_date_move_is_a_real_revision(db):
    source = one_meeting("ttd")
    row = source["meetings"][0]
    row["id"] = "date-only-case"
    row["source_meeting_url"] = "https://tahoetransportation.org/date-only-case/"
    row["date"] = "2026-11-04"
    row["start_time"] = None
    row["documents"] = []
    row["pages"] = []
    row["agenda_items"] = []
    row["items_complete"] = False
    assert ingest_bundle(db, source)["failed"] == 0
    first = export_public(db)["meetings"][0]
    assert first["date"] == "2026-11-04"
    assert first["startsAt"] is None
    assert ingest_bundle(db, source)["failed"] == 0
    assert export_public(db)["meetings"][0]["updatedAt"] == first["updatedAt"]
    row["date"] = "2026-11-05"
    assert ingest_bundle(db, source)["failed"] == 0
    moved = export_public(db)["meetings"][0]
    assert moved["date"] == "2026-11-05"
    assert moved["change"] == "rescheduled"
    assert moved["updatedAt"] > first["updatedAt"]


def test_partial_date_only_capture_does_not_keep_stale_start_time(db):
    original = one_meeting("ttd")
    assert ingest_bundle(db, original)["failed"] == 0
    previous = export_public(db)["meetings"][0]
    update = copy.deepcopy(original)
    row = update["meetings"][0]
    row["date"] = "2026-10-08"
    row["start_time"] = None
    row["partial_logistics"] = True
    row["documents"] = []
    row["pages"] = []
    row["agenda_items"] = []
    row["items_complete"] = False
    assert ingest_bundle(db, update, mode="live")["failed"] == 0
    after = export_public(db)["meetings"][0]
    assert after["date"] == "2026-10-08"
    assert after["startsAt"] is None
    assert after["change"] == "rescheduled"
    assert previous["startsAt"] is not None


def test_explicit_fixture_source_check_is_retained_without_claiming_replay_checked_now(db):
    bundle = one_meeting("ttd")
    row = bundle["meetings"][0]
    row["source_checked_at"] = "2026-10-08T16:20:00+00:00"
    assert ingest_bundle(db, bundle)["failed"] == 0
    agency = next(a for a in export_public(db)["agencies"] if a["id"] == "ttd")
    assert agency["lastCheckedAt"] == "2026-10-08T16:20:00Z"
    assert agency["status"] == "ok"
    row.pop("source_checked_at")
    assert ingest_bundle(db, bundle)["failed"] == 0
    replay = next(a for a in export_public(db)["agencies"] if a["id"] == "ttd")
    assert replay["lastCheckedAt"] == agency["lastCheckedAt"]


def test_retired_overrides_are_retained_but_hidden_and_str_implies_housing(db):
    assert ingest_bundle(db, one_meeting())["failed"] == 0
    item_id = export_public(db)["meetings"][0]["items"][0]["id"]
    db.execute("INSERT INTO topic_overrides(item_id,topic_id,included,author) VALUES (%s,'roads',true,'operator')", (item_id,))
    edit_item(db, item_id, topic=("short-term-rentals", True, "operator"))
    edit_item(db, item_id, topic=("housing", False, "operator"))
    item = export_public(db)["meetings"][0]["items"][0]
    assert {'short-term-rentals', 'housing'} <= set(item['topics'])
    assert 'roads' not in item['topics']
    assert db.execute("SELECT 1 FROM topic_overrides WHERE topic_id='roads'").fetchone()
    db.execute("INSERT INTO item_topics(item_id,topic_id,source) VALUES (%s,'budget','old')", (item_id,))
    assert reclassify_topics(db, dry_run=True)['automaticTopicsBefore']['budget'] == 1
    reclassify_topics(db)
    assert not db.execute("SELECT 1 FROM item_topics WHERE topic_id='budget'").fetchone()


def test_one_off_notice_replay_cancellation_and_unfollow(db, monkeypatch):
    from tahoe_ingest import core
    # Exercise notice fallback independently of newly verified registry URLs.
    db.execute("UPDATE agencies SET official_url='',source_url='' WHERE id='trpa-governing'")
    bundle = {'meetings': [{'agency': 'trpa-governing', 'id': 'one-off-area-plan',
        'title': 'Area plan workshop', 'date': '2026-11-12', 'status': 'scheduled',
        'source_meeting_url': 'https://example.org/official-workshop',
        'topics': ['short-term-rentals'], 'items_complete': False, 'documents_complete': False}]}
    assert ingest_bundle(db, bundle)['failed'] == 0
    assert ingest_bundle(db, bundle)['failed'] == 0
    exported = export_public(db)
    assert len(exported['meetings']) == 1
    meeting = exported['meetings'][0]
    assert meeting['startsAt'] is None
    assert meeting['topics'] == ['housing', 'short-term-rentals']
    assert meeting['items'] == []
    assert exported['agencies'][0]['sourceUrl'] == bundle['meetings'][0]['source_meeting_url']
    bundle['meetings'][0]['status'] = 'canceled'
    assert ingest_bundle(db, bundle)['failed'] == 0
    assert export_public(db)['meetings'][0]['status'] == 'canceled'
    monkeypatch.setattr(core, 'AGENCIES', {})
    assert export_public(db)['meetings'] == []
    assert export_public(db)['agencies'] == []
    assert db.execute('SELECT count(*) AS n FROM meetings').fetchone()['n'] == 1


def test_one_off_cli_upserts_without_claiming_live_freshness(db, tmp_path, monkeypatch):
    from tahoe_ingest import cli
    monkeypatch.setattr(cli, 'connect', lambda url: contextlib.nullcontext(db))
    path = tmp_path / 'one-offs.json'
    path.write_text(json.dumps({'meetings': [{'agency': 'slt', 'id': 'one-off-policy',
        'title': 'VHR policy workshop', 'date': '2026-11-12', 'topics': ['short-term-rentals'],
        'source_meeting_url': 'https://example.org/official-workshop'}]}))
    assert cli.main(['--database-url', 'unused-test-url', 'ingest-one-offs', str(path)]) == 0
    assert cli.main(['--database-url', 'unused-test-url', 'ingest-one-offs', str(path)]) == 0
    data = export_public(db)
    assert len(data['meetings']) == 1
    assert data['meetings'][0]['topics'] == ['housing', 'short-term-rentals']
    assert data['meetings'][0]['lastCheckedAt'] is None
    assert data['agencies'][0]['status'] == 'never_checked'


def test_notice_only_update_preserves_extracted_agenda(db, tmp_path):
    from tahoe_ingest.one_offs import load_one_offs
    bundle = one_meeting('slt')
    bundle['meetings'][0]['id'] = 'one-off-cited-workshop'
    assert ingest_bundle(db, bundle)['failed'] == 0
    before = export_public(db)['meetings'][0]
    path = tmp_path / 'one-offs.json'
    path.write_text(json.dumps({'meetings': [{'agency': 'slt', 'id': 'one-off-cited-workshop',
        'title': 'Housing workshop', 'source_meeting_url': before['officialUrl'],
        'date': before['date'], 'topics': ['housing']}]}))
    assert ingest_bundle(db, load_one_offs(path))['failed'] == 0
    after = export_public(db)['meetings'][0]
    assert after['documents'] == before['documents']
    assert after['items'] == before['items']
    assert after['topics'] == ['housing']


def test_partial_capture_publishes_incoming_items_and_invalidates_unprocessed_items(db):
    bundle = one_meeting()
    assert ingest_bundle(db, bundle)['failed'] == 0
    partial = copy.deepcopy(bundle)
    meeting = partial['meetings'][0]
    meeting['documents'][0]['sha256'] = 'b' * 64
    meeting['agenda_items'] = meeting['agenda_items'][:1]
    meeting['items_complete'] = False
    assert ingest_bundle(db, partial)['failed'] == 0
    output = export_public(db)['meetings'][0]['items']
    assert output[0]['summary']['status'] == 'published'
    assert output[0]['citations']
    assert output[1]['summary']['status'] == 'pending'
    assert output[1]['citations'] == []
    # Parsing an unchanged source later restores the evidence cleared above.
    partial['meetings'][0]['agenda_items'] = bundle['meetings'][0]['agenda_items']
    assert ingest_bundle(db, partial)['failed'] == 0
    assert all(item['summary']['status'] == 'published' and item['citations']
               for item in export_public(db)['meetings'][0]['items'])


def test_comment_window_is_separate_and_requires_fetched_deadline_evidence(db):
    from tahoe_ingest.comment_periods import ingest_comment_periods
    row={'agency':'ltbmu','id':'project-42','title':'Project comment period','opens_on':'2026-10-01',
         'closes_on':'2026-11-15','official_url':'https://example.gov/project-42',
         'source_quote':'Comments are accepted through November 15, 2026.','topics':['housing']}
    fetch=lambda _: {'pages':[{'text':row['source_quote']}],'sha256':'a'*64}
    assert ingest_comment_periods(db,{'comment_periods':[row]},fetch=fetch)['processed']==1
    first=export_public(db)
    assert first['meetings']==[] and first['commentPeriods'][0]['closesAt'] is None
    assert {a['id'] for a in first['agencies']}=={'ltbmu'}
    assert first['commentPeriods'][0]['closesOn']=='2026-11-15'
    stamp=first['commentPeriods'][0]['updatedAt']
    assert ingest_comment_periods(db,{'comment_periods':[row]},fetch=fetch)['processed']==1
    assert export_public(db)['commentPeriods'][0]['updatedAt']==stamp
    bad={**row,'source_quote':'Invented deadline'}
    assert ingest_comment_periods(db,{'comment_periods':[bad]},fetch=fetch)['failed']==1
    assert export_public(db)['commentPeriods'][0]['sourceQuote']==row['source_quote']


def test_quarantined_source_record_never_publishes_or_suppresses_real_evidence(db):
    assert ingest_bundle(db,one_meeting())['failed']==0
    mid=export_public(db)['meetings'][0]['id']
    db.execute('UPDATE meetings SET quarantine_reason=%s WHERE id=%s',('Wrong body',mid))
    assert export_public(db)['meetings']==[] and export_public(db)['agencies']==[]
    assert db.execute('SELECT 1 FROM document_versions WHERE meeting_id=%s',(mid,)).fetchone()


def test_source_alias_maps_calendar_and_agenda_ids_but_not_distinct_workshop(db):
    from tahoe_ingest.cli import _source_identity
    source=one_meeting('slt')['meetings'][0]
    assert ingest_bundle(db,{'meetings':[source]})['failed']==0
    row={'agency':'slt','source_id':'new-native-id','title':source['title'],
         'date':export_public(db)['meetings'][0]['date'],'official_url':'https://example.gov/new-agenda'}
    assert _source_identity(db,row)==source['id']
    assert _source_identity(db,row)==source['id']
    row={**row,'source_id':'distinct-workshop','title':'Housing Workshop'}
    assert _source_identity(db,row)=='distinct-workshop'


@contextlib.contextmanager
def statement_count(monkeypatch):
    """Count statements sent to the database; on hosted Postgres each one is a billed operation."""
    counter = {"n": 0}
    original = psycopg.Cursor.execute

    def execute(self, *args, **kwargs):
        counter["n"] += 1
        return original(self, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Cursor, "execute", execute)
        yield counter


def test_export_and_reclassify_queries_do_not_grow_with_the_archive(db, monkeypatch):
    assert ingest_bundle(db, one_meeting())["failed"] == 0
    with statement_count(monkeypatch) as small_export:
        export_public(db, forecast_days=90)
    with statement_count(monkeypatch) as small_reclassify:
        reclassify_topics(db)
    bundle = fixture_bundle()
    assert ingest_bundle(db, bundle)["failed"] == 0
    with statement_count(monkeypatch) as large_export:
        payload = export_public(db, forecast_days=90)
    with statement_count(monkeypatch) as large_reclassify:
        reclassify_topics(db)
    assert len(payload["meetings"]) > 1 and sum(len(m["items"]) for m in payload["meetings"]) > 10
    assert large_export["n"] == small_export["n"] <= 12
    assert large_reclassify["n"] == small_reclassify["n"] <= 3


def test_reingesting_an_unchanged_meeting_skips_rewrites(db, monkeypatch):
    bundle = one_meeting("slt")
    assert ingest_bundle(db, bundle)["failed"] == 0
    first = export_public(db)
    assert len(first["meetings"][0]["items"]) > 3
    with statement_count(monkeypatch) as again:
        assert ingest_bundle(db, bundle)["failed"] == 0
    # Lock, meeting, documents, items, cited pages, agency status, run record: no per-item or per-page writes.
    assert again["n"] <= 10
    assert export_public(db)["meetings"] == first["meetings"]


def test_stored_snapshot_resolves_identity_like_source_identity(db):
    from tahoe_ingest.capture import StoredMeetings, source_identity
    source = one_meeting('slt')['meetings'][0]
    assert ingest_bundle(db, {'meetings': [source]})['failed'] == 0
    date = export_public(db)['meetings'][0]['date']
    rows = [{'agency': 'slt', 'source_id': source['id'], 'title': source['title'], 'date': date, 'official_url': 'x'},
            {'agency': 'slt', 'source_id': 'calendar-id', 'title': source['title'], 'date': date, 'official_url': 'https://example.gov/a'},
            {'agency': 'slt', 'source_id': 'distinct-workshop', 'title': 'Housing Workshop', 'date': date, 'official_url': 'y'},
            {'agency': 'slt', 'source_id': 'undated', 'title': source['title'], 'date': None, 'official_url': 'z'}]
    stored = StoredMeetings(db, failure_hours=24)
    assert [stored.identity(r) for r in rows] == [source['id'], source['id'], 'distinct-workshop', 'undated']
    assert [source_identity(db, r) for r in rows] == [source['id'], source['id'], 'distinct-workshop', 'undated']
    assert StoredMeetings(db, failure_hours=24).identity({**rows[1], 'date': None}) == source['id']  # The alias now persists.
