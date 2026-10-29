"""Staff report rules for statewide bodies: Fish and Game Commission, State Parks (SPRC/OHMVR), and NDEP; no network."""
import json
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import staff_reports
from tahoe_ingest.staff_report_rules import fish_game, ndep, state_parks
from tests.test_staff_reports import run_rule

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/staff-reports"


def load(agency):
    return json.loads((FIXTURES / f"{agency}.json").read_text())


def _download(data, ending):
    return next(v for u, v in data["downloads"].items() if u.endswith(ending))


# --- California Fish and Game Commission ------------------------------------------------

def test_fish_game_summary_naming_another_item_is_not_stored():
    data = load("california-fish-game")
    # Item 3's index link now opens item 2's staff summary.
    data["downloads"][next(u for u in data["downloads"] if u.endswith("247300&inline"))] = _download(data, "247297&inline")
    found, ctx, _ = run_rule(data)
    assert found["3"] is None and "does not name item 3" in ctx.skipped["california-fish-game:2026-09-10:3"]
    assert found["2"] is not None


def test_fish_game_summary_for_another_meeting_is_not_stored():
    data = load("california-fish-game")
    first = _download(data, "247297&inline")["pages"][0]
    first["text"] = first["text"].replace("September 10, 2026 WRC", "May 14, 2026 WRC", 1)
    found, ctx, _ = run_rule(data)
    assert found["2"] is None and "another meeting date" in ctx.skipped["california-fish-game:2026-09-10:2"]


def test_fish_game_revised_agenda_maps_nothing():
    data = load("california-fish-game")
    # The year page now lists a reissued agenda (a new DocumentID) instead of the captured one.
    page = data["meeting"]["official_url"]
    data["html"][page] = data["html"][page].replace("DocumentID=246931", "DocumentID=246999")
    with pytest.raises(staff_reports.Uncertain):
        run_rule(data)


def test_fish_game_missing_index_is_retried_only_near_the_meeting():
    data = load("california-fish-game")
    page = data["meeting"]["official_url"]
    data["html"][page] = data["html"][page].replace("Meeting Documents (PDF)", "Meeting Notice (PDF)")
    with pytest.raises(staff_reports.Uncertain):
        run_rule(data)
    data["meeting"]["local_date"] = date.today().isoformat()
    with pytest.raises(staff_reports.FetchFailed):
        run_rule(data)


def test_fish_game_placeholder_index_is_retried_only_near_the_meeting():
    data = load("california-fish-game")
    index = next(u for u, v in data["downloads"].items()
                 if v["pages"] and "Meeting Documents Index" in v["pages"][0]["text"])
    data["downloads"][index]["pages"] = [{"page_number": 1, "text": "Marine Resources Committee\nMeeting materials will be "
                                          "posted here in advance of the meeting."}]
    with pytest.raises(staff_reports.Uncertain, match="not a documents index"):
        run_rule(data)
    data["meeting"]["local_date"] = date.today().isoformat()
    with pytest.raises(staff_reports.FetchFailed, match="not posted yet"):
        run_rule(data)


def _box(page, top, left, text, url=None):
    return {"page": page, "top": top, "left": left, "text": text, "url": url}


def test_fish_game_index_entries_number_lines_sub_items_and_binders():
    doc = "https://nrm.dfg.ca.gov/FileHandler.ashx?DocumentID={}&inline"
    boxes = [_box(1, 397, 243, "October 13-14, Items 1 through 25 (50 MB)", doc.format(1)),
             _box(1, 746, 81, "5."), _box(1, 746, 135, "Department Lands", doc.format(5)),
             _box(1, 862, 81, "8."), _box(1, 862, 135, "Report on the status of species listed", doc.format(8)),
             _box(1, 882, 135, "under the California Endangered Species Act", doc.format(8)),
             _box(2, 55, 81, "13."), _box(2, 55, 135, "Committee and Department Reports"),
             _box(2, 84, 135, "(A)"), _box(2, 84, 189, "Wildlife Resources Committee", doc.format(131)),
             _box(2, 114, 135, "(B)"), _box(2, 114, 189, "Department Wildlife Division", doc.format(132)),
             _box(2, 937, 81, "Bin 2 Marine Protected Area (MPA) Regulation Change Petitions"),
             _box(2, 980, 81, "Introduction to Bin 2 MPA Petitions", doc.format(99)),
             _box(3, 55, 314, "Public Receipt of Documents"), _box(3, 135, 108, "1."),
             _box(3, 218, 405, "https://fgc.ca.gov/CESA", "https://fgc.ca.gov/CESA")]
    links = [b for b in boxes if b["url"]]
    entries = fish_game.index_entries({"links": links, "boxes": [b for b in boxes if not b["url"]]})
    assert set(entries) == {"5", "8", "13"}  # the binder, the section heading link, and public receipts belong to no item
    assert list(entries["5"]["line"]) == [doc.format(5)]
    assert entries["8"]["line"][doc.format(8)].startswith("Report on the status")
    assert entries["8"]["all"] == {doc.format(8): "Report on the status of species listed under the California Endangered Species Act"}
    assert entries["13"]["line"] == {} and len(entries["13"]["all"]) == 2


# --- California State Parks ---------------------------------------------------------------

def test_state_parks_ohmvr_meetings_are_not_mapped_and_download_nothing():
    data = load("california-state-parks")
    data["meeting"]["official_url"] = "https://ohv.parks.ca.gov/?page_id=27160"
    with pytest.raises(staff_reports.Uncertain, match="no agenda item number"):
        run_rule(data)


def test_state_parks_sub_item_letter_must_match():
    data = load("california-state-parks")
    first = _download(data, "13A%20Consent%20Calendar%20Memorial%20Grove%20Resolutions.pdf")["pages"][0]
    first["text"] = first["text"].replace("13A Consent Calendar", "13B Consent Calendar")
    found, ctx, _ = run_rule(data)
    assert found["13.a"] is None and "does not name item 13.a" in ctx.skipped["california-state-parks:sprc-2026-03-05:13.a"]


def test_state_parks_links_after_the_next_meeting_agenda_are_ignored():
    data = load("california-state-parks")
    page = data["meeting"]["official_url"]
    # A later meeting's agenda now sits between the captured agenda and the staff report links.
    data["html"][page] = data["html"][page].replace(
        '<a href="https://www.parks.ca.gov/pages/29247/files/SPRC March Meeting Agenda.pdf">Meeting Agenda</a>',
        '<a href="https://www.parks.ca.gov/pages/29247/files/SPRC March Meeting Agenda.pdf">Meeting Agenda</a>\n'
        '<a href="https://www.parks.ca.gov/pages/29247/files/SPRC July Meeting Agenda.pdf">Meeting Agenda</a>')
    with pytest.raises(staff_reports.Uncertain, match="no \"Agenda Item NN\""):
        run_rule(data)


def test_state_parks_canonical_urls():
    assert state_parks.canonical("https://www.parks.ca.gov/pages/29247/files/SPRC March Meeting Agenda.pdf") == \
        state_parks.canonical("https://www.parks.ca.gov/pages/29247/files/SPRC%20March%20Meeting%20Agenda.pdf")


# --- NDEP Board for Financing Water Projects ------------------------------------------------

def _packet(data):
    return next(iter(data["downloads"].values()))["pages"]


def test_ndep_overwritten_material_maps_nothing():
    data = load("ndep")
    # Material.pdf now holds the next meeting's packet (its agenda is for another date).
    first = _packet(data)[0]
    first["text"] = first["text"].replace("August 19, 2026", "December 2, 2026")
    with pytest.raises(staff_reports.Uncertain, match="captured agenda"):
        run_rule(data)


def test_ndep_resolution_for_another_month_is_not_stored():
    data = load("ndep")
    page = next(p for p in _packet(data) if p["page_number"] == 17)
    page["text"] = page["text"].replace("D08-0826", "D08-0526")
    found, ctx, _ = run_rule(data)
    assert found["8"] is None and "another month" in ctx.skipped["ndep:2026-08-19:8"]
    assert found["9"] is not None


def test_ndep_section_for_another_recipient_is_not_stored():
    data = load("ndep")
    item = next(i for i in data["items"] if i["number"] == "9")
    item["title"] = "Carson City Utilities (For Possible Action)"
    found, ctx, _ = run_rule(data)
    assert found["9"] is None and "another recipient" in ctx.skipped["ndep:2026-08-19:9"]


def test_ndep_interrupted_page_run_is_not_a_section():
    data = load("ndep")
    page = next(p for p in _packet(data) if p["page_number"] == 27)
    page["text"] = page["text"].replace("Page 3 of 6", "Page 1 of 2")
    found, ctx, _ = run_rule(data)
    assert found["9"] is None and "no project section" in ctx.skipped["ndep:2026-08-19:9"]
    assert found["8"].page_end == 24 and found["10"] is not None


def test_ndep_sections_include_their_resolution_pages():
    data = load("ndep")
    pages = {p["page_number"]: p["text"] for p in _packet(data)}
    sections = {s["code"]: (s["start"], s["end"], s["recipient"]) for s in ndep.project_sections(pages, "August 2026")}
    assert sections["D08-0826"] == (17, 24, "Elk Point Country Club HOA")
    assert sections["G13-0826"][:2] == (55, 61)
    assert ndep.project_sections(pages, "May 2026") == []
