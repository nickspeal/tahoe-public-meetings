"""South Tahoe PUD board memos in the agenda packet (staff_report_rules/stpud.py); fixture-driven, no network."""
from datetime import date

import pytest

from tahoe_ingest import staff_reports
from tahoe_ingest.staff_report_rules import stpud
from test_staff_reports import load, run_rule

ID = "stpud:2026-10-15:"


def packet(data):
    return data["stored"][next(d["version_id"] for d in data["documents"] if d["source_document_id"] == "stpud-agenda-packet")]


def test_header_naming_another_item_is_not_stored():
    data = load("stpud")
    packet(data)["46"] = packet(data)["46"].replace("BOARD AGENDA ITEM 6c", "BOARD AGENDA ITEM 6d")
    found, ctx, _ = run_rule(data)
    assert found["6.c"] is None and "no memo headed BOARD AGENDA ITEM 6c" in ctx.skipped[ID + "6.c"]
    assert found["6.d"] is None and "2 packet pages" in ctx.skipped[ID + "6.d"]
    assert found["6.b"] is not None


def test_memo_for_another_meeting_or_project_is_not_stored():
    data = load("stpud")
    packet(data)["42"] = packet(data)["42"].replace("October 15, 2026", "October 01, 2026", 1)
    packet(data)["50"] = packet(data)["50"].replace("Asphalt Patching and Trench Paving Services", "Sewer Lateral Repairs")
    found, ctx, _ = run_rule(data)
    assert found["6.b"] is None and ctx.skipped[ID + "6.b"] == "memo is for another meeting date"
    assert found["6.d"] is None and "project name differs" in ctx.skipped[ID + "6.d"]


def test_memo_without_its_sign_off_stores_nothing():
    data = load("stpud")
    # Without "CONCURRENCE WITH REQUESTED ACTION" the memo's end is unknown; the attachments must not be swallowed.
    packet(data)["47"] = packet(data)["47"].replace("CONCURRENCE WITH REQUESTED ACTION", "")
    found, ctx, _ = run_rule(data)
    assert found["6.c"] is None and "sign-off" in ctx.skipped[ID + "6.c"]


def test_two_different_packets_are_uncertain():
    data = load("stpud")
    other = next(d for d in data["documents"] if d["source_document_id"] != "stpud-agenda-packet")
    other["sha256"] = "0" * 64
    data["stored"][other["version_id"]] = {"5": packet(data)["5"]}
    with pytest.raises(staff_reports.Uncertain):
        run_rule(data)


def test_memo_dates_may_be_zero_padded():
    assert stpud.memo_date("MEETING DATE:               September 03, 2026") == date(2026, 9, 3)
    assert stpud.memo_date("MEETING DATE: Smarch 3, 2026") is None
