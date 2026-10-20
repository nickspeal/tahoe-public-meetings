"""Replay real agency agenda text through the deterministic layouts.

Each fixture in tests/fixtures/agenda-layouts holds the extracted pages of one
official agenda and the reviewed item list. "expected": null marks a document
that must be rejected rather than guessed (a malformed agenda, or a synopsis).
"""
import json
from pathlib import Path

import pytest

from tahoe_ingest.agenda import has_parser, parse_agenda
from tahoe_ingest.agenda_layouts import LAYOUTS
from tahoe_ingest.outline import parse_layout

FIXTURES = sorted((Path(__file__).resolve().parents[3] / "tests/fixtures/agenda-layouts").glob("*.json"))


def _load(path):
    fixture = json.loads(path.read_text())
    pages = [dict(page, document_id="doc") for page in fixture["pages"]]
    return fixture, pages


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_layout_fixture(path):
    fixture, pages = _load(path)
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], pages)
    if fixture["expected"] is None:
        assert not result.complete and not result.items
        assert result.warning
        return
    assert result.complete, result.warning
    assert [[i["source_number"], i["section"], i["title"]] for i in result.items] == fixture["expected"]
    page_text = {p["page_number"]: p["text"] for p in pages}
    keys = [i.get("source_key") or i["source_number"] for i in result.items]
    assert len(keys) == len(set(keys)), "item keys must be unique within a meeting"
    for order, item in enumerate(result.items, 1):
        assert item["order"] == order
        ref = item["source_pages"][0]
        assert ref["quote"] and ref["quote"] in page_text[ref["page_number"]], item["title"]


def test_every_layout_agency_has_a_fixture():
    covered = {json.loads(p.read_text())["agency"] for p in FIXTURES}
    assert set(LAYOUTS) | {"ntcd", "south-shore-tma"} <= covered | {"el-dorado-planning"}
    assert all(has_parser(agency) for agency in covered)


def _fixture(name):
    return _load(next(p for p in FIXTURES if p.stem == name))


def test_truncated_agenda_is_rejected():
    fixture, pages = _fixture("ndep--2026-08-19")
    last_page = max(p["page_number"] for p in pages)
    result = parse_agenda("ndep", [{"id": "doc"}], [p for p in pages if p["page_number"] < last_page])
    assert not result.complete and "ending was not found" in result.warning


def test_numbering_gap_needs_the_label_to_be_absent():
    fixture, pages = _fixture("ndep--2026-08-19")
    # The source genuinely skips item 14. If a line labelled 14 existed, the gap would be a parse error.
    tampered = [dict(p, text=p["text"].replace("  15    2027 Meeting Schedule", "  14    Inserted\n  15    2027 Meeting Schedule"))
                for p in pages]
    result = parse_agenda("ndep", [{"id": "doc"}], tampered)
    assert result.complete
    assert [i["source_number"] for i in result.items][12:15] == ["13", "14", "15"]
    missing_middle = [dict(p, text=p["text"].replace("  12    Nye County", "  12x   Nye County")) for p in pages]
    assert not parse_agenda("ndep", [{"id": "doc"}], missing_middle).complete


def test_wrong_agency_cover_is_not_recognized():
    _, pages = _fixture("ntpud--2026-08-11")
    items, warning = parse_layout(LAYOUTS["stpud"][0], "doc", pages)
    assert not items and "cover was not recognized" in warning


def test_committee_agendas_before_the_board_agenda_are_skipped():
    fixture, pages = _fixture("trpa-governing--2026-09-23")
    result = parse_agenda("trpa-governing", [{"id": "doc"}], pages)
    titles = [i["title"] for i in result.items]
    assert titles[0] == "CALL TO ORDER AND DETERMINATION OF QUORUM"
    assert "August Financials" in titles  # consent item printed after the board agenda
    assert result.items[-1]["source_number"] == "13"
