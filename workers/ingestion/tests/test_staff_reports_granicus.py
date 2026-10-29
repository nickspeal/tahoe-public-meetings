"""Granicus staff report rules (staff_report_rules/douglas.py, slt_commissions.py, tcpud.py); fixture-driven, no network."""
import copy
import re
from datetime import date

import pytest

from tahoe_ingest import staff_reports
from tahoe_ingest.staff_report_rules import douglas, tcpud
from tahoe_ingest.staff_reports import RULES, Context, Document, Item, Meeting
from test_staff_reports import load, run_rule

DOUGLAS_PACKET = ("https://d3n9y02raazwpg.cloudfront.net/douglascountynv/e6a9138d-c4da-11f0-a7da-005056a89546-"
                  "4b50c5b5-ef67-4e80-82ac-7b28f0e57fa1-1790376197.pdf")
VIEW_PUBLISHER = "https://douglascountynv.granicus.com/ViewPublisher.php?view_id=1"


def packet_pages(data):
    version = next(d["version_id"] for d in data["documents"] if "agenda-packet" in d["source_document_id"])
    return data["stored"][version]


def run_with_limits(data):
    """``run_rule`` with a PDF reader that accepts the download limits a rule passes (``max_bytes``, ``max_pages``)."""
    m = data["meeting"]
    meeting = Meeting(m["id"], m["agency_id"], m["title"], date.fromisoformat(m["local_date"]),
                      [Item(i["id"], i["number"], i["title"], i["section"], i["fingerprint"], i["citations"]) for i in data["items"]],
                      [Document(**d) for d in data["documents"]], m.get("official_url"))
    limits = []

    def reader(url, *, last_page=None, links=False, **kwargs):
        limits.append(kwargs)
        if url not in data["downloads"]:
            raise staff_reports.FetchFailed(f"{url}: not in fixture")
        return copy.deepcopy(data["downloads"][url])

    def fetch_html(url):
        if url not in data["html"]:
            raise staff_reports.FetchFailed(f"{url}: not in fixture")
        return data["html"][url]

    ctx = Context(lambda v: {int(n): t for n, t in data["stored"].get(v, {}).items()}, meeting, reader, fetch_html=fetch_html)
    mappings = {mp.item_id: mp for mp in RULES[m["agency_id"]](ctx)}
    return {i["number"]: mappings.get(i["id"]) for i in data["items"]}, ctx, limits


def listed_packet(data, *, clip="1892", packet=DOUGLAS_PACKET):
    """The fixture with its packet uncaptured: the ViewPublisher row of ``clip`` lists ``packet``, served as a download."""
    data = copy.deepcopy(data)
    pages = packet_pages(data)
    data["documents"] = [d for d in data["documents"] if "agenda-packet" not in d["source_document_id"]]
    data["downloads"][DOUGLAS_PACKET] = {"sha256": "f" * 64, "page_count": 652, "links": [], "boxes": [],
                                         "pages": [{"page_number": int(n), "text": t} for n, t in pages.items()]}
    row = ('<tr class="listingRow"><td><a href="//douglascountynv.granicus.com/AgendaViewer.php?view_id=1&clip_id={c}">Agenda</a></td>'
           '<td headers="AgendaPacketLink"><a href="{p}">Agenda Packet</a></td></tr>')
    data["html"] = {VIEW_PUBLISHER: "<table>" + row.format(c=clip, p=packet) +
                    row.format(c="1880", p="https://d3n9y02raazwpg.cloudfront.net/douglascountynv/other-packet.pdf") + "</table>"}
    return data


# --- Douglas County ---------------------------------------------------------------

def test_douglas_cover_for_another_meeting_date_is_not_stored():
    data = load("douglas-commissioners")
    pages = packet_pages(data)
    pages["81"] = pages["81"].replace("October 1, 2026", "September 17, 2026", 1)
    found, ctx, _ = run_rule(data)
    assert found["9.A"] is None and "another meeting date" in ctx.skipped["douglas-commissioners:2026-10-01:9.A"]
    assert found["9.B"].page_start == 120


def test_douglas_lettered_cover_must_be_in_the_items_own_section():
    data = load("douglas-commissioners")
    pages = packet_pages(data)
    pages["81"] = pages["81"].replace("CONSENT ITEMS", "PRESENTATIONS", 1)
    found, ctx, _ = run_rule(data)
    assert found["9.A"] is None and "no packet cover page" in ctx.skipped["douglas-commissioners:2026-10-01:9.A"]
    assert found["9.B"] is not None


def test_douglas_cover_with_another_title_is_not_stored():
    data = load("douglas-commissioners")
    pages = packet_pages(data)
    pages["80"] = pages["80"].replace("On-Site Retail Unrestricted Liquor License for La", "Packaged Beer and Wine License for El", 1)
    pages["80"] = pages["80"].replace("Hacienda Del Sazon Gonzalez LLC, dba La Hacienda Del Sazon, represented", "Mirador Market LLC, represented", 1)
    found, ctx, _ = run_rule(data)
    assert found["7"] is None and "title differs" in ctx.skipped["douglas-commissioners:2026-10-01:7"]


def test_douglas_packet_that_does_not_open_with_the_agenda_maps_nothing():
    data = load("douglas-commissioners")
    packet_pages(data)["1"] = "DOUGLAS COUNTY PLANNING COMMISSION\nAGENDA\nOctober 13, 2026\n"
    with pytest.raises(staff_reports.Uncertain, match="does not open with"):
        run_rule(data)


def test_douglas_uncaptured_packet_comes_from_the_clips_listing_row_with_raised_limits():
    found, ctx, limits = run_with_limits(listed_packet(load("douglas-commissioners")))
    assert found["9.A"].url == DOUGLAS_PACKET and (found["9.A"].page_start, found["9.A"].page_end) == (81, 119)
    assert found["16"].page_end == 652 and found["5"] is None
    assert limits == [{"max_bytes": douglas.MAX_BYTES, "max_pages": douglas.MAX_PAGES}]


def test_douglas_packet_listed_for_another_clip_is_not_used():
    data = listed_packet(load("douglas-commissioners"), clip="1907")
    with pytest.raises(staff_reports.Uncertain, match="listed 0 times"):
        run_with_limits(data)


def test_douglas_oversized_packet_is_uncertain_not_retried():
    data = listed_packet(load("douglas-commissioners"))
    del data["downloads"][DOUGLAS_PACKET]

    def too_large(url, **kwargs):
        raise staff_reports.TooLarge(f"{url}: source exceeds 104857600 byte limit")
    m = data["meeting"]
    ctx = Context(lambda v: {int(n): t for n, t in data["stored"].get(v, {}).items()},
                  Meeting(m["id"], m["agency_id"], m["title"], date.fromisoformat(m["local_date"]),
                          [Item(i["id"], i["number"], i["title"], i["section"], i["fingerprint"], i["citations"]) for i in data["items"]],
                          [Document(**d) for d in data["documents"]], m["official_url"]),
                  too_large, fetch_html=lambda url: data["html"][url])
    with pytest.raises(staff_reports.Uncertain, match="larger than 100 MB"):
        RULES["douglas-commissioners"](ctx)


# --- South Lake Tahoe commissions ------------------------------------------------------

def test_slt_commission_report_for_another_meeting_is_not_stored():
    data = load("slt-parks-recreation-commission")
    url = next(u for u in data["downloads"] if u.endswith("Parks_Foundation_Update.pdf"))
    first = data["downloads"][url]["pages"][0]
    first["text"] = first["text"].replace("September 16, 2026", "August 19, 2026")
    found, ctx, _ = run_rule(data)
    assert found["3"] is None and "another meeting date" in ctx.skipped["slt-parks-recreation-commission:clip-2357:3"]
    assert found["2"] is not None and len(ctx.attachments) == 4


def test_slt_commission_report_to_another_body_is_not_stored():
    data = load("slt-planning")
    url = next(u for u in data["downloads"] if u.endswith("meta_id=218999"))
    first = data["downloads"][url]["pages"][0]
    first["text"] = first["text"].replace("Report to Planning Commission", "Report to City Council")
    found, ctx, _ = run_rule(data)
    assert found["3"] is None and "not addressed" in ctx.skipped["slt-planning:2906:3"]
    assert found["2"] is not None


def test_slt_transit_jpa_report_needs_its_own_board_report_heading():
    data = load("slt-transit-jpa")
    url = next(u for u in data["downloads"] if u.endswith("Staff_Report_Budget_20261022.pdf"))
    first = data["downloads"][url]["pages"][0]
    first["text"] = first["text"].replace("(JPA)\n                   Board Report", "Report to City Council", 1)
    found, ctx, _ = run_rule(data)
    assert found["4"] is None and "not addressed" in ctx.skipped["slt-transit-jpa:1530:4"]
    assert found["2"] is not None and found["3"] is not None


def test_slt_commission_agenda_stored_under_event_and_clip_urls_counts_once():
    data = load("slt-parks-recreation-commission")
    alone, _, _ = run_rule(data)
    copy_ = dict(data["documents"][0], version_id=data["documents"][0]["version_id"] + "-event",
                 source_document_id="slt-parks-recreation-commission-agenda-event",
                 url="https://slt.granicus.com/AgendaViewer.php?view_id=6&event_id=1396")
    data["documents"].append(copy_)
    found, ctx, _ = run_rule(data)
    assert {n: m and m.url for n, m in found.items()} == {n: m and m.url for n, m in alone.items()}
    assert sum(m is not None for m in found.values()) == 3
    other = dict(copy_, sha256="0" * 64)
    data["documents"][-1] = other
    with pytest.raises(staff_reports.Uncertain, match="no single agenda PDF"):
        run_rule(data)


def test_slt_commission_html_agenda_with_another_title_for_the_number_is_not_used():
    data = load("slt-planning")
    url = next(iter(data["html"]))
    data["html"][url] = re.sub(r'(meta_id=218998" onclick="">)[^<]+', r"\1Short-Term Rental Permit Appeal, 3401 Lake Tahoe Boulevard",
                               data["html"][url])
    found, ctx, _ = run_rule(data)
    assert found["3"] is None and "another title" in ctx.skipped["slt-planning:2906:3"]


def test_slt_commission_empty_html_agenda_without_packet_is_uncertain():
    data = load("slt-planning")
    data["html"][next(iter(data["html"]))] = ""
    with pytest.raises(staff_reports.Uncertain, match="no HTML agenda or single agenda packet"):
        run_rule(data)


# --- Tahoe City PUD ---------------------------------------------------------------

def test_tcpud_memo_follows_its_title_not_the_last_sub_item():
    data = load("tcpud-parks-recreation-committee")
    rows = {r["number"]: r for r in tcpud.agenda_rows(next(iter(data["html"].values())))}
    assert [d["name"] for d in rows["7"]["documents"]] == ["Department Reports"] and rows["7.b"]["documents"] == []
    found, ctx, _ = run_rule(data)
    # The department report filed under item 7 is the board's, dated October 23: after this meeting.
    assert found["7"] is None and "dated outside" in ctx.skipped["tcpud-parks-recreation-committee:clip-1738:7"]


def test_tcpud_changed_agenda_page_maps_nothing():
    data = load("tcpud")
    url = next(iter(data["html"]))
    data["html"][url] = data["html"][url].replace("Adopt 2027-2031 Strategic Plan", "Adopt 2027-2031 Strategic Plan (revised)")
    with pytest.raises(staff_reports.Uncertain, match="changed since"):
        run_rule(data)


def test_tcpud_memo_from_another_meeting_is_not_stored():
    data = load("tcpud")
    url = next(u for u in data["downloads"] if u.endswith("meta_id=111112"))
    first = data["downloads"][url]["pages"][0]
    first["text"] = first["text"].replace("September 10, 2026", "June 10, 2026")
    found, ctx, _ = run_rule(data)
    assert found["E.3"] is None and "dated outside" in ctx.skipped["tcpud:2026-09-18:E.3"]
    assert found["E.5"] is not None


def test_tcpud_memo_with_another_subject_is_not_stored():
    data = load("tcpud")
    swapped = {u: data["downloads"][u] for u in data["downloads"] if u.endswith(("meta_id=111112", "meta_id=111116"))}
    a, b = swapped
    data["downloads"][a], data["downloads"][b] = swapped[b], swapped[a]
    found, ctx, _ = run_rule(data)
    assert found["E.3"] is None and found["E.5"] is None
    assert "subject differs" in ctx.skipped["tcpud:2026-09-18:E.3"]


def test_tcpud_pay_estimates_and_unchecked_titles_stay_unmapped():
    found, ctx, _ = run_rule(load("tcpud"))
    assert all(found[n] is None for n in ("D.4", "D.5", "D.6", "D.7", "D.8"))
    assert "memo or department report heading" in ctx.skipped["tcpud:2026-09-18:D.5"]
    assert "too short" in ctx.skipped["tcpud:2026-09-18:D.2"]  # "Finance": one word cannot be checked against a report
    assert {a.item_id.rsplit(":", 1)[1] for a in ctx.attachments} >= {"E.3", "E.4", "G"}


def test_tcpud_empty_event_page_is_uncertain():
    data = load("tcpud")
    data["html"][next(iter(data["html"]))] = ""
    with pytest.raises(staff_reports.Uncertain, match="empty"):
        run_rule(data)
