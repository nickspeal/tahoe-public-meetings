"""Preview storage (IDEA-018 R5, R6): versions and replacement, planned meetings, subject topics, operation budget."""
import json
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from tahoe_ingest import previews as pv
from tahoe_ingest.core import ingest_bundle
from tahoe_ingest.evaluate_topics import ModelReply
from tahoe_ingest.previews import Preview, PreviewSource

from test_pipeline import db, statement_count  # noqa: F401  (pytest fixture)

TODAY = date(2026, 10, 29)
KEY = "slt:planning-calendar"
BIKES = "Prioritization of Bikes at City Intersections and Bike Lanes"
MOVED = "Tahoe Valley Area Plan Amendments"


def source(published: date, tag: str) -> PreviewSource:
    stamp = published.strftime("%m %d %Y")
    return PreviewSource(kind="planning-calendar", key=KEY, sha256=tag * 64,
                         url=f"https://slt.granicus.com/services/legistar/download/pdf/{tag}/APC {stamp}.pdf",
                         title=f"Agenda Planning Calendar (Updated {stamp})", published_on=published,
                         pages=[{"page_number": 1, "text": f"2026 Agenda Planning Calendar (Updated {stamp})"}])


def preview(day, subject, **fields) -> Preview:
    return Preview(agency_id="slt", local_date=day, month=None, subject=subject, quote=subject, page=1,
                   meeting_title="City Council Meeting", **fields)


def september_15():
    return source(date(2026, 9, 15), "a"), [
        preview(date(2026, 9, 10), "Already past when published"),
        preview(date(2026, 10, 20), "Before today"),
        preview(date(2026, 11, 3), "Pedestrian Only Zone Ordinance (First Reading)", start_time="09:30", section="New business"),
        preview(date(2026, 11, 17), MOVED, start_time="17:30", section="Public hearing"),
        preview(date(2026, 11, 17), "Rejected line", rejection="subject is not a quote from the page"),
        preview(date(2026, 12, 15), "Holiday recognition", special=True),
        Preview(agency_id="slt", local_date=None, month=date(2027, 1, 1), subject="VHR permitting status update",
                quote="VHR permitting status update", page=2),
        Preview(agency_id="slt", local_date=None, month=None, pending=True, subject="Snow removal policy",
                quote="Snow removal policy", page=3),
    ]


def september_30():
    return source(date(2026, 9, 30), "b"), [
        preview(date(2026, 9, 30), "Dated on the publication day"),
        preview(date(2026, 11, 3), "Pedestrian Only Zone Ordinance (First Reading)", start_time="09:30", section="New business"),
        preview(date(2026, 11, 17), "Cannabis retail ordinance"),
        preview(date(2026, 12, 1), BIKES, start_time="09:00", section="New business"),
        preview(date(2026, 12, 15), MOVED, section="Public hearing"),
    ]


def shown(conn):
    return {(agency, day): [p["subject"] for p in rows] for (agency, day), rows in pv.current_previews(conn).items()}


def test_replace_stores_rows_creates_planned_meetings_and_reruns_without_writes(db, monkeypatch):
    # A captured meeting on November 17 already covers that date.
    stored = {"meetings": [{"agency": "slt", "id": "9999", "title": "City Council Meeting", "date": "2026-11-17",
                            "start_time": "17:30", "status": "scheduled", "source_meeting_url": "https://slt.example/9999"}]}
    assert ingest_bundle(db, stored)["failed"] == 0
    src, rows = september_15()
    result = pv.replace_source(db, src, rows, today=TODAY)
    assert result == {"inserted": 8, "current": True, "unchanged": False, "pruned": 0,
                      "planned": ["slt:planned-2026-11-03", "slt:planned-2026-12-15"]}
    meetings = {m["id"]: m for m in db.execute("SELECT * FROM meetings WHERE source_id LIKE 'planned-%'")}
    assert set(meetings) == {"slt:planned-2026-11-03", "slt:planned-2026-12-15"}
    nov3 = meetings["slt:planned-2026-11-03"]
    assert nov3["title"] == "City Council Meeting" and nov3["status"] == "scheduled" and nov3["change"] == "new"
    assert nov3["starts_at"] == datetime(2026, 11, 3, 9, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert nov3["warnings"] == ["Subject to change."] and nov3["official_url"] == "https://www.cityofslt.gov/84/Watch-City-CouncilCommission-Meetings"
    assert pv.is_planned(nov3)
    dec15 = meetings["slt:planned-2026-12-15"]
    assert dec15["title"] == "Special City Council Meeting" and dec15["starts_at"] is None
    # Topic tags from the rules at insert time.
    tags = {r["subject"]: r["topics"] for r in db.execute("SELECT subject,topics FROM meeting_previews")}
    assert tags["Pedestrian Only Zone Ordinance (First Reading)"] == ["biking"]

    with statement_count(monkeypatch) as again:
        rerun = pv.replace_source(db, src, rows, today=TODAY)
    assert rerun["unchanged"] and rerun["current"] and rerun["inserted"] == 0 and rerun["planned"] == []
    assert again["n"] == 1  # the version lookup only
    assert db.execute("SELECT count(*) AS n FROM meetings WHERE source_id LIKE 'planned-%'").fetchone()["n"] == 2


def test_current_previews_is_one_query_and_shows_only_dated_valid_rows_after_publication(db, monkeypatch):
    src, rows = september_15()
    pv.replace_source(db, src, rows, today=TODAY)
    with statement_count(monkeypatch) as count:
        found = pv.current_previews(db)
    assert count["n"] == 1
    assert {k: [p["subject"] for p in v] for k, v in found.items()} == {
        ("slt", date(2026, 10, 20)): ["Before today"],  # stored and shown by date; the export hides past meetings
        ("slt", date(2026, 11, 3)): ["Pedestrian Only Zone Ordinance (First Reading)"],
        ("slt", date(2026, 11, 17)): [MOVED],
        ("slt", date(2026, 12, 15)): ["Holiday recognition"]}
    first = found[("slt", date(2026, 11, 3))][0]
    assert first == {"subject": "Pedestrian Only Zone Ordinance (First Reading)", "section": "New business",
                     "topics": ["biking"], "quote": "Pedestrian Only Zone Ordinance (First Reading)", "page": 1,
                     "source_kind": "planning-calendar", "source_key": KEY, "source_url": src.url,
                     "source_title": src.title, "published_on": date(2026, 9, 15), "start_time": "09:30",
                     "location": None, "remote_quote": None, "special": False, "meeting_title": "City Council Meeting"}
    # Month-only and pending rows are stored, not shown.
    assert db.execute("SELECT count(*) AS n FROM meeting_previews WHERE local_date IS NULL").fetchone()["n"] == 2


def test_newer_calendar_replaces_older_and_moves_items(db):
    pv.replace_source(db, *september_15(), today=TODAY)
    newer = pv.replace_source(db, *september_30(), today=TODAY)
    assert newer["current"] and newer["planned"] == ["slt:planned-2026-12-01"]  # other dates already planned
    assert shown(db) == {
        ("slt", date(2026, 11, 3)): ["Pedestrian Only Zone Ordinance (First Reading)"],
        ("slt", date(2026, 11, 17)): ["Cannabis retail ordinance"],
        ("slt", date(2026, 12, 1)): [BIKES],
        ("slt", date(2026, 12, 15)): [MOVED]}  # moved from November 17
    # The older version is kept as the previous one, not shown.
    assert db.execute("SELECT count(*) AS n FROM preview_sources").fetchone()["n"] == 2


def test_older_capture_ingested_later_does_not_displace_the_newer(db, monkeypatch):
    pv.replace_source(db, *september_30(), today=TODAY)
    before = shown(db)
    older = pv.replace_source(db, *september_15(), today=TODAY)
    assert not older["current"] and older["inserted"] == 8 and older["planned"] == []
    assert shown(db) == before
    with statement_count(monkeypatch) as again:
        assert pv.replace_source(db, *september_15(), today=TODAY)["unchanged"]
    assert again["n"] == 1


def test_only_the_current_and_one_previous_version_are_kept(db, monkeypatch):
    pv.replace_source(db, *september_15(), today=TODAY)
    pv.replace_source(db, *september_30(), today=TODAY)
    october = source(date(2026, 10, 14), "c")
    result = pv.replace_source(db, october, [preview(date(2026, 11, 3), "Budget workshop")], today=TODAY)
    assert result["current"] and result["pruned"] == 1
    assert {r["sha256"][0] for r in db.execute("SELECT sha256 FROM preview_sources")} == {"b", "c"}
    assert {r["source_sha256"][0] for r in db.execute("SELECT DISTINCT source_sha256 FROM meeting_previews")} == {"b", "c"}
    assert shown(db) == {("slt", date(2026, 11, 3)): ["Budget workshop"]}
    # A third, older capture would be pruned at once, so it is not written at all.
    with statement_count(monkeypatch) as count:
        stale = pv.replace_source(db, *september_15(), today=TODAY)
    assert stale["unchanged"] and stale.get("superseded") and count["n"] == 1


def test_rejected_calendar_leaves_the_previous_one_current(db):
    pv.replace_source(db, *september_30(), today=TODAY)
    before = shown(db)
    october = source(date(2026, 10, 14), "c")
    pv.reject_source(db, october, "date heading did not parse: 'Novmber 3'")
    stored = db.execute("SELECT status,error FROM preview_sources WHERE sha256=%s", (october.sha256,)).fetchone()
    assert stored == {"status": "rejected", "error": "date heading did not parse: 'Novmber 3'"}
    assert shown(db) == before
    # A later valid capture of the same file replaces the rejection.
    result = pv.replace_source(db, october, [preview(date(2026, 11, 3), "Budget workshop")], today=TODAY)
    assert result["current"] and result["inserted"] == 1
    assert db.execute("SELECT status,error FROM preview_sources WHERE sha256=%s", (october.sha256,)).fetchone() == {
        "status": "valid", "error": None}
    assert shown(db) == {("slt", date(2026, 11, 3)): ["Budget workshop"]}


def test_withdrawn_truth_table():
    planned = {"source_id": "planned-2026-11-03", "status": "scheduled", "change": "new"}
    assert pv.planned_source_id(date(2026, 11, 3)) == "planned-2026-11-03"
    assert pv.withdrawn(planned, has_previews=False, has_documents=False, has_items=False)
    assert not pv.withdrawn(planned, has_previews=True, has_documents=False, has_items=False)
    assert not pv.withdrawn(planned, has_previews=False, has_documents=True, has_items=False)
    assert not pv.withdrawn(planned, has_previews=False, has_documents=False, has_items=True)
    assert not pv.withdrawn({**planned, "status": "canceled"}, has_previews=False, has_documents=False, has_items=False)
    assert not pv.withdrawn({**planned, "change": "rescheduled"}, has_previews=False, has_documents=False, has_items=False)
    assert pv.withdrawn({**planned, "status": "unknown"}, has_previews=False, has_documents=False, has_items=False)
    assert not pv.withdrawn({**planned, "source_id": "2339"}, has_previews=False, has_documents=False, has_items=False)


def test_subject_key_and_planned_title():
    assert pv.subject_key("  Prioritization of Bikes,  at City\nIntersections (Bike-Lanes) ") == \
        "prioritization of bikes at city intersections bike lanes"
    assert pv.planned_title(Preview(agency_id="ttd-rtcpc", local_date=None, month=None, subject="x", quote="x", special=True),
                            "TTD — Regional Transit and Capital Programs Committee") == \
        "Special TTD — Regional Transit and Capital Programs Committee meeting"


class FakeModel:
    """Answers like Claude: biking for bike subjects, hot topics for the pedestrian zone, nothing otherwise."""

    def __init__(self, model):
        self.model, self.calls, self.subjects = model, 0, []

    def complete(self, *, system, user, schema, max_tokens):
        self.calls += 1
        entries = []
        for ref, body in re.findall(r'<item ref="(i\d+)">\n(.*?)</item>', user, re.S):
            title = re.search(r"Title: (.*)", body).group(1)
            self.subjects.append(title)
            topics = ["biking"] if "Bike" in title else ["hot-topics"] if "Pedestrian" in title else []
            entries.append({"ref": ref, "reason": "fake", "tahoeRelevant": True, "topics": topics})
        usage = {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        return ModelReply(text=json.dumps({"items": entries}), stop_reason="end_turn", usage=usage, model=self.model)


def test_classify_subjects_tags_new_subjects_and_reuses_the_cache(db, monkeypatch):
    pv.replace_source(db, *september_30(), today=TODAY)
    haiku = FakeModel("claude-haiku-5-5")
    clients = {"claude-haiku-5-5": haiku, "claude-sonnet-5-5": FakeModel("claude-sonnet-5-5")}
    report = pv.classify_subjects(db, max_cost=1.0, monthly_budget=5.0, clients=clients)
    # Rows dated on or before publication are never shown, so never sent.
    assert sorted(haiku.subjects) == sorted([BIKES, MOVED, "Cannabis retail ordinance",
                                             "Pedestrian Only Zone Ordinance (First Reading)"])
    assert haiku.calls == 1 and report["classified"] == {"claude-haiku-5-5": 4} and report["failed"] == []
    assert report["previewsUpdated"] == 1  # only the pedestrian zone's tags changed from the rule tags
    assert [(u["purpose"], u["ok"]) for u in db.execute("SELECT purpose,ok FROM ai_usage")] == [("subject-classification", True)]
    tags = {p["subject"]: p["topics"] for rows in pv.current_previews(db).values() for p in rows}
    assert tags[BIKES] == ["biking"]
    assert tags["Pedestrian Only Zone Ordinance (First Reading)"] == ["hot-topics"]  # Claude's tags replace the rule tags

    # A later calendar repeating the same subjects takes the cached tags at insert time; nothing new is sent.
    october = source(date(2026, 10, 14), "c")
    pv.replace_source(db, october, [preview(date(2026, 12, 1), BIKES),
                                    preview(date(2026, 11, 3), "Pedestrian Only Zone Ordinance (First Reading)")],
                      today=TODAY)
    assert {p["subject"]: p["topics"] for rows in pv.current_previews(db).values() for p in rows} == {
        BIKES: ["biking"], "Pedestrian Only Zone Ordinance (First Reading)": ["hot-topics"]}
    with statement_count(monkeypatch) as count:
        again = pv.classify_subjects(db, max_cost=1.0, monthly_budget=5.0, clients=clients)
    assert again["subjectsPending"] == 0 and haiku.calls == 1 and again["previewsUpdated"] == 0
    assert count["n"] == 2  # rows and cache, no writes


def test_classify_subjects_without_credentials_is_skipped(db, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_IDENTITY_TOKEN_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pv, "has_credentials", lambda: False)
    pv.replace_source(db, *september_30(), today=TODAY)
    report = pv.classify_subjects(db, max_cost=1.0, monthly_budget=5.0)
    assert report["skipped"] and report["subjectsPending"] == 4 and report["costUsd"] == 0
    dry = pv.classify_subjects(db, max_cost=1.0, monthly_budget=5.0, dry_run=True)
    assert dry["dryRun"] and dry["estimatedCostUsd"] > 0


def test_notice_previews_keep_seeded_topics(db):
    notice = PreviewSource(kind="notice", key="other:west-shore-trail-nop", sha256="d" * 64,
                           url="https://www.trpa.gov/notice.pdf", title="West Shore Trail Notice of Preparation",
                           published_on=date(2026, 10, 27))
    seeded = Preview(agency_id="trpa-advisory", local_date=date(2026, 11, 12), month=None,
                     subject="West Shore Trail – Meeks Bay to D.L. Bliss", quote="presented at the TRPA Advisory Planning Commission",
                     start_time="09:30", location="128 Market Street, Stateline, Nevada",
                     remote_quote="with a virtual attendance option", topics=["environment"],
                     meeting_title="TRPA Advisory Planning Commission Meeting")
    result = pv.replace_source(db, notice, [seeded], today=TODAY)
    assert result["planned"] == ["trpa-advisory:planned-2026-11-12"]
    row = next(iter(pv.current_previews(db).values()))[0]
    assert row["topics"] == ["environment"]  # the comment period's topics, seeded by the caller
    meeting = db.execute("SELECT * FROM meetings WHERE id='trpa-advisory:planned-2026-11-12'").fetchone()
    assert meeting["location"] == "128 Market Street, Stateline, Nevada"
    assert meeting["participation"]["instructions"] == "with a virtual attendance option"
    assert meeting["title"] == "TRPA Advisory Planning Commission Meeting"
