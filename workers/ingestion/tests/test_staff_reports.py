"""Staff report mapping (IDEA-013) and verbatim summary validation (IDEA-016); no network or model calls."""
import base64
import copy
import re
import json
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import item_summaries, staff_report_rules, staff_reports
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.staff_report_rules import slt, ttd
from tahoe_ingest.staff_reports import Context, Document, Item, Meeting, RULES

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/staff-reports"


def load(agency):
    return json.loads((FIXTURES / f"{agency}.json").read_text())


def run_rule(data):
    """Run the fixture's agency rule offline from its maps of URL to content: ``downloads`` (PDF reads), ``html``
    (markup), ``json`` (API responses), and ``word`` (base64 file bytes). Returns the mappings by item number, the
    context, and every URL fetched, in order. A URL missing from its map fails as a download."""
    m = data["meeting"]
    meeting = Meeting(m["id"], m["agency_id"], m["title"], date.fromisoformat(m["local_date"]),
                      [Item(i["id"], i["number"], i["title"], i["section"], i["fingerprint"], i["citations"]) for i in data["items"]],
                      [Document(**d) for d in data["documents"]], m.get("official_url"))
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

    def served(key, decode=copy.deepcopy):
        def fetch(url, **_):
            read.append(url)
            if url not in data.get(key, {}):
                raise staff_reports.FetchFailed(f"{url}: not in fixture")
            return decode(data[key][url])
        return fetch

    ctx = Context(stored, meeting, reader, fetch_html=served("html"), fetch_json=served("json"),
                  fetch_bytes=served("word", base64.b64decode))
    mappings = {m.item_id: m for m in RULES[m["agency_id"]](ctx)}
    return {i["number"]: mappings.get(i["id"]) for i in data["items"]}, ctx, read


@pytest.mark.parametrize("agency", sorted(p.stem for p in FIXTURES.glob("*.json")))
def test_each_item_cites_pages_of_its_own_staff_report(agency):
    data = load(agency)
    assert data["meeting"]["agency_id"] in RULES
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
        # The kept pages are the report's own leading pages, and the first one names this item
        # (as the fixture's "heading" prints it, or one of the common forms).
        numbers = [n for n, _ in mapping.pages]
        assert numbers and numbers[0] == mapping.page_start and all(mapping.page_start <= n <= mapping.page_end for n in numbers)
        first = mapping.pages[0][1]
        label = number.rstrip(".")
        headings = [expected["heading"]] if "heading" in expected else [
            f"#:{label}", f"AGENDA ITEM: {label}.", f"AGENDA ITEM {label}.", f"Item {label}", f"ITEM {label}"]
        assert any(h in first for h in headings), number
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


def staff_report_config(agency_id):
    return next(a for a in agency_catalog() if a["id"] == agency_id)["staffReports"]


def test_ttd_committee_footer_is_not_the_board_item():
    text = "JS/lr       AGENDA ITEM: IV.A.\n            FPC AGENDA ITEM: III.B.\nTahoe Transportation District Board ~ Page 8 ~"
    assert "footer" not in staff_report_config("ttd") and RULES["ttd"].keywords == {}  # the rule's own default
    assert ttd.footer_pattern("AGENDA ITEM").findall(text) == ["IV.A"]


def test_ttd_rtcpc_footer_reads_with_or_without_the_committee_prefix():
    text = "TS/lr          RTCPC AGENDA ITEM: III.A.\nCJ/lr          AGENDA ITEM: III.B.\nx          RTCPC AGENDA ITEM: IV"
    assert ttd.footer_pattern(staff_report_config("ttd-rtcpc")["footer"]).findall(text) == ["III.A", "III.B", "IV"]


def test_ttd_rtcpc_maps_memos_by_its_configured_footer():
    data = load("ttd")
    for pages in data["stored"].values():
        for number, text in pages.items():
            pages[number] = re.sub(r"(^|\s{2,})AGENDA ITEM\b", r"\1RTCPC AGENDA ITEM", text, flags=re.M)
    board, ctx, _ = run_rule(data)
    assert not any(board.values()) and "not marked" in ctx.skipped["ttd:7110:IV.B"]  # the board's footer reads no committee page
    data["meeting"]["agency_id"] = "ttd-rtcpc"
    found, _, _ = run_rule(data)
    expected = {number: (e["page_start"], e.get("page_end")) for number, e in data["expected"].items() if e}
    assert expected and {n: (m.page_start, expected[n][1] and m.page_end) for n, m in found.items() if m} == expected
    assert RULES["ttd-rtcpc"].func is ttd.map_ttd
    assert RULES["ttd-rtcpc"].keywords == {"footer": staff_report_config("ttd-rtcpc")["footer"]}


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
    assert slt.attachment_label(name) == label


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


# --- Rule registry and generic helpers ----------------------------------------------

def test_each_configured_agency_gets_its_rule_with_its_parameters():
    from tahoe_ingest import cli
    configured = {a["id"]: a["staffReports"] for a in agency_catalog() if "rule" in a.get("staffReports", {})}
    assert staff_reports.SUPPORTED == tuple(sorted(RULES)) == tuple(sorted(configured))
    assert {"slt", "ttd", "truckee", "lahontan-board"} <= set(RULES) and RULES is staff_report_rules.agency_rules()
    for agency, config in configured.items():
        assert RULES[agency].func is staff_report_rules.REGISTRY[config["rule"]]
    assert RULES["washoe-planning"].keywords == {"client": "washoe-nv", "body": "Washoe County Planning Commission",
                                                 "report": "agenda-item"}
    assert RULES["trpa-advisory"].keywords == {"packet_name": r"APC(?:\s+Meeting)?"}
    parser = cli._parser()
    assert parser.parse_args(["map-staff-reports", "--agency", "lahontan-board"]).agency == "lahontan-board"
    with pytest.raises(SystemExit):
        parser.parse_args(["map-staff-reports", "--agency", "no-such-agency"])


def test_the_registry_holds_rules_by_format_name():
    assert "legistar" in staff_report_rules.REGISTRY and "ttd-packet" in staff_report_rules.REGISTRY
    assert not set(staff_report_rules.REGISTRY) & {a["id"] for a in agency_catalog()}  # names describe formats, not agencies


@pytest.mark.parametrize("config,problem", [
    ({"rule": "no-such-rule"}, "unknown staff report rule"),
    ({"rule": "ttd-packet", "footr": "AGENDA ITEM"}, "takes no footr"),
    ({"rule": "ttd-packet", "published": True}, "takes no published"),
    ({"rule": "legistar", "client": "eldorado", "body": "Board of Supervisors"}, "needs report"),
    ({"rule": "legistar", "client": "eldorado", "body": "Board of Supervisors", "report": "minutes"}, "must be one of"),
    ({"rule": "ttd-packet", "footer": "(?:RTCPC AGENDA ITEM"}, "not a regular expression"),
    ({"rule": "slt-commission-report", "body": " "}, "non-empty text"),
    ({"rule": "trpa-meeting-materials", "packetName": ["Governing Board"]}, "non-empty text"),
    ({"rule": "douglas-packet", "note": ""}, "invalid"),
    ({"rule": "douglas-packet", "maxMegabytes": "300"}, "positive whole number"),
    ({"rule": "douglas-packet", "maxPages": 0}, "positive whole number"),
    ({"rule": "douglas-packet", "maxPages": 2.5}, "positive whole number"),
    ({"rule": "douglas-packet", "maxPages": True}, "positive whole number"),
    ({"published": False}, "invalid"),
    ({"published": "no", "note": "A notice only."}, "invalid"),
    ([], "invalid"),
])
def test_staff_report_entries_are_checked_when_the_registry_loads(config, problem):
    with pytest.raises(ValueError, match=problem):
        staff_report_rules.check_config({"id": "x", "staffReports": config})


def test_valid_staff_report_entries_load():
    for config in ({"rule": "douglas-packet"}, {"rule": "douglas-packet", "maxMegabytes": 400, "maxPages": 2500},
                   {"rule": "ttd-packet", "footer": "RTCPC AGENDA ITEM", "note": "Committee."},
                   {"published": False, "note": "One-page agenda."}, {"note": "Blocked."}):
        staff_report_rules.check_config({"id": "x", "staffReports": config})
    staff_report_rules.check_config({"id": "x"})


def test_a_bad_staff_report_entry_stops_the_registry_from_loading(tmp_path, monkeypatch):
    from tahoe_ingest import catalog
    agencies = json.loads(catalog.agency_catalog_path().read_text())
    next(a for a in agencies if a["id"] == "ttd-rtcpc")["staffReports"]["footer"] = "(RTCPC"
    path = tmp_path / "agencies.json"
    path.write_text(json.dumps(agencies))
    monkeypatch.setattr(catalog, "agency_catalog_path", lambda: path)
    catalog.agency_catalog.cache_clear()
    try:
        with pytest.raises(ValueError, match="footer is not a regular expression"):
            catalog.agency_catalog()
    finally:
        monkeypatch.undo()
        catalog.agency_catalog.cache_clear()


def test_rule_parameters_are_keyword_only_and_typed():
    def untyped(ctx, *, footer):
        return []

    def positional(ctx, footer: str):
        return []
    for rule in (untyped, positional):
        with pytest.raises(TypeError):
            staff_report_rules._params("x", rule)


def meeting(items=(), documents=(), *, official_url=None, day=date(2026, 10, 28)):
    return Meeting("x:1", "x", "Board", day, list(items), list(documents), official_url)


def item(number, title, citations=()):
    return Item(f"x:1:{number or title}", number, title, None, "fp", list(citations))


def pdf(pages, sha="a" * 64):
    return {"sha256": sha, "page_count": len(pages), "pages": [{"page_number": n, "text": t} for n, t in enumerate(pages, 1)]}


HTML_PAGE = """<html><body><a href="/docs/Agenda-Item-No-7-B-Aquatic-Invasive-Species.pdf">Agenda Item No. 7.B</a>
<p><a href="mailto:clerk@example.gov">Clerk</a> <a href="https://other.example/Item-5.pdf">Item 5 &amp; 6</a></p></body></html>"""


def test_html_links_are_absolute_in_order_cached_and_counted(monkeypatch):
    pauses, fetched = [], []
    monkeypatch.setattr(staff_reports.time, "sleep", pauses.append)

    def fetch(url):
        fetched.append(url)
        return HTML_PAGE
    ctx = Context(lambda v: {}, meeting(), lambda url, **_: pdf(["x"]), delay=1.5, fetch_html=fetch)
    ctx.read("https://example.gov/agenda.pdf")
    links = ctx.html_links("https://example.gov/meetings/oct")
    assert links == [{"url": "https://example.gov/docs/Agenda-Item-No-7-B-Aquatic-Invasive-Species.pdf", "text": "Agenda Item No. 7.B"},
                     {"url": "https://other.example/Item-5.pdf", "text": "Item 5 & 6"}]
    assert ctx.html_links("https://example.gov/meetings/oct") == links and fetched == ["https://example.gov/meetings/oct"]
    assert ctx.downloads == 2 and pauses == [1.5]


def test_html_links_failures_retry_later_or_are_uncertain():
    def broken(url):
        raise OSError("connection reset")
    with pytest.raises(staff_reports.FetchFailed, match="connection reset"):
        Context(lambda v: {}, meeting(), None, fetch_html=broken).html_links("https://example.gov/m")
    with pytest.raises(staff_reports.Uncertain, match="no links"):
        Context(lambda v: {}, meeting(), None, fetch_html=lambda url: "<p>Agenda coming soon</p>").html_links("https://example.gov/m")


def test_html_links_default_fetch_stays_offline_in_tests():
    # conftest replaces discovery.fetch_text; the default reader looks it up on every call.
    with pytest.raises(staff_reports.FetchFailed, match="offline test"):
        Context(lambda v: {}, meeting(), None).html_links("https://example.gov/m")


def test_json_bytes_and_markup_fetches_are_cached_counted_and_delayed(monkeypatch):
    pauses, fetched = [], []
    monkeypatch.setattr(staff_reports.time, "sleep", pauses.append)

    def fetcher(value):
        def fetch(url, **kwargs):
            fetched.append((url, kwargs))
            return value
        return fetch
    ctx = Context(lambda v: {}, meeting(), None, delay=2, fetch_html=fetcher("<p>x</p>"), fetch_json=fetcher({"a": 1}),
                  fetch_bytes=fetcher(b"PK"))
    for _ in range(2):
        assert ctx.fetch_json("https://api.example/v1/x") == {"a": 1}
        assert ctx.fetch_bytes("https://example.gov/r.docx", accept="*/*", max_bytes=10) == b"PK"
        assert ctx.html("https://example.gov/m") == "<p>x</p>"
    assert fetched == [("https://api.example/v1/x", {}), ("https://example.gov/r.docx", {"accept": "*/*", "max_bytes": 10}),
                       ("https://example.gov/m", {})]
    assert ctx.downloads == 3 and pauses == [2, 2]


def test_fetch_failures_retry_later_except_oversized_files():
    def broken(url, **_):
        raise OSError("connection refused")

    def too_big(url, **_):
        raise staff_reports.http.SourceTooLarge("source exceeds 10 byte limit")
    with pytest.raises(staff_reports.FetchFailed, match="connection refused"):
        Context(lambda v: {}, meeting(), None, fetch_json=broken).fetch_json("https://api.example/v1/x")
    with pytest.raises(staff_reports.TooLarge, match="byte limit"):
        Context(lambda v: {}, meeting(), None, fetch_bytes=too_big).fetch_bytes("https://example.gov/r.docx", accept="*/*", max_bytes=10)
    # Defaults are looked up per call, so conftest keeps them offline.
    with pytest.raises(staff_reports.FetchFailed, match="offline test"):
        Context(lambda v: {}, meeting(), None).fetch_json("https://api.example/v1/x")
    with pytest.raises(staff_reports.FetchFailed, match="offline test"):
        Context(lambda v: {}, meeting(), None).fetch_bytes("https://example.gov/r.docx", accept="*/*", max_bytes=10)


def test_read_report_skips_an_oversized_report_for_good():
    def reader(url, **_):
        if "big" in url:
            raise staff_reports.TooLarge(f"{url}: source exceeds 83886080 byte limit")
        raise staff_reports.FetchFailed(f"{url}: HTTP 503")
    ctx = Context(lambda v: {}, meeting(), reader)
    big, flaky = item("1", "Big report"), item("2", "Flaky report")
    assert ctx.read_report(big, "https://example.gov/big.pdf") is None and ctx.fetch_failures == []
    assert ctx.skipped[big.id] == "report is larger than the download limit"
    assert ctx.read_report(flaky, "https://example.gov/flaky.pdf") is None
    assert ctx.skipped[flaky.id] == "download failed" and ctx.fetch_failures == ["https://example.gov/flaky.pdf: HTTP 503"]


def test_read_pdf_raises_too_large_for_an_oversized_source(monkeypatch):
    def fetch(url, **kwargs):
        raise staff_reports.http.SourceTooLarge(f"source exceeds {kwargs['max_bytes']} byte limit")
    monkeypatch.setattr(staff_reports.http, "download", fetch)
    with pytest.raises(staff_reports.TooLarge):
        staff_reports.read_pdf("https://example.gov/big.pdf", max_bytes=5)


def test_report_names_item_needs_number_title_and_date():
    page = ("TAHOE REGIONAL PLANNING AGENCY\nGoverning Board Meeting: October 28, 2026\n\n"
            "Agenda Item No. 7.B\nAquatic Invasive Species Program Update\n\nSummary and Staff Recommendation: ...")
    seven_b = item("7.B", "Aquatic Invasive Species Program Update")
    patterns = [r"(?i)Agenda Item No\.?\s*{n}\b"]
    assert staff_reports.report_names_item(page, seven_b, number_patterns=patterns, meeting_date=date(2026, 10, 28)) == {
        "reportHeading": "Agenda Item No. 7.B", "titleOverlap": 1.0, "meetingDate": "2026-10-28"}
    # Undated reports pass without date evidence; no date given, none checked.
    undated = page.replace("Governing Board Meeting: October 28, 2026\n", "")
    assert "meetingDate" not in staff_reports.report_names_item(undated, seven_b, number_patterns=patterns, meeting_date=date(2026, 10, 28))
    assert "meetingDate" not in staff_reports.report_names_item(page, seven_b, number_patterns=patterns)
    assert staff_reports.report_names_item(page, seven_b, number_patterns=patterns, meeting_date=date(2026, 9, 23)) == (
        "report is for another meeting date")
    assert staff_reports.report_names_item(page, item("7.C", "Aquatic Invasive Species Program Update"),
                                           number_patterns=patterns).startswith("report does not name item 7.C")
    assert staff_reports.report_names_item(page, item("7.B", "Shoreline Plan Amendments for Marinas"),
                                           number_patterns=patterns) == "report title differs from the agenda item"
    assert staff_reports.report_names_item(page, item("", "Aquatic Invasive Species Program Update"),
                                           number_patterns=patterns) == "item has no number"


@pytest.mark.parametrize("text,dates", [
    ("MEETING OF OCTOBER 1-2, 2026", [(date(2026, 10, 1), date(2026, 10, 2))]),
    ("Meeting Date: Wednesday, October 28, 2026", [(date(2026, 10, 28), date(2026, 10, 28))]),
    ("Board Meeting 10/28/26", [(date(2026, 10, 28), date(2026, 10, 28))]),
    ("Date: October 21, 2026", []),  # a memo's own date is not the meeting's
])
def test_printed_meeting_dates(text, dates):
    assert staff_reports.printed_meeting_dates(text) == dates


@pytest.mark.parametrize("text,day", [
    ("October 21, 2026", date(2026, 10, 21)), ("  Oct. 21, 2026", date(2026, 10, 21)), ("JUNE 24, 2026", date(2026, 6, 24)),
    ("September 03, 2026", date(2026, 9, 3)), ("Sept 3 2026", date(2026, 9, 3)), ("Smarch 3, 2026", None),
    ("February 30, 2026", None), ("Meeting of October 21, 2026", None)])
def test_parse_printed_date(text, day):
    assert staff_reports.parse_printed_date(text) == day


def test_stored_pdf_packets_keep_one_copy_per_file():
    docs = [Document("v2", "b", "Packet", "https://example.gov/A%2BB.pdf", "s1", 40, "application/pdf"),
            Document("v1", "a", "Packet", "https://example.gov/A+B.pdf", "s1", 40, "application/pdf"),
            Document("v3", "c", "Agenda", "https://example.gov/agenda.pdf", "s2", 2, "application/pdf"),
            Document("v4", "d", "Agenda", "https://example.gov/agenda", "s3", 1, "text/html")]
    pages = {"v1": {1: "x", 30: "BOARD AGENDA ITEM 6a"}, "v2": {30: "BOARD AGENDA ITEM 6a"}, "v3": {1: "AGENDA"}, "v4": {1: "x"}}
    ctx = Context(lambda v: pages[v], meeting(documents=docs), None)
    assert [d.version_id for d in staff_reports.stored_pdf_packets(ctx)] == ["v1", "v3"]
    assert [d.version_id for d in staff_reports.stored_pdf_packets(ctx, r"BOARD AGENDA ITEM")] == ["v1"]
    assert [d.version_id for d in staff_reports.stored_pdf_packets(ctx, documents=docs[:1])] == ["v2"]


def test_linked_reports_reads_a_shared_report_once_and_maps_every_item_it_covers():
    shared, own = "https://example.gov/Items-5-B-and-5-C-Filing-Fees.pdf", "https://example.gov/Item-6.pdf"
    items = [item("5.B", "Filing Fees for Shoreline Permits"), item("5.C", "Filing Fees for Grading Permits"),
             item("6", "Annual Audit Report"), item("8", "Transportation Plan")]
    reports = {shared: pdf(["Items 5.B and 5.C\nFiling Fees for Shoreline and Grading Permits"] * 3 + ["Exhibit"]),
               own: pdf(["Item 7\nAnnual Audit Report"])}
    reads = []

    def reader(url, *, last_page=None, links=False):
        reads.append((url, last_page))
        return reports[url]
    ctx = Context(lambda v: {}, meeting(items), reader)

    def verify(it, report):
        found = staff_reports.report_names_item(report["pages"][0]["text"], it, number_patterns=[r"^Items? .*\b{n}\b"])
        return {**found, "agendaLink": report["url"].rsplit("/", 1)[1]} if isinstance(found, dict) else found
    mappings = staff_reports.linked_reports(ctx, items, {shared: {"5.b", "5.C."}, own: {"6"}}, verify=verify, kind="staff-report",
                                            title="Item {number} staff report")
    assert [(m.item_id, m.url, m.page_start, m.page_end, m.title) for m in mappings] == [
        ("x:1:5.B", shared, 1, 4, "Item 5.B staff report"), ("x:1:5.C", shared, 1, 4, "Item 5.C staff report")]
    assert reads == [(shared, staff_reports.LEADING_PAGES), (own, staff_reports.LEADING_PAGES)]
    assert [n for n, _ in mappings[0].pages] == [1, 2, 3] and mappings[0].evidence["agendaLink"] == "Items-5-B-and-5-C-Filing-Fees.pdf"
    assert ctx.skipped == {"x:1:6": "report does not name item 6", "x:1:8": "0 report links for item 8"}


def test_linked_reports_prechecks_before_downloading_and_reuses_a_failed_read():
    url = "https://example.gov/Item-1.pdf"
    items = [item("1", "Budget Workshop Report"), item("1A", "Budget Workshop Followup"), item("2", "Parking Rates")]
    calls = []

    def reader(u, **_):
        calls.append(u)
        raise staff_reports.FetchFailed(f"{u}: HTTP 500")
    ctx = Context(lambda v: {}, meeting(items), reader)
    found = staff_reports.linked_reports(ctx, items, {"1": {url}, "1A": {url}, "2": {"https://example.gov/x.pdf", "https://example.gov/y.pdf"}},
                                         verify=lambda it, r: {}, kind="staff-report", title=lambda it: it.title)
    assert found == [] and calls == [url] and ctx.fetch_failures == [f"{url}: HTTP 500"]
    assert ctx.skipped == {"x:1:1": "download failed", "x:1:1A": "download failed", "x:1:2": "2 report links for item 2"}
    ctx = Context(lambda v: {}, meeting(items), reader)
    staff_reports.linked_reports(ctx, items[:1], {"1": {url}}, verify=lambda it, r: {}, kind="staff-report", title="t",
                                 precheck=lambda it, u: "link is not next to the item")
    assert ctx.downloads == 0 and ctx.skipped == {"x:1:1": "link is not next to the item"}


def test_packet_reports_take_uninterrupted_runs_of_labelled_pages():
    pages = {1: "AGENDA", 2: "Agenda Item 3\nWater Rate Study", 3: "chart", 4: "Agenda Item 3\nmore", 5: "Agenda Item 4\nSnow Removal Contract",
             6: "Agenda Item 5\nTree Removal Permits", 7: "Agenda Item 4\nappendix", 8: "Agenda Item 6\nParking Rates"}
    items = [item("3", "Water Rate Study"), item("4", "Snow Removal Contract"), item("6", "Fire Station Design"), item("9", "Audit")]
    packet = Document("v", "packet", "Agenda packet", "https://example.gov/packet.pdf", "b" * 64, 8, "application/pdf")
    ctx = Context(lambda v: {}, meeting(items, [packet]), None)

    def label(text):
        found = re.match(r"Agenda Item (\d+)", text)
        return staff_reports.norm_number(found.group(1)) if found else None
    mappings = staff_reports.packet_reports(ctx, packet, pages, items, page_label=label, label_name="Agenda Item {number}")
    assert [(m.item_id, m.page_start, m.page_end, m.evidence) for m in mappings] == [
        ("x:1:3", 2, 4, {"header": "Agenda Item 3", "pages": "2-4"})]
    assert ctx.skipped == {"x:1:4": "no single run of pages headed Agenda Item 4",  # item 5's page interrupts it
                           "x:1:6": "staff report title differs from the agenda item",
                           "x:1:9": "no single run of pages headed Agenda Item 9"}


def test_agenda_page_column_reads_the_number_at_the_end_of_the_items_line():
    agenda = "AGENDA                                                Page\n  A.  Aquatic Invasive Species Program Update      12\n  B. Filing fees\n"
    ctx = Context(lambda v: {1: agenda}, meeting(), None)
    assert staff_reports.agenda_page_column(item("A", "x", [{"documentId": "v", "page": 1, "quote": "Aquatic Invasive Species Program Update"}]), ctx) == 12
    assert staff_reports.agenda_page_column(item("B", "x", [{"documentId": "v", "page": 1, "quote": "Filing fees"}]), ctx) is None


def test_by_title_needs_a_unique_match_both_ways():
    items = [item("", "Aquatic Invasive Species Program Update"), item("", "Shoreline Plan Implementation Update"),
             item("", "Shoreline Plan Implementation Review"), item("", "Budget"), item("", "Approval of Minutes")]
    texts = {"https://example.gov/ais.pdf": "Agenda-Item-No-7-B-Aquatic-Invasive-Species-Program-Update.pdf",
             "https://example.gov/shoreline.pdf": "Shoreline Plan Implementation Update and Review",
             "https://example.gov/budget.pdf": "Budget"}
    ctx = Context(lambda v: {}, meeting(items), None)
    assert ctx.by_title(texts) == {items[0].id: "https://example.gov/ais.pdf"}
    assert ctx.skipped == {items[1].id: "the matching report title also matches another item",
                           items[2].id: "the matching report title also matches another item",
                           items[3].id: "no report title matches the item title"}  # one distinctive word is too few


# --- Requested action labels ---------------------------------------------------------

@pytest.mark.parametrize("label", [
    "Required Motions:", "Required Motion(s):", "Summary and Staff Recommendation:", "Requested Board Action:", "Recommended Board Action:",
    "Recommended Motion:", "Proposed Motion:", "Board Action Requested:", "Action Required:", "Recommended Actions:",
    "Recommended Action(s):", "Staff Recommends:", "Requested Action / Suggested Motions:", "Recommendation for Action:",
    "Recommendations for Action:", "Possible Motion:", "Possible Motion(s):", "Action Recommended:", "Actions Recommended:"])
def test_requested_action_labels_agencies_print(label):
    from tahoe_ingest.requested_actions import for_meeting
    page = f"4.   Wastewater Rate Study.\n     {label} Approve the 2026 wastewater rate study and set a public hearing.\n\n5.   Adjournment\n"
    items = [{"id": "a", "citations": [{"documentId": "v", "page": 1, "quote": "Wastewater Rate Study."}]},
             {"id": "b", "citations": [{"documentId": "v", "page": 1, "quote": "Adjournment"}]}]
    assert for_meeting(items, {("v", 1): page})["a"]["text"] == "Approve the 2026 wastewater rate study and set a public hearing."


def test_the_longest_printed_label_is_dropped_whole():
    from tahoe_ingest.requested_actions import ACTION_LABEL
    for label in ("Recommendation for Action:", "Actions Recommended:", "Possible Motions:"):
        assert ACTION_LABEL.match(f"  {label} Approve the plan.").group().strip() == label


def test_staff_recommends_sentence_is_kept_whole_and_alone():
    from tahoe_ingest.requested_actions import ACTION_LABELS, for_meeting
    assert any("Required Motion" in label for label in ACTION_LABELS)
    page = ("4.   Wastewater Rate Study.\n     Staff recommends that the Board approve the U.S. Forest Service agreement No. 5. The study\n"
            "     was funded in 2025.\n\n5.   Snow Removal.\n     The Board staff recommends nothing here; this line does not start with it.\n\n6. Adjournment\n")
    items = [{"id": i, "citations": [{"documentId": "v", "page": 1, "quote": q}]}
             for i, q in (("a", "Wastewater Rate Study."), ("b", "Snow Removal."), ("c", "Adjournment"))]
    actions = for_meeting(items, {("v", 1): page})
    assert set(actions) == {"a"}
    assert actions["a"]["text"] == "Staff recommends that the Board approve the U.S. Forest Service agreement No. 5."
    assert actions["a"]["citation"]["quote"] in page
