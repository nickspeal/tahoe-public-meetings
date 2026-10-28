import json

from tahoe_ingest.discovery import _pdf_links, discover_slt, discover_truckee, discover_ttd


def test_ttd_discovery_is_bounded_and_only_keeps_official_upload_pdfs():
    api = json.dumps([{"id": 7110, "link": "https://tahoetransportation.org/meeting/7110/",
                       "title": {"rendered": "Board &amp; Committee"}, "modified": "2026-10-01"}])
    page = '<a href="/wp-content/uploads/agenda.pdf">Agenda</a><a href="https://other.example/ad.pdf">Unrelated PDF</a>'
    requested = []

    def fetch(url):
        requested.append(url)
        return api if "wp-json" in url else page

    rows = discover_ttd(limit=1, fetch=fetch, delay=0)
    assert len(requested) == 2
    assert rows[0]["source_id"] == "7110"
    assert rows[0]["title"] == "Board & Committee"
    assert len(rows[0]["documents"]) == 1


def test_ttd_committee_discovery_keeps_only_its_own_posts():
    titles = {7174: "TTD Regional and Transit Capital Programs Committee Meeting – November 2, 2026",
              7172: "TTD Board of Directors Meeting – November 4, 2026",
              7173: "TTD Finance and Personnel Committee Meeting – November 4, 2026",
              6013: "TTD Regional Transit &amp; Capital Programs Committee Mtg-June 2, 2025",
              6042: "Regional Transit and Capital Programs Committee Meeting – March 30, 2026"}
    api = json.dumps([{"id": i, "link": f"https://tahoetransportation.org/meeting/{i}/", "title": {"rendered": t},
                       "modified": "2026-10-13"} for i, t in titles.items()])
    rows = discover_ttd(limit=5, fetch=lambda url: api if "wp-json" in url else "", delay=0,
                        body_pattern="Regional (?:and )?Transit (?:and |& )?Capital Programs")
    assert [r["source_id"] for r in rows] == ["7174", "6013", "6042"]
    assert [r["source_id"] for r in discover_ttd(limit=5, fetch=lambda url: api if "wp-json" in url else "",
                                                 delay=0)] == ["7172"]
def test_slt_publisher_row_uses_clip_id_and_packet_pdf():
    markup = '''<table><tr><td id="10-06-2026-City-Council">City Council</td>
      <td><a href="AgendaViewer.php?view_id=6&clip_id=2339">Agenda</a></td>
      <td><a href="https://d3n.example/slt/packet.pdf">Agenda Packet</a></td></tr></table>'''
    rows = discover_slt(limit=1, fetch=lambda _: markup)
    assert rows[0]["source_id"] == "2339"
    assert rows[0]["documents"][0]["url"].endswith("packet.pdf")


def test_truckee_calendar_event_uses_native_eid_and_municode_pdf_wrapper():
    index = '<a id="eventTitle_1895" href="Calendar.aspx?EID=1895">Joint Town Council meeting</a>'
    event = '''<title>Joint Council Meeting</title><div itemprop="startDate" class="hidden">2026-10-13T17:30:00</div><a href="https://meetings.municode.com/d/f?u=https%3A%2F%2Fexample.com%2Fpacket.pdf&n=Agenda.pdf">Agenda (PDF)</a>'''
    rows = discover_truckee(year=2026, month=10, limit=1, fetch=lambda url: index if "view=list" in url else event)
    assert rows[0]["source_id"] == "1895"
    assert len(rows[0]["documents"]) == 1
    assert rows[0]["documents"][0]["title"] == "Agenda (PDF)"
    assert (rows[0]["date"], rows[0]["start_time"]) == ("2026-10-13", "17:30")
    assert rows[0]["documents"][0]["id"] == "truckee-agenda-packet"


def test_document_identifiers_do_not_depend_on_link_order():
    links = [('<a href="/agenda-packet.pdf">Agenda Packet</a>', "ttd-agenda-packet"),
             ('<a href="/minutes.pdf">Minutes</a>', "ttd-minutes")]
    first = _pdf_links("".join(html for html, _ in links), "https://tahoetransportation.org/meeting/1", "ttd")
    reversed_rows = _pdf_links("".join(html for html, _ in reversed(links)), "https://tahoetransportation.org/meeting/1", "ttd")
    assert {d["url"]: d["id"] for d in first} == {d["url"]: d["id"] for d in reversed_rows}
    assert {d["id"] for d in first} == {expected for _, expected in links}
