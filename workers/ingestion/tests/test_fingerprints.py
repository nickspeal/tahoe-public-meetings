"""Item fingerprints name what an item says, not which copy of the agenda says it.

A re-posted file, or the same text cited from another copy of the agenda (a meeting's PDF and its HTML
agenda; a duplicate stored by an earlier discovery pass), must not mark items revised, and capture,
reparse, and backfill must pick the same copy, so items never flip between copies on alternating runs.
"""
import copy
import json

from tahoe_ingest import staff_reports
from tahoe_ingest.agenda import parse_agenda
from tahoe_ingest.capture import capture_candidates
from tahoe_ingest.core import (export_public, ingest_bundle, item_fingerprint, legacy_fingerprint, migrate_fingerprints,
                               stable_id)
from tahoe_ingest.export_check import check as check_export
from tahoe_ingest.reparse import reparse_meetings

from test_pipeline import db, fixture_bundle, one_meeting  # noqa: F401  (db is a fixture)


def item_id(meeting_id, item):
    return stable_id(meeting_id, str(item.get("source_key") or item.get("source_number") or f"unnumbered-{item['order']}"))


def item_rows(db, meeting_id):
    return {r["id"]: r for r in db.execute(
        "SELECT id,source_fingerprint,change,updated_at,citations FROM agenda_items WHERE meeting_id=%s AND active", (meeting_id,))}


def versions(db):
    return db.execute("SELECT count(*) AS n FROM summary_versions").fetchone()["n"]


def two_copies(first="slt-agenda", second="slt-agenda-html"):
    """SLT's meeting with its agenda stored twice, under two document IDs with the same text."""
    bundle = one_meeting("slt")
    meeting = bundle["meetings"][0]
    original = meeting["documents"][0]
    meeting["documents"] = [{**original, "id": first, "title": "Official agenda"},
                            {**original, "id": second, "title": "Agenda", "url": original["url"] + "?copy",
                             "sha256": "e" * 64}]
    meeting["pages"] = [{**p, "document_id": doc} for doc in (first, second)
                        for p in meeting["pages"] if p["document_id"] == original["id"]]
    for item in meeting["agenda_items"]:
        for ref in item["source_pages"]:
            ref["document_id"] = first
    return bundle


def cite(bundle, document_id):
    switched = copy.deepcopy(bundle)
    for item in switched["meetings"][0]["agenda_items"]:
        for ref in item["source_pages"]:
            ref["document_id"] = document_id
    return switched


def test_a_reposted_identical_agenda_marks_nothing_revised(db):
    bundle = one_meeting("slt")
    assert ingest_bundle(db, bundle)["failed"] == 0
    before, count = item_rows(db, "slt:2339"), versions(db)
    reposted = copy.deepcopy(bundle)
    reposted["meetings"][0]["documents"][0]["sha256"] = "0" * 64
    assert ingest_bundle(db, reposted)["failed"] == 0
    after = item_rows(db, "slt:2339")
    assert {i: (r["source_fingerprint"], r["change"], r["updated_at"]) for i, r in after.items()} == \
        {i: (r["source_fingerprint"], r["change"], r["updated_at"]) for i, r in before.items()}
    assert versions(db) == count
    version = "slt:2339:slt-agenda:" + "0" * 16
    assert all(c["documentId"] == version for r in after.values() for c in r["citations"])
    exported = export_public(db)
    assert check_export(exported)[0] == []
    assert all(c["documentId"] == version for i in exported["meetings"][0]["items"]
               for c in (i["summary"] or {}).get("citations", []))


def test_the_same_text_cited_from_another_copy_is_not_a_change_either_way(db):
    bundle = two_copies()
    assert ingest_bundle(db, bundle)["failed"] == 0
    before, count = item_rows(db, "slt:2339"), versions(db)
    # A PDF-to-HTML switch, and back: no revision, no history row, no churn.
    for document_id in ("slt-agenda-html", "slt-agenda", "slt-agenda-html"):
        assert ingest_bundle(db, cite(bundle, document_id))["failed"] == 0
        after = item_rows(db, "slt:2339")
        assert {i: (r["source_fingerprint"], r["change"], r["updated_at"]) for i, r in after.items()} == \
            {i: (r["source_fingerprint"], r["change"], r["updated_at"]) for i, r in before.items()}
        assert all(c["documentId"].startswith(f"slt:2339:{document_id}:") for r in after.values() for c in r["citations"])
        assert versions(db) == count
        assert check_export(export_public(db))[0] == []


def test_genuinely_changed_item_text_is_still_revised(db):
    bundle = two_copies()
    assert ingest_bundle(db, bundle)["failed"] == 0
    before, count = item_rows(db, "slt:2339"), versions(db)
    revised = cite(bundle, "slt-agenda-html")
    meeting = revised["meetings"][0]
    text = {p["page_number"]: p["text"] for p in meeting["pages"]}
    item = next(i for i in sorted(meeting["agenda_items"], key=lambda i: i["order"])
                if i["title"] in text[i["source_pages"][0]["page_number"]])
    for page in meeting["pages"]:
        if page["page_number"] == item["source_pages"][0]["page_number"]:
            page["text"] = page["text"].replace(item["title"], item["title"] + " (amended)", 1)
    item["title"] += " (amended)"
    assert ingest_bundle(db, revised)["failed"] == 0
    after = item_rows(db, "slt:2339")
    changed = item_id("slt:2339", item)
    assert after[changed]["change"] == "revised" and after[changed]["source_fingerprint"] != before[changed]["source_fingerprint"]
    assert all(after[i]["change"] == before[i]["change"] for i in after if i != changed)
    assert versions(db) == count + 1


def test_capture_and_reparse_cite_the_same_copy(db):
    """A copy an earlier discovery pass stored, which capture no longer lists, sorts first; neither step moves to it."""
    source = one_meeting("truckee")["meetings"][0]
    original = source["documents"][0]
    pages = [{"page_number": p["page_number"], "text": p["text"]} for p in source["pages"]]
    stale = {**source, "documents": [{**original, "id": "agenda-earlier-pass", "title": "Official agenda"}],
             "pages": [{**p, "document_id": "agenda-earlier-pass"} for p in pages],
             "agenda_items": [], "items_complete": False, "documents_complete": False}
    assert ingest_bundle(db, {"meetings": [stale]})["failed"] == 0
    listed = {"id": "truckee-agenda-pdf", "title": "Official agenda (PDF)", "url": original["url"] + "&pdf"}
    candidate = {"agency": "truckee", "source_id": source["id"], "title": source["title"], "official_url": source["source_meeting_url"],
                 "date": source["date"], "start_time": source["start_time"], "status": "scheduled", "documents": [listed], "warnings": []}

    def fetch(url):
        return {"sha256": original["sha256"], "page_count": original["page_count"], "warnings": [], "pages": copy.deepcopy(pages)}
    assert capture_candidates(db, [candidate], fetch=fetch)["failed"] == 0
    captured, count = item_rows(db, "truckee:1895"), versions(db)
    assert captured and all(c["documentId"].startswith("truckee:1895:truckee-agenda-pdf:") for r in captured.values() for c in r["citations"])

    # Both copies are current, and the earlier pass's copy would be read first.
    documents = [{"id": "agenda-earlier-pass", "title": "Official agenda", "url": original["url"]}, listed]
    both = [{**p, "document_id": d["id"]} for d in documents for p in pages]
    assert parse_agenda("truckee", documents, both).items[0]["source_pages"][0]["document_id"] == "agenda-earlier-pass"
    assert parse_agenda("truckee", list(reversed(documents)), both) == parse_agenda("truckee", documents, both)
    assert parse_agenda("truckee", documents, both, prefer={"truckee-agenda-pdf"}).items[0]["source_pages"][0]["document_id"] == \
        "truckee-agenda-pdf"

    report = reparse_meetings(db, meeting="truckee:1895", force=True)
    assert report["parsed"] and not report["failed"]
    assert item_rows(db, "truckee:1895") == captured
    assert versions(db) == count
    assert capture_candidates(db, [candidate], fetch=fetch)["failed"] == 0
    assert item_rows(db, "truckee:1895") == captured


def test_reparse_leaves_items_read_from_an_html_agenda_as_captured(db):
    bundle = one_meeting("truckee")
    meeting = bundle["meetings"][0]
    meeting["documents"][0]["media_type"] = "text/html"
    assert ingest_bundle(db, bundle)["failed"] == 0
    before = item_rows(db, "truckee:1895")
    report = reparse_meetings(db, agency="truckee", force=True)
    assert report["kept"] == [{"meeting": "truckee:1895", "reason": "items come from an HTML agenda; a capture rereads it"}]
    assert item_rows(db, "truckee:1895") == before


def test_the_fingerprint_migration_moves_every_derived_row_and_marks_nothing_revised(db):
    bundle = one_meeting("slt")
    meeting = bundle["meetings"][0]
    broken = max(meeting["agenda_items"], key=lambda i: i["order"])
    broken["source_pages"][0]["quote"] = "A sentence the page does not contain"
    assert ingest_bundle(db, bundle)["failed"] == 0
    rows = db.execute("SELECT id,title,section,citations FROM agenda_items WHERE meeting_id='slt:2339'").fetchall()
    version = db.execute("SELECT version_id FROM document_current WHERE meeting_id='slt:2339'").fetchone()["version_id"]
    broken_id = item_id("slt:2339", broken)
    # The state a worker before this change left: every fingerprint names document versions, and derived rows match.
    legacy = {}
    for r in rows:
        citations = r["citations"] if r["id"] != broken_id else [
            {"documentId": version, "page": broken["source_pages"][0]["page_number"], "quote": broken["source_pages"][0]["quote"]}]
        legacy[r["id"]] = legacy_fingerprint(r["title"], r["section"], citations)
    for key, fingerprint in legacy.items():
        db.execute("UPDATE agenda_items SET source_fingerprint=%s WHERE id=%s", (fingerprint, key))
        db.execute("UPDATE summaries SET source_fingerprint=%s WHERE item_id=%s", (fingerprint, key))
        db.execute("""INSERT INTO item_attachments(item_id,source_fingerprint,method,files,evidence)
            VALUES (%s,%s,'test','[]','{}')""", (key, fingerprint))
        db.execute("""INSERT INTO ai_topic_classifications(item_id,input_sha256,source_fingerprint,prompt_sha256,model,tahoe_relevant,topics,reason)
            VALUES (%s,'x',%s,'p','m',true,'[]','r')""", (key, fingerprint))
    scans = staff_reports.scan_keys(db)
    db.execute("""INSERT INTO staff_report_scans(meeting_id,items_sha256,status,report) VALUES ('slt:2339',%s,'done','{}')""",
               (scans["slt:2339"],))
    before, count = item_rows(db, "slt:2339"), versions(db)
    db.execute("DELETE FROM ingest_migrations")

    moved = migrate_fingerprints(db)
    assert moved == len(rows) - 1  # the item whose quote failed validation stores no citations to recompute from
    assert migrate_fingerprints(db) == 0  # once
    after = item_rows(db, "slt:2339")
    for r in rows:
        expected = legacy[r["id"]] if r["id"] == broken_id else item_fingerprint(r["title"], r["section"], r["citations"])
        assert after[r["id"]]["source_fingerprint"] == expected
        assert (after[r["id"]]["change"], after[r["id"]]["updated_at"]) == (before[r["id"]]["change"], before[r["id"]]["updated_at"])
    for table in ("summaries", "item_attachments", "ai_topic_classifications"):
        stale = db.execute(f"""SELECT count(*) AS n FROM {table} t JOIN agenda_items i ON i.id=t.item_id
            WHERE t.source_fingerprint IS DISTINCT FROM i.source_fingerprint""").fetchone()["n"]
        assert stale == 0, table
    assert db.execute("SELECT items_sha256 FROM staff_report_scans").fetchone()["items_sha256"] == staff_reports.scan_keys(db)["slt:2339"]
    assert staff_reports.map_staff_reports(db, dry_run=True)["meetingsPending"] == 0  # no meeting is mapped again

    # The next capture of the same agenda changes nothing; the item left behind moves to the new formula then.
    assert ingest_bundle(db, bundle)["failed"] == 0
    again = item_rows(db, "slt:2339")
    assert {i: (r["change"], r["updated_at"]) for i, r in again.items()} == {i: (r["change"], r["updated_at"]) for i, r in before.items()}
    assert versions(db) == count
    assert again[broken_id]["source_fingerprint"] != legacy[broken_id]
    stale = db.execute("""SELECT count(*) AS n FROM item_attachments t JOIN agenda_items i ON i.id=t.item_id
        WHERE t.source_fingerprint IS DISTINCT FROM i.source_fingerprint""").fetchone()["n"]
    assert stale == 0


def test_a_claude_summary_citation_follows_the_copy_or_is_hidden(db):
    bundle = two_copies()
    meeting = bundle["meetings"][0]
    # The first copy prints a sentence the second lacks (say, a reworded requested action) on the first item's page.
    page_number = min(meeting["agenda_items"], key=lambda i: i["order"])["source_pages"][0]["page_number"]
    first_page = next(p for p in meeting["pages"] if p["document_id"] == "slt-agenda" and p["page_number"] == page_number)
    first_page["text"] += "\nOnly the first copy says this."
    assert ingest_bundle(db, bundle)["failed"] == 0
    rows = item_rows(db, "slt:2339")
    follows, hidden = [r for r in rows.values() if r["citations"] and r["citations"][0]["page"] == page_number][:2]
    for row, quote in ((follows, follows["citations"][0]["quote"]), (hidden, "Only the first copy says this.")):
        db.execute("""INSERT INTO ai_item_summaries(item_id,input_sha256,source_fingerprint,report_sha256,prompt_sha256,model,status,text,citations)
            VALUES (%s,'i',%s,'r','p','m','published','A summary.',%s)""",
                   (row["id"], row["source_fingerprint"], json.dumps([{**row["citations"][0], "quote": quote}])))
    assert ingest_bundle(db, cite(bundle, "slt-agenda-html"))["failed"] == 0
    stored = {r["item_id"]: r for r in db.execute("SELECT item_id,source_fingerprint,citations FROM ai_item_summaries")}
    after = item_rows(db, "slt:2339")
    assert stored[follows["id"]]["source_fingerprint"] == after[follows["id"]]["source_fingerprint"]
    assert stored[follows["id"]]["citations"][0]["documentId"].startswith("slt:2339:slt-agenda-html:")
    assert stored[hidden["id"]]["source_fingerprint"] != after[hidden["id"]]["source_fingerprint"]  # hidden until rebound
    assert stored[hidden["id"]]["citations"][0]["documentId"].startswith("slt:2339:slt-agenda:")
    assert all(r["change"] == rows[i]["change"] for i, r in after.items())
