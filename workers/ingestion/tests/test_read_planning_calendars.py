"""Reading agenda planning calendars into previews (IDEA-018 R4, R9): new calendars only, rejection, the export."""
import contextlib
import copy
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import pytest

from tahoe_ingest import planning_calendars, staff_reports
from tahoe_ingest.core import export_public, ingest_bundle
from tahoe_ingest.export_check import validate_export

from test_pipeline import db, fixture_bundle, statement_count  # noqa: F401  (pytest fixture)

FIXTURES = Path(__file__).resolve().parents[3] / "tests/fixtures/planning-calendars"
TODAY = date(2026, 10, 7)
WRITES = ("INSERT", "UPDATE", "DELETE")


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def bundle_of(*ids):
    source = fixture_bundle()
    source["meetings"] = [m for m in source["meetings"] if f"{m['agency']}:{m['id']}" in ids]
    return source


def attach(conn, meeting_id: str, files: list[dict]) -> None:
    """The attachment list the SLT staff report rule stores under the "Agenda Planning Calendar" item."""
    item = conn.execute("""SELECT id,source_fingerprint FROM agenda_items WHERE meeting_id=%s AND active
        AND title='Agenda Planning Calendar' ORDER BY item_order LIMIT 1""", (meeting_id,)).fetchone()
    conn.execute("""INSERT INTO item_attachments(item_id,source_fingerprint,method,files,evidence) VALUES (%s,%s,%s,%s,'{}')
        ON CONFLICT(item_id) DO UPDATE SET files=excluded.files""",
                 (item["id"], item["source_fingerprint"], staff_reports.METHOD_VERSION, json.dumps(files)))
    conn.commit()


def slt_calendar(name="slt-apc-2026-09-30"):
    data = load(name)
    return {"url": data["url"], "name": data["attachmentName"], "label": data["attachmentName"].removesuffix(".pdf")}


class Reader:
    """Stands in for staff_reports.read_pdf: fixture pages by URL, every call recorded."""

    def __init__(self, documents: dict):
        self.documents, self.calls = documents, []

    def __call__(self, url, **_):
        self.calls.append(url)
        data = self.documents[url]
        return {"sha256": data["sha256"], "page_count": data["page_count"], "pages": copy.deepcopy(data["pages"]), "links": []}


@pytest.fixture
def slt(db):
    assert ingest_bundle(db, bundle_of("slt:2339"))["failed"] == 0
    data = load("slt-apc-2026-09-30")
    attach(db, "slt:2339", [{"url": "https://slt.granicus.com/services/legistar/download/pdf/1/Item_15-_Executive_Summary.pdf",
                             "name": "Item 15- Executive Summary.pdf", "label": "Executive Summary"}, slt_calendar()])
    return Reader({data["url"]: data})


@contextlib.contextmanager
def statements(monkeypatch):
    """Every statement sent to the database, as text (a write would show as INSERT, UPDATE, or DELETE)."""
    sent = []
    original = psycopg.Cursor.execute

    def execute(self, query, *args, **kwargs):
        sent.append(query if isinstance(query, str) else query.as_string(self))
        return original(self, query, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(psycopg.Cursor, "execute", execute)
        yield sent


def writes(sent):
    return [q for q in sent if q.lstrip().split(None, 1)[0].upper() in WRITES or " INSERT INTO " in q.upper()]


def export(conn, today=TODAY):
    payload = export_public(conn, today=today)
    return payload, {m["id"]: m for m in payload["meetings"]}


def test_attachment_calendar_is_read_once_and_becomes_previews_and_planned_meetings(db, slt, monkeypatch):
    report = planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)
    assert slt.calls == [load("slt-apc-2026-09-30")["url"]]
    assert (report["calendarsFound"], report["calendarsRead"], report["calendarsUnchanged"]) == (1, 1, 0)
    assert report["rejected"] == [] and report["failed"] == [] and report["previewsInserted"] > 40
    assert set(report["plannedMeetings"]) == {f"slt:planned-2026-{d}" for d in ("10-13", "11-03", "11-17", "12-01", "12-15")}
    source = db.execute("SELECT url,title,published_on,status,jsonb_array_length(pages) AS pages FROM preview_sources").fetchone()
    assert source == {"url": load("slt-apc-2026-09-30")["url"], "title": "2026 Agenda Planning Calendar (Updated 09 30 2026)",
                      "published_on": date(2026, 9, 30), "status": "valid", "pages": 6}

    _, meetings = export(db)
    nov3, dec1 = meetings["slt:planned-2026-11-03"], meetings["slt:planned-2026-12-01"]
    assert nov3["planned"] is True and nov3["kind"] == "confirmed" and nov3["title"] == "City Council Meeting"
    assert nov3["startsAt"].startswith("2026-11-03T") and nov3["officialUrl"] == source["url"]
    assert meetings["slt:planned-2026-10-13"]["title"] == "Special City Council Meeting"
    bikes = [p for p in dec1["previews"] if "Bike" in p["subject"] or "Bicycle" in p["subject"]]
    assert len(bikes) == 2 and all("biking" in p["topics"] for p in bikes) and "biking" in dec1["topics"]
    assert bikes[0]["source"] == {"kind": "planning-calendar", "title": source["title"], "url": source["url"],
                                  "publishedOn": "2026-09-30", "quote": bikes[0]["subject"], "page": bikes[0]["source"]["page"]}
    assert "slt:2339" not in {m for m in meetings if meetings[m].get("previews")}

    # Unchanged inputs: the three lookups, no download, no write.
    with statements(monkeypatch) as sent:
        again = planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)
    assert slt.calls == [load("slt-apc-2026-09-30")["url"]]
    assert (again["calendarsRead"], again["calendarsUnchanged"], again["previewsInserted"]) == (0, 1, 0)
    assert len(sent) <= 3 and not writes(sent)


def test_older_calendars_are_never_downloaded(db, slt):
    older = load("slt-apc-2026-09-15")
    assert ingest_bundle(db, bundle_of("slt:2330"))["failed"] == 0
    attach(db, "slt:2330", [slt_calendar("slt-apc-2026-09-15")])
    slt.documents[older["url"]] = older
    report = planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)
    # Newest meeting first: once the September 30 calendar is stored, nothing an older agenda carries can replace it.
    assert slt.calls == [load("slt-apc-2026-09-30")["url"]] and report["calendarsRead"] == 1
    planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)
    assert len(slt.calls) == 1


def test_an_older_calendar_stands_in_until_the_newer_one_downloads(db, slt):
    older = load("slt-apc-2026-09-15")
    assert ingest_bundle(db, bundle_of("slt:2330"))["failed"] == 0
    attach(db, "slt:2330", [slt_calendar("slt-apc-2026-09-15")])
    newer = slt.documents.pop(load("slt-apc-2026-09-30")["url"])
    slt.documents[older["url"]] = older

    def flaky(url, **kwargs):
        if url not in slt.documents:
            raise staff_reports.FetchFailed(f"{url}: HTTP 503")
        return slt(url, **kwargs)
    first = planning_calendars.read_planning_calendars(db, reader=flaky, today=TODAY)
    assert first["failed"][0]["url"] == newer["url"] and slt.calls == [older["url"]]
    slt.documents[newer["url"]] = newer
    planning_calendars.read_planning_calendars(db, reader=flaky, today=TODAY)
    assert slt.calls == [older["url"], newer["url"]]
    current = db.execute("""SELECT DISTINCT ON (source_key) url FROM preview_sources WHERE status='valid'
        ORDER BY source_key,published_on DESC""").fetchone()
    assert current["url"] == newer["url"]


def test_dry_run_lists_calendars_without_downloads_or_writes(db, slt, monkeypatch):
    with statements(monkeypatch) as sent:
        report = planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY, dry_run=True)
    assert report["wouldRead"] == [load("slt-apc-2026-09-30")["url"]] and slt.calls == [] and not writes(sent)


def test_max_calendars_defers_downloads(db, slt):
    report = planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY, max_calendars=0)
    assert slt.calls == [] and report["deferred"] == 1
    assert planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)["calendarsRead"] == 1


def test_failed_download_is_retried_next_run(db, slt):
    def broken(url, **_):
        raise staff_reports.FetchFailed(f"{url}: HTTP 503")
    report = planning_calendars.read_planning_calendars(db, reader=broken, today=TODAY)
    assert report["failed"][0]["agency"] == "slt" and report["rejected"] == []
    assert planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)["calendarsRead"] == 1


def test_a_calendar_that_fails_validation_is_rejected_and_the_previous_one_stays(db, slt):
    planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)
    before = export(db)[1]["slt:planned-2026-12-01"]["previews"]
    tampered = copy.deepcopy(load("slt-apc-2026-09-30"))
    tampered.update(url="https://slt.granicus.com/services/legistar/download/pdf/2/APC_10_14_2026.pdf", sha256="d" * 64)
    tampered["pages"][0]["text"] = tampered["pages"][0]["text"].replace(
        "Agenda Planning Calendar (Updated 09 30 2026)", "Agenda Calendar (Updated 10 14 2026)", 1)
    slt.documents[tampered["url"]] = tampered
    attach(db, "slt:2339", [slt_calendar(), {"url": tampered["url"], "name": "APC 10 14 2026.pdf", "label": "APC 10 14 2026"}])
    report = planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)
    assert report["rejected"] == [{"agency": "slt", "url": tampered["url"], "reason": report["rejected"][0]["reason"]}]
    assert "title" in report["rejected"][0]["reason"]
    assert db.execute("SELECT status,error FROM preview_sources WHERE sha256=%s", ("d" * 64,)).fetchone()["status"] == "rejected"
    assert export(db)[1]["slt:planned-2026-12-01"]["previews"] == before
    # Stored with its reason, so it is not downloaded again.
    calls = len(slt.calls)
    assert planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)["rejected"] == []
    assert len(slt.calls) == calls


def ttd_bundle():
    """The October 7 board packet with its calendar pages, and the stored November 4 board meeting."""
    bundle = bundle_of("ttd:7110", "ttd:7172")
    data = load("ttd-packet-2026-10-07")
    october = next(m for m in bundle["meetings"] if m["id"] == "7110")
    october["pages"] += [{"document_id": "ttd-agenda-packet", "page_number": p["page_number"], "text": p["text"]}
                         for p in data["pages"]]
    return bundle


def test_packet_calendar_is_read_from_stored_pages(db, monkeypatch):
    assert ingest_bundle(db, ttd_bundle())["failed"] == 0
    def no_downloads(url, **_):
        raise AssertionError("packet calendars are read from stored pages")
    today = date(2026, 10, 8)
    report = planning_calendars.read_planning_calendars(db, reader=no_downloads, today=today)
    assert report["rejected"] == [] and report["byAgency"]["ttd"]["read"] == 1
    assert report["plannedMeetings"] == ["ttd-rtcpc:planned-2026-11-02"]
    source = db.execute("SELECT url,sha256,published_on,pages FROM preview_sources").fetchone()
    assert source["published_on"] == date(2026, 10, 7) and [p["page_number"] for p in source["pages"]] == [193, 194, 195, 196, 197]
    payload, meetings = export(db, today)
    november = meetings["ttd:7172"]
    assert "planned" not in november and "Ratification of Strategic Priorities – Action Item (Resolution)" in {
        p["subject"] for p in november["previews"]}
    committee = meetings["ttd-rtcpc:planned-2026-11-02"]
    assert committee["title"] == "TTD Regional Transit and Capital Programs Committee Meeting"
    assert {"Quarterly Parking Report", "Project Spotlight"} <= {p["subject"] for p in committee["previews"]}
    shown = [(m["agencyId"], m["date"]) for m in payload["meetings"] if m.get("previews")]
    assert sorted(shown) == [("ttd", "2026-11-04"), ("ttd-rtcpc", "2026-11-02")]  # nothing for TTC, F&P, or December 2
    assert not any(m.get("planned") for m in payload["meetings"] if m["date"] == "2026-12-02")

    with statements(monkeypatch) as sent:
        again = planning_calendars.read_planning_calendars(db, reader=no_downloads, today=today)
    assert again["calendarsUnchanged"] == 1 and again["calendarsRead"] == 0
    assert len(sent) <= 3 and not writes(sent)


def test_export_attaches_previews_only_before_the_agenda_and_checks_pass(db, slt, monkeypatch):
    from tahoe_ingest import core
    assert ingest_bundle(db, bundle_of("slt:2351"))["failed"] == 0  # October 13: its agenda is posted
    planning_calendars.read_planning_calendars(db, reader=slt, today=TODAY)
    monkeypatch.setattr(core, "now", lambda: datetime(2026, 10, 7, 19, tzinfo=timezone.utc))
    payload = export_public(db)
    meetings = {m["id"]: m for m in payload["meetings"]}
    errors, _ = validate_export(payload)
    assert errors == []
    assert "slt:planned-2026-10-13" not in meetings and "previews" not in meetings["slt:2351"]
    assert [m["date"] for m in payload["meetings"] if m.get("previews")] == ["2026-11-03", "2026-11-17", "2026-12-01", "2026-12-15"]
    text = json.dumps([m.get("previews") for m in payload["meetings"]])
    assert not re.search(r"\b[A-Z]{2}/(?:[A-Z]{2})?\b", text), "requester initials leaked into a preview"
    # Once a planned meeting is past with nothing captured it is left out; one nothing supports any more is too.
    monkeypatch.setattr(core, "now", lambda: datetime(2026, 11, 4, 19, tzinfo=timezone.utc))
    later = {m["id"]: m for m in export_public(db)["meetings"]}
    assert "slt:planned-2026-11-03" not in later and later["slt:planned-2026-11-17"]["previews"]
    db.execute("DELETE FROM preview_sources")
    assert not any(m.get("planned") for m in export_public(db)["meetings"])


def test_preview_checks_reject_what_the_export_must_not_show():
    from tahoe_ingest.export_check import check_previews
    preview = {"subject": "Bike lanes", "section": None, "topics": ["biking"],
               "source": {"kind": "planning-calendar", "title": "Agenda Planning Calendar", "url": "https://example.gov/apc.pdf",
                          "publishedOn": "2026-09-30", "quote": "Bike lanes", "page": 1}}
    meeting = {"id": "slt:planned-2026-12-01", "kind": "confirmed", "date": "2026-12-01", "planned": True, "topics": ["biking"],
               "items": [], "documents": [], "previews": [preview]}
    data = {"generatedAt": "2026-10-07T19:00:00Z", "topics": [{"id": "biking"}], "meetings": [meeting]}
    assert check_previews(data) == []

    def errors(**changes):
        changed = copy.deepcopy(data)
        for key, value in changes.items():
            target, _, field = key.rpartition("__")
            holder = changed["meetings"][0] if not target else changed["meetings"][0]["previews"][0] if target == "preview" \
                else changed["meetings"][0]["previews"][0]["source"]
            holder[field] = value
        return check_previews(changed)
    assert errors(date="2026-10-07")  # on the export day
    assert errors(items=[{"id": "x"}])
    assert errors(documents=[{"id": "d", "title": "Agenda Packet"}])
    assert not errors(documents=[{"id": "d", "title": "Agenda supplement"}])
    assert errors(source__url=None) and errors(source__quote="") and errors(source__kind="rumour")
    assert errors(source__publishedOn="2026-12-01")  # dated on its source's publication date
    assert errors(preview__topics=["not-a-topic"])
    assert errors(previews=[]) and not errors(previews=[], documents=[{"id": "d", "title": "Notice"}])  # planned needs one
    assert errors(kind="forecast")
    assert errors(previewNotOnAgenda=["West Shore Trail"])  # only on meetings with items


def test_unknown_agency_is_refused(db):
    with pytest.raises(SystemExit):
        planning_calendars.read_planning_calendars(db, agency="truckee", today=TODAY)
