"""One-time cleanup of what hand-built meeting records left in the database (decision 0033).

Until decision 0033 the scheduled run also loaded two hand-built sources: the
proof-of-concept bundle ``tests/fixtures/meetings.json`` (step ``seed-fixtures``)
and the dated candidate lists now kept in ``docs/research/verified-agency-meetings/``
(step ``verified-sources``). Meetings now come only from live discovery, whose
capture replaces a meeting's whole warning list each time it downloads the
meeting. What those records wrote stays until then, and a meeting that discovery
only sees (a settled meeting is not downloaded again) or never sees keeps it
for good. ``clean-hand-built`` finds and removes it; it is a dry run unless
``apply`` is set, and running it again changes nothing.

For each hand-built record whose meeting is stored (by ID, or through a source
alias a capture recorded):

1. Warnings: every warning the record itself carried is removed from the
   meeting (exact text, matched against that record only), so warnings a live
   step wrote stay.
2. Documents: a current document version of the meeting whose warnings the
   bundle wrote is corrected. "Text retained for N of M source pages" is
   recomputed from the pages now stored (a later capture of the same file adds
   the rest of its pages to the same version), and "Original fetch time is
   unknown" is dropped once a live capture has stored more of the file's pages
   than the bundle had (so it downloaded the same bytes), with the meeting's
   first live capture time as the fetch time.
3. Meetings that exist only because of a hand-built record are deleted when
   they hold nothing a source produced: no document, no agenda item, no
   minutes or recording link, and no source alias other than a hand-built
   one. "Only because of" means no live capture has been seen: a seed meeting
   with no live ingest and no discovery alias, or a verified-list meeting that
   still carries its record's warnings (a discovery capture would have
   replaced them) and has no discovery alias. Upcoming ones reappear through
   discovery once the agency lists them, and the schedule forecast shows them
   meanwhile; past ones within coverage come back through ``backfill`` if the
   agency's index still lists them. Quarantined meetings are kept, since
   quarantine keeps provenance by design.
4. Every other hand-built meeting is kept, and reported with the reason.

Reading is one SQL statement (the hosted database bills per operation); apply
adds one statement per change kind. Remove this module and the research
snapshots once production has been cleaned.
"""
from __future__ import annotations

import glob
import json
import re
from datetime import date, datetime
from pathlib import Path

from .core import stable_id

SEED_BUNDLE = "tests/fixtures/meetings.json"
VERIFIED_SNAPSHOTS = "docs/research/verified-agency-meetings/*.json"
COVERAGE_START = date(2026, 1, 1)  # scheduled.COVERAGE_START; repeated so this module imports nothing heavy

UNKNOWN_FETCH = "Original fetch time is unknown; the timestamp shown is when this archived record was captured."
FIXTURE_PAGES = "Only selected source pages are available as text in this fixture."
RETAINED = re.compile(r"Text retained for (\d+) of (\d+) source pages; remaining pages are not represented in this export\.")


def retained_warning(stored: int, page_count: int) -> str | None:
    """The core's partial-text warning for a version with ``stored`` of ``page_count`` pages, or None when complete."""
    if stored >= page_count:
        return None
    return f"Text retained for {stored} of {page_count} source pages; remaining pages are not represented in this export."


def load_records(root: Path) -> dict[str, dict]:
    """Hand-built records by meeting ID: agency, source ID, origins, and the warnings they carried."""
    records: dict[str, dict] = {}

    def add(origin, agency, source_id, warnings, pages=None):
        source_id = str(source_id)
        record = records.setdefault(stable_id(agency, source_id), {"agency": agency, "source_id": source_id,
                                                                   "origins": [], "warnings": [], "pages": {}})
        if origin not in record["origins"]:
            record["origins"].append(origin)
        record["warnings"] += [w for w in warnings or [] if w not in record["warnings"]]
        record["pages"].update(pages or {})

    seed = root / SEED_BUNDLE
    if seed.is_file():
        for row in json.loads(seed.read_text())["meetings"]:
            pages: dict[str, int] = {}
            for page in row.get("pages", []):
                pages[str(page["document_id"])] = pages.get(str(page["document_id"]), 0) + 1
            add("seed", row["agency"], row.get("id") or row.get("source_id"), row.get("warnings"), pages)
    for name in sorted(glob.glob(str(root / VERIFIED_SNAPSHOTS))):
        for candidate in json.loads(Path(name).read_text()).get("candidates", []):
            add("verified", candidate["agency"], candidate["source_id"], candidate.get("warnings"))
    return records


READ = """
WITH rec AS (
  SELECT r->>'id' AS record_id, r->>'agency' AS agency_id, r->>'source_id' AS source_id
  FROM jsonb_array_elements(%s::jsonb) r
), target AS (
  SELECT rec.record_id, COALESCE(direct.id, alias.meeting_id) AS meeting_id
  FROM rec
  LEFT JOIN meetings direct ON direct.id = rec.record_id
  LEFT JOIN meeting_source_aliases alias ON direct.id IS NULL AND alias.agency_id = rec.agency_id AND alias.alias = rec.source_id
)
SELECT t.record_id, m.id AS meeting_id, m.agency_id, m.local_date, m.warnings, m.review_notes, m.quarantine_reason,
  (m.minutes_url IS NOT NULL OR m.recording_url IS NOT NULL) AS has_links,
  EXISTS (SELECT 1 FROM document_versions v WHERE v.meeting_id = m.id) AS has_documents,
  EXISTS (SELECT 1 FROM agenda_items i WHERE i.meeting_id = m.id) AS has_items,
  ARRAY(SELECT a.alias FROM meeting_source_aliases a WHERE a.meeting_id = m.id ORDER BY a.alias) AS aliases,
  (SELECT min(r.started_at) FROM processing_runs r WHERE r.meeting_id = m.id AND r.stage = 'ingest'
     AND r.status = 'succeeded' AND r.detail->>'mode' = 'live') AS first_live_ingest,
  (SELECT count(*) FROM meetings x WHERE x.agency_id = m.agency_id AND x.quarantine_reason IS NULL) AS agency_meetings,
  (SELECT coalesce(jsonb_agg(jsonb_build_object('id', v.id, 'document', v.source_document_id, 'pageCount', v.page_count,
          'warnings', v.warnings,
          'storedPages', (SELECT count(*) FROM document_pages p WHERE p.document_version_id = v.id))), '[]'::jsonb)
     FROM document_current c JOIN document_versions v ON v.id = c.version_id
     WHERE c.meeting_id = m.id AND (v.warnings ? %s OR v.warnings ? %s OR v.warnings::text LIKE '%%Text retained for%%')
  ) AS documents
FROM target t LEFT JOIN meetings m ON m.id = t.meeting_id
"""


def _when(day, today: date) -> str:
    if day is None:
        return "undated"
    if day >= today:
        return "upcoming"
    return "past" if day >= COVERAGE_START else "beforeCoverage"


def plan(conn, records: dict[str, dict], *, today: date) -> dict:
    """What the cleanup would change, from one read; nothing is written."""
    payload = [{"id": mid, "agency": r["agency"], "source_id": r["source_id"]} for mid, r in records.items()]
    rows = conn.execute(READ, (json.dumps(payload), UNKNOWN_FETCH, FIXTURE_PAGES)).fetchall()
    hand_ids = {(r["agency"], r["source_id"]) for r in records.values()}
    by_meeting: dict[str, dict] = {}
    for row in rows:
        if row["meeting_id"] is None:
            continue
        entry = by_meeting.setdefault(row["meeting_id"], {**row, "records": []})
        entry["records"].append(records[row["record_id"]])

    strip, documents, delete, keep = [], [], [], []
    for meeting_id, m in sorted(by_meeting.items()):
        warnings = m["warnings"] if isinstance(m["warnings"], list) else json.loads(m["warnings"] or "[]")
        # A record's operator notes may have moved to review notes (core.migrate_review_notes, decision 0040).
        notes = m["review_notes"] if isinstance(m["review_notes"], list) else json.loads(m["review_notes"] or "[]")
        hand_warnings = {w for r in m["records"] for w in r["warnings"]}
        bundle_pages = {d: n for r in m["records"] for d, n in r["pages"].items()}
        origins = sorted({o for r in m["records"] for o in r["origins"]})
        own = meeting_id in records  # created under a hand-built ID, not a capture's ID bridged to it
        discovery_aliases = [a for a in m["aliases"] if (m["agency_id"], a) not in hand_ids]
        still_hand_written = bool(hand_warnings & set(warnings + notes))
        if not own or discovery_aliases:
            seen = True
        elif "verified" in origins:
            # Verified-list captures were ingested as live, so only replaced warnings show a discovery capture.
            seen = None if not hand_warnings else not still_hand_written
        else:
            seen = m["first_live_ingest"] is not None
        kept_warnings = [w for w in warnings if w not in hand_warnings]
        kept_notes = [n for n in notes if n not in hand_warnings]
        if kept_warnings != warnings or kept_notes != notes:
            strip.append({"meeting": meeting_id, "remove": [w for w in warnings + notes if w in hand_warnings],
                          "keep": kept_warnings, "keepReviewNotes": kept_notes})
        for doc in m["documents"] or []:
            fixed = []
            for w in doc["warnings"]:
                if RETAINED.fullmatch(w):
                    w = retained_warning(doc["storedPages"], doc["pageCount"])
                elif w == FIXTURE_PAGES:
                    w = retained_warning(doc["storedPages"], doc["pageCount"])
                elif (w == UNKNOWN_FETCH and m["first_live_ingest"] is not None
                      and doc["storedPages"] > bundle_pages.get(doc["document"], doc["storedPages"])):
                    w = None  # more pages than the bundle had: a live capture downloaded the same file
                if w and w not in fixed:
                    fixed.append(w)
            if fixed != doc["warnings"]:
                refetched = UNKNOWN_FETCH in doc["warnings"] and UNKNOWN_FETCH not in fixed
                documents.append({"version": doc["id"], "meeting": meeting_id, "warnings": fixed,
                                  "fetchedAt": m["first_live_ingest"].isoformat() if refetched else None})
        empty = not (m["has_documents"] or m["has_items"] or m["has_links"] or discovery_aliases)
        entry = {"meeting": meeting_id, "origins": origins, "date": m["local_date"].isoformat() if m["local_date"] else None,
                 "when": _when(m["local_date"], today)}
        if seen is False and empty and not m["quarantine_reason"]:
            delete.append({**entry, "agency": m["agency_id"], "agencyMeetings": m["agency_meetings"],
                           "aliases": m["aliases"]})
        else:
            reason = ("quarantined" if m["quarantine_reason"] else
                      "seen by live discovery" if seen else
                      "has source content" if not empty else
                      "cannot tell whether discovery has seen it")
            keep.append({**entry, "reason": reason})

    emptied = sorted(a for a, n in _count(delete, "agency").items()
                     if n >= next(d["agencyMeetings"] for d in delete if d["agency"] == a))
    return {"records": len(records), "stored": len(by_meeting), "strip": strip, "documents": documents,
            "delete": delete, "keep": keep, "agenciesLeftWithoutMeetings": emptied}


def apply(conn, result: dict) -> None:
    """Write a `plan` in one transaction: warnings, document versions, then deletions."""
    with conn.transaction():
        if result["strip"]:
            conn.execute("""UPDATE meetings m SET warnings = s.warnings::jsonb, review_notes = s.notes::jsonb, updated_at = now()
                FROM unnest(%s::text[], %s::text[], %s::text[]) AS s(id, warnings, notes) WHERE m.id = s.id""",
                ([s["meeting"] for s in result["strip"]], [json.dumps(s["keep"]) for s in result["strip"]],
                 [json.dumps(s["keepReviewNotes"]) for s in result["strip"]]))
        if result["documents"]:
            conn.execute("""UPDATE document_versions v SET warnings = d.warnings::jsonb,
                fetched_at = COALESCE(d.fetched_at::timestamptz, v.fetched_at)
                FROM unnest(%s::text[], %s::text[], %s::text[]) AS d(id, warnings, fetched_at) WHERE v.id = d.id""",
                ([d["version"] for d in result["documents"]], [json.dumps(d["warnings"]) for d in result["documents"]],
                 [d["fetchedAt"] for d in result["documents"]]))
        ids = [d["meeting"] for d in result["delete"]]
        if ids:
            # Every table that references meetings: run logs keep their rows without the meeting; the rest go with it.
            for ref in conn.execute("""SELECT c.conrelid::regclass::text AS tbl, a.attname AS col, a.attnotnull AS required
                    FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                    WHERE c.contype = 'f' AND c.confrelid = 'meetings'::regclass""").fetchall():
                table, column = ref["tbl"], ref["col"]
                if ref["required"]:
                    conn.execute(f'DELETE FROM {table} WHERE "{column}" = ANY(%s)', (ids,))
                else:
                    conn.execute(f'UPDATE {table} SET "{column}" = NULL WHERE "{column}" = ANY(%s)', (ids,))
            conn.execute("DELETE FROM meetings WHERE id = ANY(%s)", (ids,))


def clean_hand_built(conn, root: Path, *, dry_run: bool = True, today: date | None = None) -> dict:
    records = load_records(root)
    if not records:
        raise SystemExit(f"No hand-built records found under {root} ({SEED_BUNDLE}, {VERIFIED_SNAPSHOTS})")
    result = plan(conn, records, today=today or datetime.now().date())
    if not dry_run:
        apply(conn, result)
    summary = {
        "dryRun": dry_run, "handBuiltRecords": result["records"], "storedMeetings": result["stored"],
        "warnings": {"meetings": len(result["strip"]), "removed": sum(len(s["remove"]) for s in result["strip"])},
        "documentVersions": len(result["documents"]),
        "delete": {"count": len(result["delete"]), "byWhen": _count(result["delete"], "when")},
        "keep": {"count": len(result["keep"]), "byReason": _count(result["keep"], "reason")},
        "agenciesLeftWithoutMeetings": result["agenciesLeftWithoutMeetings"],
    }
    return {"summary": summary, **{k: result[k] for k in ("strip", "documents", "delete", "keep")}}


def _count(rows: list[dict], key: str) -> dict:
    counts: dict = {}
    for row in rows:
        counts[row[key]] = counts.get(row[key], 0) + 1
    return dict(sorted(counts.items()))
