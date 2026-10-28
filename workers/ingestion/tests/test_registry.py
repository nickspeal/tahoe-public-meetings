"""The agency registry as configuration: inheritance, canonical form, references from other config, generated docs."""
import json
import re
from functools import lru_cache
from pathlib import Path

import pytest

from tahoe_ingest import catalog, coverage, discovery, durations, source_discovery, staff_report_rules, staff_reports, topics
from tahoe_ingest.agenda import parse_agenda
from tahoe_ingest.catalog import AGENCY_KEYS, OWN_KEYS, agency_catalog, registry_text, resolve_extends
from tahoe_ingest.staff_report_rules import ttd

import test_staff_reports


ROOT = Path(__file__).resolve().parents[3]
CACHED = ((staff_report_rules, "agency_rules"), (topics, "agency_hints"), (durations, "duration_config"))


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Load the catalog, and what is cached from it, from a temporary registry. Other modules' caches are swapped for
    fresh ones and restored afterwards, so objects other tests hold (``staff_reports.RULES``) stay current."""
    for module, name in CACHED:
        monkeypatch.setattr(module, name, lru_cache(maxsize=1)(getattr(module, name).__wrapped__))

    def use(entries):
        path = tmp_path / "agencies.json"
        path.write_text(json.dumps(entries))
        monkeypatch.setattr(catalog, "agency_catalog_path", lambda: path)
        catalog.agency_catalog.cache_clear()
        for module, name in CACHED:
            getattr(module, name).cache_clear()
    yield use
    monkeypatch.undo()
    catalog.agency_catalog.cache_clear()


def raw_registry():
    return json.loads(catalog.agency_catalog_path().read_text())


def test_registry_file_is_in_canonical_form():
    text = catalog.agency_catalog_path().read_text()
    assert text == registry_text(json.loads(text)), "rewrite with catalog.registry_text (keys in AGENCY_KEYS order)"


def test_child_inherits_formats_but_not_its_parents_identity():
    parent = {"id": "board", "name": "Board", "shortName": "B", "aliases": ["The Board"], "follow": False,
              "officialUrl": "https://example.gov/", "discovery": {"type": "granicus", "indexUrl": "https://example.gov/i",
                                                                   "bodyPattern": "Board", "titlePrefix": "B "},
              "agenda": {"layouts": ["a", "b"], "agendaBeforePacket": True}, "topicHints": {"transit": [{"rule": "r", "pattern": "x"}]},
              "schedule": {"source": "configured"}, "duration": {"mode": "fixed"}, "legacy": {"sourceRow": 3},
              "participation": {"sourceUrl": "https://example.gov/p"}}
    child = {"legacy": {"sourceRow": 4}, "follow": True, "id": "committee", "name": "Committee", "extends": "board",
             "discovery": {"bodyPattern": "Committee"}, "agenda": {"layouts": ["c"]}}
    resolved = dict(zip(("board", "committee"), resolve_extends([parent, child])))
    committee = resolved["committee"]
    assert list(committee) == [k for k in AGENCY_KEYS if k in committee]  # canonical order, `extends` dropped
    assert committee["discovery"] == {"type": "granicus", "indexUrl": "https://example.gov/i", "bodyPattern": "Committee",
                                      "titlePrefix": "B "}  # objects merge, the child's keys win
    assert committee["agenda"] == {"layouts": ["c"], "agendaBeforePacket": True}  # lists replace
    assert committee["shortName"] == "B" and committee["topicHints"] == parent["topicHints"]
    assert committee["follow"] is True and committee["legacy"] == {"sourceRow": 4}
    assert not {"aliases", "schedule", "duration", "participation"} & set(committee)
    assert resolved["board"] == parent and "extends" not in committee
    assert OWN_KEYS <= set(AGENCY_KEYS)


def test_chains_resolve_parent_first_and_errors_name_the_entry():
    a = {"id": "a", "discovery": {"type": "rss", "indexUrl": "https://example.gov/f"}}
    b = {"id": "b", "extends": "a", "discovery": {"bodyPattern": "B"}}
    c = {"id": "c", "extends": "b", "discovery": {"titlePrefix": "C "}}
    assert resolve_extends([c, b, a])[0]["discovery"] == {"type": "rss", "indexUrl": "https://example.gov/f",
                                                          "bodyPattern": "B", "titlePrefix": "C "}
    with pytest.raises(ValueError, match="c: extends unknown agency 'x'"):
        resolve_extends([a, {**c, "extends": "x"}])
    with pytest.raises(ValueError, match="extend each other: a -> b -> a"):
        resolve_extends([{**a, "extends": "b"}, b])
    with pytest.raises(ValueError, match="extend each other: a -> a"):
        resolve_extends([{**a, "extends": "a"}])


def test_unknown_registry_keys_and_bad_inherited_blocks_stop_the_load(registry):
    entries = raw_registry()
    registry([*entries, {"id": "typo", "name": "T", "shortName": "T", "follow": False, "staffReport": {}}])
    with pytest.raises(ValueError, match="typo: unknown registry keys staffReport"):
        agency_catalog()
    # A child is validated as resolved: a parameter its parent's rule does not take is an error.
    registry([*entries, {"id": "x", "name": "X", "extends": "ttd", "follow": True, "staffReports": {"packetName": "X"}}])
    with pytest.raises(ValueError, match="ttd-packet takes no packetName"):
        agency_catalog()


def test_sibling_bodies_share_their_parents_formats():
    agencies = {a["id"]: a for a in agency_catalog()}
    raw = {e["id"]: e for e in raw_registry()}
    rtcpc, board = agencies["ttd-rtcpc"], agencies["ttd"]
    assert set(raw["ttd-rtcpc"]) <= {"id", "name", "aliases", "extends", "follow", "discovery", "staffReports"}
    assert {k: rtcpc[k] for k in ("shortName", "officialUrl", "agenda", "topicHints")} == \
        {k: board[k] for k in ("shortName", "officialUrl", "agenda", "topicHints")}
    assert rtcpc["discovery"]["indexUrl"] == board["discovery"]["indexUrl"] and rtcpc["discovery"]["documentIdPrefix"] == "ttd"
    assert rtcpc["staffReports"]["rule"] == "ttd-packet" and "schedule" not in rtcpc and "duration" not in rtcpc


def test_config_files_name_registry_agencies():
    """Per-agency settings outside the registry (recording feeds, snapshots, reviews) must name a registry entry."""
    ids = {a["id"] for a in agency_catalog()}

    def named(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                if key in ("agency", "agencyId") and isinstance(inner, str):
                    yield inner
                yield from named(inner)
        elif isinstance(value, list):
            for inner in value:
                yield from named(inner)
    for path in sorted((ROOT / "config").glob("*.json")):
        unknown = set(named(json.loads(path.read_text()))) - ids
        assert not unknown, f"{path.name} names unknown agencies {sorted(unknown)}"


def test_meeting_durations_file_holds_only_defaults():
    assert "agencies" not in json.loads((ROOT / "config/meeting-durations.json").read_text())
    assert durations.duration_config()["agencies"]["ttd"]["mode"] == "fixed"


def test_coverage_doc_is_current():
    text = (ROOT / coverage.DOC).read_text()
    assert coverage.render(text, ROOT) == text, "run `python3 scripts/project.py worker coverage-doc`"


def test_a_new_sibling_body_needs_only_its_registry_entry(registry, monkeypatch):
    """A TTD committee the registry has never seen, added as one entry: discovery, agenda layouts, staff report rule,
    and topic hints all come from configuration, with no code change."""
    registry([*raw_registry(), {
        "id": "ttd-fpc", "name": "TTD — Finance and Personnel Committee", "aliases": ["FPC"], "extends": "ttd", "follow": True,
        "discovery": {"bodyPattern": "Finance and Personnel Committee"}, "staffReports": {"footer": "(?:FPC\\s+)?AGENDA ITEM"}}])
    fpc = next(a for a in agency_catalog() if a["id"] == "ttd-fpc")

    # Discovery: the board's WordPress feed, filtered to the committee's posts.
    titles = {7172: "TTD Board of Directors Meeting – November 4, 2026",
              7173: "TTD Finance and Personnel Committee Meeting – November 4, 2026",
              7174: "TTD Regional and Transit Capital Programs Committee Meeting – November 2, 2026"}
    api = json.dumps([{"id": i, "link": f"https://tahoetransportation.org/meeting/{i}/", "title": {"rendered": t},
                       "modified": "2026-10-13"} for i, t in titles.items()])
    page = '<a href="/wp-content/uploads/fpc-packet.pdf">Agenda Packet</a>'
    monkeypatch.setattr(discovery.time, "sleep", lambda _: None)
    [row] = source_discovery.discover_configured("ttd-fpc", fpc["discovery"], limit=5,
                                                 fetch=lambda url: api if "wp-json" in url else page)
    assert row["source_id"] == "7173" and row["documents"][0]["id"] == "ttd-fpc-agenda-packet"

    # Agenda layouts: a TTD agenda parses for the committee exactly as for the board.
    meeting = next(m for m in json.loads((ROOT / "tests/fixtures/meetings.json").read_text())["meetings"]
                   if m["agency"] == "ttd" and m["documents"])
    board = parse_agenda("ttd", meeting["documents"], meeting["pages"])
    assert board.complete and parse_agenda("ttd-fpc", meeting["documents"], meeting["pages"]) == board

    # Staff reports: the board's packet rule, bound to the committee's footer, maps the committee's memos.
    assert "ttd-fpc" in staff_reports.SUPPORTED and staff_reports.RULES["ttd-fpc"].func is ttd.map_ttd
    monkeypatch.setattr(test_staff_reports, "RULES", staff_reports.RULES)  # bound when that module was imported
    data = test_staff_reports.load("ttd")
    data["meeting"]["agency_id"] = "ttd-fpc"
    for pages in data["stored"].values():
        for number, text in pages.items():
            pages[number] = re.sub(r"(^|\s{2,})AGENDA ITEM\b", r"\1FPC AGENDA ITEM", text, flags=re.M)
    found, _, _ = test_staff_reports.run_rule(data)
    expected = {number: (e["page_start"], e.get("page_end")) for number, e in data["expected"].items() if e}
    assert expected and {n: (m.page_start, expected[n][1] and m.page_end) for n, m in found.items() if m} == expected

    # Topic hints carry over; the board's meeting length and schedule do not.
    assert topics.classify("ttd-fpc", "Fleet replacement plan", [])["transit"]["rule"] == "ttd-fleet"
    assert "schedule" not in fpc and durations.agency_settings("ttd-fpc")["mode"] == "estimated"
