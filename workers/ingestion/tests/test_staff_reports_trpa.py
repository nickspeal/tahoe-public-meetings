"""TRPA staff reports (staff_report_rules/trpa.py): each item's own file, its attachments, and the packet fallback; no network."""
import re
from datetime import date

import pytest

from tahoe_ingest import staff_reports
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.staff_report_rules import trpa
from test_staff_reports import load, run_rule

UP = "https://www.trpa.gov/wp-content/uploads/"
PACKET = UP + "October-28-Governing-Board-Packet.pdf"
AGENDA = UP + "October-28-Governing-Board-Agenda.pdf"
REPORT_7A = UP + "Agenda-Item-No-7-A-Biodervisity-and-Recreation-Threshold-Updates.pdf"
REPORT_7B = UP + "Agenda-Item-No-7-B-Aquatic-Invasive-Species-Program-Update.pdf"
ID = "trpa-governing:2026-10-28:"


def page(data, url, number):
    return next(p for p in data["downloads"][url]["pages"] if p["page_number"] == number)


def without_links(data, *urls):
    """The agenda and meeting page no longer link these files (as for items TRPA publishes only in the packet)."""
    data["downloads"][AGENDA]["links"] = [link for link in data["downloads"][AGENDA]["links"] if link["url"] not in urls]
    for key, markup in data["html"].items():
        for url in urls:
            markup = re.sub(rf'<a href="{re.escape(url)}"[^>]*>(.*?)</a>', r"\1", markup, flags=re.S)
        data["html"][key] = markup
    return data


def test_items_use_their_own_report_file_and_list_its_attachments():
    found, ctx, read = run_rule(load("trpa-governing"))
    assert found["6.B"].url == found["6.C"].url and found["6.B"].title == "Staff report"
    evidence = found["6.B"].evidence
    assert evidence["footer"].endswith("CONSENT CALENDAR ITEM NOS. 6.B AND 6.C") and evidence["agendaPage"] == 9
    assert evidence["agendaLink"] == "Inflation adjustment to TRPA Filing Fee Schedule and Mobility Mitigation Fee"
    assert "titleOverlap" in evidence
    files = {ctx.meeting.items[[i.id for i in ctx.meeting.items].index(a.item_id)].number: a.files for a in ctx.attachments}
    assert [f["label"] for f in files["6.B"]] == ["Staff report", "Attachment A: Fee Resolution 2026",
                                                  "Exhibit 1 to Attachment A Fee Schedule", "Attachment B: Fee Resolution 2026"]
    assert files["6.C"] == files["6.B"] and [f["url"] for f in files["7.A"]] == [REPORT_7A]
    assert all(f["url"].startswith(UP) for fs in files.values() for f in fs)
    assert PACKET not in read and len(read) == 8  # agenda, meeting page, six report files


def test_report_footer_naming_another_item_falls_back_to_the_packet():
    data = load("trpa-governing")
    first = page(data, REPORT_7A, 1)
    first["text"] = first["text"].replace("AGENDA ITEM NO. 7.A", "AGENDA ITEM NO. 7.B")
    found, ctx, read = run_rule(data)
    assert found["7.A"].url == PACKET and (found["7.A"].page_start, found["7.A"].page_end) == (107, 108)
    assert found["7.A"].title == "Staff report in the board packet"
    assert ID + "7.A" not in {a.item_id for a in ctx.attachments}
    packet_page = page(data, PACKET, 107)
    packet_page["text"] = packet_page["text"].replace("AGENDA ITEM NO. 7.A", "AGENDA ITEM NO. 7.B")
    found, ctx, _ = run_rule(data)
    assert found["7.A"] is None and "does not name item 7.A" in ctx.skipped[ID + "7.A"]


def test_row_linking_another_items_file_makes_the_item_uncertain():
    data = load("trpa-governing")
    for link in data["downloads"][AGENDA]["links"]:
        if link["url"] == REPORT_7A:
            link["url"] = REPORT_7B
    found, ctx, _ = run_rule(data)
    assert found["7.A"] is None and "names item 7.B" in ctx.skipped[ID + "7.A"]
    assert ID + "7.A" not in {a.item_id for a in ctx.attachments}
    assert found["7.B"].url == REPORT_7B


def test_committee_number_alone_does_not_count():
    data = load("trpa-governing")
    url = UP + "Ops-Item-No-5-A-Consent-Calendar-Item-No-6-A-September-Financials.pdf"
    first = page(data, url, 1)
    first["text"] = first["text"].replace("CONSENT CALENDAR ITEM NO. 6.A", "")
    found, ctx, _ = run_rule(data)
    assert found["6.A"] is None  # "COMMITTEE AGENDA ITEM NO. 5.A" remains, but that is the committee's numbering


def test_items_without_their_own_file_map_to_the_packet():
    found, _, read = run_rule(without_links(load("trpa-governing"), REPORT_7A, REPORT_7B))
    assert (found["7.A"].url, found["7.A"].page_start, found["7.A"].page_end) == (PACKET, 107, 108)
    assert (found["7.B"].page_start, found["7.B"].page_end) == (109, 110)
    assert found["7.A"].evidence["agendaPageColumn"] == 107  # "Informational 107", one space before the page
    assert found["7.B"].evidence["packetStamp"] == "109" and found["6.D"].url.endswith("Staff-Report.pdf")
    assert read.count(PACKET) == 1


def test_stored_packet_is_used_without_a_download():
    data = without_links(load("trpa-governing"), REPORT_7A, REPORT_7B, PACKET)
    packet = data["downloads"][PACKET]
    data["documents"].append({"version_id": ID + "trpa-governing-agenda-packet:0", "source_document_id": "trpa-governing-agenda-packet",
                              "title": "October 28 Governing Board Packet", "url": PACKET, "sha256": packet["sha256"],
                              "page_count": packet["page_count"], "media_type": "application/pdf"})
    data["stored"][ID + "trpa-governing-agenda-packet:0"] = {str(p["page_number"]): p["text"].strip() for p in packet["pages"]}
    found, _, read = run_rule(data)
    assert (found["7.A"].url, found["7.A"].page_start) == (PACKET, 107) and found["7.B"].page_start == 109
    assert PACKET not in read


def test_packet_that_does_not_open_with_the_agenda_is_not_used():
    data = without_links(load("trpa-governing"), REPORT_7A, REPORT_7B)
    page(data, PACKET, 9)["text"] = "TRPA GOVERNING BOARD\nA different consent calendar\n"
    found, ctx, _ = run_rule(data)
    assert found["7.A"] is None and "not the captured agenda" in ctx.skipped[ID + "7.A"]
    assert found["6.A"] is not None  # items with their own file do not need the packet


def test_packet_link_for_another_meeting_is_not_used():
    data = without_links(load("trpa-governing"), REPORT_7A, REPORT_7B)
    url = data["meeting"]["official_url"]
    data["html"][url] = data["html"][url].replace("October 28 Governing Board Packet", "September 23 Governing Board Packet")
    found, ctx, _ = run_rule(data)
    assert found["7.A"] is None and "dated September 23" in ctx.skipped[ID + "7.A"]


def test_packet_page_stamped_with_another_number_is_not_used():
    data = without_links(load("trpa-governing"), REPORT_7A, REPORT_7B)
    packet_page = page(data, PACKET, 109)
    packet_page["text"] = packet_page["text"].rstrip().rsplit("109", 1)[0] + "110\n"
    found, ctx, _ = run_rule(data)
    assert found["7.B"] is None and "stamped 110" in ctx.skipped[ID + "7.B"]


def test_mis_encoded_report_is_not_stored_but_its_attachments_are():
    data = load("trpa-governing")
    # The May 13, 2026 APC reports' text layer: "STAFF REPORT" and "Date:" in a shifted glyph map; only the footer is readable.
    for url, number in ((REPORT_7A, 1), (PACKET, 107)):
        p = page(data, url, number)
        p["text"] = ("^d&&ZWKZd\nĂƚĞ͗ ϭϬͬϮϭͬϮϬϮϲ\n" * 40) + "\n".join(p["text"].splitlines()[-3:])
    found, ctx, _ = run_rule(data)
    assert found["7.A"] is None and "not readable" in ctx.skipped[ID + "7.A"]
    assert ID + "7.A" in {a.item_id for a in ctx.attachments}


def test_report_downloads_are_capped(monkeypatch):
    monkeypatch.setattr(trpa, "MAX_REPORTS", 2)
    found, ctx, read = run_rule(load("trpa-governing"))
    assert len([u for u in read if u not in (AGENDA, PACKET) and u.endswith(".pdf")]) == 2
    assert found["7.B"].url == PACKET  # past the limit, items fall back to the packet
    assert found["6.D"] is None and "per-meeting limit" in ctx.skipped[ID + "6.D"]  # its packet page is not in the fixture


@pytest.mark.parametrize("footer, board", [
    ("                         CONSENT CALENDAR ITEM NO.6.A\n                  59", {"6.A"}),
    ("     91               LEGAL COMMITTEE AGENDA ITEM NO. 5.A &\n               CONSENT CALENDAR ITEM NO. 6. B", {"6.B"}),
    ("  103       TRANSPORTATION COMMITTEE AGENDA ITEM NO. 5.C\n           & TMPO CONSENT CALENDAR ITEM NO. 7.A", {"7.A"}),
    ("                    ENVIRONMENTAL IMPROVEMENT PROGRAM\n                    COMMITTEE AGENDA ITEM NO. 5.A", set()),
    ("                    LEGAL COMITTEE AGENDA ITEM NO. 5.B", set()),
    ("                                         AGENDA ITEM NO. 8.A.\n          115", {"8.A"}),
    ("     1   AGENDA ITEM NO. IV.A.", set()),
])
def test_footer_numbers(footer, board):
    assert trpa.footer_numbers("Body text of the report.\n" + footer)[0] == board


@pytest.mark.parametrize("name, numbers", [
    ("Ops-Item-Nos-5-B-and-5-C-Consent-Calendar-Item-Nos-6-B-and-6-C-Filing-Fees", {"6.B", "6.C"}),
    ("LC-5-A-CC-6-D-Brady-Sessions-Resolution-of-Enforcement-Action-Staff-Report", {"6.D"}),
    ("Transportation-Committee-Agenda-Item-No.-5C-TMPO-Consent-Calendar-Item-No.-7A-2027-FTIP", {"7.A"}),
    ("Agenda-Item-8A-Appeal-of-Verification", {"8.A"}),
    ("EIP-Agenda-Item-5A-Herbicide-Use", set()),
    ("Consent Calendar Item No. 6.A & Operations and Governance Committee Agenda Item No. 5.A September Financials", {"6.A"}),
    ("Agenda Item No. 4.A. Proposed amendments", {"4.A"}),
])
def test_named_numbers(name, numbers):
    assert trpa.named_numbers(name) == numbers


def test_page_value_comes_from_the_items_own_row():
    # APC 2026-05-13: both planning items begin with the same words; the row letter tells them apart.
    text = ("     Item                                                  Purpose               Staff         Page\n"
            " A   Consideration and possible recommendation for          For Possible Action   Staff A       57\n"
            "     adoption of the Chapter 37 amendments\n"
            " B   Consideration and possible recommendation for          For Possible Action   Staff B       91\n"
            " C   Briefing on the plan                       Staff C      Informational 69\n"
            " D   Closed session                                         For Possible Action   Staff D       --\n")
    meeting = staff_reports.Meeting("m", "trpa-advisory", "APC", None, [], [])
    ctx = staff_reports.Context(lambda version: {1: text}, meeting, reader=None)

    def item(number, quote):
        return staff_reports.Item(number, number, quote, None, "f", [{"documentId": "v", "page": 1, "quote": quote}])
    assert trpa._row_page(ctx, item("4.B", "Consideration and possible recommendation for")) == 91
    assert trpa._row_page(ctx, item("4.A", "Consideration and possible recommendation for")) == 57
    assert trpa._row_page(ctx, item("4.C", "Briefing on the plan")) == 69
    assert trpa._row_page(ctx, item("4.D", "Closed session")) is None


@pytest.mark.parametrize("agency,packet", [("trpa-advisory", "May 13, 2026 APC Meeting Packet"),
                                           ("trpa-governing", "May 13 Governing Board Packet")])
def test_packet_link_is_the_one_named_for_the_agencys_body(agency, packet):
    name = next(a for a in agency_catalog() if a["id"] == agency)["staffReports"]["packetName"]
    meeting = staff_reports.Meeting("m", agency, "Board", date(2026, 5, 13), [], [], "https://www.trpa.gov/meeting/")
    links = [{"url": UP + "APC-Packet.pdf", "text": "May 13, 2026 APC Meeting Packet"},
             {"url": UP + "GB-Packet.pdf", "text": "May 13 Governing Board Packet"}]
    assert trpa._packet_link(staff_reports.Context(None, meeting, None), links, name)["text"] == packet
