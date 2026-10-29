import json
from datetime import date

import pytest

from tahoe_ingest import backfill, capture, source_discovery
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.discovery import _pdf_links, granicus_row_ids, wordpress_meetings
from tahoe_ingest.source_discovery import validate_discovery

TTD_FEED = "https://tahoetransportation.org/wp-json/wp/v2/meeting"
RTCPC = "Regional (?:and )?Transit (?:and |& )?Capital Programs"


def wordpress(body="board", **extra):
    return {"type": "wordpress-meetings", "indexUrl": TTD_FEED, "bodyPattern": body, **extra}


def test_wordpress_discovery_is_bounded_and_only_keeps_official_upload_pdfs():
    api = json.dumps([{"id": 7110, "link": "https://tahoetransportation.org/meeting/7110/",
                       "title": {"rendered": "Board &amp; Committee"}, "modified": "2026-10-01"}])
    page = '<a href="/wp-content/uploads/agenda.pdf">Agenda</a><a href="https://other.example/ad.pdf">Unrelated PDF</a>'
    requested = []

    def fetch(url):
        requested.append(url)
        return api if "wp-json" in url else page

    rows = wordpress_meetings("ttd", wordpress(), limit=1, fetch=fetch, delay=0)
    assert requested == [TTD_FEED + "?per_page=10&page=1&_fields=id,link,title,modified", "https://tahoetransportation.org/meeting/7110/"]
    assert rows[0]["source_id"] == "7110"
    assert rows[0]["title"] == "Board & Committee"
    assert [d["id"] for d in rows[0]["documents"]] == ["ttd-agenda"]


def test_wordpress_committee_keeps_only_its_own_posts_and_the_feed_document_ids():
    titles = {7174: "TTD Regional and Transit Capital Programs Committee Meeting – November 2, 2026",
              7172: "TTD Board of Directors Meeting – November 4, 2026",
              7173: "TTD Finance and Personnel Committee Meeting – November 4, 2026",
              6013: "TTD Regional Transit &amp; Capital Programs Committee Mtg-June 2, 2025",
              6042: "Regional Transit and Capital Programs Committee Meeting – March 30, 2026"}
    api = json.dumps([{"id": i, "link": f"https://tahoetransportation.org/meeting/{i}/", "title": {"rendered": t},
                       "modified": "2026-10-13"} for i, t in titles.items()])
    packet = '<a href="/wp-content/uploads/packet.pdf">Agenda Packet</a>'
    rows = wordpress_meetings("ttd-rtcpc", wordpress(RTCPC), limit=5, fetch=lambda url: api if "wp-json" in url else "", delay=0)
    assert [r["source_id"] for r in rows] == ["7174", "6013", "6042"]
    assert [r["source_id"] for r in wordpress_meetings("ttd", wordpress(), limit=5,
                                                       fetch=lambda url: api if "wp-json" in url else "", delay=0)] == ["7172"]
    # A committee that shares the board's feed keeps the document IDs it was first captured with.
    [row] = wordpress_meetings("ttd-rtcpc", wordpress(RTCPC, documentIdPrefix="ttd"), limit=1,
                               fetch=lambda url: api if "wp-json" in url else packet, delay=0)
    assert row["documents"][0]["id"] == "ttd-agenda-packet"
    [row] = wordpress_meetings("ttd-rtcpc", wordpress(RTCPC), limit=1, fetch=lambda url: api if "wp-json" in url else packet, delay=0)
    assert row["documents"][0]["id"] == "ttd-rtcpc-agenda-packet"


def test_granicus_row_ids_use_clip_id_row_title_and_agenda_viewer_before_packet():
    config = {"type": "granicus-row-ids", "indexUrl": "https://slt.granicus.com/ViewPublisher.php?view_id=6", "bodyPattern": "City Council"}
    markup = '''<table><tr><td id="10-13-2026-Special-City-Council">Special City Council</td>
      <td><a href="AgendaViewer.php?view_id=6&clip_id=2351">Agenda</a></td>
      <td><a href="https://d3n.example/slt/packet.pdf">Agenda Packet</a></td></tr>
      <tr><td id="10-08-2026-Planning-Commission">Planning Commission</td>
      <td><a href="AgendaViewer.php?view_id=6&clip_id=2350">Agenda</a></td></tr>
      <tr><td id="10-06-2026-City-Council">City Council</td>
      <td><a href="AgendaViewer.php?view_id=6&clip_id=2339">Agenda</a></td></tr></table>'''
    rows = granicus_row_ids("slt", config, limit=5, fetch=lambda _: markup)
    assert [(r["source_id"], r["date"], r["title"]) for r in rows] == [("2351", "2026-10-13", "10 13 2026 Special City Council"),
                                                                      ("2339", "2026-10-06", "10 06 2026 City Council")]
    assert rows[0]["official_url"] == "https://slt.granicus.com/AgendaViewer.php?view_id=6&clip_id=2351"
    # The agenda viewer redirects to the compact agenda PDF: first, under the ID stored meetings hold their agenda under.
    assert rows[0]["documents"] == [{"id": "slt-agenda", "title": "Agenda", "url": "https://slt.granicus.com/AgendaViewer.php?view_id=6&clip_id=2351"},
                                    {"id": "slt-agenda-packet", "title": "Agenda Packet", "url": "https://d3n.example/slt/packet.pdf"}]
    assert [d["id"] for d in rows[1]["documents"]] == ["slt-agenda"]


def test_document_identifiers_do_not_depend_on_link_order():
    links = [('<a href="/agenda-packet.pdf">Agenda Packet</a>', "ttd-agenda-packet"),
             ('<a href="/minutes.pdf">Minutes</a>', "ttd-minutes")]
    first = _pdf_links("".join(html for html, _ in links), "https://tahoetransportation.org/meeting/1", "ttd")
    reversed_rows = _pdf_links("".join(html for html, _ in reversed(links)), "https://tahoetransportation.org/meeting/1", "ttd")
    assert {d["url"]: d["id"] for d in first} == {d["url"]: d["id"] for d in reversed_rows}
    assert {d["id"] for d in first} == {expected for _, expected in links}


def test_every_discovery_type_goes_through_one_scheduled_and_one_backfill_path(monkeypatch):
    calls = []

    def fake(name):
        def reader(agency, config, *args, **kwargs):
            calls.append((name, agency, config["bodyPattern"]))
            rows = [{"agency": "feed", "source_id": "1", "date": "2026-11-04", "documents": []}]
            return rows if name.endswith("now") else (rows, "2026-11-04")
        return reader
    for name in ("wordpress_meetings", "granicus_row_ids", "municode"):
        monkeypatch.setattr(source_discovery, name, fake(name + "-now"))
        monkeypatch.setattr(source_discovery, name + "_range", fake(name + "-range"))
    catalog = {a["id"]: a for a in agency_catalog()}
    for aid in ("ttd", "ttd-rtcpc", "slt", "truckee"):
        assert capture.discover_agency(aid, catalog[aid], limit=6)[0]["agency"] == aid
        found = backfill.discover_agency_range(aid, catalog[aid], since=date(2026, 6, 1), until=date(2026, 12, 31))
        assert found["candidates"][0]["agency"] == aid
        assert found["queryByDate"] is (aid == "truckee")
    assert [c[0] for c in calls] == ["wordpress_meetings-now", "wordpress_meetings-range"] * 2 + \
        ["granicus_row_ids-now", "granicus_row_ids-range", "municode-now", "municode-range"]
    assert [c[2] for c in calls[:4:2]] == ["board", RTCPC]
    with pytest.raises(ValueError, match="no discovery source"):
        capture.discover_agency("ltbmu", catalog["ltbmu"], limit=6)


def test_every_followed_source_names_a_known_type():
    followed = [a for a in agency_catalog() if a["follow"] and a.get("discovery")]
    assert followed and all(a["discovery"]["type"] in source_discovery.TYPES for a in followed)
    assert not any("discoveryAdapter" in a for a in agency_catalog())


@pytest.mark.parametrize("entry,message", [
    ({"discoveryAdapter": "ttd"}, "discoveryAdapter is retired"),
    ({"discovery": {"type": "calendar", "indexUrl": "https://example.gov/"}}, "invalid source configuration"),
    ({"discovery": {"type": "granicus", "indexUrl": "http://example.gov/"}}, "invalid source configuration"),
    ({"discovery": {"type": "granicus", "indexUrl": "https://example.gov/", "titlePrefx": "X "}}, "does not read titlePrefx"),
    ({"discovery": {"type": "wordpress-meetings", "indexUrl": TTD_FEED, "horizonDays": 30}}, "does not read horizonDays"),
    ({"discovery": {"type": "municode", "indexUrl": "https://meetings.municode.com/MeetingsPage/index"}}, "clientCode"),
    ({"discovery": {"type": "trpa", "indexUrl": "https://example.gov/", "documentPattern": "("}}, "unterminated"),
])
def test_discovery_blocks_are_checked_against_their_type(entry, message):
    with pytest.raises(Exception, match=message):
        validate_discovery({"id": "example", **entry})


def test_valid_discovery_blocks_and_entries_without_one_pass():
    validate_discovery({"id": "example"})
    validate_discovery({"id": "example", "discovery": wordpress(RTCPC, documentIdPrefix="ttd")})
    validate_discovery({"id": "example", "discovery": {"type": "dated-table", "indexUrl": "https://example.gov/2026",
                                                       "indexUrlTemplate": "https://example.gov/{year}", "horizonDays": 120}})
