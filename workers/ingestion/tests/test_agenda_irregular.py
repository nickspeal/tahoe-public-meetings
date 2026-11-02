"""Agendas with irregular numbering are published as far as they can be read (decision 0030).

The Douglas County (Nov 5, 2026) and Washoe County (Oct 27, 2026) agendas are
replayed in full by test_agenda_layouts; tests/fixtures/agenda-copies holds the
first pages of an agenda and the packet that reprints it (IVGID Sep 23, NTPUD
Nov 3 special), which were rejected as "differing item sequences".
"""
import json
import re
from pathlib import Path

from tahoe_ingest.agenda import parse_agenda
from tahoe_ingest.capture import _capture_row
from tahoe_ingest.outline import PLACEHOLDER_TITLE, is_placeholder

ROOT = Path(__file__).resolve().parents[3] / "tests/fixtures"


def _layout_fixture(name):
    fixture = json.loads((ROOT / "agenda-layouts" / f"{name}.json").read_text())
    return fixture, [dict(page, document_id="doc") for page in fixture["pages"]]


def _copies(name):
    fixture = json.loads((ROOT / "agenda-copies" / f"{name}.json").read_text())
    return fixture, fixture["documents"], fixture["pages"]


def test_captured_meeting_publishes_the_items_with_the_notes_as_warnings():
    fixture, pages = _layout_fixture("douglas-commissioners--2026-11-05")

    def fetch(url):
        return {"sha256": "0" * 64, "page_count": len(pages), "warnings": [],
                "pages": [{"page_number": p["page_number"], "text": p["text"]} for p in pages]}
    candidate = {"agency": fixture["agency"], "source_id": "2064", "title": "Douglas County Board of County Commissioners",
                 "official_url": "https://example.org/m", "date": "2026-11-05",
                 "documents": [{"id": "douglas-agenda", "title": "Agenda", "url": "https://example.org/a.pdf"}]}
    row = _capture_row(candidate, 2, fetch, [], [], 0)
    assert row["items_complete"] and len(row["agenda_items"]) == 38
    assert [w for w in row["warnings"] if w.endswith("review required.")] == fixture["notes"]


def test_a_skipped_consent_letter_becomes_a_placeholder():
    # Consent calendar item 9 lists A-D, then F-U: E is printed nowhere in the agenda.
    fixture, pages = _layout_fixture("douglas-commissioners--2026-11-05")
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], pages)
    assert result.complete
    numbers = [i["source_number"] for i in result.items]
    assert numbers[numbers.index("9.A"):numbers.index("9.U") + 1] == [f"9.{c}" for c in "ABCDEFGHIJKLMNOPQRSTU"]
    placeholder = result.items[numbers.index("9.E")]
    assert is_placeholder(placeholder)
    assert placeholder["title"] == PLACEHOLDER_TITLE == "Not Found In Agenda"
    assert placeholder["section"] == "Consent Items" and placeholder["source_pages"][0]["quote"] == ""
    assert [i for i in result.items if is_placeholder(i)] == [placeholder]
    assert "Douglas County Board of Commissioners agenda skips item 9.E; it is listed as “Not Found In Agenda”; review required." \
        in result.notes
    # "6.68 acres ..." inside item 12 reads like a dotted label; it is passed over with the existing warning text.
    assert any(n.startswith("Douglas County Board of Commissioners agenda numbering is not sequential near “6.68 acres")
               for n in result.notes)
    assert numbers[-6:] == ["12", "13.1", "13.2", "14", "15", "16"]


def test_indentation_that_contradicts_sequential_numbers_gives_a_flat_list():
    # Items 8-20 are printed indented like subitems; 21 returns to the margin.
    fixture, pages = _layout_fixture("washoe-commissioners--2026-10-27")
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], pages)
    assert result.complete and len(result.items) == 41
    top = [i["source_number"] for i in result.items if i["source_number"] and "." not in i["source_number"]]
    assert top == [str(n) for n in [1, 2, 3, 4] + list(range(8, 23))]
    assert result.notes == ("Washoe County Board of Commissioners agenda numbering is not sequential near "
                            "“21.    Public Comment. Comment heard under this item will be”; review required.",)


def _stpud():
    fixture, pages = _layout_fixture("stpud--2026-10-15")
    return fixture["agency"], pages


def _replace(pages, old, new):
    assert any(old in p["text"] for p in pages), old
    return [dict(p, text=p["text"].replace(old, new, 1)) for p in pages]


def test_numbering_that_goes_backwards_keeps_the_other_items():
    agency, pages = _stpud()
    clean = parse_agenda(agency, [{"id": "doc"}], pages)
    # Item 7 printed as a second "6": the line is passed over, items before and after it are kept.
    line = next(l for p in pages for l in p["text"].split("\n") if l.startswith("7. "))
    result = parse_agenda(agency, [{"id": "doc"}], _replace(pages, line, "6." + line[2:]))
    assert result.complete
    numbers = [i["source_number"] for i in result.items]
    assert "7" not in numbers and {"5", "6.a", "8", "9.a"} <= set(numbers)
    assert len(result.items) == len(clean.items) - 1
    assert len(result.notes) == 1 and "numbering is not sequential near “6." in result.notes[0]
    assert result.notes[0].endswith("review required.")


def test_too_few_recognizable_items_still_publish_nothing():
    fixture, pages = _layout_fixture("ndep--2026-08-19")
    # Items 2-17 all repeat "1": only item 1 and the numbered adjournment can be read, fewer than the layout's three.
    broken = [dict(p, text=re.sub(r"(?m)^(\s*)(?:[2-9]|1[0-7])(?=\s{2,}\S)", r"\g<1>1", p["text"])) for p in pages]
    result = parse_agenda(fixture["agency"], [{"id": "doc"}], broken)
    assert not result.complete and result.items == []
    assert "numbering is not sequential" in result.warning and result.warning.endswith("review required.")


def test_the_agenda_is_preferred_to_a_packet_that_adds_page_references():
    for name, count in (("ivgid--2026-09-23", 12), ("ntpud--2026-11-03-special", 6)):
        fixture, documents, pages = _copies(name)
        packet_first = sorted(documents, key=lambda d: "-agenda-packet" not in d["id"])
        result = parse_agenda(fixture["agency"], packet_first, pages)
        assert result.complete, (name, result.warning)
        assert len(result.items) == count
        assert {i["source_pages"][0]["document_id"] for i in result.items} == {d["id"] for d in documents if "packet" not in d["id"]}
        assert not any("(Pages" in i["title"] or "– pages" in i["title"] for i in result.items)
        assert result.notes == ()  # page references and a long title cut at another line are not a difference


def test_differing_agenda_copies_publish_the_agenda_with_a_note():
    fixture, documents, pages = _copies("ntpud--2026-11-03-special")
    packet = next(d for d in documents if "packet" in d["id"])
    altered = [dict(p, text=p["text"].replace("PUBLIC COMMENT AND QUESTIONS", "PUBLIC COMMENTS"))
               if p["document_id"] == packet["id"] else p for p in pages]
    result = parse_agenda(fixture["agency"], documents, altered)
    assert result.complete and len(result.items) == 6
    assert all(i["source_pages"][0]["document_id"] != packet["id"] for i in result.items)
    assert result.notes == ("Multiple agenda PDFs contain differing item sequences; items are taken from “Agenda”; review required.",)


def test_ntcd_skipped_number_becomes_a_placeholder():
    fixture, pages = _layout_fixture("ntcd--2026-08-12")
    renumbered = _replace(_replace(_replace(pages, "15   Adjournment", "16   Adjournment"), "\n14   matters", "\n15   matters"),
                          "\n13\n", "\n14\n")
    result = parse_agenda("ntcd", [{"id": "doc"}], renumbered)
    assert result.complete
    numbers = [i["source_number"] for i in result.items]
    assert numbers[-4:] == ["13", "14", "15", "16"]
    assert is_placeholder(result.items[-4]) and result.items[-4]["title"] == PLACEHOLDER_TITLE
    assert result.notes == ("NTCD agenda skips item 13; it is listed as “Not Found In Agenda”; review required.",)
    # A number that goes backwards is passed over; its subitems with it.
    backwards = _replace(pages, "\n12\n", "\n11\n")
    result = parse_agenda("ntcd", [{"id": "doc"}], backwards)
    assert result.complete
    assert not any(i["source_number"].startswith("12.") for i in result.items if i["source_number"])
    assert result.notes[0] == "NTCD agenda numbering is not sequential at item 11; review required."
