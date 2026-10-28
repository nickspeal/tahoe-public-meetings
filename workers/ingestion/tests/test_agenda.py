import json
import re
from pathlib import Path

from tahoe_ingest.agenda import parse_agenda


FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/meetings.json"


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def test_known_agendas_recover_ordered_items_with_page_exact_quotes():
    bundle = json.loads(FIXTURE.read_text())
    for meeting in bundle["meetings"]:
        if not meeting["documents"]:
            continue
        parsed = parse_agenda(meeting["agency"], meeting["documents"], meeting["pages"])
        assert parsed.complete, (meeting["agency"], parsed.warning)
        expected = meeting["agenda_items"]
        assert [i["source_number"] for i in parsed.items] == [i["source_number"] for i in expected]
        assert [i["order"] for i in parsed.items] == list(range(1, len(expected) + 1))
        page_map = {(p["document_id"], p["page_number"]): p["text"] for p in meeting["pages"]}
        for item, known in zip(parsed.items, expected):
            ref = item["source_pages"][0]
            assert ref["page_number"] == known["source_pages"][0]["page_number"]
            assert ref["quote"] in page_map[(ref["document_id"], ref["page_number"])]
            if known["source_number"] is not None:
                overlap = len(_tokens(item["title"]) & _tokens(known["title"]))
                assert overlap >= min(2, len(_tokens(known["title"]))), (meeting["agency"], known["source_number"], item["title"])
            if meeting["agency"] == "ttd":
                assert not re.search(r"(?:\b\d{1,3}\s+LINK\b|\bPage$)", item["title"]), item


def test_ttd_committee_meeting_agenda_cover_parses_like_the_board():
    page1 = """                         TAHOE TRANSPORTATION DISTRICT (TTD)
                   REGIONAL TRANSIT AND CAPITAL PROGRAMS COMMITTEE

                                           Meeting Agenda

  I.      CALL TO ORDER AND GENERAL MATTERS
            A. Roll Call and Determination of Quorum
            B. For Possible Action: Approval of Agenda for November 2, 2026
            C. For Possible Action: Approval of Minutes of August 3, 2026                      Page
                                                                                                1
                                            Agenda Page 1"""
    page2 = """ II.    PUBLIC INTEREST COMMENTS
        All comments are to be limited to no more than three minutes per person.

 III.   DISCUSSION ITEMS
                                                                                               Page
          A.   Informational Only: Informational Summary Update on the Overall Park               5
               Tahoe® Program

 IV.     COMMITTEE MEMBER REQUESTS AND COMMENTS
        This portion of the agenda is for members to make requests for future agenda items.

 V.     ADJOURNMENT
                                            Agenda Page 2"""
    pages = [{"document_id": "a", "page_number": 1, "text": page1}, {"document_id": "a", "page_number": 2, "text": page2}]
    parsed = parse_agenda("ttd-rtcpc", [{"id": "a"}], pages)
    assert parsed.complete, parsed.warning
    titles = {i["source_number"]: i["title"] for i in parsed.items}
    assert titles["III.A"] == "Informational Only: Informational Summary Update on the Overall Park Tahoe® Program"
    assert titles["IV"] == "COMMITTEE MEMBER REQUESTS AND COMMENTS"


def test_unknown_layout_is_review_only():
    pages = [{"document_id": "x", "page_number": 1, "text": "Agenda items need no evidence. 1. Adopt everything."}]
    parsed = parse_agenda("slt", [{"id": "x"}], pages)
    assert not parsed.complete
    assert parsed.items == []
    assert "review" in parsed.warning.lower()


def test_different_agenda_pdfs_require_review():
    bundle = json.loads(FIXTURE.read_text())
    meeting = next(m for m in bundle["meetings"] if m["agency"] == "truckee" and m["documents"])
    original = meeting["documents"][0]
    pages = meeting["pages"]
    altered = [{**page, "document_id": "second", "text": page["text"].replace("5.1    Tahoe Truckee", "5.2    Tahoe Truckee")}
               for page in pages]
    result = parse_agenda("truckee", [original, {"id": "second"}], pages + altered)
    assert not result.complete
    assert result.items == []


def test_slt_standalone_number_uses_same_page_title_quote():
    bundle = json.loads(FIXTURE.read_text())
    meeting = next(m for m in bundle["meetings"] if m["agency"] == "slt" and m["id"] == "2339")
    pages = [{**page} for page in meeting["pages"]]
    page = next(p for p in pages if p["page_number"] == 3)
    page["text"] = page["text"].replace("5.   Agreement with Eide Bailly", "5.\n     Agreement with Eide Bailly")
    parsed = parse_agenda("slt", meeting["documents"], pages)
    assert parsed.complete, parsed.warning
    item = next(i for i in parsed.items if i["source_number"] == "5")
    assert item["title"].startswith("Agreement with Eide Bailly")
    assert item["source_pages"][0]["page_number"] == 3
    assert item["source_pages"][0]["quote"] in page["text"]


def test_reparse_clears_every_parse_agenda_failure_warning():
    import re
    from pathlib import Path
    from tahoe_ingest.reparse import STALE
    package = Path(__file__).resolve().parents[1] / "tahoe_ingest"
    # The dispatcher, the hand-written parsers, and the outline engine each phrase their own reasons.
    for module, least in (("agenda.py", 5), ("agenda_custom.py", 10), ("outline.py", 10)):
        reasons = re.findall(r'"([^"]*(?:review|could not be validated)[^"]*)"', (package / module).read_text())
        assert len(reasons) >= least and all(STALE.search(w) for w in reasons), (module, reasons)


def test_hand_written_parsers_name_sections_as_printed():
    bundle = json.loads(FIXTURE.read_text())
    meetings = {(m["agency"], m["id"]): m for m in bundle["meetings"]}

    def sections(agency, meeting_id):
        meeting = meetings[(agency, meeting_id)]
        return {i["source_number"] or i["title"]: i["section"]
                for i in parse_agenda(agency, meeting["documents"], meeting["pages"]).items}

    # Closed session items followed "Agenda Planning Calendar" when "Closed Session" was not a known heading.
    slt = sections("slt", "2295")
    assert [slt[str(n)] for n in range(21, 25)] == ["Closed Session"] * 4
    assert slt["15"] == "Public Hearing"
    special = sections("slt", "2351")  # a special meeting's shorter headings
    assert (special["1"], special["2"], special["4"]) == ("Closed Session", "Consent", "New Business")
    # TTD keeps acronyms and drops the packet's "Page" column header.
    ttd = sections("ttd", "6049")
    assert ttd["IV.A"] == "Tahoe Transportation District (TTD) Consent Items"
    assert ttd["V.A"] == "Tahoe Transportation District Business Items"
