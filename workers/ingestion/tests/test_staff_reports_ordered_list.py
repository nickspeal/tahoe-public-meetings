"""The ``ordered-list-item-reports`` rule (Olympic Valley PSD agenda posts); no network."""
import json
from pathlib import Path

from tests.test_staff_reports import run_rule

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/staff-reports"
PAGE = "https://ovpsd.org/agenda/regular-board-meeting-tuesday-october-27-2026-830-a-m/"
REPORT = "https://ovpsd.org/wp-content/uploads/2026/10/F-2_2026-10-27_Bike-Trail-Snow-Removal-Contract_Compiled-1.pdf"
ITEM = "ovpsd:2026-10-27:F-2"


def load():
    return json.loads((FIXTURES / "ovpsd.json").read_text())


def test_item_maps_to_the_board_report_its_entry_links_with_the_evidence_that_agreed():
    found, ctx, read = run_rule(load())
    mapping = found["F-2"]
    assert mapping.url == REPORT and (mapping.page_start, mapping.page_end) == (1, 13)
    assert mapping.evidence["agendaLink"] == REPORT.rsplit("/", 1)[1] and mapping.evidence["header"] == "BOARD REPORT"
    assert mapping.evidence["reportHeading"] == "EXHIBIT: F-2" and mapping.evidence["meetingDate"] == "2026-10-27"
    assert mapping.title == "Item F-2 board report"
    assert ctx.skipped["ovpsd:2026-10-27:D-1"] == "not a board report"  # "EXHIBIT D-1" on a check register
    assert ctx.skipped["ovpsd:2026-10-27:B-1"] == "not a board report"  # another agency's meeting summary
    assert ctx.skipped["ovpsd:2026-10-27:G-6"] == "0 PDF links under item G-6"  # a verbal report
    assert ctx.skipped["ovpsd:2026-10-27:E-1"] == "procedural"
    assert read[0] == PAGE and len(read) == 6  # the page, then each linked file once


def test_report_for_another_meeting_date_is_not_stored():
    data = load()
    data["downloads"][REPORT]["pages"][0]["text"] = data["downloads"][REPORT]["pages"][0]["text"].replace(
        "October 27, 2026", "September 29, 2026")
    found, ctx, _ = run_rule(data)
    assert found["F-2"] is None and ctx.skipped[ITEM] == "report is for another meeting date"


def test_report_naming_another_item_is_not_stored():
    data = load()
    first = data["downloads"][REPORT]["pages"][0]
    first["text"] = first["text"].replace("F-2, 13 Pages", "F-3, 13 Pages")
    found, ctx, _ = run_rule(data)
    assert found["F-2"] is None and ctx.skipped[ITEM] == "report does not name item F-2"


def test_a_link_named_for_another_item_is_not_followed():
    data = load()
    wrong = REPORT.replace("/F-2_", "/F-3_")
    data["html"][PAGE] = data["html"][PAGE].replace(REPORT, wrong)
    found, ctx, read = run_rule(data)
    assert found["F-2"] is None and wrong not in read
    assert ctx.skipped[ITEM] == "the linked file is not named for the item"


def test_a_changed_agenda_is_not_mapped():
    data = load()
    data["items"][[i["number"] for i in data["items"]].index("F-2")]["title"] = "Award Contract – 2026 Sewer Rehabilitation Project"
    found, ctx, read = run_rule(data)
    assert found["F-2"] is None and REPORT not in read
    assert ctx.skipped[ITEM] == "agenda page title differs from the captured item"
