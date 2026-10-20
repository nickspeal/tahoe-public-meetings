"""Meeting-length model, observed durations, minutes-text detection, and export."""
import pytest

from tahoe_ingest.cli import main
from tahoe_ingest.core import export_public, ingest_bundle
from tahoe_ingest.duration_ops import detect_from_minutes, duration_report, set_duration
from tahoe_ingest.durations import detect_duration, duration_config, estimate_duration, validate

from test_pipeline import db, fixture_bundle  # noqa: F401  (pytest fixture)


def items(*pairs):
    return [{"section": s, "title": t} for s, t in pairs]


def test_shipped_config_is_valid():
    validate(duration_config())


def test_estimate_is_long_biased_rounded_up_and_clamped():
    cfg = {"version": 1, "defaults": {"mode": "estimated", "baseMinutes": 60, "perItemMinutes": 8, "fixedMinutes": 120,
           "sectionWeights": {"consent": 1, "public hearing": 25}, "minMinutes": 60, "maxMinutes": 360,
           "roundUpToMinutes": 30, "longBiasFactor": 1.25}, "agencies": {}}
    agenda = items(("Consent Agenda", "Approve minutes"), ("Business", "Public Hearing on code"), ("Business", "Contract"))
    result = estimate_duration("x", agenda, cfg)
    # (60 + 1 + 25 + 8) * 1.25 = 117.5 -> 120
    assert result["minutes"] == 120
    assert "3 agenda items including 1 public hearing" in result["basis"]
    assert estimate_duration("x", items(*[("B", "t")] * 100), cfg)["minutes"] == 360  # clamped
    assert estimate_duration("x", [], cfg)["minutes"] == 90  # 60*1.25=75 -> 90, never short of the minimum


def test_fixed_mode_and_agency_override():
    cfg = duration_config()
    ttd = estimate_duration("ttd", [], cfg)
    assert ttd["minutes"] == 210 and "usually runs about 2 h 30 min" in ttd["basis"]
    assert estimate_duration("ttd", items(("A", "x")) * 40, cfg)["minutes"] == 210  # agenda size is irrelevant


def test_detector_requires_both_unambiguous_times():
    ok = detect_duration("The meeting was called to order at 9:03 a.m.\n...\nThe meeting was adjourned at 12:15 p.m.")
    assert ok["minutes"] == 192
    assert detect_duration("Called to order at 6:00 PM. Adjourned at 8:45 P.M.")["minutes"] == 165
    assert detect_duration("Called to order at 9:00 a.m.")["minutes"] is None
    assert detect_duration("Called to order at 9:00 a.m. Adjourned at 9:02 a.m.")["minutes"] is None  # implausible
    assert detect_duration("Called to order at 9:00 a.m. Reconvened and called to order at 1:00 p.m. Adjourned at 3:00 p.m.")["minutes"] is None
    assert detect_duration("Item 9. Adjournment")["minutes"] is None


def test_observed_wins_in_export_and_report_and_detection(db):
    ingest_bundle(db, fixture_bundle())
    exported = export_public(db)
    assert all(m["duration"]["minutes"] > 0 and m["duration"]["basis"] for m in exported["meetings"])
    assert {m["duration"]["source"] for m in exported["meetings"]} == {"estimated"}
    target = exported["meetings"][0]
    set_duration(db, target["id"], 95, "recording")
    changed = next(m for m in export_public(db)["meetings"] if m["id"] == target["id"])
    assert changed["duration"]["minutes"] == 95 and changed["duration"]["source"] == "observed"
    with pytest.raises(ValueError):
        set_duration(db, target["id"], 95, "guess")
    with pytest.raises(ValueError):
        set_duration(db, "nope", 95, "manual")
    report = duration_report(db)
    assert report["calibrated"][0]["agency"] == target["agencyId"] and report["calibrated"][0]["count"] == 1

    # Minutes-text detection: dry run writes nothing; apply never overwrites a recorded value.
    other = exported["meetings"][1]
    version = db.execute("SELECT v.id FROM document_current c JOIN document_versions v ON v.id=c.version_id WHERE c.meeting_id=%s LIMIT 1",
                         (other["id"],)).fetchone()
    db.execute("UPDATE document_versions SET title='Approved minutes' WHERE id=%s", (version["id"],))
    db.execute("DELETE FROM document_pages WHERE document_version_id=%s", (version["id"],))
    db.execute("INSERT INTO document_pages VALUES (%s,1,'Called to order at 9:00 a.m. Adjourned at 11:30 a.m.')", (version["id"],))
    dry = detect_from_minutes(db)
    assert dry["detected"] == 1 and dry["dryRun"]
    assert db.execute("SELECT observed_duration_minutes FROM meetings WHERE id=%s", (other["id"],)).fetchone()["observed_duration_minutes"] is None
    detect_from_minutes(db, apply=True)
    row = db.execute("SELECT observed_duration_minutes AS m, duration_source AS s FROM meetings WHERE id=%s", (other["id"],)).fetchone()
    assert (row["m"], row["s"]) == (150, "minutes-text")


def test_cli_rejects_bad_source():
    with pytest.raises(SystemExit):
        main(["set-duration", "x:1", "--minutes", "30", "--source", "guess"])
