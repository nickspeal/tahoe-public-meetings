"""The "Order of Business" HTML agenda (California State Lands Commission meeting pages)."""
import json
import re
from pathlib import Path

from tahoe_ingest import html_order_of_business as oob
from tahoe_ingest.html_agenda import parse_html_agenda
from tahoe_ingest.html_source import html_text

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/html-agendas"
PAGE = (FIXTURES / "state-lands--2026-10-21.html").read_text(encoding="utf-8")
EXPECTED = json.loads((FIXTURES / "state-lands--2026-10-21.json").read_text(encoding="utf-8"))


def parse(markup):
    return parse_html_agenda(markup, html_text(markup))


def test_meeting_page_gives_every_part_and_numbered_item_with_exact_quotes():
    items, complete = parse(PAGE)
    assert complete
    assert [[i["source_number"], i["section"], i["title"]] for i in items] == EXPECTED["expected"]
    assert len(items) == 74 and sum(i["source_number"].isdigit() for i in items) == 64
    text = html_text(PAGE)
    for order, item in enumerate(items, 1):
        assert item["order"] == order
        quote = item["source_pages"][0]["quote"]
        assert quote in text and item["source_pages"][0]["page_number"] == 1
    by_number = {i["source_number"]: i for i in items}
    # The quote is the whole row, with its CEQA note and staff; public comment letters are left out.
    assert by_number["01"]["source_pages"][0]["quote"].endswith("(Staff: M. McGough)")
    assert "Public Comments" not in by_number["27"]["source_pages"][0]["quote"]
    assert by_number["42"]["title"].endswith("(Removed from the agenda 10/19/2026)")
    assert by_number["I.a"]["title"].startswith("CITY OF PACIFICA (PARTY) Consider approval for a limited term Emergency")
    assert by_number["58"]["section"] == "Regular Calendar 58-64"
    assert all(len(i["title"]) <= oob.MAX_TITLE + 40 for i in items)
    # A title cut at MAX_TITLE keeps the whole description, up to its CEQA note, beside it.
    cut = [i for i in items if i.get("full_title")]
    assert cut and all(len(i["full_title"]) > len(i["title"]) and "CEQA Consideration" not in i["full_title"] for i in cut)
    assert all(i["full_title"].startswith(i["title"].removesuffix(" (Removed from the agenda 10/19/2026)")) for i in cut)
    assert not any(i.get("full_title") for i in items if len(i["title"]) < oob.MAX_TITLE - 40)


def test_capture_stores_the_page_text_and_its_items(monkeypatch):
    import io
    from tahoe_ingest import http, source

    class Response(io.BytesIO):
        headers = {"Content-Type": "text/html; charset=UTF-8"}

        def geturl(self):
            return "https://www.slc.ca.gov/meeting/10-21-26-voting-record/"
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **k: Response(PAGE.encode("utf-8")))
    fetched = source.download_extract("https://www.slc.ca.gov/meeting/10-21-26-voting-record/")
    assert fetched["media_type"] == "text/html" and fetched["items_complete"] and len(fetched["agenda_items"]) == 74
    assert fetched["pages"] == [{"page_number": 1, "text": html_text(PAGE)}]


def test_voting_record_above_the_order_of_business_is_not_read():
    rows = oob.agenda_rows(PAGE)
    assert "Voting Record" in html_text(PAGE)
    assert not any("vote of" in r["passage"] for r in rows)


def test_rows_keep_the_item_staff_report_link_first():
    rows = {r["number"]: r for r in oob.agenda_rows(PAGE, "https://www.slc.ca.gov/meeting/10-21-26-voting-record/")}
    assert rows["01"]["pdfs"] == ["https://slcprdwordpressstorage.blob.core.windows.net/wordpressdata/archive/2026/20261021/1-10-21-26_01.pdf"]
    assert rows["27"]["pdfs"][0].endswith("/27-10-21-26_27.pdf") and rows["27"]["pdfs"][1].endswith("/10-21-26_27-PC.pdf")
    assert rows["42"]["pdfs"] == []
    assert rows["I.a"]["pdfs"][0].endswith("/I(a)-10-21-26_Ia.pdf")


def test_an_incomplete_or_inconsistent_page_publishes_no_items():
    without_43 = re.sub(r'<tr[^>]*>\s*<th[^>]*><span[^>]*>43</span></th>.*?</tr>', "", PAGE, count=1, flags=re.S)
    assert without_43 != PAGE
    assert parse(without_43) == ([], False)
    cut_off = PAGE[:PAGE.index('<span style="font-size: 11pt;">61</span>')] + "</table></div></div></body></html>"
    assert parse(cut_off) == ([], False)  # items 61-64 missing from "Regular Calendar 58-64"
    assert parse(PAGE.replace("Consent Calendar 01-57</h2>", "Consent Calendar 01-56</h2>")) == ([], False)
    assert parse(PAGE.replace("Regular Calendar 58-64</h2>", "Regular Calendar</h2>")) == ([], False)  # 58-64 unstated
    # Pages before 2026 have no Adjourn part; the numbering checks still apply.
    items, complete = parse(PAGE.replace("<h2>IX. Adjourn</h2>", ""))
    assert complete and items[-1]["source_number"] == "VIII"
    assert parse(PAGE.replace("<h2>VII. Commissioners", "<h2>VIII. Commissioners")) == ([], False)


def test_other_html_agendas_are_not_taken_for_this_format():
    markup = '''<h1>Town Council Meeting</h1><h2 class="section-header">1. Call to Order</h2>
    <h2 class="section-header">2. Adjournment</h2>'''
    assert oob.agenda_rows(markup) == []
    assert oob.roman("IX") == 9 and oob.roman("XIV") == 14
