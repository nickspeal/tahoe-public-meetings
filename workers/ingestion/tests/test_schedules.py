"""Schedule patterns: learning from stored meetings and export-time forecasts."""
import contextlib
import copy
import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from tahoe_ingest import core
from tahoe_ingest.catalog import agency_catalog
from tahoe_ingest.core import export_public, ingest_bundle
from tahoe_ingest.schedules import (PACIFIC, describe, forecast_meetings, learn_schedule, local_start, regular_title,
                                    rule_dates, validate_schedule, write_schedules)

from test_pipeline import db, fixture_bundle  # noqa: F401  (shared temporary-schema fixture)

ROOT = Path(__file__).resolve().parents[3]
FIRST_WEDNESDAY = {"ordinal": 1, "weekday": "wednesday", "time": "15:00", "timezone": PACIFIC, "label": "Board meeting"}
TTD_SCHEDULE = {"source": "configured", "rules": [FIRST_WEDNESDAY]}


def meeting(day, clock="15:00", title="Board of Directors Meeting", status="scheduled", source_id=None):
    local_day = date.fromisoformat(day)
    return {"id": f"agency:{source_id or day}", "source_id": source_id or day, "title": title, "status": status,
            "starts_at": local_start(local_day, clock) if clock else None, "local_date": local_day, "timezone": PACIFIC}


def days(values):
    return [date.fromisoformat(v) for v in values]


def forecast(schedule=TTD_SCHEDULE, records=(), today="2026-10-20", horizon=90, generated_at="2026-10-20T15:00:00Z"):
    return forecast_meetings("ttd", schedule, days(records), today=date.fromisoformat(today), horizon_days=horizon,
                             official_url="https://agency.example/", generated_at=generated_at)


# ------------------------------------------------------------- rule dates

def test_rule_dates_cover_ordinals_last_months_and_every():
    assert rule_dates(FIRST_WEDNESDAY, date(2026, 10, 21), date(2026, 12, 31)) == days(["2026-11-04", "2026-12-02"])
    last_friday = {"ordinal": "last", "weekday": "friday", "time": "09:00", "timezone": PACIFIC, "label": "x"}
    assert rule_dates(last_friday, date(2026, 10, 1), date(2026, 10, 31)) == days(["2026-10-30"])
    quarterly = {"ordinal": 3, "weekday": "thursday", "months": [3, 6, 9, 12], "time": "09:30", "timezone": PACIFIC, "label": "x"}
    assert rule_dates(quarterly, date(2026, 10, 1), date(2027, 6, 30)) == days(["2026-12-17", "2027-03-18", "2027-06-17"])
    biweekly = {"every": 2, "anchor": "2026-08-11", "weekday": "tuesday", "time": "17:30", "timezone": PACIFIC, "label": "x"}
    assert rule_dates(biweekly, date(2026, 10, 1), date(2026, 10, 31)) == days(["2026-10-06", "2026-10-20"])
    # The cadence also extends before its anchor.
    assert rule_dates(biweekly, date(2026, 7, 20), date(2026, 8, 1)) == days(["2026-07-28"])


@pytest.mark.parametrize("change", [
    {"weekday": "Wednesday"}, {"ordinal": 5}, {"ordinal": True}, {"time": "3pm"}, {"time": "24:00"},
    {"timezone": "UTC"}, {"months": [13]}, {"months": []}, {"months": [3, 3]}, {"label": " "}, {"color": "red"},
    {"every": 2}, {"every": 2, "anchor": "2026-08-11"}, {"anchor": "2026-08-12"},
])
def test_invalid_rules_are_rejected(change):
    rule = {**FIRST_WEDNESDAY, **change}
    if "every" in change:
        rule.pop("ordinal")
    with pytest.raises(ValueError):
        validate_schedule("ttd", {"source": "configured", "rules": [rule]})


@pytest.mark.parametrize("schedule", [
    {"source": "guessed", "rules": [FIRST_WEDNESDAY]}, {"source": "configured", "rules": []},
    {"source": "learned", "rules": [FIRST_WEDNESDAY], "confidence": 1.5}, [FIRST_WEDNESDAY],
])
def test_invalid_schedules_are_rejected(schedule):
    with pytest.raises(ValueError):
        validate_schedule("ttd", schedule)


def test_registry_ttd_is_configured_and_learned_schedules_clear_the_bar():
    agencies = {a["id"]: a for a in agency_catalog()}
    ttd = agencies["ttd"]["schedule"]
    assert ttd["source"] == "configured"
    assert [(r["ordinal"], r["weekday"], r["time"], r["timezone"]) for r in ttd["rules"]] == [(1, "wednesday", "15:00", PACIFIC)]
    assert describe(ttd["rules"]) == "Usually the first Wednesday at 3:00 PM"
    for agency in agencies.values():
        schedule = agency.get("schedule")
        if schedule and schedule["source"] == "learned":
            assert schedule["learnedFrom"]["matched"] >= 3
            assert schedule["confidence"] >= 0.6


def test_descriptions_read_as_plain_patterns():
    pair = [{**FIRST_WEDNESDAY, "ordinal": 2, "weekday": "tuesday", "time": "17:00"},
            {**FIRST_WEDNESDAY, "ordinal": 4, "weekday": "tuesday", "time": "17:00"}]
    assert describe(pair) == "Usually the second and fourth Tuesday at 5:00 PM"
    assert describe([{**FIRST_WEDNESDAY, "ordinal": 3, "weekday": "thursday", "months": [3, 6, 9, 12], "time": "09:30"}]) == (
        "Usually the third Thursday of March, June, September, and December at 9:30 AM")
    biweekly = {"every": 2, "anchor": "2026-08-11", "weekday": "tuesday", "time": "17:30", "timezone": PACIFIC, "label": "x"}
    assert describe([biweekly]) == "Usually every other Tuesday at 5:30 PM"


# ---------------------------------------------------------------- learner

def test_learns_first_wednesday_with_exceptions_and_ignores_irregular_records():
    regular = ["2026-01-07", "2026-02-04", "2026-03-04", "2026-04-01", "2026-05-06", "2026-06-03",
               "2026-07-01", "2026-08-05", "2026-10-07", "2026-11-04", "2026-12-02"]
    rows = [meeting(d, title=f"Board of Directors Meeting – {d}") for d in regular]
    rows += [
        meeting("2026-09-16", title="Board of Directors Meeting"),  # Off pattern: counts against the match rate.
        meeting("2026-09-09", clock="10:00", title="Special Board Meeting"),
        meeting("2026-09-23", clock="10:00", title="Board Strategic Planning Workshop"),
        meeting("2026-08-19", status="canceled"),
        meeting("2026-08-26", clock=None),
        meeting("2026-08-12", source_id="one-off-transit-forum"),
    ]
    report = learn_schedule("ttd", rows, learned_on=date(2026, 10, 20))
    assert report["status"] == "proposed"
    assert report["excluded"] == {"canceled": 1, "noStartTime": 1, "oneOff": 1, "special": 2}
    assert (report["considered"], report["matched"]) == (12, 11)
    schedule = report["schedule"]
    assert schedule["source"] == "learned" and schedule["confidence"] == 0.92
    assert schedule["rules"] == [{"ordinal": 1, "weekday": "wednesday", "time": "15:00", "timezone": PACIFIC,
                                  "label": "Board of Directors Meeting"}]
    assert schedule["learnedFrom"] == {"meetings": 12, "matched": 11, "firstDate": "2026-01-07",
                                       "lastDate": "2026-12-02", "learnedOn": "2026-10-20"}
    assert [e["date"] for e in report["exceptions"]] == ["2026-09-16"]
    # No stored record of any kind falls within three days of the September pattern date.
    assert report["unrecordedDates"] == ["2026-09-02"]
    validate_schedule("ttd", schedule)


def test_unrecorded_pattern_dates_are_reported():
    rows = [meeting(d) for d in ["2026-05-06", "2026-06-03", "2026-08-05", "2026-10-07"]]
    report = learn_schedule("ttd", rows)
    assert report["unrecordedDates"] == ["2026-07-01", "2026-09-02"]


def test_learns_second_and_fourth_tuesday_pair():
    rows = [meeting(d, clock="17:00") for d in ["2026-09-08", "2026-09-22", "2026-10-27", "2026-11-10", "2026-11-24"]]
    report = learn_schedule("truckee", rows)
    assert report["status"] == "proposed"
    assert [(r["ordinal"], r["weekday"], r["time"]) for r in report["schedule"]["rules"]] == [(2, "tuesday", "17:00"), (4, "tuesday", "17:00")]


def test_learns_biweekly_when_it_explains_more_than_ordinal_pairs():
    dates = ["2026-08-11", "2026-08-25", "2026-09-08", "2026-09-22", "2026-10-06", "2026-10-20", "2026-11-03", "2026-11-17"]
    report = learn_schedule("slt", [meeting(d, clock="17:30") for d in dates])
    assert report["schedule"]["rules"] == [{"every": 2, "anchor": "2026-08-11", "weekday": "tuesday", "time": "17:30",
                                            "timezone": PACIFIC, "label": "Board of Directors Meeting"}]
    assert report["matched"] == 8


def test_ordinal_pair_wins_an_equal_support_tie_with_biweekly():
    report = learn_schedule("douglas", [meeting(d, clock="10:00") for d in ["2026-09-17", "2026-10-01", "2026-10-15"]])
    assert [r["ordinal"] for r in report["schedule"]["rules"]] == [1, 3]


def test_quarterly_months_are_inferred_from_regular_gaps():
    report = learn_schedule("ctc", [meeting(d, clock="09:30") for d in ["2026-03-19", "2026-06-18", "2026-09-17"]])
    assert report["schedule"]["rules"][0]["months"] == [3, 6, 9, 12]
    assert forecast(report["schedule"], today="2026-10-20")[0]["date"] == "2026-12-17"


def test_too_few_matches_is_insufficient():
    report = learn_schedule("trcd", [meeting("2026-05-21", "13:00"), meeting("2026-06-18", "13:00")])
    assert report["status"] == "insufficient" and report["schedule"] is None
    assert "need 3" in report["reason"]


def test_low_match_rate_is_insufficient():
    rows = [meeting(d) for d in ["2026-05-06", "2026-06-03", "2026-07-01"]]
    rows += [meeting(d) for d in ["2026-05-19", "2026-06-11", "2026-07-24"]]  # Three unrelated weekdays.
    report = learn_schedule("x", rows)
    assert (report["matched"], report["considered"]) == (3, 6)
    assert report["status"] == "insufficient" and "below 60%" in report["reason"]


def test_a_different_start_time_is_an_exception_not_a_match():
    rows = [meeting(d) for d in ["2026-05-06", "2026-06-03", "2026-07-01"]] + [meeting("2026-08-05", "10:00")]
    report = learn_schedule("ttd", rows)
    assert report["status"] == "proposed" and (report["matched"], report["considered"]) == (3, 4)
    assert report["exceptions"] == [{"date": "2026-08-05", "time": "10:00", "title": "Board of Directors Meeting",
                                     "reason": "starts 10:00 instead of 15:00"}]


def test_a_changed_meeting_time_splits_support():
    # South Lake Tahoe's 2026 records move from 9:00 AM to 5:30 PM; neither time has enough support yet.
    rows = [meeting("2026-08-11", "09:00"), meeting("2026-09-08", "09:00"), meeting("2026-09-22", "17:30"), meeting("2026-10-06", "17:30")]
    report = learn_schedule("slt", rows)
    assert report["status"] == "insufficient" and report["matched"] == 2 and report["schedule"] is None


def test_no_timed_meetings_reports_reason():
    report = learn_schedule("ivgid", [meeting("2026-09-09", clock=None)])
    assert report["status"] == "insufficient" and report["considered"] == 0
    assert "verified start time" in report["reason"]


@pytest.mark.parametrize("title,expected", [
    ("TTD Board of Directors Meeting – May 6, 2026", "TTD Board of Directors Meeting"),
    ("City Council Meeting Agenda — August 11, 2026", "City Council Meeting"),
    ("California Fish and Game Commission Meeting (June 17–18, 2026)", "California Fish and Game Commission Meeting"),
    ("Lahontan Regional Water Quality Control Board Meeting — May 6–7, 2026", "Lahontan Regional Water Quality Control Board Meeting"),
])
def test_regular_titles_drop_dates(title, expected):
    assert regular_title(title) == expected


# ------------------------------------------------------------ forecasting

def test_forecasts_are_bounded_future_and_stable():
    first = forecast()
    assert [m["date"] for m in first] == ["2026-11-04", "2026-12-02", "2027-01-06"]  # Feb 3 is past the 90-day horizon.
    assert [m["id"] for m in first] == ["forecast:ttd:2026-11-04", "forecast:ttd:2026-12-02", "forecast:ttd:2027-01-06"]
    again = forecast(generated_at="2026-10-21T15:00:00Z")
    assert [m["id"] for m in again] == [m["id"] for m in first]
    shape = first[0]
    assert shape["kind"] == "forecast" and shape["status"] == "scheduled"
    assert shape["documents"] == [] and shape["items"] == [] and shape["topics"] == []
    assert shape["officialUrl"] == "https://agency.example/"
    assert shape["forecast"] == {"ruleLabel": "Board meeting", "basis": "Usually the first Wednesday at 3:00 PM", "source": "configured"}
    # PST after the November DST change; PDT before it.
    assert shape["startsAt"] == "2026-11-04T23:00:00Z"
    assert forecast(today="2026-09-20", horizon=20)[0]["startsAt"] == "2026-10-07T22:00:00Z"


def test_no_forecast_for_today_or_the_past_and_zero_horizon_disables():
    assert forecast(today="2026-11-04")[0]["date"] == "2026-12-02"
    assert forecast(horizon=0) == []
    assert forecast(schedule=None) == []


def test_nearby_real_records_suppress_forecasts():
    assert "2026-12-02" not in [m["date"] for m in forecast(records=["2026-12-05"])]  # +3 days: replaced.
    assert "2026-12-02" not in [m["date"] for m in forecast(records=["2026-11-29"])]  # -3 days: replaced.
    assert "2026-12-02" in [m["date"] for m in forecast(records=["2026-12-06"])]  # +4 days: independent.


# --------------------------------------------------------- export boundary

def _check_export_module():
    spec = importlib.util.spec_from_file_location("check_export", ROOT / "scripts/check_export.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schedules(monkeypatch, **schedules):
    """Pin schedules and the export clock (Oct 20, 2026 Pacific) so results never depend on the run date."""
    agencies = {aid: {key: value for key, value in entry.items() if key != "schedule"} for aid, entry in core.AGENCIES.items()}
    for aid, schedule in schedules.items():
        agencies[aid]["schedule"] = schedule
    monkeypatch.setattr(core, "AGENCIES", agencies)
    monkeypatch.setattr(core, "now", lambda: datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc))


def test_export_appends_valid_forecasts_without_storing_them(db, monkeypatch):
    _schedules(monkeypatch, ttd=TTD_SCHEDULE)
    assert ingest_bundle(db, fixture_bundle())["failed"] == 0
    stored = db.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"]
    plain = export_public(db)
    assert plain["schemaVersion"] == 2
    assert all(m["kind"] == "confirmed" and "forecast" not in m for m in plain["meetings"])
    data = export_public(db, forecast_days=90, today=date(2026, 10, 20))
    expected = [m for m in data["meetings"] if m["kind"] == "forecast"]
    # The stored Nov 4 notice (date only, no agenda yet) replaces that forecast.
    assert [m["id"] for m in expected] == ["forecast:ttd:2026-12-02", "forecast:ttd:2027-01-06"]
    # Stored records keep their order; forecasts follow them.
    assert [m["id"] for m in data["meetings"][:len(plain["meetings"])]] == [m["id"] for m in plain["meetings"]]
    assert data["meetings"][len(plain["meetings"]):] == expected
    assert expected[0]["officialUrl"] == next(a["officialUrl"] for a in data["agencies"] if a["id"] == "ttd")
    assert db.execute("SELECT count(*) AS n FROM meetings").fetchone()["n"] == stored
    checker = _check_export_module()
    errors, _ = checker.check(json.loads(json.dumps(data)))
    kind_errors, count = checker.check_kinds(data)
    assert errors == [] and kind_errors == [] and count == 2


def test_a_canceled_record_replaces_the_forecast(db, monkeypatch):
    _schedules(monkeypatch, ttd=TTD_SCHEDULE)
    bundle = {"meetings": [{"agency": "ttd", "id": "one-off-december-board", "title": "TTD Board of Directors Meeting",
                            "date": "2026-12-02", "status": "canceled", "source_meeting_url": "https://tahoetransportation.org/",
                            "items_complete": False, "documents_complete": False}]}
    assert ingest_bundle(db, bundle)["failed"] == 0
    dates = [m["date"] for m in export_public(db, forecast_days=90, today=date(2026, 10, 20))["meetings"] if m["kind"] == "forecast"]
    assert dates == ["2026-11-04", "2027-01-06"]


def test_check_export_rejects_misleading_forecasts(db, monkeypatch):
    _schedules(monkeypatch, ttd=TTD_SCHEDULE)
    assert ingest_bundle(db, fixture_bundle())["failed"] == 0
    data = export_public(db, forecast_days=90, today=date(2026, 10, 20))
    checker = _check_export_module()
    bad = copy.deepcopy(data)
    target = next(m for m in bad["meetings"] if m["kind"] == "forecast")
    target["items"] = [{"id": "invented"}]
    target["date"] = "2026-11-05"  # Next to the stored Nov 4 record, and inconsistent with its ID.
    errors, _ = checker.check_kinds(bad)
    assert any("no documents, agenda items" in e for e in errors)
    assert any("forecast ID" in e for e in errors)
    assert any("must replace the forecast" in e for e in errors)
    unlabeled = copy.deepcopy(data)
    next(m for m in unlabeled["meetings"] if m["kind"] == "confirmed")["kind"] = None
    assert any("kind must be" in e for e in checker.check_kinds(unlabeled)[0])


# --------------------------------------------------------------------- CLI

def test_write_schedules_keeps_configured_and_places_schedule_before_legacy(tmp_path):
    configured = {"source": "configured", "rules": [FIRST_WEDNESDAY]}
    path = tmp_path / "agencies.json"
    path.write_text(json.dumps([{"id": "a", "name": "A", "legacy": {"row": 1}}, {"id": "b", "schedule": configured}]))
    learned = {"source": "learned", "rules": [{**FIRST_WEDNESDAY, "ordinal": 3}], "confidence": 1.0,
               "learnedFrom": {"meetings": 3, "matched": 3}}
    proposals = [{"agency": aid, "action": "write learned", "schedule": learned} for aid in ("a", "b")]
    assert write_schedules(path, proposals) == ["a"]
    a, b = json.loads(path.read_text())
    assert list(a) == ["id", "name", "schedule", "legacy"] and a["schedule"] == learned
    assert b["schedule"] == configured


def test_learn_schedules_cli_writes_learned_but_never_replaces_configured(db, tmp_path, monkeypatch, capsys):
    from tahoe_ingest import cli
    monkeypatch.setattr(cli, "connect", lambda url: contextlib.nullcontext(db))
    assert ingest_bundle(db, fixture_bundle())["failed"] == 0
    registry = [{key: value for key, value in entry.items() if key != "schedule"} for entry in agency_catalog()]
    keep = {"source": "configured", "rules": [{**FIRST_WEDNESDAY, "ordinal": 2, "time": "10:00"}]}
    next(e for e in registry if e["id"] == "ttd")["schedule"] = keep
    path = tmp_path / "agencies.json"
    path.write_text(json.dumps(registry, indent=2) + "\n")
    before = path.read_text()

    assert cli.main(["--database-url", "unused", "learn-schedules", "--dry-run"]) == 0  # Reads the real registry; changes nothing.
    assert cli.main(["--database-url", "unused", "learn-schedules", "--registry", str(path)]) == 0
    assert path.read_text() == before
    out = capsys.readouterr().out
    assert "would write" in out and "configured (kept)" in out and "insufficient data" in out

    assert cli.main(["--database-url", "unused", "learn-schedules", "--write", "--registry", str(path)]) == 0
    written = {e["id"]: e for e in json.loads(path.read_text())}
    assert written["ttd"]["schedule"] == keep
    truckee = written["truckee"]["schedule"]
    assert truckee["source"] == "learned"
    assert [(r["ordinal"], r["weekday"], r["time"]) for r in truckee["rules"]] == [(2, "tuesday", "17:00"), (4, "tuesday", "17:00")]
    assert "schedule" not in written["slt"]  # The fixture's SLT start times changed; not enough support.
    capsys.readouterr()

    assert cli.main(["--database-url", "unused", "learn-schedules", "--json", "--agency", "truckee", "--registry", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dryRun"] is True and report["written"] == []
    assert [(r["agency"], r["action"]) for r in report["agencies"]] == [("truckee", "unchanged")]
    with pytest.raises(SystemExit, match="not a followed agency"):
        cli.main(["--database-url", "unused", "learn-schedules", "--agency", "not-followed"])
