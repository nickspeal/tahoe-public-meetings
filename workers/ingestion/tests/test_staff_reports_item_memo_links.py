"""Item memos linked from a LINKED agenda PDF (staff_report_rules/item_memo_links.py); fixture-driven, no network.

The fixture is the North Tahoe Fire Protection District's October 27, 2026 agenda: its link
annotations and the first pages of the "<number>-CL-..." memos they open.
"""
import pytest

from tahoe_ingest.staff_reports import Uncertain
from test_staff_reports import load, run_rule

ID = "north-tahoe-fire:6ad94f679afb5f265256a341:"
AGENDA = "https://www.ntfire.net/s/AgendaOctober-2026LINKED.pdf"
MEMO_11 = "https://www.ntfire.net/s/11-CL-TTAD-Funding-Agreement-for-River-Road-Bike-Path-Fuels-Reduction-Project.pdf"


def test_memo_maps_by_its_file_number_and_subject_line():
    found, _, read = run_rule(load("north-tahoe-fire"))
    memo = found["11"]
    assert memo.url == MEMO_11 and (memo.page_start, memo.page_end) == (1, 2)
    assert memo.evidence == {"agendaLink": MEMO_11.rsplit("/", 1)[1], "reportHeading": "AGENDA ITEM #11", "titleOverlap": 1.0}
    assert read[0] == AGENDA  # the agenda is read once, for its link annotations


def test_department_reports_numbered_without_the_dot_are_not_item_links():
    found, ctx, _ = run_rule(load("north-tahoe-fire"))
    # "131-Oct-Mtg-Purchases-Journal-....PDF" is 13.1's file, never item 13 or a 131st item.
    assert found["13.1"] is None and ctx.skipped[ID + "13.1"] == "0 item links for item 13.1"


def test_memo_naming_another_item_is_not_stored():
    data = load("north-tahoe-fire")
    memo = data["downloads"][MEMO_11]["pages"][0]
    memo["text"] = memo["text"].replace("AGENDA ITEM #11,", "AGENDA ITEM #12,")
    found, ctx, _ = run_rule(data)
    assert found["11"] is None and ctx.skipped[ID + "11"] == "report does not name this item number"


def test_memo_without_the_number_sign_still_maps():
    data = load("north-tahoe-fire")
    memo = data["downloads"][MEMO_11]["pages"][0]
    memo["text"] = memo["text"].replace("AGENDA ITEM #11,", "AGENDA ITEM 11,")
    found, _, _ = run_rule(data)
    assert found["11"].evidence["reportHeading"] == "AGENDA ITEM 11"


def test_memo_whose_subject_differs_from_the_item_is_not_stored():
    data = load("north-tahoe-fire")
    memo = data["downloads"][MEMO_11]["pages"][0]
    start = memo["text"].index("AGENDA ITEM #11,")
    end = memo["text"].index("\n\n", start)
    memo["text"] = memo["text"][:start] + "AGENDA ITEM #11, CORRESPONDENCE" + memo["text"][end:]
    found, ctx, _ = run_rule(data)
    assert found["11"] is None and ctx.skipped[ID + "11"] == "report title differs from the agenda item"


def test_changed_agenda_maps_nothing():
    data = load("north-tahoe-fire")
    data["downloads"][AGENDA]["sha256"] = "0" * 64
    with pytest.raises(Uncertain, match="changed since it was captured"):
        run_rule(data)
