"""PCTPA memos in the agenda packet (staff_report_rules/pctpa.py); fixture-driven, no network."""
from tahoe_ingest.staff_report_rules import pctpa
from test_staff_reports import load, run_rule

ID = "placer-transportation:2026-10-28:"


def packet(data):
    return data["stored"][data["documents"][0]["version_id"]]


def test_stamp_on_two_pages_is_not_stored():
    data = load("placer-transportation")
    packet(data)["47"] = packet(data)["47"].replace("41\n", "40\n")
    found, ctx, _ = run_rule(data)
    assert found["K"] is None and ctx.skipped[ID + "K"] == "packet page 40 is stamped on 2 pages"
    assert found["J"] is not None


def test_memo_for_another_meeting_or_subject_is_not_stored():
    data = load("placer-transportation")
    packet(data)["76"] = packet(data)["76"].replace("DATE: October 28, 2026", "DATE: September 23, 2026")
    packet(data)["37"] = packet(data)["37"].replace("SERVICE POLICY REVISIONS FOR THE PLACER RIDES PROGRAM", "BUDGET AMENDMENT")
    found, ctx, _ = run_rule(data)
    assert found["L.1"] is None and found["L.2"] is None and ctx.skipped[ID + "L.1"] == "memo is for another meeting date"
    assert found["J"] is None and "subject differs" in ctx.skipped[ID + "J"]
    assert found["K"] is not None


def test_numbered_motions_need_their_parent_heading():
    data = load("placer-transportation")
    for item in data["items"]:
        if item["number"] in ("L.1", "L.2"):
            item["section"] = "SR 65 Phase 1 Project Construction Funding and Agreement Approvals"
    found, ctx, _ = run_rule(data)
    assert found["L.1"] is None and "parent heading differs" in ctx.skipped[ID + "L.1"]


def test_memo_stops_at_the_attachment_label():
    found, _, _ = run_rule(load("placer-transportation"))
    assert (found["K"].page_start, found["K"].page_end) == (46, 49)  # page 50 is "Agenda Item K, Attachment 1"
    assert found["K"].evidence["agendaPageColumn"] == "Pg. 40" and found["L.2"].evidence["agendaHeading"].startswith("L. ")


def test_packet_stamp():
    footer = "2260 Douglas Blvd, Ste 130  Roseville, CA 95661  (530) 823-4030\n        www.pctpa.net           40\n      Rev. 10/21/2026\n"
    assert pctpa.packet_stamp("MEMORANDUM\n...\n" + footer) == "40"
    assert pctpa.packet_stamp("Conflict of Interest Code\n\n                                      12A\n") == "12A"
    assert pctpa.packet_stamp("Unmet needs map\n10/13/2026 10\n") is None  # one space: a date, not a stamp
