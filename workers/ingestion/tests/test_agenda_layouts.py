"""Replay real agency agenda text through the configured agenda layouts.

Each fixture in tests/fixtures/agenda-layouts holds the extracted pages of one
official agenda and the reviewed item list. "expected": null marks a document
that must be rejected rather than guessed (a malformed agenda, or a synopsis).
"notes" lists the review notes of an agenda published despite irregular
numbering (decision 0032); a fixture without it must parse cleanly.
Fixtures name an agency, so they also exercise the layouts its registry entry lists.
"""
import json
import re
from pathlib import Path

import pytest

from tahoe_ingest.agenda import _parse_document, agenda_config, has_parser, parse_agenda
from tahoe_ingest.agenda_layouts import _layout, layouts, validate_agency_agendas
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.outline import PLACEHOLDER_TITLE, is_placeholder, parse_layout

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
    assert list(result.notes) == fixture.get("notes", [])
    assert all("review required" in note for note in result.notes)
    page_text = {p["page_number"]: p["text"] for p in pages}
    keys = [i.get("source_key") or i["source_number"] for i in result.items]
    assert len(keys) == len(set(keys)), "item keys must be unique within a meeting"
    for order, item in enumerate(result.items, 1):
        assert item["order"] == order
        ref = item["source_pages"][0]
        if is_placeholder(item):  # a skipped number: nothing in the agenda to quote
            assert item["title"] == PLACEHOLDER_TITLE and not ref["quote"]
            continue
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
    # A malformed label ("12x") is not a gap in the source, so it gets no placeholder; the items after it
    # are still published, with a review note (decision 0032).
    missing_middle = [dict(p, text=p["text"].replace("  12    Nye County", "  12x   Nye County")) for p in pages]
    result = parse_agenda("ndep", [{"id": "doc"}], missing_middle)
    assert result.complete
    assert [i["source_number"] for i in result.items][10:13] == ["11", "13", "15"]
    assert not any(is_placeholder(i) for i in result.items)
    assert result.notes == ("Nevada Division of Environmental Protection board agenda numbering is not sequential near "
                            "“13    Las Vegas Valley Water District – Blue Diamond Water S”; review required.",)


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
    # An addendum that skips a number keeps its items and lists the skipped number as a placeholder.
    skipped = [dict(p, text=p["text"].replace("    29. 26-1714", "    30. 26-1714")) for p in pages]
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], skipped)
    assert result.complete
    numbers = [i["source_number"] for i in result.items]
    assert numbers.index("29") + 1 == numbers.index("30") == numbers.index("9") + 2
    assert is_placeholder(result.items[numbers.index("29")])
    assert result.notes == ("El Dorado County Legistar agenda skips item 29; it is listed as “Not Found In Agenda”; review required.",)
    # A jump too far to bridge keeps the main agenda and notes the line it passed over.
    jumped = [dict(p, text=p["text"].replace("    29. 26-1714", "    35. 26-1714")) for p in pages]
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], jumped)
    assert result.complete and {"29", "35"}.isdisjoint(i["source_number"] for i in result.items)
    assert len(result.notes) == 1 and "addendum numbering is not sequential" in result.notes[0]


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


def test_ttd_excerpts_leave_out_the_packet_page_column():
    from tahoe_ingest.core import _summary_from_citations, excerpt_text

    fixture, pages = _fixture("ttd--2026-10-07")
    quotes = {i["source_number"]: i["source_pages"][0]["quote"] for i in parse_agenda("ttd", [{"id": "doc"}], pages).items}
    # The quotes stay verbatim, so they span the column; the excerpt shown from them does not.
    assert quotes["I.C"].endswith("Page 1")
    assert _summary_from_citations([{"quote": quotes["I.C"]}]) == (
        "Agenda excerpt: “For Possible Action: Approval of Minutes of September 16, 2026”")
    assert excerpt_text(quotes["IV.B"]) == ("For Possible Action: Review and Accept the District’s Financial\n"
                                            "            Statement of Operations for the First Month of Fiscal Year 2027 Through\n"
                                            "            July 31, 2026")
    assert excerpt_text(quotes["VII"]) == "FOR INFORMATION: EXECUTIVE DIRECTOR REPORT AND AGENDA\n        CALENDAR CHECK"
    for number, quote in quotes.items():
        assert not re.search(r"\s{3,}(?:Page\s*)?\d*\s*$", excerpt_text(quote), re.M), number
    # A title's own number is kept: it follows a single space, or starts a wrapped line.
    assert excerpt_text(quotes["IV.D"]).startswith("For Possible Action: Adopt Resolution 2026-009 Authorizing the\n")
    for quote in ("Adopt Resolution 2026-12", "Approval of Minutes of May 6, 2026", "Fiscal Year\n            2026",
                  "Phase 2", "Amendment No. 3"):
        assert excerpt_text(quote) == quote


def test_ntcd_titles_and_excerpts_run_across_wrapped_lines():
    from tahoe_ingest.core import excerpt_text

    def parsed(name):
        fixture, pages = _fixture(name)
        items = parse_agenda("ntcd", [{"id": "doc"}], pages).items
        return ({i["source_number"]: i["title"] for i in items},
                {i["source_number"]: " ".join(excerpt_text(i["source_pages"][0]["quote"]).split()) for i in items})

    titles, excerpts = parsed("ntcd--2026-04-27")
    # The cell's label and page number ("A ... 4") sit between the title's two lines; the excerpt leaves them out.
    assert excerpts["5.A"] == "For Possible Action: Approval of the Board of Supervisors Meeting Minutes for March 23, 2026"
    assert excerpts["5.B"] == "For Possible Action: Approval of the NTCD Proposed Budget and Work Plan for Fiscal Year 2027"
    assert titles["11"].endswith("with Tahoe Douglas Fire Protection District in the Amount of $170,000")
    # The description below a title ("This item is to update ...") stays out of it.
    assert titles["9"] == "Increase of District Manager Signing Authority"
    titles, excerpts = parsed("ntcd--2026-08-12")
    # A wrapped title line in title case continues the title; the description's first sentence does not.
    assert titles["5.E"] == ("Approval of the Upper Third Creek and Rosewood Creek Construction Inspection Agreement with "
                             "Washoe County for $35,765.00, Expiring on December 31, 2026")
    assert excerpts["9"] == ("For Possible Action: Approval of the Tahoe Meadows West Trailhead Improvement Project Grant "
                             "Agreement for $145,000 with the Nevada Department of Conservation and Natural Resources")
    assert excerpts["12.C"] == ("Report: Department of Conservation and Natural Resources Conservation District Program "
                                "report (No Action)")


def test_lahontan_board_titles_end_at_the_description_dash():
    # Regular and special sessions share one agenda; numbering runs on across the day headings,
    # lettered closed session and spill notices stay inside their items, and the description
    # after the first " – " is dropped (with a trailing dash when it wraps to the next line).
    for name, count, last in (("lahontan-board--2026-05-06", 13, "Board Member Requests for Future Agenda Items and Other Items of Interest"),
                              ("lahontan-board--2026-10-01", 11, "Closed Session")):
        fixture, pages = _fixture(name)
        result = parse_agenda("lahontan-board", [{"id": "doc"}], pages)
        assert result.complete, result.warning
        assert [i["source_number"] for i in result.items] == [str(n) for n in range(1, count + 1)]
        assert result.items[-1]["title"] == last
        assert not any("–" in i["title"] or i["title"].startswith("*") for i in result.items)
    fixture, pages = _fixture("lahontan-board--2026-05-06")
    titles = [i["title"] for i in parse_agenda("lahontan-board", [{"id": "doc"}], pages).items]
    assert titles[7:9] == ["Closed Session", "Public Forum"]  # "Closed Session1" footnote marker, then Thursday's session
    assert "Adjournment" not in titles and "Call to Order and Introductions" not in titles
    # A missing item number is published as a placeholder with a warning rather than renumbered (decision 0032).
    tampered = [dict(p, text=p["text"].replace("\n9. Public Forum", "\n90. Public Forum")) for p in pages]
    result = parse_agenda("lahontan-board", [{"id": "doc"}], tampered)
    assert result.complete and any(i["title"] == "Not Found In Agenda" and i["source_number"] == "9" for i in result.items)
    assert any("skips item 9" in note for note in result.notes)


def test_a_title_cut_at_the_line_limit_keeps_its_full_text():
    # El Dorado County, September 29, 2026: item 11's recommendation runs past the layout's eight title lines.
    fixture, pages = _fixture("el-dorado-supervisors--2026-09-29")
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], pages)
    by_number = {i["source_number"]: i for i in result.items}
    cut = by_number["11"]
    assert cut["title"].endswith("with a term expiration of September 29, 2028;")
    assert cut["full_title"].startswith(cut["title"])
    assert cut["full_title"].endswith("4) Reappoint Christine Noonan, General Manager/Owner Delfino Farms, "
                                      "with a term expiration of September 29, 2028.")
    assert "FUNDING" not in cut["full_title"]  # the funding line after the blank row is not the title
    # A title within the limit has no full text, and the quote (the source fingerprint's evidence) is unchanged.
    assert all("full_title" not in i for i in result.items if i["title"].endswith((".", ")")) and len(i["title"]) < 200)
    assert len(cut["source_pages"][0]["quote"]) <= 300


def test_tnt_tma_word_list_residue_is_not_an_item_number():
    # January 8, 2026: Word's list numbering prints "1.​ DISCUSSION ITEMS" and "2.​ 1. Discussion about ...".
    fixture, pages = _fixture("truckee-north-shore-tma--2026-01-08")
    assert "2.​ 1. Discussion about" in pages[0]["text"]
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], pages)
    assert result.complete and not result.notes
    discussion = [i for i in result.items if i["section"] == "Discussion Items"]
    assert [(i["source_number"], i["title"][:24]) for i in discussion] == [("1", "Discussion about TMA Hel")]
    assert discussion[0]["full_title"].endswith("267 and Old Brockway Road")
    assert not any(i["title"] == "DISCUSSION ITEMS" for i in result.items)
    # Presenter and stray time lines end a title.
    assert {i["title"] for i in result.items} >= {"TRPA Comprehensive Operational Analysis (COA) Presentation",
                                                  "TMA Strategic Plan and Workplan, One-Year Review"}
