"""Agenda planning calendars: previews from each agency's tentative calendar of future agenda items (IDEA-018 R4).

Every registry entry with a ``planningCalendar`` block names a format in ``planning_calendar_rules``. Where the
calendar is depends on what the format's ``locate`` reads, which its first parameter names:

- ``items``: the agenda's items with the attachment lists the staff report step stored (``item_attachments``). Each
  calendar URL not yet stored is downloaded once, temporarily, and its page text, hash, and URL are kept
  (decision 0004). South Lake Tahoe attaches "APC MM DD YYYY.pdf" to its "Agenda Planning Calendar" item.
- ``pages``: the stored pages of the meeting's current agenda packet, so nothing is downloaded. TTD prints its
  calendar on the last pages of the board packet. A packet whose hash is already a stored version is not read again.

Each calendar is parsed whole (``parse``) and stored through ``previews.replace_source``, or, when it fails
validation, through ``previews.reject_source`` with the reason, which leaves the previous calendar's previews
current and is named in the run summary.

Only calendars that could become current are read. A calendar is never published after the meeting whose agenda
carries it, so meetings dated before the current calendar's publication date are skipped, and meetings are read
newest first: once a valid calendar is stored, older meetings in the same run are skipped too. Unchanged inputs
cost three lookups (stored versions, attachment lists, packet documents) and no downloads or writes.
"""
from __future__ import annotations

import inspect
import re
import subprocess
import time
from datetime import date, timedelta

from . import previews
from .http import EndpointSkipped
from .planning_calendar_rules import CalendarInvalid, agency_rules, first_line
from .staff_reports import FetchFailed, Uncertain, read_pdf

KIND = "planning-calendar"
WINDOW_DAYS = 120  # meetings this far back are searched for a calendar when no calendar is stored yet
DEFAULT_MAX_CALENDARS = 4  # downloads per run; South Lake Tahoe posts about two calendars a month
PACKET = re.compile(r"packet", re.I)
SOURCES = ("items", "pages")


def source_key(agency_id: str) -> str:
    return f"{agency_id}:{KIND}"


def source_type(rule) -> str:
    """What the format's ``locate`` reads: ``items`` (agenda items with attachments) or ``pages`` (packet pages)."""
    first = next(iter(inspect.signature(rule.locate).parameters), None)
    if first not in SOURCES:
        raise TypeError(f"planning calendar format {rule.format}: locate must take {' or '.join(SOURCES)} first")
    return first


def _stored(conn, keys: list[str]) -> dict[str, dict]:
    """Per source key: stored URLs and hashes (valid or rejected) and the current calendar's publication date."""
    found = {key: {"urls": set(), "shas": set(), "floor": None} for key in keys}
    for r in conn.execute("""SELECT source_key,url,sha256,status,published_on FROM preview_sources
            WHERE source_kind=%s AND source_key=ANY(%s)""", (KIND, keys)):
        entry = found[r["source_key"]]
        entry["urls"].add(r["url"])
        entry["shas"].add(r["sha256"])
        if r["status"] == "valid" and (entry["floor"] is None or r["published_on"] > entry["floor"]):
            entry["floor"] = r["published_on"]
    return found


def _attachment_meetings(conn, floors: dict[str, date]) -> dict[str, list[dict]]:
    """Per agency, newest first: ``{"id", "local_date", "items": [{"title", "attachments"}]}``; one query."""
    meetings: dict[str, dict] = {}
    if not floors:
        return {}
    for r in conn.execute("""SELECT m.id,m.agency_id,m.local_date,i.title,a.files FROM meetings m
            JOIN unnest(%s::text[],%s::date[]) AS f(agency_id,since) ON f.agency_id=m.agency_id AND m.local_date>=f.since
            JOIN agenda_items i ON i.meeting_id=m.id AND i.active
            JOIN item_attachments a ON a.item_id=i.id
            WHERE m.quarantine_reason IS NULL ORDER BY m.local_date DESC,m.id,i.item_order,i.id""",
                          (list(floors), list(floors.values()))):
        meeting = meetings.setdefault(r["id"], {"id": r["id"], "agency_id": r["agency_id"], "local_date": r["local_date"],
                                                "items": []})
        meeting["items"].append({"title": r["title"], "attachments": list(r["files"] or [])})
    grouped: dict[str, list[dict]] = {}
    for meeting in meetings.values():
        grouped.setdefault(meeting["agency_id"], []).append(meeting)
    return grouped


def _packet_meetings(conn, floors: dict[str, date]) -> dict[str, list[dict]]:
    """Per agency, newest first: ``{"id", "local_date", "packets": [version rows]}``; one query.

    The packet is the current PDF whose title or source document ID says "packet" (as the TTD staff report rule
    chooses it); a meeting that posts a single PDF has it as both agenda and packet.
    """
    meetings: dict[str, dict] = {}
    if not floors:
        return {}
    for r in conn.execute("""SELECT m.id,m.agency_id,m.local_date,v.id AS version_id,v.source_document_id,v.title,v.url,
                v.sha256,v.media_type FROM meetings m
            JOIN unnest(%s::text[],%s::date[]) AS f(agency_id,since) ON f.agency_id=m.agency_id AND m.local_date>=f.since
            JOIN document_current c ON c.meeting_id=m.id JOIN document_versions v ON v.id=c.version_id
            WHERE m.quarantine_reason IS NULL ORDER BY m.local_date DESC,m.id,v.source_document_id""",
                          (list(floors), list(floors.values()))):
        meeting = meetings.setdefault(r["id"], {"id": r["id"], "agency_id": r["agency_id"], "local_date": r["local_date"],
                                                "documents": []})
        if r["media_type"] == "application/pdf":
            meeting["documents"].append(dict(r))
    grouped: dict[str, list[dict]] = {}
    for meeting in meetings.values():
        named = [d for d in meeting["documents"] if PACKET.search(f"{d['source_document_id']} {d['title']}")]
        meeting["packets"] = named or (meeting["documents"] if len(meeting["documents"]) == 1 else [])
        grouped.setdefault(meeting["agency_id"], []).append(meeting)
    return grouped


def _pages(conn, version_ids: list[str]) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    if not version_ids:
        return found
    for r in conn.execute("""SELECT document_version_id,page_number,text FROM document_pages
            WHERE document_version_id=ANY(%s) ORDER BY document_version_id,page_number""", (version_ids,)):
        found.setdefault(r["document_version_id"], []).append({"page_number": r["page_number"], "text": r["text"]})
    return found


class _Reading:
    """One run's counters and the shared store step."""

    def __init__(self, conn, today: date):
        self.conn, self.today = conn, today
        self.report = {"calendarsFound": 0, "calendarsRead": 0, "calendarsUnchanged": 0, "previewsInserted": 0,
                       "plannedMeetings": [], "rejected": [], "failed": [], "byAgency": {}}

    def agency(self, agency_id: str) -> dict:
        return self.report["byAgency"].setdefault(agency_id, {"found": 0, "read": 0, "unchanged": 0, "inserted": 0,
                                                              "planned": 0, "rejected": 0})

    def count(self, agency_id: str, name: str, n: int = 1) -> None:
        self.report[{"found": "calendarsFound", "read": "calendarsRead", "unchanged": "calendarsUnchanged"}[name]] += n
        self.agency(agency_id)[name] += n

    def store(self, rule, *, carried_by: date, sha256: str, url: str, pages: list[dict]) -> date | None:
        """Parse and store one calendar; returns its publication date when it was stored as valid."""
        key = source_key(rule.agency_id)
        totals = self.agency(rule.agency_id)
        try:
            parsed = rule.parse(pages, carried_by=carried_by)
            source = previews.PreviewSource(kind=KIND, key=key, sha256=sha256, url=url, title=parsed["title"],
                                            published_on=parsed["published_on"], pages=pages)
            result = previews.replace_source(self.conn, source, [previews.Preview(**e) for e in parsed["entries"]],
                                             today=self.today)
        except (CalendarInvalid, ValueError) as exc:
            self.conn.rollback()
            reason = getattr(exc, "reason", None) or str(exc) or type(exc).__name__
            title = (first_line(pages[0]) if pages else "") or rule.config["title"]
            previews.reject_source(self.conn, previews.PreviewSource(
                kind=KIND, key=key, sha256=sha256, url=url, title=title[:300], published_on=carried_by, pages=pages), reason)
            self.conn.commit()
            self.report["rejected"].append({"agency": rule.agency_id, "url": url, "reason": reason})
            totals["rejected"] += 1
            return None
        self.conn.commit()
        self.report["previewsInserted"] += result["inserted"]
        self.report["plannedMeetings"] += result["planned"]
        totals["inserted"] += result["inserted"]
        totals["planned"] += len(result["planned"])
        for error in result.get("errors", []):
            self.report["failed"].append({"agency": rule.agency_id, "url": url, "reason": f"planned meeting: {error}"})
        return parsed["published_on"]


def read_planning_calendars(conn, *, agency: str | None = None, dry_run: bool = False, reader=read_pdf,
                            delay: float = 0.0, today: date | None = None, max_calendars: int | None = None) -> dict:
    """Read every configured agency's new planning calendars and store their previews (see the module docstring).

    ``reader(url)`` downloads a PDF temporarily (``staff_reports.read_pdf``); ``max_calendars`` bounds downloads
    per run (the rest wait for the next run); ``dry_run`` lists what would be read, with no downloads or writes.
    """
    today = today or date.today()
    rules = agency_rules()
    if agency is not None:
        if agency not in rules:
            raise SystemExit(f"{agency}: no planningCalendar in the registry (configured: {', '.join(sorted(rules)) or 'none'})")
        rules = {agency: rules[agency]}
    reading = _Reading(conn, today)
    report = reading.report
    report["dryRun"] = dry_run
    if dry_run:
        report["wouldRead"] = []
    if not rules:
        return report
    stored = _stored(conn, [source_key(a) for a in rules])
    since = today - timedelta(days=WINDOW_DAYS)
    floors = {a: max(since, stored[source_key(a)]["floor"] or since) for a in rules}
    by_type = {kind: {a: floors[a] for a, rule in rules.items() if source_type(rule) == kind} for kind in SOURCES}
    attachment_meetings = _attachment_meetings(conn, by_type["items"])
    packet_meetings = _packet_meetings(conn, by_type["pages"])
    conn.commit()  # no transaction stays open while calendars download

    downloads = 0
    for agency_id, rule in sorted(rules.items()):
        seen = stored[source_key(agency_id)]
        floor = floors[agency_id]
        reading.agency(agency_id)
        if source_type(rule) == "items":
            for meeting in attachment_meetings.get(agency_id, []):
                for found in rule.locate(meeting["items"]):
                    reading.count(agency_id, "found")
                    if found["url"] in seen["urls"]:
                        reading.count(agency_id, "unchanged")
                        continue
                    if meeting["local_date"] < floor:
                        continue  # a calendar stored earlier in this run is newer than anything this meeting carries
                    if dry_run:
                        report["wouldRead"].append(found["url"])
                        continue
                    if max_calendars is not None and downloads >= max_calendars:
                        report["deferred"] = report.get("deferred", 0) + 1
                        continue
                    if downloads and delay:
                        time.sleep(delay)
                    downloads += 1
                    try:
                        pdf = reader(found["url"])
                    except (FetchFailed, EndpointSkipped, Uncertain, subprocess.SubprocessError) as exc:
                        report["failed"].append({"agency": agency_id, "url": found["url"], "reason": str(exc)[:300]})
                        continue
                    reading.count(agency_id, "read")
                    seen["urls"].add(found["url"])
                    published = reading.store(rule, carried_by=meeting["local_date"], sha256=pdf["sha256"],
                                              url=found["url"], pages=pdf["pages"])
                    if published is not None:
                        floor = max(floor, published)
        else:
            meetings = packet_meetings.get(agency_id, [])
            pending = []
            for meeting in meetings:
                for packet in meeting["packets"]:
                    if packet["sha256"] in seen["shas"]:
                        reading.count(agency_id, "found")
                        reading.count(agency_id, "unchanged")
                    elif packet["sha256"] not in {p["sha256"] for _, p in pending}:
                        pending.append((meeting, packet))
            if dry_run:
                report["wouldRead"] += [packet["url"] for _, packet in pending]
                continue
            pages = _pages(conn, [packet["version_id"] for _, packet in pending])
            conn.commit()
            for meeting, packet in pending:
                if meeting["local_date"] < floor:
                    continue
                calendar = rule.locate(pages.get(packet["version_id"], []))
                if not calendar:
                    continue  # this packet carries no calendar; it is checked again only while it is the newest
                reading.count(agency_id, "found")
                reading.count(agency_id, "read")
                seen["shas"].add(packet["sha256"])
                published = reading.store(rule, carried_by=meeting["local_date"], sha256=packet["sha256"],
                                          url=packet["url"], pages=calendar)
                if published is not None:
                    floor = max(floor, published)
    return report
