"""Replay real agency agenda text through the configured agenda layouts.

Each fixture in tests/fixtures/agenda-layouts holds the extracted pages of one
official agenda and the reviewed item list. "expected": null marks a document
that must be rejected rather than guessed (a malformed agenda, or a synopsis).
Fixtures name an agency, so they also exercise the layouts its registry entry lists.
"""
import json
import re
from pathlib import Path

import pytest

from tahoe_ingest.agenda import _parse_document, agenda_config, has_parser, parse_agenda
from tahoe_ingest.agenda_layouts import _layout, layouts, validate_agency_agendas
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.outline import parse_layout

FIXTURES = sorted((Path(__file__).resolve().parents[3] / "tests/fixtures/agenda-layouts").glob("*.json"))
MEETINGS = Path(__file__).resolve().parents[3] / "tests/fixtures/meetings.json"


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


def test_every_layout_is_used_and_replayed_by_a_fixture():
    named = {name for agency in agency_catalog() for name in agency.get("agenda", {}).get("layouts", [])}
    assert named == set(layouts()), "a layout no agency lists is dead configuration"
    documents = [(fixture["agency"], pages) for fixture, pages in map(_load, FIXTURES)]
    documents += [(m["agency"], [dict(p, document_id="doc") for p in m["pages"] if p["document_id"] == d["id"]])
                  for m in json.loads(MEETINGS.read_text())["meetings"] for d in m["documents"]]
    assert all(has_parser(agency) for agency, _ in documents)
    replayed = {name for agency, pages in documents for name in agenda_config(agency)["layouts"]
                if pages and _parse_document((layouts()[name],), "doc", pages)[0].complete}
    assert replayed == set(layouts())


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
    items, warning = parse_layout(layouts()["stpud-board"], "doc", pages)
    assert not items and "cover was not recognized" in warning


def test_committee_agendas_before_the_board_agenda_are_skipped():
    fixture, pages = _fixture("trpa-governing--2026-09-23")
    result = parse_agenda("trpa-governing", [{"id": "doc"}], pages)
    titles = [i["title"] for i in result.items]
    assert titles[0] == "CALL TO ORDER AND DETERMINATION OF QUORUM"
    assert "August Financials" in titles  # consent item printed after the board agenda
    assert result.items[-1]["source_number"] == "13"


def test_accessible_html_copy_does_not_discard_a_validated_pdf():
    # Legistar lists the agenda PDF and its accessible HTML copy (M=AADA) for the same meeting.
    fixture, pages = _load(next(p for p in FIXTURES if p.stem == "el-dorado-supervisors--2026-09-29"))
    html = [{"document_id": "html", "page_number": 1, "text": pages[0]["text"]}]
    docs = [{"id": "doc", "media_type": "application/pdf"}, {"id": "html", "media_type": "text/html"}]
    result = parse_agenda(fixture["agency"], docs, pages + html)
    assert result.complete, result.warning
    assert len(result.items) == len(fixture["expected"])
    # A second PDF that fails validation still blocks publication.
    docs[1]["media_type"] = "application/pdf"
    assert not parse_agenda(fixture["agency"], docs, pages + html).complete


def test_addendum_items_join_their_named_sections():
    fixture, pages = _fixture("el-dorado-supervisors--2026-10-27-addendum")
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], pages)
    numbers = [i["source_number"] for i in result.items]
    assert numbers.index("28") == numbers.index("5") + 1  # "Item 28 is hereby added to Closed Session"
    assert numbers.index("29") == numbers.index("9") + 1  # after the last General Government consent item
    # An addendum that skips a number is rejected rather than published without its items.
    skipped = [dict(p, text=p["text"].replace("    29. 26-1714", "    30. 26-1714")) for p in pages]
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], skipped)
    assert not result.complete and "addendum numbering" in result.warning


def test_a_redesigned_cover_blocks_publication_instead_of_being_ignored():
    # "recognize" tells the publisher's agenda with an unexpected cover from an unrelated attachment.
    meeting = next(m for m in json.loads(MEETINGS.read_text())["meetings"] if m["agency"] == "ttd" and m["documents"])
    redesigned = [dict(p, text=p["text"].replace("Notice of Agenda", "Notice of Meeting")) for p in meeting["pages"]]
    second = [dict(p, document_id="second") for p in redesigned]
    result = parse_agenda("ttd", meeting["documents"] + [{"id": "second"}], meeting["pages"] + second)
    assert not result.complete  # the second copy's cover no longer matches, but it is still a TTD agenda
    assert parse_agenda("ttd", meeting["documents"][:1], redesigned).warning == \
        "TTD agenda cover was not recognized; items require review."
    unrelated = [dict(p, text="Staff report\n" + p["text"].replace("TAHOE TRANSPORTATION DISTRICT", "")) for p in meeting["pages"]]
    memo = [dict(p, document_id="memo") for p in unrelated]
    assert parse_agenda("ttd", meeting["documents"] + [{"id": "memo"}], meeting["pages"] + memo).complete


@pytest.mark.parametrize("spec,problem", [
    ({"cover": ["X"], "levels": ["NUM"]}, "label and cover are required"),
    ({"label": "L", "cover": ["X"]}, "levels are required"),
    ({"label": "L", "cover": ["X"], "levels": ["NUMBER"]}, "unknown level NUMBER"),
    ({"label": "L", "cover": ["X"], "levels": [{"kind": "num", "pattern": "\\d+"}]}, "group v"),
    ({"label": "L", "cover": ["X"], "levels": [{"kind": "digits", "pattern": "(?P<v>\\d)"}]}, "level kind"),
    ({"label": "L", "cover": ["X"], "levels": ["NUM"], "headings": "@CAPS"}, "unknown fragment @CAPS"),
    ({"label": "L", "cover": ["X"], "levels": ["NUM"], "headings": "@FOR_ACTION"}, "not a pattern"),
    ({"label": "L", "cover": ["X"], "levels": ["NUM"], "titleCleanup": ["@CAPS_HEADING"]}, "not a cleanup pair"),
    ({"label": "L", "cover": ["X"], "levels": ["NUM"], "titleCleanup": [["(a)", "\\2"]]}, "invalid group reference"),
    ({"label": "L", "cover": ["(unclosed"], "levels": ["NUM"]}, "missing )"),
    ({"label": "L", "cover": ["X"], "levels": ["NUM"], "wrap": "never"}, "wrap must be"),
    ({"label": "L", "cover": ["X"], "levels": ["NUM"], "firstPages": 0}, "first_pages"),
    ({"label": "L", "cover": ["X"], "levels": ["NUM"], "maxLines": 2}, "unknown key maxLines"),
    ({"label": "L", "cover": ["X"], "parser": "ttd"}, "unknown parser"),
    ({"label": "L", "cover": ["X"], "parser": "ttd-sections", "levels": ["NUM"]}, "a parser layout takes only"),
])
def test_layout_configuration_is_validated(spec, problem):
    with pytest.raises(ValueError, match="agenda layout example: .*" + re.escape(problem)):
        _layout("example", spec)


def test_agency_agenda_blocks_name_configured_layouts():
    validate_agency_agendas([{"id": "a", "agenda": {"layouts": ["ttd"], "agendaBeforePacket": False}}])
    with pytest.raises(ValueError, match="unknown agenda layout 'ttd-board': a"):
        validate_agency_agendas([{"id": "a", "agenda": {"layouts": ["ttd-board"]}}])
    for agenda in ({"layouts": []}, {"layouts": "ttd"}, {"layouts": ["ttd"], "beforePacket": True},
                   {"layouts": ["ttd"], "agendaBeforePacket": "yes"}, {"layouts": ["ttd", "ttd"]}):
        with pytest.raises(ValueError, match="invalid agenda: a"):
            validate_agency_agendas([{"id": "a", "agenda": agenda}])
