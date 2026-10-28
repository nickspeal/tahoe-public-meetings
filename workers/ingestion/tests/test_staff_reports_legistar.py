"""Legistar staff reports (staff_report_rules/legistar.py); fixture-driven, no network.

The fixtures live in tests/fixtures/staff-reports/legistar/ because they also need the
rule's API and Word fetch hooks (``json`` and ``word`` maps), which the shared test's
``run_rule`` does not provide yet.
"""
import base64
import copy
import io
import urllib.parse
import zipfile
from datetime import date

import pytest

from tahoe_ingest import staff_reports
from tahoe_ingest.staff_report_rules import legistar
from test_staff_reports import FIXTURES, run_rule

LEGISTAR = FIXTURES / "legistar"
AGENCIES = sorted(p.stem for p in LEGISTAR.glob("*.json"))


def load(agency):
    import json
    return json.loads((LEGISTAR / f"{agency}.json").read_text())


@pytest.fixture
def run(monkeypatch):
    """Run a Legistar fixture offline: ``json`` feeds the API hook and ``word`` (base64) the Word hook; antiword is absent."""
    def go(data):
        calls = []

        def fetch_json(url):
            calls.append(url)
            if url not in data["json"]:
                raise OSError(f"{url}: not in fixture")
            return copy.deepcopy(data["json"][url])

        def fetch_word(url):
            calls.append(url)
            if url not in data.get("word", {}):
                raise OSError(f"{url}: not in fixture")
            return base64.b64decode(data["word"][url])
        monkeypatch.setattr(legistar, "fetch_json", fetch_json)
        monkeypatch.setattr(legistar, "fetch_word", fetch_word)
        monkeypatch.setattr(legistar, "antiword_available", lambda: False)
        found, ctx, read = run_rule(data)
        return found, ctx, read + calls
    return go


def item_id(data, number):
    return next(i["id"] for i in data["items"] if i["number"] == number)


def rows(data):
    return next(v for k, v in data["json"].items() if "/EventItems?" in k)


def row(data, number):
    return next(r for r in rows(data) if (r["EventItemAgendaNumber"] or "").rstrip(".") == number)


@pytest.mark.parametrize("agency", AGENCIES)
def test_each_item_maps_to_its_own_report(agency, run):
    data = load(agency)
    found, ctx, _ = run(data)
    assert set(found) == set(data["expected"])
    for number, expected in data["expected"].items():
        mapping = found[number]
        if expected is None:
            assert mapping is None, number
            continue
        assert mapping is not None, (number, ctx.skipped)
        assert mapping.page_start == expected["page_start"]
        assert mapping.page_end == expected.get("page_end", mapping.page_end) >= mapping.page_start
        assert mapping.url.endswith(expected.get("url_end", mapping.url)) and "webapi.legistar.com" not in mapping.url
        numbers = [n for n, _ in mapping.pages]
        assert numbers and numbers[0] == mapping.page_start and len(numbers) <= staff_reports.LEADING_PAGES
        assert all(len(text) <= staff_reports.MAX_PAGE_CHARS for _, text in mapping.pages)
        assert expected["heading"] in mapping.pages[0][1], number
        title = next(i["title"] for i in data["items"] if i["number"] == number)
        assert staff_reports.title_overlap(title, "\n".join(text for _, text in mapping.pages)) >= 0.5


def test_api_budget_per_meeting(run):
    data = load("el-dorado-supervisors")
    _, ctx, read = run(data)
    # The meeting page, the day's events, the agenda rows, then Versions and Texts for each of the four reports.
    assert ctx.downloads == len(read) == 1 + 2 + 2 * 4
    assert ctx.skipped[item_id(data, "1")] == "closed session" and ctx.skipped[item_id(data, "6")] == "minutes"


def test_legislation_text_is_kept_as_pseudo_pages_with_a_stable_hash(run):
    data = load("el-dorado-supervisors")
    found, _, _ = run(data)
    first = found["7"]
    assert first.kind == "staff-report" and first.title == "Legislation text"
    assert first.url.startswith("https://eldorado.legistar.com/LegislationDetail.aspx?ID=8296509")
    assert first.pages[0][1].startswith("File #: 26-1680, Version: 1\n\nChief Administrative Office recommending the Board:")
    assert "DISCUSSION / BACKGROUND" in first.pages[0][1] and "..Body" not in first.pages[0][1]
    assert first.evidence == {"agendaLine": "7. 26-1680", "agendaPage": 6, "fileNumber": "26-1680",
                              "reportHeading": "File #: 26-1680, Version: 1", "titleOverlap": 1.0}
    assert run(load("el-dorado-supervisors"))[0]["7"].sha256 == first.sha256


def test_agenda_printing_another_file_number_is_not_stored(run):
    data = load("el-dorado-supervisors")
    page = data["stored"][next(iter(data["stored"]))]
    page["6"] = page["6"].replace("7. 26-1680", "7. 26-1681")
    found, ctx, _ = run(data)
    assert found["7"] is None and "file 26-1680" in ctx.skipped[item_id(data, "7")]
    assert found["10"] is not None


def test_legislation_text_of_another_file_is_not_stored(run):
    data = load("el-dorado-supervisors")
    text = next(v for k, v in data["json"].items() if "/Matters/41405/Texts/" in k)
    text["MatterTextMatterId"] = 41186
    found, ctx, _ = run(data)
    assert found["7"] is None and "not for file 26-1680" in ctx.skipped[item_id(data, "7")]


def test_meeting_page_without_the_file_falls_back_to_the_agendas_gateway_link(run):
    data = load("el-dorado-supervisors")
    url = data["meeting"]["official_url"]
    data["html"][url] = data["html"][url].replace(">26-1680<", ">26-1999<")
    found, _, _ = run(data)
    assert found["7"].url == "https://eldorado.legistar.com/gateway.aspx?m=l&id=/matter.aspx?key=41405"


def test_another_bodys_event_that_day_is_not_used(run):
    data = load("el-dorado-supervisors")
    events = next(v for k, v in data["json"].items() if "$filter=" in k)
    for event in events:
        if event["EventBodyName"] == "Board of Supervisors":
            event["EventBodyName"] = "Planning Commission"
    with pytest.raises(staff_reports.Uncertain, match="0 Legistar Board of Supervisors events"):
        run(data)


def test_report_cap_per_meeting(run, monkeypatch):
    monkeypatch.setattr(legistar, "MAX_REPORTS", 2)
    data = load("el-dorado-supervisors")
    found, ctx, _ = run(data)
    order = [i["number"] for i in data["items"] if i["number"] in ("7", "10", "23", "29")]  # agenda order
    assert [n for n in order if found[n]] == order[:2]
    assert all(ctx.skipped[item_id(data, n)] == "more than 2 reports in one meeting" for n in order[2:])


def test_planning_report_dated_for_another_meeting_is_not_stored(run):
    data = load("el-dorado-planning")
    url = next(a["MatterAttachmentHyperlink"] for a in row(data, "2")["EventItemMatterAttachments"]
               if a["MatterAttachmentName"] == "A - Staff Report")
    first = data["downloads"][url]["pages"][0]
    first["text"] = first["text"].replace("October 22, 2026", "November 12, 2026")
    found, ctx, _ = run(data)
    assert found["2"] is None and "dated November 12, 2026" in ctx.skipped[item_id(data, "2")]
    assert found["3"] is not None and found["3"].kind == "staff-memo"


def test_planning_report_for_another_case_is_not_stored(run):
    data = load("el-dorado-planning")
    url = next(a["MatterAttachmentHyperlink"] for a in row(data, "3")["EventItemMatterAttachments"]
               if a["MatterAttachmentName"] == "B - Staff Memo")
    for page in data["downloads"][url]["pages"]:
        page["text"] = page["text"].replace("ADM-A26-0001", "ADM-A26-0002").replace("ADM26-0083", "ADM26-0084")
    found, ctx, _ = run(data)
    assert found["3"] is None and "case number" in ctx.skipped[item_id(data, "3")]


def test_planning_item_without_a_report_attachment_uses_the_legislation_text(run):
    data = load("el-dorado-planning")
    r = row(data, "2")
    r["EventItemMatterAttachments"] = [a for a in r["EventItemMatterAttachments"] if a["MatterAttachmentName"] != "A - Staff Report"]
    found, ctx, read = run(data)
    # The Legislation Text is not in the fixture, so the item is skipped as a failed download (and retried later).
    assert found["2"] is None and ctx.skipped[item_id(data, "2")] == "download failed"
    assert any("/Matters/41326/Versions" in url for url in read)


def test_washoe_word_report_for_another_board_meeting_is_skipped(run):
    data = load("washoe-commissioners")
    found, ctx, _ = run(data)
    assert ctx.skipped[item_id(data, "7.A.1")] == "legacy Word file"
    assert "does not list the staff report" in ctx.skipped[item_id(data, "9")]
    # Had the agenda listed it, the report itself still names the October 27 meeting.
    page = data["stored"][next(iter(data["stored"]))]
    page["17"] = page["17"].replace("BCC 10-13-26 - Staff Report- OSRPC", "BCC 10-27-26 - Staff Report- OSRPC")
    found, ctx, _ = run(data)
    assert found["9"] is None and ctx.skipped[item_id(data, "9")] == "staff report is for the October 27, 2026 meeting"


def test_washoe_report_not_listed_under_the_item_is_not_stored(run):
    data = load("washoe-commissioners")
    page = data["stored"][next(iter(data["stored"]))]
    page["5"] = page["5"].replace("BCC 10-13-26 TMP12398 HSA_Accept Prepacked Meal", "BCC 10-13-26 TMP12398 HSA_Accept Meal")
    found, ctx, _ = run(data)
    assert found["7.B.1"] is None and "does not list" in ctx.skipped[item_id(data, "7.B.1")]


def test_washoe_report_with_another_subject_is_not_stored(run):
    data = load("washoe-commissioners")
    url14 = next(iter(data["downloads"]))
    first = data["downloads"][url14]["pages"][0]
    first["text"] = first["text"].split("SUBJECT:")[0] + "SUBJECT: Recommendation to approve a lease for the regional animal shelter.\n"
    found, ctx, _ = run(data)
    assert found["14"] is None and "subject differs" in ctx.skipped[item_id(data, "14")]


def test_washoe_word_report_downloads_count_and_honour_the_delay(run):
    data = load("washoe-commissioners")
    found, ctx, read = run(data)
    word = [u for u in read if u.endswith(".docx")]  # 7.B.1 and 8.E.4; item 9's is never fetched
    assert len(word) == 2 and found["7.B.1"].sha256 == __import__("hashlib").sha256(base64.b64decode(data["word"][word[0]])).hexdigest()
    assert ctx.downloads == len(read) == 5  # the event, its rows, two Word files, one PDF


def test_washoe_planning_report_naming_another_agenda_item_is_not_stored(run):
    data = load("washoe-planning")
    url = next(u for u in data["downloads"] if "6b7ebd75" in u)
    first = data["downloads"][url]["pages"][0]
    first["text"] = first["text"].replace("Agenda Item: 8A", "Agenda Item: 8B")
    found, ctx, _ = run(data)
    assert found["8.A"] is None and "does not name agenda item 8.A" in ctx.skipped[item_id(data, "8.A")]
    assert found["8.B"] is not None


def test_washoe_planning_oversized_report_is_skipped_for_good(run, monkeypatch):
    data = load("washoe-planning")
    found, ctx, _ = run(data)
    assert ctx.skipped[item_id(data, "8.C")] == "download failed" and ctx.fetch_failures

    def too_big(url, **kwargs):
        raise staff_reports.FetchFailed(f"{url}: source exceeds 83886080 byte limit")
    ctx = staff_reports.Context(lambda v: {}, ctx.meeting, too_big)
    run_ = legistar._Run(ctx, legistar.AGENCIES["washoe-planning"], None, {})
    item = next(i for i in ctx.meeting.items if i.number == "8.C")
    assert run_.pdf(item, "https://example.invalid/report.pdf") is None
    assert ctx.skipped[item.id] == "staff report PDF is larger than the download limit" and not ctx.fetch_failures


def test_pre_legistar_agenda_is_uncertain(run):
    data = load("washoe-planning")
    for document in data["documents"]:
        document["url"] = "https://www.washoecounty.gov/csd/planning_and_development/board_commission/planning_commission/2026/Files/040726_pcag.pdf"
    with pytest.raises(staff_reports.Uncertain, match="not a Legistar agenda"):
        run(data)


def test_agenda_rows_compose_split_numbers():
    rows_ = [{"EventItemAgendaSequence": 1, "EventItemAgendaNumber": "8.", "EventItemMatterId": None, "EventItemTitle": "Public Hearings"},
             {"EventItemAgendaSequence": 2, "EventItemAgendaNumber": "A.", "EventItemMatterId": None, "EventItemTitle": "Case WAB26-0009"},
             {"EventItemAgendaSequence": 3, "EventItemAgendaNumber": None, "EventItemMatterId": 13793, "EventItemTitle": "For hearing"},
             {"EventItemAgendaSequence": 4, "EventItemAgendaNumber": "7.A.1.", "EventItemMatterId": 1, "EventItemTitle": "x"},
             {"EventItemAgendaSequence": 5, "EventItemAgendaNumber": "9.", "EventItemMatterId": 2, "EventItemTitle": "y"},
             {"EventItemAgendaSequence": 6, "EventItemAgendaNumber": "9.", "EventItemMatterId": 3, "EventItemTitle": "z"}]
    found = legistar.agenda_rows(rows_)
    assert found["8.A"]["EventItemMatterId"] == 13793 and found["8.A"]["EventItemTitle"] == "Case WAB26-0009\nFor hearing"
    assert found["8"]["EventItemMatterId"] is None and found["7.A.1"]["EventItemMatterId"] == 1 and found["9"] is None


def test_docx_text_and_pseudo_pages():
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           '<w:p><w:r><w:t>Staff Report</w:t><w:tab/><w:t>BOARD MEETING DATE: October 13, 2026</w:t></w:r></w:p>'
           '<w:p><w:r><w:t xml:space="preserve">SUBJECT:  Recommendation</w:t><w:br/><w:t>to accept</w:t></w:r></w:p></w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    assert legistar.docx_text(buf.getvalue()) == "Staff Report BOARD MEETING DATE: October 13, 2026\nSUBJECT: Recommendation\nto accept"
    pages = legistar.pseudo_pages("\n".join(["x" * 3000] * 7))
    assert [len(p) for p in pages.values()] == [6001, 6001, 6001, 3000]
    assert list(legistar.pseudo_pages("y" * 17000).values()) == ["y" * 8000, "y" * 8000, "y" * 1000]


def test_event_found_from_the_agenda_file_name_is_checked():
    data = load("washoe-commissioners")
    event = next(v for k, v in data["json"].items() if k.endswith("/Events/1809"))
    assert event["EventDate"].startswith("2026-10-13")
    ctx = type("Ctx", (), {"meeting": type("M", (), {"local_date": date(2026, 10, 20)})})()
    api = type("Api", (), {"get": lambda self, path: event})()
    agendas = [staff_reports.Document(**d) for d in data["documents"]]
    with pytest.raises(staff_reports.Uncertain, match="is not the Board of County Commissioners meeting of 2026-10-20"):
        legistar.find_event(api, agendas, ctx, "Board of County Commissioners")
    assert urllib.parse.quote("EventDate eq datetime'2026-10-06'") in next(k for k in load("washoe-planning")["json"] if "$filter" in k)
