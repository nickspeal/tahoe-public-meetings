"""Meeting warnings name a known, specific issue (decision 0039).

A changed source document on a meeting with nothing to reprocess adds no warning; reparse rewrites a stale
"await reprocessing" note even when the parse failure itself is unchanged; a canceled meeting, or documents
that hold no agenda, record no parse failure.
"""
import copy
from pathlib import Path

from tahoe_ingest import retired_warnings
from tahoe_ingest.agenda import parse_agenda
from tahoe_ingest.capture import _capture_row
from tahoe_ingest.core import REPROCESSING_WARNING, ingest_bundle
from tahoe_ingest.reparse import reparse_meetings

from test_pipeline import db, one_meeting  # noqa: F401  (db is a fixture)


def stored_warnings(db, meeting_id):
    return db.execute("SELECT warnings FROM meetings WHERE id=%s", (meeting_id,)).fetchone()["warnings"]


def unreadable(bundle):
    """The meeting with its agenda text replaced by a page no layout reads, and no items."""
    meeting = bundle["meetings"][0]
    meeting["pages"] = [{**meeting["pages"][0], "text": "Nothing that reads as an agenda.\n"}]
    meeting["agenda_items"] = []
    meeting["items_complete"] = False
    return meeting


def revise(bundle):
    revised = copy.deepcopy(bundle)
    meeting = revised["meetings"][0]
    meeting["documents"][0]["sha256"] = "b" * 64
    meeting["agenda_items"] = []
    meeting["items_complete"] = False
    return revised


def test_a_changed_document_on_a_meeting_without_items_adds_no_warning(db):
    bundle = one_meeting("truckee")
    unreadable(bundle)
    assert ingest_bundle(db, bundle)["failed"] == 0
    assert ingest_bundle(db, revise(bundle))["failed"] == 0
    assert REPROCESSING_WARNING not in stored_warnings(db, "truckee:1895")


def test_a_changed_document_that_voids_published_items_is_noted_once(db):
    bundle = one_meeting("truckee")
    assert ingest_bundle(db, bundle)["failed"] == 0
    revised = revise(bundle)
    assert ingest_bundle(db, revised)["failed"] == 0
    assert stored_warnings(db, "truckee:1895").count(REPROCESSING_WARNING) == 1
    again = revise(revised)
    again["meetings"][0]["documents"][0]["sha256"] = "c" * 64
    again["meetings"][0]["warnings"] = [REPROCESSING_WARNING]  # as a reparse that kept it would write it
    assert ingest_bundle(db, again)["failed"] == 0
    assert stored_warnings(db, "truckee:1895").count(REPROCESSING_WARNING) == 1


def test_reparse_drops_a_stale_reprocessing_note_when_the_failure_is_unchanged(db):
    bundle = one_meeting("truckee")
    meeting = unreadable(bundle)
    failure = parse_agenda("truckee", meeting["documents"], meeting["pages"]).warning
    assert failure
    meeting["warnings"] = [failure, REPROCESSING_WARNING, "Publisher row date/time and agenda items require source review."]
    assert ingest_bundle(db, bundle)["failed"] == 0
    report = reparse_meetings(db)
    assert report["failed"] == [{"meeting": "truckee:1895", "warning": failure}]
    assert stored_warnings(db, "truckee:1895") == [failure]
    # The next run finds nothing stuck and writes nothing.
    db.execute("UPDATE meetings SET updated_at='2026-01-01' WHERE id='truckee:1895'")
    reparse_meetings(db)
    assert stored_warnings(db, "truckee:1895") == [failure]


def test_reparse_records_no_parse_failure_for_a_canceled_meeting(db):
    bundle = one_meeting("truckee")
    meeting = unreadable(bundle)
    meeting["status"] = "canceled"
    meeting["warnings"] = [parse_agenda("truckee", meeting["documents"], meeting["pages"]).warning]
    assert ingest_bundle(db, bundle)["failed"] == 0
    report = reparse_meetings(db)
    assert report["failed"] == [{"meeting": "truckee:1895", "warning": None}]
    assert stored_warnings(db, "truckee:1895") == []


def test_reparse_clears_the_reprocessing_note_of_an_agency_without_a_parser(db):
    bundle = one_meeting("truckee")
    meeting = unreadable(bundle)
    meeting.update(agency="tcpud-parks-recreation-committee", id="clip-1727",
                   warnings=["No parser exists for agency tcpud-parks-recreation-committee; review required.", REPROCESSING_WARNING])
    assert ingest_bundle(db, bundle)["failed"] == 0
    meeting_id = "tcpud-parks-recreation-committee:clip-1727"
    assert reparse_meetings(db, dry_run=True)["cleared"] == [meeting_id]
    assert REPROCESSING_WARNING in stored_warnings(db, meeting_id)
    assert reparse_meetings(db)["cleared"] == [meeting_id]
    assert stored_warnings(db, meeting_id) == ["No parser exists for agency tcpud-parks-recreation-committee; review required."]
    assert reparse_meetings(db)["cleared"] == []


def test_documents_without_an_agenda_record_no_parse_failure():
    page = {"page_number": 1, "text": "MINUTES\nThe board met and adjourned.\n"}
    for document in ({"id": "tnttma-board-minutes-2026-07-09", "title": "Official Board meeting minutes"},
                     {"id": "douglas-rtc-supplement-2026-07-13-item-3",
                      "title": "Official supplemental material for RTC Item 3 (not the full agenda)"}):
        result = parse_agenda("truckee-north-shore-tma", [document], [{**page, "document_id": document["id"]}])
        assert not result.complete and result.warning is None
    # An agenda that does not parse is still reported, under the agency's name.
    agenda = {"id": "tnttma-agenda-2026-07-09", "title": "Official board meeting agenda"}
    result = parse_agenda("truckee-north-shore-tma", [agenda], [{**page, "document_id": agenda["id"]}])
    assert result.warning and result.warning.endswith("review required.")
    assert "TRUCKEE-NORTH-SHORE-TMA" not in result.warning


def _fetch(text):
    return lambda url: {"sha256": "0" * 64, "page_count": 1, "warnings": [], "pages": [{"page_number": 1, "text": text}]}


def test_capture_of_a_canceled_meeting_records_no_parse_failure():
    candidate = {"agency": "truckee", "source_id": "1771", "title": "Town Council Meeting", "status": "canceled",
                 "official_url": "https://example.org/m", "date": "2026-10-28",
                 "documents": [{"id": "truckee-agenda-pdf", "title": "Official agenda (PDF)", "url": "https://example.org/a.pdf"}]}
    row = _capture_row(candidate, 2, _fetch("Notice of Cancelation of Town Council Meeting\nThe meeting is canceled.\n"), [], [], 0)
    assert row["status"] == "canceled" and row["documents"] and row["warnings"] == []
    # The same notice on a meeting not known to be canceled is reported.
    row = _capture_row({**candidate, "status": "unknown"}, 2, _fetch("Notice of Cancelation\n"), [], [], 0)
    assert any(w.endswith("review required.") for w in row["warnings"])


def test_capture_of_minutes_alone_records_no_parse_failure():
    candidate = {"agency": "truckee-north-shore-tma", "source_id": "2026-07-09-board", "title": "Board of Directors Meeting",
                 "official_url": "https://example.org/m", "date": "2026-07-09",
                 "documents": [{"id": "tnttma-board-minutes-2026-07-09", "title": "Official Board meeting minutes",
                                "url": "https://example.org/minutes.pdf"}]}
    row = _capture_row(candidate, 2, _fetch("MINUTES\nThe board met.\n"), [], [], 0)
    assert row["documents"] and row["warnings"] == [] and not row["items_complete"]


def test_retired_warnings_are_removed_once_and_others_kept(db):
    itemless = one_meeting("truckee")
    unreadable(itemless)
    itemless["meetings"][0]["warnings"] = ["Check the notice for changes.", REPROCESSING_WARNING,
                                           "TNT/TMA Board agenda item 2 contains a second item number; review required."]
    with_items = one_meeting("slt")
    with_items["meetings"][0]["warnings"] = ["Publisher row date/time and agenda items require source review.", REPROCESSING_WARNING]
    for bundle in (itemless, with_items):
        assert ingest_bundle(db, bundle)["failed"] == 0
    before = {r["id"]: r["updated_at"] for r in db.execute("SELECT id, updated_at FROM meetings")}

    report = retired_warnings.clean_retired_warnings(db)
    assert report["summary"] == {"dryRun": True, "meetings": 2, "removed": {
        REPROCESSING_WARNING: 1, "Check the notice for changes.": 1,
        "Publisher row date/time and agenda items require source review.": 1}}
    assert stored_warnings(db, "truckee:1895")[0] == "Check the notice for changes."

    report = retired_warnings.clean_retired_warnings(db, dry_run=False)
    assert not report["summary"]["dryRun"]
    assert stored_warnings(db, "truckee:1895") == ["TNT/TMA Board agenda item 2 contains a second item number; review required."]
    # Published items still await reprocessing, so that note stays.
    assert stored_warnings(db, "slt:2339") == [REPROCESSING_WARNING]
    assert {r["id"]: r["updated_at"] for r in db.execute("SELECT id, updated_at FROM meetings")} == before
    assert retired_warnings.clean_retired_warnings(db, dry_run=False)["summary"]["meetings"] == 0


def test_no_code_writes_a_retired_warning():
    package = Path(__file__).resolve().parents[1] / "tahoe_ingest"
    for path in package.rglob("*.py"):
        if path.name != "retired_warnings.py":
            text = path.read_text()
            assert not [w for w in retired_warnings.RETIRED if w in text], path
