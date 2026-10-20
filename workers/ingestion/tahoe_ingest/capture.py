"""Fetch documents for discovered or configured candidates and ingest them.

Shared by the `capture` command and the scheduled pipeline. Source PDFs are
temporary: `download_extract` keeps each file in a scratch directory that is
removed as soon as its text is extracted.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .agenda import ParseResult, parse_agenda
from .core import ingest_bundle, record_failure
from .discovery import discover_slt, discover_truckee, discover_ttd
from .source_discovery import discover_configured
from .source import download_extract

PACIFIC = ZoneInfo("America/Los_Angeles")


TRUCKEE_PREVIOUS_MONTH_DAYS = 14  # Early in a month, also read last month's calendar for recent meetings.


def truckee_months(today: date) -> list[tuple[int, int]]:
    """Calendar months the Truckee adapter reads when no month is named: current and next, plus the previous one early in a month."""
    def shift(offset):
        index = today.year * 12 + today.month - 1 + offset
        return index // 12, index % 12 + 1
    return ([shift(-1)] if today.day <= TRUCKEE_PREVIOUS_MONTH_DAYS else []) + [shift(0), shift(1)]


def _discover_truckee_months(months: list[tuple[int, int]], limit: int) -> list[dict]:
    found, seen = [], set()
    for year, month in months:
        for candidate in discover_truckee(year=year, month=month, limit=limit):
            if candidate["source_id"] not in seen:
                seen.add(candidate["source_id"])
                found.append(candidate)
    return found


def discover_agency(agency_id: str, config: dict, *, limit: int, year: int | None = None, month: int | None = None) -> list[dict]:
    """Bounded candidates from one followed agency's discovery adapter.

    The Truckee calendar adapter reads one month when `year`/`month` are given
    and otherwise the months from `truckee_months` (each bounded by `limit`).
    """
    adapter = config.get("discoveryAdapter")
    if not adapter:
        raise ValueError(f"{agency_id}: no discovery adapter")
    now = datetime.now(PACIFIC)
    if adapter in ("configured", "ttd", "slt"):
        candidates = (discover_configured(agency_id, config["discovery"], limit=limit) if adapter == "configured" else
                      discover_ttd(limit=limit) if adapter == "ttd" else discover_slt(limit=limit))
    elif year is None and month is None:
        candidates = _discover_truckee_months(truckee_months(now.date()), limit)
    else:
        candidates = discover_truckee(year=year or now.year, month=month or now.month, limit=limit)
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
    exact = [r for r in rows if r['official_url'] == candidate['official_url']]

    def qualifiers(title):
        return {word for word in ('workshop', 'special', 'joint') if re.search(r'\b' + word + r'\b', title, re.I)}
    matching = exact or [r for r in rows if qualifiers(r['title']) == qualifiers(candidate['title'])]
    if len(matching) == 1:
        row = matching[0]
        conn.execute("INSERT INTO meeting_source_aliases(agency_id,alias,meeting_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                     (aid, sid, row['id']))
        return row['source_id']
    return sid


def _capture_row(candidate: dict, max_documents: int, fetch, failures: list, failure_records: list, delay: float) -> dict:
    docs = []
    pages = []
    html_items = None
    document_errors = []
    for index, document in enumerate(candidate["documents"][:max_documents]):
        if index and delay:
            time.sleep(delay)
        try:
            fetched = fetch(document["url"])
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
    complete_document_set = not document_errors and len(candidate["documents"]) <= max_documents
    parsed = ParseResult(html_items, True) if html_items else (
        parse_agenda(candidate["agency"], docs, pages) if docs and complete_document_set else None)
    warnings = list(candidate.get("warnings", []))
    if document_errors:
        warnings.append("Some official documents could not be retrieved; the meeting notice is retained and document capture will need a retry.")
    if parsed and parsed.complete:
        warnings = [w for w in warnings if "agenda items require" not in w.lower()]
    else:
        warnings.append(parsed.warning if parsed else
                        "Document limit omitted source PDFs; agenda items require review." if docs else
                        "No agenda PDF is linked; agenda items require review.")
    return {"agency": candidate["agency"], "id": candidate["source_id"], "title": candidate["title"],
            "source_meeting_url": candidate["official_url"], "status": candidate.get("status", "unknown"),
            "date": candidate.get("date"), "start_time": candidate.get("start_time"),
            "timezone": "America/Los_Angeles", "location": candidate.get("location"),
            "warnings": warnings, "partial_logistics": True,
            "documents": docs, "documents_complete": bool(candidate.get("documents_complete")) and complete_document_set,
            "pages": pages, "agenda_items": parsed.items if parsed and parsed.complete else [],
            "items_complete": bool(parsed and parsed.complete)}


def capture_candidates(conn, candidates: list[dict], *, max_documents: int = 2, fetch=download_extract,
                       incremental: bool = False, delay: float = 0.0) -> dict:
    """Download each candidate's first ``max_documents`` documents and ingest the meetings.

    With ``incremental`` each meeting is ingested and committed on its own, and
    no transaction stays open during downloads (for long scheduled runs against
    a remote database). Otherwise all meetings are ingested together at the end.
    """
    rows, failures, failure_records = [], [], []
    result = {"processed": 0, "failed": 0, "errors": []}

    def ingest(batch):
        outcome = ingest_bundle(conn, {"meetings": batch}, mode="live")
        for key in ("processed", "failed"):
            result[key] += outcome[key]
        result["errors"].extend(outcome["errors"])

    for number, candidate in enumerate(candidates):
        if number and delay:
            time.sleep(delay)
        candidate = dict(candidate)
        candidate["source_id"] = source_identity(conn, candidate)
        if incremental:
            conn.commit()
        try:
            row = _capture_row(candidate, max_documents, fetch, failures, failure_records, delay)
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
    if incremental:
        conn.commit()
    result["captureFailures"] = failures
    return result
