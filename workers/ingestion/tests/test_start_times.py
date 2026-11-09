from datetime import date

from tahoe_ingest.capture import _capture_row
from tahoe_ingest.start_times import agenda_date, agenda_start_time, page_meeting_date, page_start_time

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


TTD = ("TAHOE TRANSPORTATION DISTRICT (TTD)\n Notice of Agenda and Agenda\n\n"
       "Tahoe Regional Planning Agency                                    March 4, 2026\n"
       "128 Market Street                                                     3:00 p.m.\n"
       "Stateline, NV 89449\n\nI. CALL TO ORDER AND GENERAL MATTERS\n"
       "B. For Possible Action: Approval of Agenda for March 4, 2026\n"
       "C. For Possible Action: Approval of Minutes of February 4, 2026      Page 1\n")


def test_meeting_date_is_the_heading_date_beside_the_start_time():
    assert page_meeting_date(TTD) == date(2026, 3, 4)
    assert page_meeting_date(TRPA) == date(2026, 10, 28)
    # A multi-day notice, or a start time beside a later posting date rather than the heading's, gives no date.
    multi_day = "Wednesday, September 30, 2026, 2:30 p.m. REGULAR MEETING Thursday, October 1, 2026, 9:00 a.m."
    assert page_meeting_date(multi_day) is None
    posted = ("REGULAR MEETING AGENDA FOR SEPTEMBER 23, 2026" + " filler" * 40 +
              " Posted September 18, 2026 REGULAR MEETING: 4:30 PM")
    assert page_meeting_date(posted) is None
    assert page_meeting_date("Regular Meeting October 1, 2026") is None


def test_agenda_date_needs_agreeing_agendas_and_ignores_minutes():
    title = "TTD Board of Directors Meeting"
    agenda = doc("Agenda Packet", TTD)
    assert agenda_date(title, [agenda]) == date(2026, 3, 4)
    assert agenda_date(title, [agenda, doc("Minutes", "February 4, 2026 called to order at 3:02 p.m.")]) == date(2026, 3, 4)
    assert agenda_date(title, [agenda, doc("Revised agenda", "Board Meeting April 1, 2026 3:00 p.m.")]) is None


def test_capture_dates_an_undated_listing_from_its_agenda():
    def fetch(url):
        return {"sha256": "0" * 64, "page_count": 1, "warnings": [], "pages": [{"page_number": 1, "text": TTD}]}
    candidate = {"agency": "ttd", "source_id": "7156", "title": "TTD Board of Directors Meeting", "official_url": "https://example.org/m",
                 "documents": [{"id": "ttd-agenda-packet", "title": "Agenda Packet", "url": "https://example.org/a.pdf"}]}
    assert _capture_row(candidate, 2, fetch, [], [], 0)["date"] == "2026-03-04"
    # A date stated on the listing is kept.
    assert _capture_row({**candidate, "date": "2026-03-05"}, 2, fetch, [], [], 0)["date"] == "2026-03-05"


# South Lake Tahoe's November 3, 2026 agenda, page 1 as captured. The agenda planning calendar had listed
# "Nov 3, 9:30 a.m.", and the planned meeting kept that start after the Granicus capture (whose row states no time).
SLT_NOV_3 = ("City of South Lake Tahoe\n                                     City Council\n"
             "                              Meeting Agenda\n                   Tuesday, November 3, 2026 at 9:00 am\n"
             "   City Hall - Council Chambers - 1901 Lisa Maloff Way, South Lake Tahoe\n\n\n\n"
             "City Council                                              Mission of the City Council\n"
             "Cody Bass, Mayor                                          We provide responsive and effective local\n")


def test_agenda_time_replaces_a_planned_meetings_calendar_time():
    from datetime import datetime, timezone

    from tahoe_ingest.core import exported_start

    calendar_start = datetime(2026, 11, 3, 17, 30, tzinfo=timezone.utc)  # 9:30 a.m. Pacific
    agenda_start = datetime(2026, 11, 3, 17, 0, tzinfo=timezone.utc)  # 9:00 a.m. Pacific, as the agenda says
    meeting = {"title": "11 03 2026 City Council", "local_date": date(2026, 11, 3), "timezone": "America/Los_Angeles",
               "starts_at": calendar_start}
    docs = [doc("Agenda", SLT_NOV_3)]
    assert exported_start(meeting, docs, [])[0] == agenda_start
    # A meeting stored without a start gets the agenda's.
    start, warnings = exported_start({**meeting, "starts_at": None}, docs, ["Confirm the scheduled start in the agenda."])
    assert start == agenda_start and warnings == []
    # With no agenda time, the stored start stands.
    assert exported_start(meeting, [], [])[0] == calendar_start


# tcpud:1190's revised agenda (Granicus HTML text). The upcoming row said 4:00 p.m., and that start stayed stored
# because the past meeting's archive row states no time.
TCPUD_NOV_4 = ("TAHOE CITY PUBLIC UTILITY DISTRICT\nSpecial Meeting of the Board of Directors\n"
               "A\xa0\xa0\xa0G\xa0\xa0\xa0E\xa0\xa0\xa0N\xa0\xa0\xa0D\xa0\xa0\xa0A\n"
               "To access online viewing of board meetings:\nhttp://www.tahoecitypud.com/general/agenda.shtml\n"
               "Instructions for Public Participation:\nMembers of the public may attend the Zoom teleconference.\n"
               "DATE: Wednesday, November 4, 2026\nTIME: 03:45\np.m.\nPLACE:\nTCPUD Administration Building\n")


def test_agenda_time_wins_over_a_stored_listing_time_and_retired_caveats_are_dropped():
    from datetime import datetime, timezone

    from tahoe_ingest.core import exported_start

    listed = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)  # 4:00 p.m. Pacific, from the upcoming row
    meeting = {"title": "Board of Directors Meeting - Budget Workshop", "local_date": date(2026, 11, 4),
               "timezone": "America/Los_Angeles", "starts_at": listed}
    stored = ["Archive playback timestamps are not used as scheduled start times; see the official agenda.",
              "Confirm the scheduled start in the agenda; CivicClerk wall-clock timestamps are not treated as UTC instants.",
              "Meets November 4–5, 2026."]
    start, warnings = exported_start(meeting, [doc("Agenda", TCPUD_NOV_4)], stored)
    assert start == datetime(2026, 11, 4, 23, 45, tzinfo=timezone.utc)  # 3:45 p.m. Pacific
    assert warnings == ["Meets November 4–5, 2026."]
    # No agenda time: the stored start stays, and the retired caveats still go.
    start, warnings = exported_start(meeting, [], stored)
    assert start == listed and warnings == ["Meets November 4–5, 2026."]
