"""Re-run agenda parsers over stored document text, without fetching sources.

Use after adding or changing an agency layout. By default only meetings that
have no active agenda items, or whose items await reprocessing after a source
change, are parsed; ``force`` reparses every stored meeting of a parsed
agency. Results go through the normal ingest path, so citations are
validated against the stored pages and topic tags are generated as usual.
"""
from __future__ import annotations

import json
import re

from .agenda import has_parser, parse_agenda
from .core import _json, ingest_bundle, iso

STALE = re.compile(r"No parser exists|items require review|review required|await reprocessing|"
                   r"agenda text (?:is )?not (?:yet )?(?:captured|extracted)", re.I)


def reparse_meetings(conn, *, agency: str | None = None, meeting: str | None = None,
                     force: bool = False, dry_run: bool = False) -> dict:
    report = {"parsed": [], "failed": [], "skipped": 0}
    rows = conn.execute("""SELECT m.* ,
            (SELECT count(*) FROM agenda_items i WHERE i.meeting_id=m.id AND i.active) AS active_items
        FROM meetings m
        WHERE m.quarantine_reason IS NULL
          AND EXISTS (SELECT 1 FROM document_current c WHERE c.meeting_id=m.id)
          AND (%s::text IS NULL OR m.agency_id=%s) AND (%s::text IS NULL OR m.id=%s)
        ORDER BY m.agency_id, m.local_date NULLS LAST, m.id""", (agency, agency, meeting, meeting)).fetchall()
    for m in rows:
        warnings = _json(m["warnings"]) or []
        stuck = any("await reprocessing" in w for w in warnings)
        if not has_parser(m["agency_id"]) or not (force or meeting or stuck or m["active_items"] == 0):
            report["skipped"] += 1
            continue
        documents, pages = _stored_documents(conn, m["id"])
        parsed = parse_agenda(m["agency_id"], documents, pages)
        entry = {"meeting": m["id"], "items": len(parsed.items)} if parsed.complete else {"meeting": m["id"], "warning": parsed.warning}
        report["parsed" if parsed.complete else "failed"].append(entry)
        if dry_run:
            continue
        kept = [w for w in warnings if not STALE.search(w)]
        if not parsed.complete:
            if parsed.warning and parsed.warning not in warnings:
                conn.execute("UPDATE meetings SET warnings=%s WHERE id=%s",
                             (json.dumps(kept + [parsed.warning]), m["id"]))
            continue
        row = {"agency": m["agency_id"], "id": m["source_id"], "title": m["title"],
               "starts_at": iso(m["starts_at"]), "date": m["local_date"].isoformat() if m["local_date"] else None,
               "timezone": m["timezone"], "status": m["status"], "location": m["location"],
               "source_meeting_url": m["official_url"], "participation": _json(m["participation"]),
               "minutes_url": m["minutes_url"], "recording_url": m["recording_url"],
               "warnings": kept, "partial_logistics": True, "documents": documents, "documents_complete": False,
               "pages": pages, "agenda_items": parsed.items, "items_complete": True}
        result = ingest_bundle(conn, {"meetings": [row]}, mode="fixture")
        if result["failed"]:
            report["parsed"].pop()
            report["failed"].append({"meeting": m["id"], "warning": "; ".join(result["errors"])})
    return report


def _stored_documents(conn, meeting_id: str) -> tuple[list[dict], list[dict]]:
    documents, pages = [], []
    for v in conn.execute("""SELECT v.* FROM document_current c JOIN document_versions v ON v.id=c.version_id
            WHERE c.meeting_id=%s ORDER BY c.source_document_id""", (meeting_id,)):
        documents.append({"id": v["source_document_id"], "title": v["title"], "url": v["url"], "sha256": v["sha256"],
                          "fetched_at": iso(v["fetched_at"]), "page_count": v["page_count"],
                          "media_type": v["media_type"], "warnings": _json(v["warnings"]) or []})
        for p in conn.execute("SELECT page_number,text FROM document_pages WHERE document_version_id=%s ORDER BY page_number",
                              (v["id"],)):
            pages.append({"document_id": v["source_document_id"], "page_number": p["page_number"], "text": p["text"]})
    return documents, pages

