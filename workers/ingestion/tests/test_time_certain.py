import json
from pathlib import Path

import pytest

from tahoe_ingest.agenda import parse_agenda
from tahoe_ingest.time_certain import time_certain

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/agenda-layouts/el-dorado-supervisors--2026-09-29.json"


def exported(fixture: dict) -> tuple[list[dict], list[dict]]:
    pages = [{"document_id": "agenda", "page_number": p["page_number"], "text": p["text"]} for p in fixture["pages"]]
    result = parse_agenda(fixture["agency"], [{"id": "agenda"}], pages)
    items = [{"id": i["source_number"] or f"order-{i['order']}", "title": i["title"],
              "citations": [{"documentId": c["document_id"], "page": c["page_number"], "quote": c["quote"]}
                            for c in i["source_pages"]]} for i in result.items]
    return [{"id": "agenda", "pages": [{"number": p["page_number"], "text": p["text"]} for p in pages]}], items


def test_time_allocation_headings_apply_to_the_items_below_them():
    docs, items = exported(json.loads(FIXTURE.read_text()))
    timed = time_certain(docs, items)
    assert {k: v["time"] for k, v in timed.items()} == {"31": "9:00 AM", "32": "11:00 AM"}
    assert timed["32"]["quote"] == "11:00 A.M. - TIME ALLOCATION (Items will not be heard prior to time stated)"
    assert timed["32"]["page"] == 14


def page_doc(text: str) -> list[dict]:
    return [{"id": "doc", "pages": [{"number": 1, "text": text}]}]


def item(item_id: str, quote: str, title: str | None = None) -> dict:
    return {"id": item_id, "title": title or quote, "citations": [{"documentId": "doc", "page": 1, "quote": quote}]}


def test_heading_group_ends_at_next_section_heading():
    text = ("10:00 A.M. - TIME ALLOCATION (Items will not be heard prior to the time stated)\n"
            "  5. Sheriff presentation\n     FUNDING: N/A\n  6. Second timed item\n"
            "OPEN FORUM\n  7. Untimed item\n")
    timed = time_certain(page_doc(text), [item("5", "Sheriff presentation"), item("6", "Second timed item"),
                                          item("7", "Untimed item")])
    assert {k: v["time"] for k, v in timed.items()} == {"5": "10:00 AM", "6": "10:00 AM"}


def test_cover_page_list_of_timed_items_is_not_a_heading():
    text = ("Timed items on this agenda will be heard no earlier than the time listed below:\n"
            "3:00 P.M. Public Hearing Approving Notice of Exemption\n"
            "A. CALL TO ORDER\nB. REVIEW AGENDA\n")
    assert time_certain(page_doc(text), [item("A", "CALL TO ORDER"), item("B", "REVIEW AGENDA")]) == {}


@pytest.mark.parametrize("quote,expected", [
    ("Receive a Clean Water Project Presentation (Time-Certain 2:00 p.m.)", "2:00 PM"),
    ("APPROVE NOTICE OF EXEMPTION (Timed Item 3:00 p.m.) – Adopt Resolution", "3:00 PM"),
    ("SUBJECT: (For Possible Action) PUBLIC HEARING - Time: 4:00 PM and as soon thereafter as practicable.", "4:00 PM"),
    ("9:00 AM – PUBLIC HEARING: City of Auburn Draft 2045 General Plan", "9:00 AM"),
    ("12:15 p.m. (no earlier than) Environmental Improvement Program Committee", "12:15 PM"),
    ("Changing the 2026 Election Day to Thursday, November 5th at 5:30 PM", None),
    ("ADJOURNMENT (The next Regular Board Meeting is Thursday, October 15, 2026, at 2:00 p.m.)", None),
])
def test_time_written_into_the_item(quote, expected):
    timed = time_certain(page_doc(quote), [item("x", quote)])
    assert (timed["x"]["time"] if "x" in timed else None) == expected
