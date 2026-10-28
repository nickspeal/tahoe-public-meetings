"""IVGID staff memos in the stored packet (staff_report_rules/civicclerk.py); fixture-driven, no network."""
from tahoe_ingest.staff_report_rules import civicclerk
from test_staff_reports import load, run_rule

PACKET = "ivgid:2026-10-14:ivgid-agenda-packet:ee28afe0fce46380"


def test_reads_stored_pages_only():
    found, ctx, read = run_rule(load("ivgid"))
    assert read == [] and ctx.downloads == 0
    assert found["G.1"].evidence == {"header": "ITEM G.1.", "pages": "5-9", "titleOverlap": 1.0, "meetingDate": "2026-10-14"}
    assert found["G.1"].kind == "staff-memo" and "GetMeetingFileStream(fileId=2872" in found["G.1"].url


def test_memo_dated_another_day_is_not_stored():
    data = load("ivgid")
    data["stored"][PACKET]["5"] = data["stored"][PACKET]["5"].replace("October 14, 2026", "September 9, 2026")
    found, ctx, _ = run_rule(data)
    assert found["G.1"] is None and "no packet memo stamped ITEM G.1." in ctx.skipped["ivgid:2026-10-14:G.1"]


def test_stamp_naming_another_item_ends_the_run_and_is_not_its_memo():
    data = load("ivgid")
    data["stored"][PACKET]["10"] = data["stored"][PACKET]["10"].replace("Item G.2.", "Item G.6.", 1)
    found, _, _ = run_rule(data)
    assert found["G.1"].page_end == 9
    assert found["G.2"].page_start == 11  # the G.2.A memo still opens G.2's pages


def test_memo_with_another_subject_is_not_stored():
    data = load("ivgid")
    page = data["stored"][PACKET]["208"]
    data["stored"][PACKET]["403"] = page.replace("ITEM G.4.", "ITEM I.3.")
    found, ctx, _ = run_rule(data)
    assert found["I.3"] is None and "subject differs" in ctx.skipped["ivgid:2026-10-14:I.3"]


def test_earlier_memo_and_quoted_item_numbers_do_not_cut_a_run():
    found, _, _ = run_rule(load("ivgid"))
    assert found["I.3"].page_end == 488  # June's "ITEM H.3." memo on page 451 is backup
    assert found["G.3"].page_end == 207  # minutes on page 333 quote "Item G.3." mid-page
    assert found["I.4"].page_end == 502  # the long-range calendar is stamped "Item J.1." after its running title
    assert civicclerk.page_stamp("LONG RANGE CALENDAR                 Item J.1.\nLast updated") == ("J.1", None)
    assert civicclerk.page_stamp("IVGID Board of Trustees    -12-\n  Item G.3. Presentation") is None
