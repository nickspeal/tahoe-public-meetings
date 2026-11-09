"""Granicus agenda pages lettered by section (Tahoe City PUD), read from their markup by ``html_agenda``."""
import json
from pathlib import Path

from tahoe_ingest.agenda import parse_agenda
from tahoe_ingest.html_agenda import parse_html_agenda
from tahoe_ingest.html_source import html_text

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests/fixtures/html-agendas"
REPEATED = (FIXTURES / "tcpud--clip-1748.html").read_text(encoding="utf-8")


def parse(markup):
    return parse_html_agenda(markup, html_text(markup))


def rows(items):
    return [[i["source_number"], i["section"], i["title"]] for i in items]


def test_section_printed_again_by_an_unclosed_blockquote_keeps_every_item_once():
    items, complete = parse(REPEATED)
    assert complete
    assert rows(items) == json.loads((FIXTURES / "tcpud--clip-1748.json").read_text(encoding="utf-8"))["expected"]
    numbers = [i["source_number"] for i in items]
    # F.5 follows the copy of section G that Granicus nests in F.4; G's items appear once, and the empty
    # second bold G heading adds nothing.
    assert numbers[numbers.index("F.4"):numbers.index("H")] == ["F.4", "F.5", "G.1", "G.2", "G.3", "G.4", "G.5", "G.6"]
    by_number = {i["source_number"]: i for i in items}
    assert by_number["F.5"]["title"] == "2027-2031 - Draft Capital Improvement Plan"
    assert by_number["G.1"]["section"] == "Department Reports And Updates"
    assert by_number["H"]["title"] == "CORRESPONDENCE" and by_number["H"]["section"] is None
    text = html_text(REPEATED)
    for order, item in enumerate(items, 1):
        assert item["order"] == order and item["source_pages"][0]["quote"] in text


def test_well_formed_page_reads_as_the_outline_engine_reads_its_text():
    """The same items, sections, and titles as the tcpud-board layout gives from the page's text."""
    page = next(iter(json.loads((ROOT / "tests/fixtures/staff-reports/tcpud.json").read_text())["html"].values()))
    layout = json.loads((ROOT / "tests/fixtures/agenda-layouts/tcpud--2026-09-18.json").read_text())
    items, complete = parse(page)
    assert complete and rows(items) == layout["expected"]
    from_text = parse_agenda("tcpud", [{"id": "doc"}], [{"document_id": "doc", "page_number": 1, "text": html_text(page)}])
    assert rows(from_text.items) == rows(items)


def test_the_outline_engine_drops_the_item_the_markup_keeps():
    """Why the markup is read: the page's text alone loses F.5 (the stored tcpud:1187 items)."""
    from_text = parse_agenda("tcpud", [{"id": "doc"}], [{"document_id": "doc", "page_number": 1, "text": html_text(REPEATED)}])
    items, _ = parse(REPEATED)
    assert "F.5" not in [i["source_number"] for i in from_text.items]
    assert [r for r in rows(items) if r not in rows(from_text.items)] == [
        ["F.5", "Action Items / General Business – Discussion And Possible Action", "2027-2031 - Draft Capital Improvement Plan"]]


def _row(label, title, *, bold=False):
    label = f"<strong>{label}.</strong>" if bold else f"{label}."
    return f'<table><tbody><tr><td class = "numberspace">{label}</td><td>{title}</td></tr></tbody></table>'


def _page(*parts):
    return "<p>TAHOE CITY PUBLIC UTILITY DISTRICT</p>" + "".join(parts)


def _quote(*parts):
    return "<blockquote>" + "".join(parts) + "</blockquote>"


def test_only_verbatim_repeats_are_skipped():
    good = _page(_row("A", "CALL TO ORDER", bold=True),
                 _row("B", "ACTION ITEMS", bold=True), _quote(_row("1", "Budget"), _quote(_row("C", "REPORTS"), _quote(_row("1", "Report"))), _row("2", "Fees")),
                 _row("C", "REPORTS", bold=True), _quote(_row("1", "Report")),
                 _row("D", "ADJOURNMENT", bold=True))
    items, complete = parse(good)
    assert complete and [i["source_number"] for i in items] == ["A", "B.1", "B.2", "C.1", "D"]
    # A nested section that does not repeat a real one verbatim is not dropped: the page is rejected.
    altered = good.replace(_quote(_row("C", "REPORTS"), _quote(_row("1", "Report"))),
                           _quote(_row("C", "REPORTS"), _quote(_row("1", "Another report"))))
    assert parse(altered) == ([], False)
    # Letters must follow in order, and items within a section.
    assert parse(good.replace(_row("C", "REPORTS", bold=True), _row("E", "REPORTS", bold=True))) == ([], False)
    assert parse(good.replace(_row("2", "Fees"), _row("3", "Fees"))) == ([], False)
    assert parse(good.replace("ADJOURNMENT", "CLOSED SESSION")) == ([], False)


def test_lettered_reading_is_a_fallback_for_pages_the_numbered_reading_rejects():
    # Numbered rows that run 1..n through the page keep their existing reading; letters are not read.
    numbered = _page(_row("A", "OPENING", bold=True), _quote(_row("1", "Call to order")),
                     _row("B", "BUSINESS", bold=True), _quote(_row("2", "Budget"), _row("3", "Adjournment")))
    items, complete = parse(numbered)
    assert complete and [i["source_number"] for i in items] == ["1", "2", "3"]
