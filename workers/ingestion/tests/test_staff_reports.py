"""Staff report mapping (IDEA-013) and verbatim summary validation (IDEA-016); no network or model calls."""
import copy
import json
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import item_summaries, staff_reports
from tahoe_ingest.staff_reports import Context, Document, Item, Meeting, RULES

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/staff-reports"


def load(agency):
    return json.loads((FIXTURES / f"{agency}.json").read_text())


def run_rule(data):
    m = data["meeting"]
    meeting = Meeting(m["id"], m["agency_id"], m["title"], date.fromisoformat(m["local_date"]),
                      [Item(i["id"], i["number"], i["title"], i["section"], i["fingerprint"], i["citations"]) for i in data["items"]],
                      [Document(**d) for d in data["documents"]])
    downloads, read = data["downloads"], []

    def reader(url, *, last_page=None, links=False):
        read.append(url)
        if url not in downloads:
            raise staff_reports.FetchFailed(f"{url}: not in fixture")
        found = copy.deepcopy(downloads[url])
        found["pages"] = [p for p in found["pages"] if not last_page or p["page_number"] <= last_page]
        return found

    def stored(version_id):
        return {int(n): text for n, text in data["stored"].get(version_id, {}).items()}

    ctx = Context(stored, meeting, reader)
    mappings = {m.item_id: m for m in RULES[m["agency_id"]](ctx)}
    return {i["number"]: mappings.get(i["id"]) for i in data["items"]}, ctx, read


@pytest.mark.parametrize("agency", ["slt", "ttd", "truckee", "lahontan-board"])
def test_each_item_cites_pages_of_its_own_staff_report(agency):
    data = load(agency)
    found, ctx, _ = run_rule(data)
    assert set(found) == set(data["expected"])
    for number, expected in data["expected"].items():
        mapping = found[number]
        if expected is None:
            assert mapping is None, number
            continue
        assert mapping is not None, (number, ctx.skipped)
        assert mapping.page_start == expected["page_start"]
        assert mapping.page_end == expected.get("page_end", mapping.page_end) >= mapping.page_start
        assert mapping.url.endswith(expected.get("url_end", mapping.url))
        # The kept pages are the report's own leading pages, and the first one names this item.
        numbers = [n for n, _ in mapping.pages]
        assert numbers and numbers[0] == mapping.page_start and all(mapping.page_start <= n <= mapping.page_end for n in numbers)
        first = mapping.pages[0][1]
        label = number.rstrip(".")
        assert (f"#:{label}" in first or f"AGENDA ITEM: {label}." in first or f"AGENDA ITEM {label}." in first
                or f"Item {label}" in first or f"ITEM {label}" in first), number
        assert staff_reports.title_overlap(next(i["title"] for i in data["items"] if i["number"] == number), "\n".join(
            text for _, text in mapping.pages)) >= 0.5


def test_slt_link_to_another_items_summary_is_not_stored():
    data = load("slt")
    # The agenda's Item 9 link now opens the executive summary of item 5.
    url9 = next(u for u in data["downloads"] if u.endswith("Item_9-_Executive_Summary.pdf"))
    url5 = next(u for u in data["downloads"] if u.endswith("Item_5-_Executive_Summary.pdf"))
    data["downloads"][url9] = data["downloads"][url5]
    found, ctx, _ = run_rule(data)
    assert found["9"] is None and "item number" in ctx.skipped["slt:2339:9"]
    assert found["5"] is not None


def test_slt_revised_agenda_maps_nothing():
    data = load("slt")
    agenda = data["documents"][0]
    data["downloads"][agenda["url"]]["sha256"] = "0" * 64
    with pytest.raises(staff_reports.Uncertain):
        run_rule(data)


def test_ttd_page_column_must_land_on_the_items_own_footer():
    data = load("ttd")
    packet = data["documents"][0]["version_id"]
    # Page 37 (stamp 32, item IV.B) now claims to belong to IV.C.
    data["stored"][packet]["37"] = data["stored"][packet]["37"].replace("AGENDA ITEM: IV.B.", "AGENDA ITEM: IV.C.")
    found, ctx, _ = run_rule(data)
    assert found["IV.B"] is None and "not marked" in ctx.skipped["ttd:7110:IV.B"]
    assert found["IV.A"].page_end == 23  # IV.A's own run still ends where its footer does
    assert "190" in ctx.skipped["ttd:7110:VII"]  # the executive director's report carries no item footer


def test_ttd_committee_footer_is_not_the_board_item():
    text = "JS/lr       AGENDA ITEM: IV.A.\n            FPC AGENDA ITEM: III.B.\nTahoe Transportation District Board ~ Page 8 ~"
    assert [m for m in staff_reports._TTD_FOOTER.findall(text)] == ["IV.A"]


def test_truckee_packet_must_open_with_the_captured_agenda():
    data = load("truckee")
    url = next(iter(data["downloads"]))
    data["downloads"][url]["pages"][0]["text"] = "Planning Commission Meeting Agenda\nOctober 20, 2026"
    with pytest.raises(staff_reports.Uncertain):
        run_rule(data)


def test_truckee_item_interrupted_by_another_item_is_uncertain():
    data = load("truckee")
    pages = next(iter(data["downloads"].values()))["pages"]
    page31 = next(p for p in pages if p["page_number"] == 31)
    page31["text"] = page31["text"].replace("Item 7.1", "Item 7.2", 1)
    found, _, _ = run_rule(data)
    assert found["7.1"] is None and found["7.2"] is None


def test_lahontan_report_for_another_meeting_is_not_stored():
    data = load("lahontan-board")
    url = next(u for u in data["downloads"] if u.endswith("item6.pdf"))
    first = data["downloads"][url]["pages"][0]
    first["text"] = first["text"].replace("MEETING OF OCTOBER 1, 2026", "MEETING OF AUGUST 6, 2026")
    found, _, _ = run_rule(data)
    assert found["6"] is None and found["7"] is not None


def test_procedural_titles():
    for title in ("Roll Call", "Approval of Minutes of September 16, 2026", "For Possible Action: Approval of Agenda for October 7, 2026",
                  "City Council Meeting Minutes", "Closed Session - Property Negotiations:", "Reports by Water Board Chair and Water Board Members",
                  "ADJOURN AS TTD AND CONVENE AS TTC", "PUBLIC INTEREST COMMENTS"):
        assert staff_reports.procedural(Item("x", "1", title, None, "", [])), title
    for title in ("Public Safety Dispatch Services Agreement.", "Fiscal Year 2026-2027 Appropriation Limit",
                  "Executive Officer’s Report", "Fire Prevention Week Proclamation"):
        assert not staff_reports.procedural(Item("x", "1", title, None, "", [])), title


def test_clean_text_repairs_misread_quotes():
    assert staff_reports.clean_text("Cityâ€™s â€œsecond economyâ€​ sectors") == "City’s “second economy” sectors"


def test_pdf_links_reads_pdftohtml_xml():
    xml = ('<page number="3" position="absolute"><text top="812" left="118" width="226" height="18" font="8">'
           '<a href="https://x.example/Item_5-_Executive_Summary.pdf">Item 5- Executive Summary.pdf</a></text>'
           '<text top="64" left="81" width="13" height="18" font="4"><b>5.</b></text></page>'
           '<page number="4"><text top="9" left="118" width="9" height="9" font="8"><a href="https://x.example/a&amp;b.pdf">A &amp; B</a></text></page>')
    assert staff_reports.pdf_links(xml) == [
        {"page": 3, "top": 812, "left": 118, "url": "https://x.example/Item_5-_Executive_Summary.pdf", "text": "Item 5- Executive Summary.pdf"},
        {"page": 4, "top": 9, "left": 118, "url": "https://x.example/a&b.pdf", "text": "A & B"}]
    assert staff_reports.pdf_boxes(xml)[0] == {"page": 3, "top": 64, "left": 81, "url": None, "text": "5."}


# --- Summary validation -------------------------------------------------------------

PAGE = """Requested Action / Suggested Motions: Pass a Motion authorizing and directing the Mayor to execute
a Professional Services Agreement with Zenith Economics for the City’s Economic Development Market
Analysis and Business Attraction Strategy in an amount not to exceed $80,100.00.

The proposed agreement amount is within the approved project budget of $100,000.00. The City issued
a Request for Proposals and received 23 proposals. The project will not require additional staff."""

ACTION = ("Pass a Motion authorizing and directing the Mayor to execute a Professional Services Agreement with Zenith Economics "
          "for the City’s Economic Development Market Analysis and Business Attraction Strategy in an amount not to exceed $80,100.00.")


def sentence(text, source, page=1):
    return {"page": page, "source": source, "sentence": text}


def test_verbatim_and_trimmed_sentences_publish_with_page_citations():
    text, citations = item_summaries.validate([
        sentence("Pass a Motion authorizing the Mayor to execute a Professional Services Agreement with Zenith Economics "
                 "in an amount not to exceed $80,100.00.", ACTION),
        sentence("The proposed agreement amount is within the approved project budget of $100,000.00",
                 "The proposed agreement amount is within the approved project budget of $100,000.00."),
        sentence("the City received 23 proposals.", "The City issued a Request for Proposals and received 23 proposals."),
    ], {1: PAGE}, "slt:1:9:staff-report")
    assert text.startswith("Pass a Motion authorizing the Mayor") and text.endswith("The City received 23 proposals.")
    assert [c["page"] for c in citations] == [1, 1, 1]
    # Each quote is the page's own text (line breaks included), which the export check finds on the page.
    for citation in citations:
        assert " ".join(citation["quote"].split()) in " ".join(PAGE.split())
        assert citation["documentId"] == "slt:1:9:staff-report"


@pytest.mark.parametrize("bad,reason", [
    # Paraphrase: same meaning, different words.
    ("The council will consider hiring Zenith Economics to study the local economy for about $80,000.", "adds words"),
    # Characterization the report does not make.
    ("Pass a Motion authorizing the Mayor to execute a costly Professional Services Agreement with Zenith Economics.", "adds words"),
    # A changed number.
    ("Pass a Motion authorizing the Mayor to execute a Professional Services Agreement in an amount not to exceed $90,100.00.", "adds words"),
    # A dropped negation reverses the cap.
    ("Pass a Motion authorizing the Mayor to execute a Professional Services Agreement with Zenith Economics in an amount to exceed $80,100.00.", "negation"),
    # Cutting the middle joins words into a different claim.
    ("Pass a Motion authorizing and directing the Mayor in an amount not to exceed $80,100.00.", "middle"),
])
def test_unsupported_sentences_fail(bad, reason):
    with pytest.raises(ValueError, match=reason):
        item_summaries.validate([sentence(bad, ACTION)], {1: PAGE}, "doc")


def test_sentence_must_come_from_the_cited_page():
    with pytest.raises(ValueError, match="not on page 2"):
        item_summaries.validate([sentence("The City received 23 proposals.", "The City issued a Request for Proposals and received 23 proposals.", page=2)],
                                {1: PAGE, 2: "Fiscal Impact: None."}, "doc")
    with pytest.raises(ValueError, match="not provided"):
        item_summaries.validate([sentence("The City received 23 proposals.", "received 23 proposals", page=7)], {1: PAGE}, "doc")


def test_dropping_a_trailing_negation_fails():
    with pytest.raises(ValueError, match="negation"):
        item_summaries.validate([sentence("The project will require additional staff.", "The project will not require additional staff.")],
                                {1: PAGE}, "doc")


def test_summary_shape_limits():
    one = sentence("The City received 23 proposals.", "The City issued a Request for Proposals and received 23 proposals.")
    with pytest.raises(ValueError, match="sentences"):
        item_summaries.validate([one] * 4, {1: PAGE}, "doc")
    with pytest.raises(ValueError, match="repeats"):
        item_summaries.validate([one, one], {1: PAGE}, "doc")
    with pytest.raises(ValueError, match="words"):
        item_summaries.validate([sentence("Pass a Motion.", ACTION)], {1: PAGE}, "doc")


def test_misread_quotes_on_the_page_still_validate():
    page = "It will identify and prioritize viable “second economyâ€​ sectors that can complement the Cityâ€™s visitor-serving economy."
    text, citations = item_summaries.validate([sentence(
        "It will identify and prioritize viable “second economy” sectors that can complement the City’s visitor-serving economy.",
        "It will identify and prioritize viable “second economy” sectors that can complement the City’s visitor-serving economy.")],
        {1: page}, "doc")
    assert citations[0]["quote"].endswith("visitor-serving economy")


def test_a_slightly_miscopied_source_still_finds_the_page_passage():
    # The model dropped "Analysis and" when copying the source; the passage checked is the page's own.
    source = ACTION.replace("Analysis and ", "")
    _, citations = item_summaries.validate([sentence(
        "Pass a Motion authorizing the Mayor to execute a Professional Services Agreement with Zenith Economics.", source)], {1: PAGE}, "doc")
    assert "Analysis and Business" in citations[0]["quote"]


# --- Attachment links (SLT) ---------------------------------------------------------

def test_slt_item_7_gets_exactly_its_four_attachments_in_agenda_order():
    data = load("slt")
    _, ctx, _ = run_rule(data)
    files = {a.item_id: a.files for a in ctx.attachments}
    assert [f["name"] for f in files["slt:2339:7"]] == [
        "Item 7- Executive Summary.pdf", "01-SR_Paradise_Ave_Abandonment.docx",
        "02-Resolution-Paradise_Ave_Abandonment.docx", "03 - Attachment 1 - Abandonment Map.pdf"]
    assert [f["label"] for f in files["slt:2339:7"]] == [
        "Executive Summary", "Staff report: Paradise Ave Abandonment", "Resolution: Paradise Ave Abandonment", "Attachment 1: Abandonment Map"]
    seven = {f["url"] for f in files["slt:2339:7"]}
    for neighbour in ("slt:2339:6", "slt:2339:8"):
        assert files[neighbour] and not seven & {f["url"] for f in files[neighbour]}
        assert files[neighbour][0]["name"] == f"Item {neighbour[-1]}- Executive Summary.pdf"


def test_slt_attachments_are_uncertain_when_a_printed_name_or_summary_number_disagrees():
    data = load("slt")
    links = data["downloads"][data["documents"][0]["url"]]["links"]
    # The agenda text no longer prints the resolution under item 7.
    version = data["documents"][0]["version_id"]
    data["stored"][version] = {n: t.replace("02-Resolution-Paradise_Ave_Abandonment.docx", "") for n, t in data["stored"][version].items()}
    # Item 8's executive summary link now claims to be item 9's.
    for link in links:
        if link["text"] == "Item 8- Executive Summary.pdf":
            link["text"] = "Item 9- Executive Summary.pdf"
    _, ctx, _ = run_rule(data)
    found = {a.item_id for a in ctx.attachments}
    assert "slt:2339:7" not in found and "slt:2339:8" not in found and "slt:2339:6" in found
    assert ctx.skipped["slt:2339:7"].startswith("attachments:")


@pytest.mark.parametrize("name,label", [
    ("Item 12- Executive Summary.pdf", "Executive Summary"),
    ("01-Staff Report Financial Reporting Services.docx", "Staff report: Financial Reporting Services"),
    ("01-Staff_Report_Appropriations_Limit_2026-2027.pdf", "Staff report: Appropriations Limit 2026-2027"),
    ("02-Resolution - 2026-2027 Appropriations Limit.docx", "Resolution: 2026-2027 Appropriations Limit"),
    ("Att3_TahoeWorkforceHousingNeedsAssessment2025.pdf", "Attachment 3: TahoeWorkforceHousingNeedsAssessment2025"),
    ("03 - RFP EDC AQMD.pdf", "RFP EDC AQMD"),
    ("03 - Professional Services Agreement-Zenith", "Professional Services Agreement-Zenith"),
])
def test_attachment_labels_read_the_printed_file_name(name, label):
    assert staff_reports.attachment_label(name) == label


# --- Requested actions printed in the agenda ---------------------------------------

def test_requested_action_belongs_to_its_own_item_only():
    from tahoe_ingest.requested_actions import for_meeting
    data = load("slt")
    pages = {(version, int(n)): text for version, stored in data["stored"].items() for n, text in stored.items()}
    actions = for_meeting(data["items"], pages)
    assert actions["slt:2339:7"]["text"] == ("Pass a Resolution of Intent to Abandon the Paradise Avenue Right-of-Way and set "
                                             "public hearing date for November 3, 2026.")
    assert actions["slt:2339:7"]["citation"]["page"] == 4
    assert actions["slt:2339:7"]["citation"]["quote"] in pages[(data["documents"][0]["version_id"], 4)]
    assert actions["slt:2339:6"]["text"].startswith("Pass a Resolution authorizing the a City Manager")
    assert actions["slt:2339:8"]["text"].startswith("Pass a Resolution authorizing the City to submit a grant application")
    assert len({a["text"] for a in actions.values()}) == len(actions)


def test_incomplete_or_title_only_actions_are_not_published():
    from tahoe_ingest.requested_actions import for_meeting
    page = ("5.1    Navigation Center Funding.\n       Recommendation: That Truckee Town Council and Tahoe Forest Hospital District Board:\n\n"
            "       A. Accept the recommendation.\n\n"
            "6.     For Possible Action: Approve an increase to the Executive Director’s signing authority from $75,000 to $100,000\n\n"
            "7.1   Language Access Plan.\n      Recommended Action: That Council adopt the Language Access Plan (LAP).\n\n8.   Adjournment\n")
    items = [{"id": "a", "citations": [{"documentId": "v", "page": 1, "quote": "Navigation Center Funding."}]},
             {"id": "b", "citations": [{"documentId": "v", "page": 1, "quote": "For Possible Action: Approve an increase"}]},
             {"id": "c", "citations": [{"documentId": "v", "page": 1, "quote": "Language Access Plan."}]},
             {"id": "d", "citations": [{"documentId": "v", "page": 1, "quote": "Adjournment"}]}]
    actions = for_meeting(items, {("v", 1): page})
    assert set(actions) == {"c"} and actions["c"]["text"] == "That Council adopt the Language Access Plan (LAP)."


def test_slt_reads_the_agenda_even_when_a_packet_is_also_stored():
    data = load("slt")
    data["documents"].append({**data["documents"][0], "version_id": "slt:2339:slt-agenda-packet:x", "source_document_id": "slt-agenda-packet",
                              "title": "agenda packet", "url": "https://example.gov/packet.pdf", "sha256": "1" * 64})
    found, _, _ = run_rule(data)
    assert found["7"] is not None


def test_sentences_start_where_the_reports_sentences_start():
    page = ("Staff recommends approval. The resolution would abandon\napproximately 1,438 square feet of the easterly portion.\n\n"
            "If these plans are selected, the program is expected to cost approximately $393,513 annually.")
    # A fragment lifted from the middle of a sentence fails, even though every word is the report's.
    with pytest.raises(ValueError, match="middle of a sentence"):
        item_summaries.validate([sentence("Approximately 1,438 square feet of the easterly portion.",
                                          "approximately 1,438 square feet of the easterly portion.")], {1: page}, "doc")
    with pytest.raises(ValueError, match="middle of the passage"):
        item_summaries.validate([sentence("Approximately 1,438 square feet of the easterly portion.",
                                          "The resolution would abandon approximately 1,438 square feet of the easterly portion.")], {1: page}, "doc")
    # Dropping an opening clause up to its comma, or the end of a sentence, is fine.
    text, _ = item_summaries.validate([
        sentence("The program is expected to cost approximately $393,513 annually.",
                 "If these plans are selected, the program is expected to cost approximately $393,513 annually."),
        sentence("The resolution would abandon approximately 1,438 square feet.",
                 "The resolution would abandon approximately 1,438 square feet of the easterly portion.")], {1: page}, "doc")
    assert text.startswith("The program is expected")


def test_a_list_entry_may_be_taken_on_its_own():
    page = "Recommended Action: That Council: (1) authorize staff to use the budget; and (2) approve the rebate program guidelines."
    text, _ = item_summaries.validate([sentence("Approve the rebate program guidelines.", page)], {1: page}, "doc")
    assert text == "Approve the rebate program guidelines."
