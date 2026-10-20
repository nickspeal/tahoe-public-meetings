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
