"""Planning calendar formats (IDEA-018 R4): locating the calendar, parsing it whole, and rejecting it whole."""
import copy
import json
import re
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import catalog
from tahoe_ingest import planning_calendar_rules as rules
from tahoe_ingest.planning_calendar_rules import FIELDS, REGISTRY, CalendarInvalid, check_config, check_quotes

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/planning-calendars"
INITIALS = re.compile(r"\b[A-Z]{2}/(?:[A-Z]{2})?(?:\s|$)|Motion \d{4}-\d+")


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def config(agency_id):
    return copy.deepcopy(next(a for a in catalog.agency_catalog() if a["id"] == agency_id)["planningCalendar"])


def parse_slt(name="slt-apc-2026-09-30", pages=None):
    data = load(name)
    return REGISTRY["slt-apc-attachment"].parse(pages or data["pages"], config=config("slt"), carried_by=date(2026, 10, 6))


def parse_ttd(pages=None):
    data = load("ttd-packet-2026-10-07")
    return REGISTRY["ttd-packet-pages"].parse(pages or data["pages"], config=config("ttd"),
                                              carried_by=date.fromisoformat(data["meeting_date"]))


def subjects(result, **where):
    return [e["subject"] for e in result["entries"] if all(e[k] == v for k, v in where.items())]


def tampered(pages, old, new):
    pages = copy.deepcopy(pages)
    hits = [p for p in pages if old in p["text"]]
    assert hits, old
    hits[0]["text"] = hits[0]["text"].replace(old, new, 1)
    return pages


def test_slt_calendar_blocks_sections_and_times():
    result = parse_slt()
    assert (result["title"], result["year"], result["published_on"]) == (
        "2026 Agenda Planning Calendar (Updated 09 30 2026)", 2026, date(2026, 9, 30))
    dated = [(b["local_date"], b["start_time"], b["special"]) for b in result["blocks"] if b["local_date"]]
    assert dated == [(date(2026, 10, 13), None, True), (date(2026, 11, 3), "09:30", False), (date(2026, 11, 17), "17:30", False),
                     (date(2026, 12, 1), "09:00", False), (date(2026, 12, 15), "17:30", False)]
    months = {b["month"]: b["entries"] for b in result["blocks"] if b["month"]}
    assert months == {date(2027, 1, 1): 7, date(2027, 2, 1): 3, date(2027, 3, 1): 0, date(2027, 4, 1): 0}
    by_subject = {(e["local_date"], e["subject"]): e for e in result["entries"] if e["local_date"]}
    nov3, dec1 = date(2026, 11, 3), date(2026, 12, 1)
    assert by_subject[(nov3, "Pedestrian Only Zone Ordinance (First Reading)")]["section"] == "New business"
    assert by_subject[(nov3, "Paradise Avenue Right-of-Way Abandonment")]["section"] == "Public hearing"
    assert by_subject[(nov3, "Amendment No. 1 to Agreement with CDM Smith for Mainstreet Corridor Plan")]["section"] == "Consent"
    assert by_subject[(nov3, "Pedestrian Only Zone Ordinance (First Reading)")]["start_time"] == "09:30"
    for subject in ("Prioritization of Bikes at City Intersections and Bike Lanes", "Bicycle Safety Regulation Discussion"):
        assert by_subject[(dec1, subject)]["section"] == "New business"
    assert {"Certification of Election", "Outgoing Councilmember Recognition", "Oath of Office",
            "City Council Re-Organization Mayor/Mayor Pro Tem"} <= set(subjects(result, local_date=dec1))
    assert by_subject[(date(2026, 10, 13), "Sale and Use of Glyphosate in the City")]["special"] is True
    assert "2027 Pavement Management Plan Update and Prioritization of all users" in subjects(result, month=date(2027, 1, 1))
    assert "Bike Paths/Speed Bumps, Bike Parking, Vehicle Parking" in subjects(result, pending=True)
    assert len(subjects(result, local_date=date(2026, 11, 17))) == 7


def test_slt_calendar_publishes_no_initials_departments_or_notes():
    result = parse_slt()
    text = " ".join(e["subject"] + " " + e["quote"] for e in result["entries"])
    assert not INITIALS.search(text), "requester initials or a motion number leaked into a subject"
    assert "Election is Certified" not in text and "brought forward" not in text and "December 15" not in text
    assert not {"City Manager", "Dev Services", "Public Works", "Police/PW"} & set(subjects(result))
    for e in result["entries"]:
        assert list(e) == list(FIELDS)
        assert (e["agency_id"], e["meeting_title"], e["location"], e["remote_quote"], e["rejection"], e["topics"]) == (
            "slt", "City Council Meeting", None, None, None, [])
        assert e["quote"] == e["subject"] and not e["subject"].endswith(("NB", "Consent", "PH", "UB"))


def test_slt_earlier_calendar_parses_too():
    result = parse_slt("slt-apc-2026-09-15")
    assert result["published_on"] == date(2026, 9, 15)
    assert [b["heading"] for b in result["blocks"] if b["local_date"]][:2] == ["Oct 6, 5:30 p.m.", "10/13/2026 Special Mtg"]
    assert "Airport Master Plan adoption" in subjects(result, local_date=date(2026, 11, 3))
    proclamation = next(e for e in result["entries"] if e["subject"] == "Proclamation - Fire Prevention Month")
    assert proclamation["section"] is None  # "Proc" is not one of the section codes the site labels
    assert not INITIALS.search(" ".join(subjects(result)))


def test_ttd_calendar_follows_mapped_bodies_only():
    result = parse_ttd()
    assert (result["title"], result["year"], result["published_on"]) == (
        "2026 TTD/C Board/Committees Tentative Agenda Calendar", 2026, date(2026, 10, 7))
    assert subjects(result, local_date=date(2026, 11, 4)) == [
        "Purchase Non-Revenue Vehicle (facility truck)- Consent", "Ratification of Strategic Priorities – Action Item (Resolution)",
        "Final and Balancing Change Order – North Parking Project", "Purchase Non-Revenue Vehicle - maintenance truck - (Consent)",
        "Steve Teshara appointments"]
    assert {e["agency_id"] for e in result["entries"] if e["local_date"] == date(2026, 11, 4)} == {"ttd"}
    assert subjects(result, agency_id="ttd-rtcpc", local_date=date(2026, 11, 2)) == [
        "Quarterly Parking Report", "Project Spotlight", "Draft service plan", "North Parking"]
    assert not subjects(result, local_date=date(2026, 12, 2)) and not subjects(result, local_date=date(2026, 11, 30))
    # Fin & Pers and TTC items never appear; canceled columns have no entries.
    everything = set(subjects(result))
    assert not {"TDA Approval", "TRPA – AIS Construction", "Purchase Non-Revenue Vehicle (facility truck)"} & everything
    assert not subjects(result, agency_id="ttd-rtcpc", local_date=date(2026, 10, 5))
    assert {b["label"] for b in result["blocks"]} == {"TTD", "RTCPC"}
    # Earlier months parse too (storage ignores what is dated on or before published_on); dates stay in 2026.
    assert min(e["local_date"] for e in result["entries"]) == date(2026, 2, 2)
    assert {e["local_date"].year for e in result["entries"]} == {2026}
    wrapped = next(e for e in result["entries"] if e["subject"].startswith("Purchase Non-Revenue Vehicle - maintenance"))
    assert wrapped["quote"] == "Purchase Non-Revenue Vehicle - maintenance truck -\n(Consent)" and wrapped["page"] == 196
    assert all(e["section"] is None and e["start_time"] is None and list(e) == list(FIELDS) for e in result["entries"])
    assert all("Kira" not in e["subject"] + e["quote"] for e in result["entries"])
    meeting_titles = {e["agency_id"]: e["meeting_title"] for e in result["entries"]}
    assert meeting_titles == {"ttd": "TTD Board of Directors Meeting",
                              "ttd-rtcpc": "TTD Regional Transit and Capital Programs Committee Meeting"}


def test_locate_slt_attachment_under_the_calendar_item():
    data = load("slt-apc-2026-09-30")
    calendar = {"url": data["url"], "name": data["attachmentName"], "label": "APC 09 30 2026"}
    items = [{"title": "Approval of Minutes", "attachments": [{"url": "https://slt.granicus.com/x/1/APC_09_01_2026.pdf",
                                                               "name": "APC 09 01 2026.pdf", "label": "APC"}]},
             {"title": "  agenda   planning calendar ", "attachments": [
                 {"url": "https://slt.granicus.com/x/2/Item_7-_Executive_Summary.pdf", "name": "Item 7- Executive Summary.pdf",
                  "label": "Executive summary"}, calendar]}]
    assert rules.agency_rules()["slt"].locate(items) == [{"url": data["url"], "name": "APC 09 30 2026.pdf"}]
    assert data["url"].startswith("https://slt.granicus.com/services/legistar/download/pdf/")


def test_locate_ttd_calendar_pages_in_the_packet():
    pages = load("ttd-packet-2026-10-07")["pages"]
    packet = [{"page_number": 191, "text": "VII. Executive Director Report\nAgenda Calendar Check"},
              {"page_number": 192, "text": "\n\nMemo\nThe Tentative Agenda Calendar follows."}] + pages
    found = rules.agency_rules()["ttd"].locate(packet)
    assert [p["page_number"] for p in found] == [193, 194, 195, 196, 197]
    lone = [{"page_number": 5, "text": pages[0]["text"]}, {"page_number": 7, "text": pages[0]["text"]}]
    assert len(rules.agency_rules()["ttd"].locate(lone + packet)) == 5  # the longest consecutive run wins


@pytest.mark.parametrize("old, new, reason", [
    ("2026 Agenda Planning Calendar (Updated 09 30 2026)", "2026 Agenda Calendar (Updated 09 30 2026)", "title"),
    ("Nov 3, 9:30 a.m.", "Nov 33, 9:30 a.m.", "date heading"),
    ("Nov 17, 5:30 p.m.", "Nvo 17, 5:30 p.m.", "date heading"),
    ("Dec 1, 9 a.m.", "Dec 1, 9 x.m.", "date heading"),
    ("                         City Clerk         Ordinance Increasing", "Novembr 3, 9:30          City Clerk         Ordinance Increasing",
     "date heading"),
])
def test_slt_calendar_that_fails_validation_publishes_nothing(old, new, reason):
    pages = load("slt-apc-2026-09-30")["pages"]
    with pytest.raises(CalendarInvalid, match=reason):
        parse_slt(pages=tampered(pages, old, new))


def test_slt_rows_outside_any_block_or_columns_fail():
    pages = load("slt-apc-2026-09-30")["pages"]
    early = tampered(pages, "\n10/13/2026 Special Mtg", "\n                         Clerk              Stray Item"
                                                          "                                                        NB\n10/13/2026 Special Mtg")
    with pytest.raises(CalendarInvalid, match="outside any date block"):
        parse_slt(pages=early)
    shifted = tampered(pages, "                                            Arctic Wolf", "                                                        Arctic Wolf")
    with pytest.raises(CalendarInvalid, match="outside the item and section columns"):
        parse_slt(pages=shifted)


@pytest.mark.parametrize("old, new, reason", [
    ("2026 TTD/C Board/Committees Tentative Agenda Calendar\n\n\n                                                Pending",
     "2026 TTD Board Tentative Agenda Calendar\n\n\n                                                Pending", "title"),
    ("November 4\nTTD", "November 44\nTTD", "date heading"),
    ("November 4\nTTD", "Novmber 4\nTTD", "column header"),
    ("RTCPC November 2 ", "RTCPC Novmber 2 ", "date heading"),
    ("December 2\nTTD", "December 2 - Moved\nTTD", "date heading"),
    ("December 2\nTTD                                                             TTC", "December 2\n• Stray item", "column header"),
])
def test_ttd_calendar_that_fails_validation_publishes_nothing(old, new, reason):
    pages = load("ttd-packet-2026-10-07")["pages"]
    with pytest.raises(CalendarInvalid, match=reason):
        parse_ttd(pages=tampered(pages, old, new))


def test_ttd_text_before_the_first_block_fails():
    pages = load("ttd-packet-2026-10-07")["pages"]
    with pytest.raises(CalendarInvalid, match="outside any date block"):
        parse_ttd(pages=tampered(pages, "\n\n\n                                                Pending Items",
                                 "\n• Stray item\n\n                                                Pending Items"))


def test_a_subject_that_is_not_a_quote_of_its_page_fails():
    for result, data in ((parse_slt(), load("slt-apc-2026-09-30")), (parse_ttd(), load("ttd-packet-2026-10-07"))):
        check_quotes(result["entries"], data["pages"])
        changed = copy.deepcopy(result["entries"])
        changed[3]["subject"] += " and More"
        with pytest.raises(CalendarInvalid, match="quoted text"):
            check_quotes(changed, data["pages"])
        changed[3]["quote"] = changed[3]["subject"]
        with pytest.raises(CalendarInvalid, match="not a quote of page"):
            check_quotes(changed, data["pages"])
        moved = copy.deepcopy(result["entries"])
        moved[0]["page"] += 3
        with pytest.raises(CalendarInvalid, match="not a quote of page"):
            check_quotes(moved, data["pages"])


def agency(**calendar):
    return {"id": "slt", "planningCalendar": {**config("slt"), **calendar}}


@pytest.mark.parametrize("entry, message", [
    (agency(format="slt-apc"), "unknown planning calendar format"),
    ({"id": "slt", "planningCalendar": {k: v for k, v in config("slt").items() if k != "title"}}, "title"),
    ({"id": "slt", "planningCalendar": {k: v for k, v in config("slt").items() if k != "bodies"}}, "bodies"),
    (agency(bodies={}), "bodies"),
    (agency(bodies={"*": {"id": "fows", "meetingTitle": "Meeting"}}), "not a followed registry entry"),
    (agency(bodies={"*": {"id": "no-such-body", "meetingTitle": "Meeting"}}), "not a followed registry entry"),
    (agency(bodies={"*": {"id": "slt"}}), "meetingTitle"),
    (agency(heading="Tentative"), "takes no heading"),
    ({"id": "slt", "planningCalendar": {k: v for k, v in config("slt").items() if k != "itemTitle"}}, "needs itemTitle"),
    (agency(attachmentPattern="(APC"), "not a regular expression"),
    (agency(itemTitle=" "), "non-empty text"),
])
def test_check_config_rejects_bad_entries(entry, message):
    with pytest.raises(ValueError, match=message):
        check_config(entry)


def test_check_config_accepts_the_registry_entries():
    for agency_id in ("slt", "ttd"):
        check_config({"id": agency_id, "planningCalendar": config(agency_id)})
    check_config({"id": "trpa-governing"})


def test_agency_rules_bind_own_calendars_only():
    bound = rules.agency_rules()
    assert {"slt", "ttd"} <= set(bound) and "ttd-rtcpc" not in bound  # planningCalendar is an own key, not inherited
    assert bound["slt"].format == "slt-apc-attachment" and bound["ttd"].format == "ttd-packet-pages"
    data = load("ttd-packet-2026-10-07")
    assert bound["ttd"].parse(data["pages"], carried_by=date(2026, 10, 7)) == parse_ttd()
