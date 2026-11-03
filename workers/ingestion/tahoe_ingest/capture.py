"""Fetch documents for discovered or configured candidates and ingest them.

Shared by the `capture` command and the scheduled pipeline. Source PDFs are
temporary: `download_extract` keeps each file in a scratch directory that is
removed as soon as its text is extracted.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from datetime import date, datetime, timezone

from .agenda import ParseResult, parse_agenda
from .core import cited_documents, ingest_bundle, record_failure, record_oversized, stable_id
from .source_discovery import discover_configured
from .http import EndpointSkipped, SourceTooLarge
from .records import apply_links, links_of, match_meeting, qualifiers
from .source import download_extract
from .start_times import agenda_date


# Capture reads every document discovery lists for a meeting. The discovery readers keep a
# meeting's own agenda and packet and leave out minutes, staff reports, attachments, and
# other bodies' agendas, so a listing holds one to three documents (Legistar: accessible
# agenda, agenda PDF, packet). This cap only guards against a page that suddenly links
# dozens of files: reaching it is logged, and the meeting is then stored without items
# until the listing is fixed or the cap raised.
MAX_DOCUMENTS = 10


def capture_documents(candidate: dict, max_documents: int = MAX_DOCUMENTS) -> list[dict]:
    """The documents a capture of ``candidate`` fetches: all of them, in discovery's order, up to the safety cap."""
    return candidate.get("documents", [])[:max_documents]


def discover_agency(agency_id: str, config: dict, *, limit: int) -> list[dict]:
    """Bounded candidates from one followed agency's `discovery` block (see source_discovery)."""
    if not config.get("discovery"):
        raise ValueError(f"{agency_id}: no discovery source")
    candidates = discover_configured(agency_id, config["discovery"], limit=limit)
    for candidate in candidates:
        candidate["agency"] = agency_id
    return candidates


def source_identity(conn, candidate):
    """Bridge a previously captured calendar ID to the same official agenda.

    Only a unique same-date notice can be bridged, and workshops/special/joint
    meetings must agree. The alias is persisted for subsequent fetches.
    """
    aid, sid = candidate['agency'], str(candidate['source_id'])
    direct = conn.execute("SELECT source_id FROM meetings WHERE agency_id=%s AND source_id=%s", (aid, sid)).fetchone()
    if direct:
        return sid
    alias = conn.execute("SELECT m.source_id FROM meeting_source_aliases a JOIN meetings m ON m.id=a.meeting_id "
                         "WHERE a.agency_id=%s AND a.alias=%s", (aid, sid)).fetchone()
    if alias:
        return alias['source_id']
    rows = list(conn.execute("SELECT id,source_id,title,official_url FROM meetings WHERE agency_id=%s AND local_date=%s "
                             "AND quarantine_reason IS NULL", (aid, candidate.get('date'))))
    return _bridge(conn, candidate, rows)


def _bridge(conn, candidate, same_date_rows):
    """Persist and return the stored source ID of the one same-date meeting this candidate matches, else its own ID."""
    aid, sid = candidate['agency'], str(candidate['source_id'])
    exact = [r for r in same_date_rows if r['official_url'] == candidate['official_url']]

    def qualifiers(title):
        return {word for word in ('workshop', 'special', 'joint') if re.search(r'\b' + word + r'\b', title, re.I)}
    matching = exact or [r for r in same_date_rows if qualifiers(r['title']) == qualifiers(candidate['title'])]
    if len(matching) == 1:
        row = matching[0]
        conn.execute("INSERT INTO meeting_source_aliases(agency_id,alias,meeting_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                     (aid, sid, row['id']))
        return row['source_id']
    return sid


def is_legistar_meeting_page(url: str) -> bool:
    """A Legistar ``MeetingDetail.aspx`` page: the meeting's own record, not a news post or a file."""
    return urllib.parse.urlparse(url).path.lower().endswith("/meetingdetail.aspx")


class StoredMeetings:
    """One bulk read of what is stored, for deciding which of many candidates to fetch.

    The scheduled run checks hundreds of candidates; every hosted query is a billed
    operation, so these checks read this snapshot instead of querying per candidate.
    Load it before a batch of decisions. Captures made afterwards are not reflected.

    A packet recorded as over the capture limits (see `oversized_urls`) counts as
    stored in ``document_urls``, so a meeting whose agenda was captured settles.
    """

    def __init__(self, conn, *, failure_hours: int):
        self.conn = conn
        self.meetings = {r["id"]: r for r in conn.execute(
            """SELECT id,agency_id,source_id,title,official_url,local_date,status,quarantine_reason,
            minutes_url,minutes_status,minutes_note,recording_url,location FROM meetings""")}
        self.by_source = {(r["agency_id"], r["source_id"]): r for r in self.meetings.values()}
        self.aliases = {(r["agency_id"], r["alias"]): r["meeting_id"] for r in conn.execute(
            "SELECT agency_id,alias,meeting_id FROM meeting_source_aliases")}
        self.document_urls = {}
        for r in conn.execute("""SELECT c.meeting_id,v.url FROM document_current c JOIN document_versions v ON v.id=c.version_id"""):
            self.document_urls.setdefault(r["meeting_id"], set()).add(r["url"])
        oversized = oversized_urls(conn)
        self.oversized = frozenset(url for _, url in oversized)  # for `capture_candidates`, from the same snapshot
        for meeting_id, url in oversized:
            self.document_urls.setdefault(meeting_id, set()).add(url)
        self.cited = cited_documents(conn)  # for `capture_candidates`: the documents stored items cite
        self.failed = {r["meeting_id"] for r in conn.execute(
            """SELECT DISTINCT meeting_id FROM processing_runs WHERE stage='capture' AND status='failed'
            AND meeting_id IS NOT NULL AND started_at > now() - make_interval(hours => %s)""", (failure_hours,))}

    def identity(self, candidate) -> str:
        """`source_identity` answered from the snapshot; only a new alias is written."""
        aid, sid = candidate['agency'], str(candidate['source_id'])
        if (aid, sid) in self.by_source:
            return sid
        if (aid, sid) in self.aliases and self.aliases[(aid, sid)] in self.meetings:
            return self.meetings[self.aliases[(aid, sid)]]["source_id"]
        try:
            day = date.fromisoformat(candidate.get('date') or "")
        except ValueError:
            return sid
        rows = [r for r in self.meetings.values()
                if r["agency_id"] == aid and r["local_date"] == day and r["quarantine_reason"] is None]
        return _bridge(self.conn, candidate, rows) if rows else sid

    def apply_links(self, candidates: list[dict], *, coverage_start: date) -> int:
        """Store the minutes and recording links of candidates that are not being captured.

        A candidate's own meeting is found by source identity; a records-only entry
        by date among the agency's stored meetings. Only changed meetings are written.
        """
        updated = 0
        for candidate in candidates:
            links = links_of(candidate)
            if (candidate.get("date") or "") < coverage_start.isoformat():
                continue
            moved = not candidate.get("records_only") and self._upgrade_official_url(candidate)
            moved = (not candidate.get("records_only") and self._apply_location(candidate)) or moved
            if not links:
                updated += moved
                continue
            if candidate.get("records_only"):
                day = date.fromisoformat(candidate["date"])
                pool = [m for m in self.meetings.values() if m["agency_id"] == candidate["agency"] and m["quarantine_reason"] is None]
                meeting = match_meeting(pool, day, qualifiers(candidate.get("title", "")))
            else:
                meeting = self.meetings.get(stable_id(candidate["agency"], self.identity(candidate)))
            if (meeting and apply_links(self.conn, meeting, links)) or moved:
                updated += 1
        return updated

    def _upgrade_official_url(self, candidate: dict) -> bool:
        """Point a stored meeting at its Legistar meeting page when it links something else.

        A hand-checked candidate may link a news recap or the agenda file; a Legistar listing
        links the meeting record itself, and a settled meeting is never captured again to take it.
        """
        url = candidate.get("official_url") or ""
        meeting = self.meetings.get(stable_id(candidate["agency"], self.identity(candidate)))
        if not meeting or not is_legistar_meeting_page(url) or is_legistar_meeting_page(meeting["official_url"] or "") \
                or meeting["official_url"] == url:
            return False
        self.conn.execute("UPDATE meetings SET official_url=%s,updated_at=clock_timestamp() WHERE id=%s", (url, meeting["id"]))
        meeting["official_url"] = url
        return True

    def _apply_location(self, candidate: dict) -> bool:
        """Store the venue a listing gives (a Legistar location field) on a meeting that is not captured again.

        Capture takes the listing's location; a settled meeting is never captured again, so a venue
        the listing gives or corrects later is applied here. A listing without one changes nothing.
        """
        location = candidate.get("location")
        meeting = self.meetings.get(stable_id(candidate["agency"], self.identity(candidate)))
        if not location or not meeting or meeting["location"] == location:
            return False
        self.conn.execute("UPDATE meetings SET location=%s,updated_at=clock_timestamp() WHERE id=%s", (location, meeting["id"]))
        meeting["location"] = location
        return True

    def local_date(self, meeting_id: str):
        row = self.meetings.get(meeting_id)
        return row["local_date"] if row else None


def oversized_urls(conn) -> set[tuple[str, str]]:
    """(meeting ID, URL) of every packet recorded as over the capture limits, in one query."""
    return {(r["meeting_id"], r["url"]) for r in conn.execute(
        """SELECT DISTINCT meeting_id,detail->>'url' AS url FROM processing_runs
        WHERE stage='capture' AND status='oversized' AND meeting_id IS NOT NULL AND detail ? 'url'""")}


def _is_packet(document: dict) -> bool:
    """The `staff_reports.pdf_agenda` convention: a packet says so in its ID or title."""
    return bool(re.search(r"packet", f"{document.get('id', '')} {document.get('title', '')}", re.I))


def _capture_row(candidate: dict, max_documents: int, fetch, failures: list, failure_records: list, delay: float,
                 *, known_oversized: frozenset = frozenset(), oversized: list | None = None, prefer=()) -> dict:
    """One meeting's ingest row from its documents (every one listed, up to ``max_documents``).

    Discovery's order is kept: the agenda first, then its packet, so the first document is
    the one labeled as it. Which copy items cite is ``parse_agenda``'s choice, the same a
    reparse makes: ``prefer`` names the documents the stored items cite. A listing over
    the cap is logged; the documents read are stored, but items are not parsed from a
    partial set.

    A packet over the size or page limits (``SourceTooLarge``) beside another captured
    document is left out without a failure: the agenda still parses, and the URL is
    appended to ``oversized`` as ``(agency, source_id, url, reason)`` so it can be
    recorded and never downloaded again. URLs in ``known_oversized`` are treated that
    way without being fetched. An oversized document that is not a packet, or a packet
    with nothing else captured, is a document error as before.
    """
    docs = []
    pages = []
    html_items = None
    document_errors = []
    skipped = []
    too_large = []
    fetched_any = False
    listed = len(candidate.get("documents", []))
    truncated = listed > max_documents
    if truncated:
        print(json.dumps({"severity": "WARNING", "message": f"capture: {candidate['agency']}:{candidate['source_id']} lists "
                          f"{listed} documents; only the first {max_documents} are read",
                          "agency": candidate["agency"], "sourceId": str(candidate["source_id"]),
                          "documentsListed": listed, "documentLimit": max_documents}), file=sys.stderr, flush=True)
    for document in capture_documents(candidate, max_documents):
        if document["url"] in known_oversized:
            too_large.append((document, "previously exceeded the capture limits; not downloaded again"))
            continue
        if fetched_any and delay:
            time.sleep(delay)
        fetched_any = True
        try:
            fetched = fetch(document["url"])
        except EndpointSkipped as exc:
            skipped.append(str(exc))
            continue
        except SourceTooLarge as exc:
            too_large.append((document, str(exc)))
            continue
        except (OSError, RuntimeError, ValueError) as exc:
            document_errors.append(str(exc))
            failures.append(f"{candidate['agency']}:{candidate['source_id']}: {exc}")
            failure_records.append((candidate["agency"], candidate["source_id"], str(exc)))
            continue
        if fetched.get("items_complete") and html_items is None:
            html_items = fetched["agenda_items"]
            for item in html_items:
                for ref in item["source_pages"]:
                    ref["document_id"] = document["id"]
        docs.append({**document, "sha256": fetched["sha256"], "fetched_at": datetime.now(timezone.utc).isoformat(),
                     "page_count": fetched["page_count"], "media_type": fetched.get("media_type", "application/pdf"),
                     "warnings": fetched["warnings"]})
        pages.extend({"document_id": document["id"], **page} for page in fetched["pages"])
    omitted = bool(too_large)
    for document, reason in too_large:
        if docs and _is_packet(document):
            if oversized is not None:
                oversized.append((candidate["agency"], candidate["source_id"], document["url"], reason))
            continue
        document_errors.append(reason)
        failures.append(f"{candidate['agency']}:{candidate['source_id']}: {reason}")
        failure_records.append((candidate["agency"], candidate["source_id"], reason))
    complete_document_set = not document_errors and not skipped and not truncated
    parsed = ParseResult(html_items, True) if html_items else (
        parse_agenda(candidate["agency"], docs, pages, prefer=prefer) if docs and complete_document_set else None)
    warnings = list(candidate.get("warnings", []))
    if document_errors:
        warnings.append("Some official documents could not be retrieved; the meeting notice is retained and document capture will need a retry.")
    if skipped:
        warnings.append("Official documents at this source are not retrieved automatically; read them at the official link.")
    if parsed and parsed.complete:
        warnings = [w for w in warnings if "agenda items require" not in w.lower()]
        warnings.extend(parsed.notes)  # irregular numbering published for review (decision 0032)
    elif parsed or docs:
        warnings.append(parsed.warning if parsed else
                        f"The listing links {listed} documents, more than the {max_documents} capture reads; "
                        "agenda items require review." if truncated else
                        "Not every listed document was captured; agenda items require review.")
    # With no document at all nothing is stored: the export derives "no agenda yet" from the empty document list.
    day = candidate.get("date")
    if not day and (found := agenda_date(candidate["title"], [
            {**d, "pages": [{"number": p["page_number"], "text": p["text"]} for p in pages if p["document_id"] == d["id"]]}
            for d in docs])):
        day = found.isoformat()  # The listing states no date; the agenda heading does.
    return {"agency": candidate["agency"], "id": candidate["source_id"], "title": candidate["title"],
            "source_meeting_url": candidate["official_url"], "status": candidate.get("status", "unknown"),
            "date": day, "start_time": candidate.get("start_time"),
            "timezone": "America/Los_Angeles", "location": candidate.get("location"),
            "warnings": warnings, "partial_logistics": True,
            "documents": docs, "documents_complete": bool(candidate.get("documents_complete")) and complete_document_set and not omitted,
            "pages": pages, "agenda_items": parsed.items if parsed and parsed.complete else [],
            "items_complete": bool(parsed and parsed.complete), **links_of(candidate)}


def capture_candidates(conn, candidates: list[dict], *, max_documents: int = MAX_DOCUMENTS, fetch=download_extract,
                       incremental: bool = False, delay: float = 0.0, oversized: frozenset | None = None,
                       cited: dict | None = None) -> dict:
    """Download every document of each candidate (up to ``max_documents``) and ingest the meetings.

    With ``incremental`` each meeting is ingested and committed on its own, and
    no transaction stays open during downloads (for long scheduled runs against
    a remote database). Otherwise all meetings are ingested together at the end.

    Packets recorded as oversized are not downloaded again. Pass ``oversized`` (a
    `StoredMeetings.oversized` snapshot) to skip the one query that loads them, and
    ``cited`` (`StoredMeetings.cited`) to skip the one that reads which documents the
    stored items cite.
    """
    rows, failures, failure_records, new_oversized = [], [], [], []
    known_oversized = oversized if oversized is not None else frozenset(url for _, url in oversized_urls(conn))
    result = {"processed": 0, "failed": 0, "errors": []}

    def ingest(batch):
        outcome = ingest_bundle(conn, {"meetings": batch}, mode="live")
        for key in ("processed", "failed"):
            result[key] += outcome[key]
        result["errors"].extend(outcome["errors"])

    candidates = [c for c in candidates if not c.get("records_only")]  # links for stored meetings, nothing to capture
    if cited is None:
        cited = cited_documents(conn, agencies=sorted({c["agency"] for c in candidates})) if candidates else {}
    for number, candidate in enumerate(candidates):
        if number and delay:
            time.sleep(delay)
        candidate = dict(candidate)
        candidate["source_id"] = source_identity(conn, candidate)
        if incremental:
            conn.commit()
        try:
            row = _capture_row(candidate, max_documents, fetch, failures, failure_records, delay,
                               known_oversized=known_oversized, oversized=new_oversized,
                               prefer=cited.get(stable_id(candidate["agency"], candidate["source_id"]), ()))
        except Exception as exc:  # One bad source never stops the batch.
            failures.append(f"{candidate['agency']}:{candidate['source_id']}: {exc}")
            failure_records.append((candidate["agency"], candidate["source_id"], str(exc)))
            continue
        if incremental:
            ingest([row])
            conn.commit()
        else:
            rows.append(row)
    if rows or not incremental:
        ingest(rows)
    for agency_id, source_id, error in failure_records:
        record_failure(conn, agency_id, source_id, "capture", error)
    for agency_id, source_id, url, reason in new_oversized:
        record_oversized(conn, agency_id, source_id, url, reason)
    if incremental:
        conn.commit()
    result["captureFailures"] = failures
    return result
