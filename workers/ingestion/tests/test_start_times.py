from datetime import date

from tahoe_ingest.start_times import agenda_start_time, page_start_time

TRPA = ("Tahoe Regional Planning Agency (TRPA)\n                         Governing Board and Committee Meetings\n\n"
        "                                   PUBLIC MEETING NOTICE & AGENDAS\n\n"
        " 9:15 a.m. TRPA Governing                           Wednesday,            Join at 128 Market St., Stateline, NV\n"
        " Board Meeting*                                   October 28, 2026            or https://www.trpa.gov/public-\n"
        "                                                                                               meeting-zoom\n\n"
        "Agenda Outline:\n8:30 a.m. TRPA Legal Committee\n8:30 a.m. TRPA Operations & Governance Committee\n"
        "9:15 a.m. TRPA Governing Board\n")


def doc(title: str, text: str) -> dict:
    return {"id": title, "title": title, "pages": [{"number": 1, "text": text}]}


def test_column_layout_time_belongs_to_the_date_beside_it():
    assert page_start_time(TRPA, date(2026, 10, 28)) == "09:15"


def test_heading_formats():
    cases = {
        "DATE: Friday, August 21, 2026 TIME: 08:30\na.m. PLACE: TCPUD Administrative Office": ("2026-08-21", "08:30"),
        "will be held starting at 4:00 PM on September 9, 2026, in the Boardroom": ("2026-09-09", "16:00"),
        "AGENDA Tuesday, September 22, 2026 at 4:30 pm City Hall": ("2026-09-22", "16:30"),
        "COUNTY OF PLACER PLANNING COMMISSION AGENDA THURSDAY, SEPTEMBER 10, 2026 9:00 A.M. MEETING LOCATION": ("2026-09-10", "09:00"),
        "Board of Directors Agenda\n    Friday, July 17, 2026\n      8:30-10:00 am\n": ("2026-07-17", "08:30"),
        "Board Meeting 9/3/2026 11:30-1:00 pm": ("2026-09-03", "11:30"),
    }
    for text, (day, expected) in cases.items():
        assert page_start_time(text, date.fromisoformat(day)) == expected, text


def test_times_for_other_days_deadlines_and_lunches_are_not_starts():
    lahontan = ("SPECIAL MEETING/LUNCH Wednesday, September 30, 2026, 11:30 a.m. Lahontan Regional Water Quality "
                "Control Board Wednesday, September 30, 2026, 2:30 p.m. REGULAR MEETING Thursday, October 1, 2026, 9:00 a.m.")
    assert page_start_time(lahontan, date(2026, 10, 1)) == "09:00"
    lunch = ("SPECIAL MEETING/LUNCH Wednesday, May 6, 2026, 10:30 a.m. 15095 Amargosa Rd., Bldg 2 - Suite 210, Victorville, "
             "CA 92394 Regular Meeting Wednesday, May 6, 2026, 1:00 p.m.")
    assert page_start_time(lunch, date(2026, 5, 6)) == "13:00"
    deadline = "Comments must be received by 5:00 p.m. on Monday, October 26, 2026."
    assert page_start_time(deadline, date(2026, 10, 26)) is None
    assert page_start_time("available on October 26, 2026 at the District office during the hours of 8:00 a.m. and 5:00 p.m.", date(2026, 10, 26)) is None


def test_a_time_far_from_any_date_is_ignored():
    text = "Regular Meeting October 1, 2026" + " filler" * 40 + " Item 4 will be heard at 2:00 p.m."
    assert page_start_time(text, date(2026, 10, 1)) is None


def test_agenda_documents_must_agree_and_minutes_or_committees_do_not_count():
    governing = doc("Official meeting agenda", TRPA)
    day = date(2026, 10, 28)
    assert agenda_start_time("TRPA Governing Board Meeting", day, [governing]) == "09:15"
    committee = doc("Legal Committee Agenda", "Legal Committee October 28, 2026 8:30 a.m.")
    assert agenda_start_time("TRPA Governing Board Meeting", day, [governing, committee]) == "09:15"
    minutes = doc("Official meeting minutes", "October 28, 2026 called to order at 9:22 a.m.")
    assert agenda_start_time("TRPA Governing Board Meeting", day, [governing, minutes]) == "09:15"
    revised = doc("Revised agenda", "Board Meeting October 28, 2026 10:00 a.m.")
    assert agenda_start_time("TRPA Governing Board Meeting", day, [governing, revised]) is None
    assert agenda_start_time("TRPA Governing Board Meeting", None, [governing]) is None
