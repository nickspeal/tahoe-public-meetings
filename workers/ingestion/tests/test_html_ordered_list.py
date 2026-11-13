"""The ordered-list HTML agenda (the Olympic Valley Public Service District's agenda posts)."""
import json
from pathlib import Path

from tahoe_ingest import html_ordered_list as ordered
from tahoe_ingest.html_agenda import parse_html_agenda
from tahoe_ingest.html_source import html_text

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/html-agendas"
PAGE = (FIXTURES / "ovpsd--2026-10-27.html").read_text(encoding="utf-8")
EXPECTED = json.loads((FIXTURES / "ovpsd--2026-10-27.json").read_text(encoding="utf-8"))
SEPTEMBER = (FIXTURES / "ovpsd--2026-09-29.html").read_text(encoding="utf-8")
MARCH = (FIXTURES / "ovpsd--2026-03-31.html").read_text(encoding="utf-8")
URL = EXPECTED["source"]


def parse(markup):
    return parse_html_agenda(markup, html_text(markup))


def test_agenda_post_gives_each_lettered_part_and_its_numbered_items_with_exact_quotes():
    items, complete = parse(PAGE)
    assert complete
    assert [[i["source_number"], i["section"], i["title"]] for i in items] == EXPECTED["expected"]
    text = html_text(PAGE)
    for order, item in enumerate(items, 1):
        assert item["order"] == order
        ref = item["source_pages"][0]
        assert ref["quote"] in text and ref["page_number"] == 1 and ref["document_id"] == "__html__"


def test_a_part_title_split_over_bold_runs_and_a_time_note_before_a_label_are_read():
    items, complete = parse(SEPTEMBER)
    by_number = {i["source_number"]: i for i in items}
    assert complete
    # "<strong>Old </strong><strong>and New Business. </strong>"; F-1 opens with "8:30 a.m. or as soon as ...".
    assert by_number["F-1"]["section"] == "Old and New Business"
    assert by_number["F-1"]["title"] == "PUBLIC HEARING: Fire Impact Fee Program"


def test_a_nested_list_without_labels_describes_its_part():
    items, complete = parse(MARCH)
    by_number = {i["source_number"]: i for i in items}
    assert complete
    assert by_number["H"]["title"] == "Closed Session – Public Employee Performance Evaluation"
    assert by_number["H"]["section"] is None
    assert [i["source_number"] for i in items][-3:] == ["H", "I", "J"]
    assert not any("Government Code" in i["title"] for i in items)


def test_a_label_typed_with_a_space_keeps_its_number():
    typo = PAGE.replace(">F-5 ", ">F- 5 ", 1)
    assert typo != PAGE
    items, complete = parse(typo)
    assert complete and "F-5" in [i["source_number"] for i in items]


def test_letters_come_from_the_page_style_and_must_agree_with_printed_labels():
    unstyled = PAGE.replace("list-style-type: upper-alpha", "list-style-type: decimal")
    assert parse(unstyled) == ([], False)  # "B-1" printed under the second part, which would be "2"
    skipped = PAGE.replace("<strong>D-12</strong>", "<strong>D-13</strong>", 1)
    assert skipped != PAGE and parse(skipped) == ([], False)
    unended = PAGE.replace("<strong>Adjourn</strong>", "<strong>Closing</strong>")
    assert unended != PAGE and parse(unended) == ([], False)


def test_rows_keep_each_item_s_own_links():
    rows = {r["number"]: r for r in ordered.agenda_rows(PAGE, URL)}
    assert rows["F-2"]["links"] == ["https://ovpsd.org/wp-content/uploads/2026/10/F-2_2026-10-27_Bike-Trail-Snow-Removal-Contract_Compiled-1.pdf"]
    assert rows["B-2"]["links"] == [] and rows["A"]["links"] == []
    assert rows["E-1"]["links"][0].endswith("/E-1_2026-09-29_RegularMeetingMinutes_DRAFT.pdf")
