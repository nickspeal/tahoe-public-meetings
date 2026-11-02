"""``itemScope``: statewide agencies map staff reports and summarize only their Tahoe items (no network, no database)."""
import copy
import json
from datetime import date
from pathlib import Path

import pytest

from tahoe_ingest import item_scope, staff_reports
from tahoe_ingest.html_agenda import parse_html_agenda
from tahoe_ingest.html_source import html_text

ROOT = Path(__file__).resolve().parents[3]
PAGE = (ROOT / "tests/fixtures/html-agendas/state-lands--2026-10-21.html").read_text(encoding="utf-8")
MAPPING = json.loads((ROOT / "tests/fixtures/staff-reports/state-lands.json").read_text())

# The October 21, 2026 agenda's Lake Tahoe items: piers and buoys on the California shore, and item 58,
# the Lake Tahoe benchmark rents. The other 56 items are elsewhere in California or statewide.
TAHOE_ITEMS = ["01", "02", "03", "04", "05", "06", "07", "10", "12", "13", "15", "17", "20", "21", "23", "24", "26", "58"]


def october_items():
    items, complete = parse_html_agenda(PAGE, html_text(PAGE))
    assert complete
    return items


def qualifies(item, agency="state-lands"):
    return item_scope.in_scope(agency, item["title"], [{"quote": p["quote"]} for p in item["source_pages"]])


def test_only_the_october_2026_tahoe_items_qualify():
    items = october_items()
    assert [i["source_number"] for i in items if qualifies(i)] == TAHOE_ITEMS
    assert len(items) == 74  # 64 numbered items, the meeting's parts, and an emergency item


def test_the_cited_row_counts_when_the_title_is_cut_before_the_location():
    item = next(i for i in october_items() if i["source_number"] == "13")
    assert "Tahoe" not in item["title"]  # a long list of trustees fills the title
    assert not item_scope.in_scope("state-lands", item["title"])
    assert qualifies(item)


def test_a_tahoe_county_alone_does_not_qualify():
    elsewhere = ("COUNTY OF EL DORADO (APPLICANT) Consider application for a General Lease – Public Agency Use, of sovereign land "
                 "located in Folsom Lake, near Folsom, El Dorado County and Placer County")
    assert not item_scope.in_scope("state-lands", elsewhere)
    assert item_scope.in_scope("state-lands", elsewhere.replace("Folsom Lake, near Folsom", "the Truckee River, near Truckee"))


def test_terms_match_whole_words_across_line_breaks_and_case():
    found = item_scope.pattern(["Kings Beach", "Tahoe"])
    assert found.search("adjacent to 8690 Brockway Vista, near KINGS\nBEACH, Placer County")
    assert found.search("South Lake Tahoe, El Dorado County")
    assert not found.search("Tahoeville") and not found.search("Kings Beaches")


def test_agencies_without_the_block_keep_every_item():
    assert item_scope.in_scope("trpa-governing", "Pacific Ocean lease") and item_scope.in_scope("no-such-agency", "Pacific Ocean lease")


@pytest.mark.parametrize("block, problem", [
    ({"mode": "tahoe-only"}, "needs a non-empty list of terms"),
    ({"mode": "tahoe-only", "terms": []}, "needs a non-empty list of terms"),
    ({"mode": "tahoe-only", "terms": ["Tahoe", " "]}, "needs a non-empty list of terms"),
    ({"mode": "everywhere", "terms": ["Tahoe"]}, "mode must be one of"),
    ({"mode": "whole-agenda", "terms": ["Tahoe"]}, "whole-agenda takes no terms"),
    ({"mode": "tahoe-only", "terms": ["Tahoe"], "counties": True}, "invalid itemScope"),
    ({"mode": "tahoe-only", "terms": ["Tahoe"], "note": ""}, "note must be non-empty text"),
])
def test_a_malformed_block_is_refused(block, problem):
    with pytest.raises(ValueError, match=problem):
        item_scope.check_config({"id": "x", "itemScope": block})
    item_scope.check_config({"id": "x", "itemScope": {"mode": "whole-agenda"}})


class FakeConn:
    """Answers ``map_staff_reports``' reads from the State Lands mapping fixture and records its one write."""

    def __init__(self, data):
        self.data, self.stored = data, None

    def execute(self, sql, params=None):
        m = self.data["meeting"]
        if "FROM agenda_items i JOIN meetings m" in sql:
            return [{"id": i["id"], "meeting_id": m["id"], "number": i["number"], "title": i["title"], "section": i["section"],
                     "source_fingerprint": i["fingerprint"], "citations": i["citations"], "agency_id": m["agency_id"],
                     "meeting_title": m["title"], "local_date": date.fromisoformat(m["local_date"]),
                     "official_url": m["official_url"]} for i in self.data["items"]]
        if "FROM document_current" in sql:
            return [{"meeting_id": m["id"], "id": d["version_id"], **{k: v for k, v in d.items() if k != "version_id"}}
                    for d in self.data["documents"]]
        if "INSERT INTO staff_report_scans" in sql:
            self.stored = params
        return []

    def commit(self):
        pass


def run_mapping(data):
    conn, read = FakeConn(data), []

    def reader(url, *, last_page=None, links=False):
        read.append(url)
        found = copy.deepcopy(data["downloads"][url])
        found["pages"] = [p for p in found["pages"] if not last_page or p["page_number"] <= last_page]
        return found

    def fetch_html(url):
        read.append(url)
        return data["html"][url]
    report = staff_reports.map_staff_reports(conn, agency="state-lands", reader=reader, delay=0, today=date(2024, 12, 1),
                                             fetch_html=fetch_html)
    return report, json.loads(conn.stored["rows"]), json.loads(conn.stored["detail"]), read


def test_mapping_reads_and_stores_only_items_in_scope(monkeypatch):
    report, rows, detail, read = run_mapping(MAPPING)
    # Item 11 is a Lake Tahoe marina; the Consent Calendar part is out of scope, so it is left out without a skip reason.
    assert report["itemsMapped"] == 1 and [r["item_id"] for r in rows] == ["state-lands:2024-12-17:11"]
    assert detail["outOfScope"] == 1 and "state-lands:2024-12-17:V" not in detail["skipped"]
    assert len(read) == 2

    monkeypatch.setattr(item_scope, "_matcher", lambda agency: item_scope.pattern(["Mono Lake"]))
    report, rows, detail, read = run_mapping(MAPPING)
    # Nothing is in scope: no page or report is downloaded, and the scan still stores (and so clears) the meeting.
    assert report["meetingsScanned"] == 1 and report["itemsMapped"] == 0 and rows == [] and read == []
    assert detail == {"mapped": 0, "skipped": {}, "outOfScope": 2, "attachments": 0}
