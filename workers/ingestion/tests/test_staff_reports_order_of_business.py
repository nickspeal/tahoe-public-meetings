"""The ``order-of-business-item-files`` rule (State Lands Commission meeting pages); no network."""
import json
from pathlib import Path

from tests.test_staff_reports import run_rule

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/staff-reports"
PAGE = "https://www.slc.ca.gov/meeting/12-17-24-voting-record/"
ITEM = "state-lands:2024-12-17:11"


def load():
    return json.loads((FIXTURES / "state-lands.json").read_text())


def test_item_maps_to_the_report_its_row_links_with_the_evidence_that_agreed():
    found, ctx, read = run_rule(load())
    mapping = found["11"]
    assert mapping.url.endswith("/2024/12/12-17-24_11.pdf") and (mapping.page_start, mapping.page_end) == (1, 7)
    assert mapping.evidence["agendaLink"] == "12-17-24_11.pdf" and mapping.evidence["reportHeading"] == "Staff Report 11"
    assert mapping.evidence["meetingDate"] == "2024-12-17" and mapping.evidence["titleOverlap"] >= 0.6
    assert mapping.title == "Item 11 staff report"
    assert "state-lands:2024-12-17:V" in ctx.skipped  # a part of the meeting (Consent Calendar), not an item
    assert read == [PAGE, mapping.url]  # the page, then only the item's own report, never its public comment letters


def test_report_for_another_meeting_date_is_not_stored():
    data = load()
    report = next(iter(data["downloads"].values()))
    report["pages"][0]["text"] = report["pages"][0]["text"].replace("Meeting Date: 12/17/24", "Meeting Date: 10/17/24")
    found, ctx, _ = run_rule(data)
    assert found["11"] is None and ctx.skipped[ITEM] == "report is for another meeting date"


def test_report_naming_another_item_is_not_stored():
    data = load()
    report = next(iter(data["downloads"].values()))
    report["pages"][0]["text"] = report["pages"][0]["text"].replace("Staff Report 11", "Staff Report 12")
    found, ctx, _ = run_rule(data)
    assert found["11"] is None and ctx.skipped[ITEM] == "report does not name item 11"


def test_a_row_link_named_for_another_item_or_meeting_is_not_followed():
    for wrong in ("12-17-24_12.pdf", "10-17-24_11.pdf"):
        data = load()
        data["html"][PAGE] = data["html"][PAGE].replace("12-17-24_11.pdf", wrong)
        found, ctx, read = run_rule(data)
        assert found["11"] is None and read == [PAGE]
        assert ctx.skipped[ITEM] == "0 report links named for item 11 of this meeting"


def test_a_renumbered_agenda_is_not_mapped():
    data = load()
    data["items"][1]["title"] = "NAPA SANITATION DISTRICT (LESSEE/APPLICANT) Consider amendment of a General Lease – Public Agency Use"
    found, ctx, read = run_rule(data)
    assert found["11"] is None and read == [PAGE]
    assert ctx.skipped[ITEM] == "agenda page title differs from the captured item"
