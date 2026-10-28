"""TRPA meeting pages: the board's own agenda and packet, never committee material.

The fixtures under tests/fixtures/discovery are excerpts of the official meeting
pages saved on 2026-10-28 (site navigation PDFs and the post body).
"""
import json
from pathlib import Path

from tahoe_ingest.agenda import parse_agenda
from tahoe_ingest.capture import _capture_row
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.source_discovery import agenda_then_packet, discover_configured, documents_in, trpa_documents

ROOT = Path(__file__).resolve().parents[3] / "tests/fixtures"
INDEX = "https://www.trpa.gov/meeting-materials/"
PAGES = {
    "https://www.trpa.gov/governing-board-documents-october-28-2026-hybrid-meeting/": "trpa-governing-board-2026-10-28.html",
    "https://www.trpa.gov/governing-board-documents-september-23-2026-hybrid-meeting/": "trpa-governing-board-2026-09-23.html",
    "https://www.trpa.gov/advisory-planning-commission-meeting-may-13-2026-hybrid-meeting/": "trpa-advisory-planning-commission-2026-05-13.html",
}
INDEX_MARKUP = """<a href="https://www.trpa.gov/how-we-operate/board-members/">Governing Board</a>
<a href="https://www.trpa.gov/governing-board-documents-october-28-2026-hybrid-meeting/">Governing Board Documents October 28, 2026-Hybrid Meeting</a>
<a href="https://www.trpa.gov/governing-board-documents-september-23-2026-hybrid-meeting/">Governing Board Documents September 23, 2026-Hybrid Meeting</a>
<a href="https://www.trpa.gov/advisory-planning-commission-meeting-may-13-2026-hybrid-meeting/">Advisory Planning Commission Meeting May 13, 2026 &#8211; Hybrid Meeting</a>"""


def _config(agency):
    return next(a["discovery"] for a in agency_catalog() if a["id"] == agency)


def _fetch(url):
    return INDEX_MARKUP if url == INDEX else (ROOT / "discovery" / PAGES[url]).read_text()


def _meetings(agency):
    return {r["date"]: r for r in discover_configured(agency, _config(agency), fetch=_fetch) if not r.get("records_only")}


def test_governing_board_pages_give_the_board_agenda_then_its_packet():
    meetings = _meetings("trpa-governing")
    assert sorted(meetings) == ["2026-09-23", "2026-10-28"]
    october = meetings["2026-10-28"]
    assert [(d["id"], d["title"], d["url"]) for d in october["documents"]] == [
        ("trpa-governing-agenda-2026-10-28", "October 28 Governing Board Agenda",
         "https://www.trpa.gov/wp-content/uploads/October-28-Governing-Board-Agenda.pdf"),
        ("trpa-governing-agenda-packet-2026-10-28", "October 28 Governing Board Packet",
         "https://www.trpa.gov/wp-content/uploads/October-28-Governing-Board-Packet.pdf")]
    # The reviewed meeting files already use these IDs, so a capture updates rather than duplicates them.
    assert [d["id"] for d in meetings["2026-09-23"]["documents"]] == [
        "trpa-governing-agenda-2026-09-23", "trpa-governing-agenda-packet-2026-09-23"]
    assert meetings["2026-09-23"]["documents"][1]["url"].endswith("/September-23-Governing-Board-Packet-1.pdf")
    # The selection is the meeting's whole document set: the Legal Committee agenda stored before stops being current.
    assert all(m["documents_complete"] for m in meetings.values())


def test_minutes_on_a_meeting_page_still_become_links_for_earlier_meetings():
    rows = discover_configured("trpa-governing", _config("trpa-governing"), fetch=_fetch)
    earlier = {(r["date"], r["minutes_url"]) for r in rows if r.get("records_only")}
    assert ("2026-09-23", "https://www.trpa.gov/wp-content/uploads/Draft-September-23-Governing-Board-Minutes.pdf") in earlier
    assert ("2026-08-26", "https://www.trpa.gov/wp-content/uploads/Draft-August-26-2026-Governing-Board-Minutes.pdf") in earlier
    assert not any("Committee" in url for _, url in earlier)


def test_advisory_planning_commission_names_its_documents_apc():
    [meeting] = _meetings("trpa-advisory").values()
    assert [(d["id"], d["title"]) for d in meeting["documents"]] == [
        ("trpa-advisory-agenda-2026-05-13", "May 13, 2026 APC Meeting Agenda"),
        ("trpa-advisory-agenda-packet-2026-05-13", "May 13, 2026 APC Meeting Packet")]
    assert meeting["documents"][0]["url"].endswith("/DRAFT-May-13-2026-APC-Meeting-Agenda-V2.pdf")


def test_committee_staff_report_and_other_dates_are_not_meeting_documents():
    config = _config("trpa-governing")
    base = "https://www.trpa.gov/meeting/"
    markup = """<a href="/wp-content/uploads/Legal-Committee-Agenda-10-28-2026.pdf">Legal Committee</a>
    <a href="/wp-content/uploads/Transportation-Committee-Governing-Board-Agenda.pdf">Transportation Committee Governing Board Agenda</a>
    <a href="/wp-content/uploads/Agenda-Item-No-7-A.pdf">Governing Board Agenda Item No. 7.A Staff Report</a>
    <a href="/wp-content/uploads/September-23-Governing-Board-Agenda.pdf">September 23 Governing Board Agenda</a>
    <a href="/wp-content/uploads/Draft-September-23-Governing-Board-Minutes.pdf">Draft September 23, 2026 Governing Board Minutes</a>"""
    assert trpa_documents("trpa-governing", config, markup, base, "2026-10-28") == ([], False)
    # A bare "Agenda" link is recognized by its file name; the anchor text stays the title.
    docs, complete = trpa_documents("trpa-governing", config,
                                    '<a href="/wp-content/uploads/October-28-Governing-Board-Agenda.pdf">Agenda</a>', base, "2026-10-28")
    assert complete and [(d["id"], d["title"]) for d in docs] == [("trpa-governing-agenda-2026-10-28", "Agenda")]


def test_a_page_without_an_agenda_never_removes_stored_documents():
    config = {"type": "trpa", "indexUrl": INDEX, "bodyPattern": "Governing Board"}
    index = '<a href="https://www.trpa.gov/gb-november/">Governing Board Documents November 18, 2026-Hybrid Meeting</a>'
    [row] = discover_configured("trpa-governing", config, fetch=lambda url: index if url == INDEX else "<p>Materials coming soon</p>")
    assert row["documents"] == [] and "documents_complete" not in row


def test_agenda_and_packet_are_both_within_the_default_document_limit():
    # Legistar lists an accessible HTML agenda, the same agenda as a PDF, the packet, and minutes.
    links = [("https://example.legistar.com/View.ashx?M=AADA&ID=1", "Accessible Agenda"),
             ("https://example.legistar.com/View.ashx?M=A&ID=1", "Agenda"),
             ("https://example.legistar.com/View.ashx?M=M&ID=1", "Minutes"),
             ("https://example.legistar.com/View.ashx?M=PA&ID=1", "Agenda Packet")]
    docs = documents_in("".join(f'<a href="{u}">{label}</a>' for u, label in links), "https://example.legistar.com/", "board")
    assert [d["title"] for d in docs[:2]] == ["Accessible Agenda", "Agenda Packet"]
    # A committee's agenda and minutes on the same page wait behind the packet.
    markup = """<a href="/minutes.pdf">Board Minutes</a><a href="/agenda.pdf">Board Agenda</a>
    <a href="/committee-agenda.pdf">Finance Committee Agenda</a><a href="/packet.pdf">Board Packet</a>
    <a href="/staff.pdf">Agenda Item 4 staff report</a>"""
    docs = documents_in(markup, "https://example.gov/meeting", "board")
    assert [d["url"].rsplit("/", 1)[1] for d in docs] == ["agenda.pdf", "packet.pdf", "committee-agenda.pdf", "minutes.pdf", "staff.pdf"]


def test_a_page_without_a_short_agenda_keeps_its_order():
    # With no short agenda the earlier preference stands: minutes, then the packet.
    packet_only = [{"id": "b-minutes", "title": "Minutes", "url": "https://example.gov/m.pdf"},
                   {"id": "b-agenda-packet", "title": "Agenda Packet", "url": "https://example.gov/p.pdf"}]
    assert agenda_then_packet(list(reversed(packet_only))) == packet_only
    pair = [{"id": "b-agenda", "title": "Agenda", "url": "https://example.gov/a.pdf"},
            {"id": "b-agenda-packet", "title": "Agenda Packet", "url": "https://example.gov/p.pdf"}]
    assert agenda_then_packet(list(reversed(pair))) == pair


def _agenda_pages():
    fixture = json.loads((ROOT / "agenda-layouts/trpa-governing--2026-09-23.json").read_text())
    return fixture["pages"]


def test_items_come_from_the_agenda_not_the_packet_that_reprints_it():
    agenda = [dict(p, document_id="trpa-governing-agenda-2026-09-23") for p in _agenda_pages()]
    # The packet opens with an older copy of the agenda that lacks the last item.
    older = [dict(p, document_id="trpa-governing-agenda-packet-2026-09-23",
                  text=p["text"].replace("13.", "XIII.")) for p in _agenda_pages()]
    documents = [{"id": "trpa-governing-agenda-2026-09-23"}, {"id": "trpa-governing-agenda-packet-2026-09-23"}]
    result = parse_agenda("trpa-governing", documents, agenda + older)
    assert result.complete and result.items[-1]["source_number"] == "13"
    assert {ref["document_id"] for item in result.items for ref in item["source_pages"]} == {"trpa-governing-agenda-2026-09-23"}
    # Without the agenda document the packet's copy is still read.
    packet = [dict(p, document_id="trpa-governing-agenda-packet-2026-09-23") for p in _agenda_pages()]
    result = parse_agenda("trpa-governing", documents[1:], packet)
    assert result.complete and len(result.items) == 21


def test_capture_keeps_agenda_and_packet_and_marks_the_set_complete():
    page = (ROOT / "discovery/trpa-governing-board-2026-09-23.html").read_text()
    docs, complete = trpa_documents("trpa-governing", _config("trpa-governing"), page, "https://www.trpa.gov/", "2026-09-23")
    candidate = {"agency": "trpa-governing", "source_id": "2026-09-23", "title": "Governing Board Documents September 23, 2026",
                 "date": "2026-09-23", "official_url": "https://www.trpa.gov/gb/", "documents": docs, "documents_complete": complete}
    agenda_pages = [{"page_number": p["page_number"], "text": p["text"]} for p in _agenda_pages()]
    packet_pages = agenda_pages + [{"page_number": len(agenda_pages) + 1, "text": "STAFF REPORT"}]

    def fetch(url):
        pages = packet_pages if "Packet" in url else agenda_pages
        return {"sha256": ("b" if "Packet" in url else "a") * 64, "page_count": len(pages), "pages": pages, "warnings": []}
    row = _capture_row(candidate, 2, fetch, [], [], 0)
    assert [d["id"] for d in row["documents"]] == ["trpa-governing-agenda-2026-09-23", "trpa-governing-agenda-packet-2026-09-23"]
    assert row["documents_complete"] and row["items_complete"] and len(row["agenda_items"]) == 21
    assert {ref["document_id"] for item in row["agenda_items"] for ref in item["source_pages"]} == {"trpa-governing-agenda-2026-09-23"}
