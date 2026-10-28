"""Truckee Tahoe Airport District staff reports by agenda TAB (staff_report_rules/airport.py); fixture-driven, no network."""
import re
from datetime import date

from tahoe_ingest.staff_report_rules import airport
from test_staff_reports import load, run_rule

ID = "truckee-tahoe-airport:412:"


def report(data, tab):
    url = next(u for u in data["downloads"] if re.search(rf"file=tab\+{tab:02d}\+", u))
    return data["downloads"][url]["pages"][0]


def agenda(data):
    return data["stored"][data["documents"][0]["version_id"]]


def test_report_naming_another_item_is_not_stored():
    data = load("truckee-tahoe-airport")
    first = report(data, 4)
    first["text"] = first["text"].replace("AGENDA ITEM: ___04____", "AGENDA ITEM: ___05____")
    found, ctx, _ = run_rule(data)
    assert found["4"] is None and ctx.skipped[ID + "4"] == "report is headed AGENDA ITEM 05"
    assert found["5"] is not None


def test_changed_agenda_tab_is_not_stored():
    data = load("truckee-tahoe-airport")
    agenda(data)["2"] = re.sub(r"(Scholarship Program – TTAD Funding 2026 -+ )TAB 05", r"\1TAB 04", agenda(data)["2"])
    found, ctx, _ = run_rule(data)
    assert found["5"] is None and ctx.skipped[ID + "5"] == "report is headed AGENDA ITEM 04"
    assert found["4"] is not None


def test_another_meeting_and_tab_numbered_reports_are_not_stored():
    found, ctx, _ = run_rule(load("truckee-tahoe-airport"))
    assert found["8"] is None and ctx.skipped[ID + "8"] == "report is for another meeting date"  # MEETING DATE: October 13
    assert found["17"] is None and ctx.skipped[ID + "17"] == "report is headed AGENDA ITEM 16"  # GM report numbered by TAB
    assert found["10"].evidence["agendaTab"] == "TAB 10" and found["10"].evidence["agendaLink"].startswith("TAB 10 - ")


def test_blank_item_number_falls_back_to_the_title():
    data = load("truckee-tahoe-airport")
    first = report(data, 4)
    first["text"] = first["text"].replace("AGENDA ITEM: ___04____", "AGENDA ITEM: _________")
    found, ctx, _ = run_rule(data)
    assert found["4"] is not None and found["4"].evidence["reportHeading"].startswith("AGENDA TITLE: Notice of Exemption")
    assert found["4"].evidence["titleOverlap"] >= airport.TITLE_FALLBACK
    assert found["6"] is None and "title differs" in ctx.skipped[ID + "6"]  # BBK waiver: blank number, other wording


def test_late_and_duplicate_files_are_not_used():
    data = load("truckee-tahoe-airport")
    url = data["meeting"]["official_url"]
    data["html"][url] = data["html"][url].replace("</ul>", '<li><a href="/board_meetings/412/view_file?file=tab+00+-+x.pdf">'
                                                  'TAB 00 - Staff Report - Late.pdf</a></li>\n<li><a href="/board_meetings/412/'
                                                  'view_file?file=tab+05+-+copy.pdf">TAB 05 - Copy.pdf</a></li></ul>')
    found, ctx, read = run_rule(data)
    assert found["5"] is None and ctx.skipped[ID + "5"] == "2 TAB files for item 5"
    assert not any("tab+00" in u or "tab+04a" in u for u in read)  # neither late nor lettered attachment files are read
    assert found["4"] is not None


def test_meeting_dates():
    assert airport.meeting_date("MEETING DATE:           Oct 21, 2026") == date(2026, 10, 21)
    assert airport.meeting_date("MEETING DATE: JUNE 24, 2026") == date(2026, 6, 24)
