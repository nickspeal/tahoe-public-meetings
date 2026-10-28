"""North Tahoe PUD staff reports in the stored agenda packet (staff_report_rules/ntpud.py); fixture-driven, no network."""
from tahoe_ingest.staff_report_rules import ntpud
from test_staff_reports import load, run_rule

ID = "ntpud:2026-10-13:"


def packet(data):
    return data["stored"][next(d["version_id"] for d in data["documents"] if d["source_document_id"] == "ntpud-agenda-packet")]


def test_header_naming_another_item_is_not_stored():
    found, ctx, _ = run_rule(load("ntpud"))
    # The T-TSA report for D.1 is misprinted "ITEM: E-1"; E.1's own report (page 8) still maps.
    assert found["D.1"] is None and ctx.skipped[ID + "D.1"] == "page 4 is headed ITEM: E-1"
    assert found["E.1"].page_start == 8


def test_report_for_another_meeting_is_not_stored():
    data = load("ntpud")
    packet(data)["15"] = packet(data)["15"].replace("October 13, 2026", "September 9, 2026", 1)
    found, ctx, _ = run_rule(data)
    assert found["E.3"] is None and ctx.skipped[ID + "E.3"] == "report is for another meeting date"


def test_range_holding_another_report_is_not_stored():
    data = load("ntpud")
    packet(data)["17"] = "DATE:        October 13, 2026                              ITEM: E-4\n" + packet(data)["17"]
    found, ctx, _ = run_rule(data)
    assert found["E.3"] is None and "item E.4" in ctx.skipped[ID + "E.3"]


def test_glued_page_stamp_and_wrapped_rows():
    data = load("ntpud")
    pages = packet(data)
    # pdftotext can drop page 1's stamp and glue it to page 2's first item: "13. Award ..." is stamp 1 + item 3.
    pages["1"] = pages["1"].rstrip().rsplit("\n", 1)[0]
    pages["2"] = "1" + pages["2"].lstrip()
    found, _, _ = run_rule(data)
    assert (found["E.3"].page_start, found["E.3"].page_end) == (15, 18)
    assert found["E.3"].evidence["agendaPages"] == "(Pages 15-18)"
    rows = ntpud.agenda_rows(ntpud.agenda_lines({1: "E.  CONSENT\n   2. Approve the\n      Minutes\n      (Pages 9-14)\n1",
                                                 2: "3. Award a Contract\n   (Pages 15-18)\n2"}))
    assert rows["E.2"] == ["Approve the Minutes (Pages 9-14)"] and rows["E.3"] == ["Award a Contract (Pages 15-18)"]


def test_changed_agenda_row_is_not_stored():
    data = load("ntpud")
    next(i for i in data["items"] if i["number"] == "E.5")["title"] = "Approve the Lakeside Pier Lease Renewal"
    found, ctx, _ = run_rule(data)
    assert found["E.5"] is None and "differs from the item title" in ctx.skipped[ID + "E.5"]
