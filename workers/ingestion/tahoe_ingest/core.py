"""PostgreSQL ingestion and versioned public export.

The database is authoritative. Input bundles are observations, never instructions.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .catalog import public_topics, topic_ids, agency_catalog, followed_agencies, per_record_host_agencies, expand_topics
from .topics import CLASSIFIER_VERSION, classify
from .durations import meeting_duration
from .time_certain import time_certain
from .start_times import agenda_start_time
from .history import HISTORY_SQL, document_history
from .participation import annotated_join_url, merged as merged_participation
from .schedules import PACIFIC, forecast_meetings
from .records import links_of, merged as merged_links
from .http import skipped_endpoint
from .outline import NOT_PLACEHOLDER_SQL, is_placeholder


TOPICS = public_topics()
AGENCIES = followed_agencies()
# Added when a source change voids the summaries of published items that a partial capture did not reparse.
# Meeting warnings are shown to residents as stored (decision 0041), so this is the resident's wording.
REPROCESSING_WARNING = "Source documents changed; some agenda details still need to be refreshed."
# The operator's wording stored before decision 0041; ``migrate_review_notes`` rewrites it once.
LEGACY_REPROCESSING_WARNING = "A source PDF changed; affected agenda items await reprocessing."
# Before decision 0041 parser and operator notes shared ``meetings.warnings`` with resident warnings, and the website
# hid most of them by pattern (apps/web/src/lib/data.ts). ``migrate_review_notes`` moves stored strings that match
# into ``meetings.review_notes`` once; new notes are written there explicitly. This also catches two parse failures
# the website showed ("… items require review."), and the planned meetings' caveat that they may change, which the
# website derives from the export's ``planned`` flag.
LEGACY_REVIEW_NOTE = re.compile(r"No parser exists|review required|require (?:source )?review|review all agenda documents|"
                                r"No complete .* agenda recognized|differing item sequences|"
                                r"^Check the notice for changes\.$|^Subject to change\.$", re.I)


def connect(url: str) -> psycopg.Connection:
    # Hosted URLs carry their own sslmode (e.g. ?sslmode=require); keepalives protect
    # long scheduled runs whose connection idles during source downloads.
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=30,
                           keepalives=1, keepalives_idle=60, keepalives_interval=15, keepalives_count=4)


def init_db(conn: psycopg.Connection) -> None:
    """Apply the schema and agency catalog, skipping both when neither changed since the last run.

    The applied version is a hash kept as the comment on the agencies table, so an
    unchanged scheduled run costs one query instead of the whole schema script.
    """
    schema = (Path(__file__).parent / "schema.sql").read_text()
    agencies = [{"id": a["id"], "name": a["name"], "short_name": a["shortName"],
                 "official_url": a.get("officialUrl") or "", "source_url": a.get("sourceUrl") or ""} for a in agency_catalog()]
    version = "schema " + _hash_text({"schema": schema, "agencies": agencies})
    with conn.transaction():
        applied = conn.execute("SELECT obj_description(to_regclass('agencies'),'pg_class') AS version").fetchone()["version"]
        if applied == version:
            return
        conn.execute(schema)
        conn.execute("""INSERT INTO agencies(id,name,short_name,official_url,source_url)
            SELECT id,name,short_name,official_url,source_url FROM jsonb_to_recordset(%s::jsonb)
              AS a(id text,name text,short_name text,official_url text,source_url text)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name,short_name=excluded.short_name,
            official_url=excluded.official_url,source_url=excluded.source_url""", (json.dumps(agencies),))
        migrate_fingerprints(conn)
        migrate_review_notes(conn)
        conn.execute(sql.SQL("COMMENT ON TABLE agencies IS {}").format(sql.Literal(version)))


def stable_id(*parts: str) -> str:
    return ":".join(parts)


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def now() -> datetime:
    return datetime.now(timezone.utc)


def starts_at(row: dict):
    if row.get("starts_at"):
        value = datetime.fromisoformat(row["starts_at"].replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("starts_at requires an explicit UTC offset")
        return value
    if not row.get("date") or not row.get("start_time"):
        return None
    local = datetime.combine(datetime.fromisoformat(row["date"]).date(), time.fromisoformat(row["start_time"]))
    zone = ZoneInfo(row.get("timezone") or "America/Los_Angeles")
    candidates = [local.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
    valid = [c for c in candidates if c.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local]
    if not valid:
        raise ValueError("meeting time falls in a daylight-saving gap")
    if len(valid) == 2 and valid[0].utcoffset() != valid[1].utcoffset():
        if row.get("fold") not in (0, 1):
            raise ValueError("ambiguous daylight-saving meeting time requires fold 0 or 1")
        return candidates[row["fold"]]
    return valid[0]


def _hash_text(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _citation(version_id: str, page: int, quote: str) -> dict:
    return {"documentId": version_id, "page": page, "quote": quote}


def cited_text(citations: list[dict]) -> list[dict]:
    """What citations quote, without the document version they name: each page number and quote, in order."""
    return [{"page": int(c["page"]), "quote": c["quote"]} for c in citations]


def item_fingerprint(title: str, section, citations: list[dict]) -> str:
    """An item's source fingerprint: its title, section, and the page and text each citation quotes.

    The document version is left out, so an agenda posted again as a new file, or the same text cited from
    another copy (a meeting's PDF and its HTML agenda), is not a change: nothing is marked revised, mapped,
    or summarized again. Staff report mappings, attachment lists, and Claude results are keyed on it.
    """
    return _hash_text({"title": title, "section": section, "citations": cited_text(citations)})


def legacy_fingerprint(title: str, section, citations: list[dict]) -> str:
    """The fingerprint before November 2026, which also hashed each citation's document version ID.

    Read only to carry rows stored under it over to ``item_fingerprint`` (``migrate_fingerprints`` and ingest)."""
    return _hash_text({"title": title, "section": section, "citations": citations})


# Every table that keys a derived result on the item's fingerprint, with the item ID column.
FINGERPRINT_TABLES = ("summaries", "item_staff_reports", "item_attachments", "ai_item_summaries", "ai_topic_classifications")


def rebind_fingerprints(conn, pairs: list[tuple[str, str, str]], *, items: bool = False) -> None:
    """Move each (item ID, old, new) fingerprint's derived rows from old to new in one statement; with ``items``,
    the items too. A row bound to another (older) fingerprint stays stale, as it was."""
    if not pairs:
        return
    updates = [("agenda_items", "id")] if items else []
    updates += [(table, "item_id") for table in FINGERPRINT_TABLES]
    steps = sql.SQL(",").join(
        sql.SQL("{name} AS (UPDATE {table} t SET source_fingerprint=p.new FROM p WHERE t.{key}=p.item_id AND t.source_fingerprint=p.old)")
        .format(name=sql.Identifier(f"u{n}"), table=sql.Identifier(table), key=sql.Identifier(key))
        for n, (table, key) in enumerate(updates))
    conn.execute(sql.SQL("WITH p AS (SELECT * FROM jsonb_to_recordset(%s::jsonb) AS p(item_id text,old text,new text)),{} SELECT 1")
                 .format(steps), (json.dumps([{"item_id": i, "old": o, "new": n} for i, o, n in pairs]),))


def migrate_fingerprints(conn) -> int:
    """Carry every item stored under ``legacy_fingerprint`` over to ``item_fingerprint`` once; returns the items moved.

    Run by ``init_db`` in the schema's transaction. An item whose stored citations reproduce its stored fingerprint
    takes the new one, and so do the rows derived from it, and each staff report scan that was current stays
    current, so the deploy marks nothing revised, maps nothing, and summarizes nothing again. An item whose
    citations failed validation (stored empty) cannot be recomputed here; ingest moves it when it next reads the
    same citations (``_ingest_meeting``).
    """
    if not conn.execute("""INSERT INTO ingest_migrations(name) VALUES ('item-fingerprint-v2')
            ON CONFLICT DO NOTHING RETURNING name""").fetchone():
        return 0
    pairs = []
    for r in conn.execute("SELECT id,title,section,citations,source_fingerprint FROM agenda_items"):
        citations = _json(r["citations"]) or []
        new = item_fingerprint(r["title"], r["section"], citations)
        if new != r["source_fingerprint"] and legacy_fingerprint(r["title"], r["section"], citations) == r["source_fingerprint"]:
            pairs.append((r["id"], r["source_fingerprint"], new))
    if not pairs:
        return 0
    from .staff_reports import scan_keys
    before = scan_keys(conn)
    rebind_fingerprints(conn, pairs, items=True)
    after = scan_keys(conn)
    moved = [{"meeting_id": m, "old": key, "new": after[m]} for m, key in before.items() if after.get(m) not in (None, key)]
    if moved:
        conn.execute("""UPDATE staff_report_scans s SET items_sha256=r.new
            FROM jsonb_to_recordset(%s::jsonb) AS r(meeting_id text,old text,new text)
            WHERE s.meeting_id=r.meeting_id AND s.items_sha256=r.old""", (json.dumps(moved),))
    return len(pairs)


def split_legacy_warnings(warnings: list[str]) -> tuple[list[str], list[str]]:
    """Stored warnings from before decision 0041, as (resident warnings, review notes)."""
    warnings = [REPROCESSING_WARNING if w == LEGACY_REPROCESSING_WARNING else w for w in warnings]
    return [w for w in warnings if not LEGACY_REVIEW_NOTE.search(w)], [w for w in warnings if LEGACY_REVIEW_NOTE.search(w)]


def migrate_review_notes(conn) -> int:
    """Move parser and operator notes stored among a meeting's warnings to ``review_notes`` once (decision 0041).

    Run by ``init_db`` in the schema's transaction: one read of the meetings that have warnings and one write of
    those that change. Notes join any already in ``review_notes``; the old "await reprocessing" text takes the
    resident wording. ``updated_at`` is left alone: nothing the source says changed. Returns the meetings changed.
    """
    if not conn.execute("""INSERT INTO ingest_migrations(name) VALUES ('meeting-review-notes-v1')
            ON CONFLICT DO NOTHING RETURNING name""").fetchone():
        return 0
    changes = []
    for r in conn.execute("SELECT id,warnings,review_notes FROM meetings WHERE warnings <> '[]'::jsonb ORDER BY id"):
        stored = _json(r["warnings"]) or []
        warnings, notes = split_legacy_warnings(stored)
        if warnings != stored:
            existing = _json(r["review_notes"]) or []
            changes.append((r["id"], json.dumps(warnings), json.dumps(existing + [n for n in notes if n not in existing])))
    if changes:
        conn.execute("""UPDATE meetings m SET warnings=s.warnings::jsonb, review_notes=s.notes::jsonb
            FROM unnest(%s::text[], %s::text[], %s::text[]) AS s(id, warnings, notes) WHERE m.id=s.id""",
                     ([c[0] for c in changes], [c[1] for c in changes], [c[2] for c in changes]))
    return len(changes)


def cited_documents(conn, *, agencies=None, meetings=None) -> dict[str, set[str]]:
    """The current source documents each meeting's active items cite, in one query (by agency or by meeting).

    Capture and reparse pass these to ``agenda.parse_agenda`` as ``prefer``, so items keep citing the copy they
    cite while it is current and still reads the same items.
    """
    cited = {}
    for r in conn.execute("""SELECT DISTINCT i.meeting_id,c.source_document_id FROM agenda_items i
            JOIN meetings m ON m.id=i.meeting_id CROSS JOIN LATERAL jsonb_array_elements(i.citations) e
            JOIN document_current c ON c.meeting_id=i.meeting_id AND c.version_id=e->>'documentId'
            WHERE i.active AND (%s::text[] IS NULL OR m.agency_id=ANY(%s)) AND (%s::text[] IS NULL OR i.meeting_id=ANY(%s))""",
            (agencies, agencies, meetings, meetings)):
        cited.setdefault(r["meeting_id"], set()).add(r["source_document_id"])
    return cited


def one_per_url(docs: list[dict], cited: set[str]) -> list[dict]:
    """Each URL once, under the label of its first document. Two discovery passes can store one agenda under two ids
    (the scheduled listing and a verified-source manifest); the copy that items cite keeps its id so citations resolve."""
    groups = {}
    for d in docs:
        groups.setdefault(d["url"], []).append(d)
    return [{**next((d for d in group if d["id"] in cited), group[0]), "title": group[0]["title"]} for group in groups.values()]


def stored_page_texts(conn, citations: list[dict]) -> dict:
    """Stored text of every page the citations name, keyed by (version id, page), in one query."""
    keys = sorted({(c["documentId"], int(c["page"])) for c in citations})
    if not keys:
        return {}
    return {(r["document_version_id"], r["page_number"]): r["text"] for r in conn.execute(
        """SELECT p.document_version_id,p.page_number,p.text FROM document_pages p
        JOIN unnest(%s::text[],%s::int[]) AS k(version_id,page) ON p.document_version_id=k.version_id AND p.page_number=k.page""",
        ([k[0] for k in keys], [k[1] for k in keys]))}


def validate_citations(conn, citations: list[dict], page_texts: dict | None = None) -> None:
    if not citations:
        raise ValueError("at least one citation is required")
    if page_texts is None:
        page_texts = stored_page_texts(conn, citations)
    for citation in citations:
        text = page_texts.get((citation["documentId"], int(citation["page"])))
        if text is None or not citation["quote"].strip() or citation["quote"] not in text:
            raise ValueError(f"citation quote is absent from {citation['documentId']} page {citation['page']}")


def _first_quote(text: str, title: str) -> str:
    if not text.strip():
        return ""
    # Match the title across line breaks while keeping the verbatim source span.
    words = re.findall(r"[\w]+", title.casefold())
    if not words:
        return ""
    fragments = [re.escape(word) for word in words]
    pattern = r"\b" + r"[\W_]+".join(fragments) + r"\b"
    match = re.search(pattern, text, re.I)
    if match:
        return text[match.start():match.end()][:300].strip()
    # Minor wording differences are common. Require strong token overlap on
    # adjacent source lines; a packet header alone is never evidence.
    significant = {w for w in words if len(w) > 3 and w not in {"with", "from", "that", "this", "through", "review", "approve"}}
    if not significant:
        return ""
    lines = list(re.finditer(r"[^\n]+", text))
    best = (0.0, "")
    for pos in range(len(lines)):
        for end in range(pos + 1, min(pos + 3, len(lines)) + 1):
            span = text[lines[pos].start():lines[end - 1].end()].strip()
            tokens = set(re.findall(r"[\w]+", span.casefold()))
            score = len(tokens & significant) / len(significant)
            if score > best[0]:
                best = (score, span)
    return best[1][:300] if best[0] >= 0.65 else ""


# A packet page reference in a right-hand column: a wide gap after the line's text, then "Page 12", "Page", or "12"
# ending the line. A number that belongs to the title ("Resolution 2026-12", "of\n   2026") has no such gap.
PAGE_COLUMN_REFERENCE = re.compile(r"(?<=\S)[ \t]{3,}(?:Page(?:[ \t]+\d{1,3})?|\d{1,3})[ \t]*$", re.M)
# A row inside a quote that holds only an item label and/or a page number: a table's label and page columns beside
# a wrapped title (NTCD prints "A ... 5" between a title's two lines).
LABEL_ROW = re.compile(r"^[ \t]*(?:[A-Z]|\d{1,3})(?:[ \t]{3,}\d{1,3})?[ \t]*\n", re.M)
EXCERPT_METHOD = "deterministic-source-excerpt-v1"


def excerpt_text(quote: str) -> str:
    """A citation quote as shown in an excerpt: verbatim, less the page-number and label columns it spans."""
    return PAGE_COLUMN_REFERENCE.sub("", LABEL_ROW.sub("", quote))


def _summary_from_citations(citations: list[dict]) -> str:
    # This is a plainly labeled source excerpt, not an inferred account of an outcome.
    return f"Agenda excerpt: “{excerpt_text(citations[0]['quote'])}”"


def _tags_stale(previous: dict, evidence: dict) -> bool:
    """Stored generated tags (topic -> item_topics row) differ in set, classifier version, or evidence."""
    return set(previous) != set(evidence) or any(
        previous[topic]["source"] != CLASSIFIER_VERSION or _json(previous[topic]["evidence"]) != detail
        for topic, detail in evidence.items())


def _replace_topics(conn, item_id: str, agency_id: str, title: str, citations: list[dict]) -> bool:
    """Replace generated tags only; operator overrides are an independent layer."""
    evidence = classify(agency_id, title, citations)
    previous = {r["topic_id"]: r for r in conn.execute(
        "SELECT topic_id,source,evidence FROM item_topics WHERE item_id=%s", (item_id,))}
    changed = set(previous) != set(evidence)
    if _tags_stale(previous, evidence):
        conn.execute("DELETE FROM item_topics WHERE item_id=%s", (item_id,))
        for topic, detail in sorted(evidence.items()):
            conn.execute("INSERT INTO item_topics(item_id,topic_id,source,evidence) VALUES (%s,%s,%s,%s)",
                         (item_id, topic, CLASSIFIER_VERSION, json.dumps(detail)))
    conn.execute("UPDATE agenda_items SET classifier_version=%s WHERE id=%s AND classifier_version IS DISTINCT FROM %s",
                 (CLASSIFIER_VERSION, item_id, CLASSIFIER_VERSION))
    return changed


def ingest_bundle(conn: psycopg.Connection, bundle: dict, *, mode: str = "fixture") -> dict:
    results = {"processed": 0, "failed": 0, "errors": []}
    errors_by_agency = {}
    for row in bundle["meetings"]:
        aid = row.get("agency") or row.get("agency_id")
        source_id = str(row.get("id") or row.get("source_id"))
        meeting_id = stable_id(aid, source_id)
        try:
            with conn.transaction():
                _ingest_meeting(conn, row, aid, source_id, meeting_id, mode, bundle.get("captured_at"))
            results["processed"] += 1
        except Exception as exc:
            results["failed"] += 1
            results["errors"].append(f"{meeting_id}: {exc}")
            if aid in AGENCIES:
                errors_by_agency[aid] = str(exc)[:1000]
            with conn.transaction():
                conn.execute("""INSERT INTO processing_runs(agency_id,meeting_id,stage,status,error,finished_at)
                    VALUES (%s,%s,'ingest','failed',%s,now())""",
                    (aid if aid in AGENCIES else None, meeting_id if conn.execute("SELECT 1 FROM meetings WHERE id=%s", (meeting_id,)).fetchone() else None, str(exc)[:1000]))
                if aid in AGENCIES:
                    conn.execute("UPDATE agencies SET status='error', error=%s WHERE id=%s", (str(exc)[:1000], aid))
    for aid, error in errors_by_agency.items():
        with conn.transaction():
            conn.execute("UPDATE agencies SET status='error',error=%s WHERE id=%s", (error, aid))
    return results


def record_failure(conn, agency_id: str, source_id: str, stage: str, error: str) -> None:
    meeting_id = stable_id(agency_id, source_id)
    existing = conn.execute("SELECT id FROM meetings WHERE id=%s", (meeting_id,)).fetchone()
    with conn.transaction():
        conn.execute("""INSERT INTO processing_runs(agency_id,meeting_id,stage,status,error,finished_at)
            VALUES (%s,%s,%s,'failed',%s,now())""",
            (agency_id, meeting_id if existing else None, stage, error[:1000]))
        conn.execute("UPDATE agencies SET status='error',error=%s WHERE id=%s",
                     (error[:1000], agency_id))


def record_oversized(conn, agency_id: str, source_id: str, url: str, reason: str) -> None:
    """Record a packet left out for being over the capture limits, so it is not downloaded again.

    Not a failure: the agency's status is untouched. Written after the meeting is
    ingested so its ID resolves; a URL already recorded for the meeting is skipped.
    """
    meeting_id = stable_id(agency_id, source_id)
    with conn.transaction():
        conn.execute("""INSERT INTO processing_runs(agency_id,meeting_id,stage,status,finished_at,detail)
            SELECT %s,m.id,'capture','oversized',now(),%s FROM meetings m WHERE m.id=%s AND NOT EXISTS (
                SELECT 1 FROM processing_runs r WHERE r.meeting_id=m.id AND r.stage='capture'
                AND r.status='oversized' AND r.detail->>'url'=%s)""",
            (agency_id, json.dumps({"url": url, "reason": reason[:1000]}), meeting_id, url))


def _ingest_meeting(conn, row, aid, source_id, meeting_id, mode, captured_at=None):
    if aid not in AGENCIES:
        raise ValueError(f"unknown agency {aid}")
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (meeting_id,))
    old = conn.execute("SELECT * FROM meetings WHERE id=%s", (meeting_id,)).fetchone()
    status = row.get("status") or "unknown"
    partial = bool(row.get("partial_logistics"))
    if partial and old and status == "unknown":
        status = old["status"]
    if status not in {"scheduled", "canceled", "rescheduled", "unknown"}:
        raise ValueError(f"invalid meeting status {status}")
    start = starts_at(row)
    if partial and old and start is None and (not row.get("date") or
            (old["local_date"] and date.fromisoformat(row["date"]) == old["local_date"])):
        start = old["starts_at"]
    zone_name = row.get("timezone") or "America/Los_Angeles"
    local_date = date.fromisoformat(row["date"]) if row.get("date") else (
        start.astimezone(ZoneInfo(zone_name)).date() if start else None)
    if partial and old and local_date is None:
        local_date = old["local_date"]
    ts = now()
    checked_at = ts if mode == "live" else (
        datetime.fromisoformat(row["source_checked_at"].replace("Z", "+00:00"))
        if row.get("source_checked_at") else None)
    date_moved = bool(old and old["local_date"] and local_date and old["local_date"] != local_date)
    start_moved = bool(old and old["starts_at"] and start and old["starts_at"] != start)
    change = "new" if not old else ("canceled" if status == "canceled" and old["status"] != status else
        "rescheduled" if date_moved or start_moved else
        "revised" if old["title"] != row["title"] or old["status"] != status else old["change"])
    participation = row.get("participation") or {"instructions": row.get("participation_instructions"), "url": row.get("participation_url"), "remoteUrl": row.get("remote_url"), "deadline": row.get("deadline")}
    if partial and old and not row.get("participation") and not any(participation.values()):
        participation = old["participation"]
    location = row.get("location") if row.get("location") is not None else old["location"] if partial and old else None
    host = row.get("host") if row.get("host") is not None else old["host"] if partial and old else None
    if host is not None and (not isinstance(host, str) or not host.strip()):
        raise ValueError("meeting host must be a non-empty string")
    if aid in per_record_host_agencies() and not host:
        raise ValueError(f"meetings under {aid} need a host")
    # A capture without a link never removes one; see tahoe_ingest.records for precedence.
    links = merged_links(dict(old) if old else {}, links_of(row))
    # Warnings are shown to residents as written; review notes (parser outcomes for the operator) are not (decision 0041).
    # Like warnings, a row's review notes replace the stored ones.
    meeting_warnings = list(row.get("warnings", []))
    if row.get("source_warning"):
        meeting_warnings.append(row["source_warning"])
    review_notes = list(dict.fromkeys(row.get("review_notes", [])))
    conn.execute("""INSERT INTO meetings(id,agency_id,source_id,title,starts_at,local_date,timezone,status,location,official_url,participation,minutes_url,minutes_status,minutes_note,recording_url,last_checked_at,change,warnings,review_notes,host)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title, starts_at=excluded.starts_at, local_date=excluded.local_date, timezone=excluded.timezone,
        status=excluded.status, location=excluded.location, official_url=excluded.official_url,
        participation=excluded.participation, minutes_url=excluded.minutes_url, minutes_status=excluded.minutes_status,
        minutes_note=excluded.minutes_note, recording_url=excluded.recording_url,
        last_checked_at=GREATEST(excluded.last_checked_at,meetings.last_checked_at), change=excluded.change, warnings=excluded.warnings,
        review_notes=excluded.review_notes, host=excluded.host,
        updated_at=CASE WHEN meetings.title IS DISTINCT FROM excluded.title OR meetings.starts_at IS DISTINCT FROM excluded.starts_at
          OR meetings.local_date IS DISTINCT FROM excluded.local_date
          OR meetings.status IS DISTINCT FROM excluded.status OR meetings.participation IS DISTINCT FROM excluded.participation
          OR meetings.location IS DISTINCT FROM excluded.location OR meetings.host IS DISTINCT FROM excluded.host
          OR meetings.official_url IS DISTINCT FROM excluded.official_url
          OR meetings.minutes_url IS DISTINCT FROM excluded.minutes_url OR meetings.minutes_status IS DISTINCT FROM excluded.minutes_status
          OR meetings.minutes_note IS DISTINCT FROM excluded.minutes_note OR meetings.recording_url IS DISTINCT FROM excluded.recording_url
          THEN now() ELSE meetings.updated_at END""",
        (meeting_id, aid, source_id, row["title"], start, local_date, zone_name, status,
         location, row.get("source_meeting_url") or row.get("official_url") or (old["official_url"] if old else None), json.dumps(participation),
         links["minutes_url"], links["minutes_status"], links["minutes_note"], links["recording_url"],
         checked_at, change, json.dumps(meeting_warnings), json.dumps(review_notes), host))

    if "topics" in row:
        supplied_topics = row["topics"]
        if not isinstance(supplied_topics, list) or not set(supplied_topics) <= topic_ids():
            raise ValueError("meeting topics must be a list of active topic IDs")
        conn.execute("""UPDATE meetings SET declared_topics=%s,updated_at=clock_timestamp()
            WHERE id=%s AND declared_topics IS DISTINCT FROM %s::jsonb""",
            (json.dumps(supplied_topics), meeting_id, json.dumps(supplied_topics)))

    # Rereading an unchanged meeting is the common case, so current state is read up front
    # and statements that would rewrite identical rows are skipped.
    current_versions = {r["source_document_id"]: r for r in conn.execute(
        """SELECT c.source_document_id,c.version_id,v.url,(SELECT count(*) FROM document_pages p WHERE p.document_version_id=c.version_id) AS pages
        FROM document_current c JOIN document_versions v ON v.id=c.version_id WHERE c.meeting_id=%s""", (meeting_id,))}
    previous_document_ids = set(current_versions)
    version_ids = {}
    revised_documents = set()
    changed_items = False
    changed_topics = False
    page_rows = {(p["document_id"], int(p["page_number"])): p["text"] for p in row.get("pages", [])}
    page_links = {(p["document_id"], int(p["page_number"])): _page_links(p) for p in row.get("pages", []) if p.get("links")}
    for doc in row.get("documents", []):
        doc_id = str(doc["id"])
        hash_value = doc["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", hash_value):
            raise ValueError(f"invalid SHA-256 for {doc_id}")
        version_id = stable_id(meeting_id, doc_id, hash_value[:16])
        current = current_versions.get(doc_id)
        if current and current["version_id"] != version_id:
            revised_documents.add(doc_id)
        page_count = int(doc.get("page_count") or 0)
        pages = [(n, text) for (d, n), text in page_rows.items() if d == doc_id]
        if any(n > page_count for n, _ in pages):
            raise ValueError(f"page number exceeds page count for {doc_id}")
        warnings = list(doc.get("warnings", []))
        if len(pages) < page_count:
            warnings.append(f"Text retained for {len(pages)} of {page_count} source pages; remaining pages are not represented in this export.")
        fetched_at = doc.get("fetched_at")
        if not fetched_at:
            if not captured_at:
                raise ValueError(f"{doc_id} has neither fetched_at nor capture timestamp")
            fetched_at = captured_at
            warnings.append("Original fetch time is unknown; the timestamp shown is when this archived record was captured.")
        if not pages or not any(text.strip() for _, text in pages):
            raise ValueError(f"no extracted pages for {doc_id}; refusing empty publication")
        version_ids[doc_id] = version_id
        links = {n: page_links[(doc_id, n)] for n, _ in pages if (doc_id, n) in page_links}
        if current and current["version_id"] == version_id and current["pages"] >= len(pages):
            if current["url"] != doc["url"]:
                # The same file at a new address (South Lake Tahoe's agenda viewer link for the
                # attachment URL stored earlier): the link follows, so the meeting can settle.
                conn.execute("UPDATE document_versions SET url=%s WHERE id=%s", (doc["url"], version_id))
            if links:
                # Pages stored before link annotations were read get them on the next capture of the same file.
                conn.execute("""UPDATE document_pages d SET links=p.links::jsonb
                    FROM unnest(%s::int[],%s::text[]) AS p(n,links)
                    WHERE d.document_version_id=%s AND d.page_number=p.n AND d.links IS DISTINCT FROM p.links::jsonb""",
                    (list(links), [json.dumps(v) for v in links.values()], version_id))
            continue  # The same file with all of its pages is already current.
        # A stored version captured again with more of its pages takes the new capture's warnings,
        # which describe the pages now stored ("Text retained for 3 of 383" would otherwise outlive them).
        conn.execute("""INSERT INTO document_versions(id,meeting_id,source_document_id,title,url,sha256,fetched_at,page_count,status,warnings,media_type)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'extracted',%s,%s)
            ON CONFLICT(id) DO UPDATE SET warnings=excluded.warnings
            WHERE document_versions.warnings IS DISTINCT FROM excluded.warnings""",
            (version_id, meeting_id, doc_id, doc.get("title") or doc.get("kind") or "Official document", doc["url"], hash_value,
             datetime.fromisoformat(fetched_at.replace("Z", "+00:00")), page_count, json.dumps(warnings), doc.get("media_type", "application/pdf")))
        conn.execute("""INSERT INTO document_pages(document_version_id,page_number,text,links)
            SELECT %s,n,text,links::jsonb FROM unnest(%s::int[],%s::text[],%s::text[]) AS p(n,text,links)
            ON CONFLICT(document_version_id,page_number) DO NOTHING""",
            (version_id, [n for n, _ in pages], [text for _, text in pages], [json.dumps(links.get(n, [])) for n, _ in pages]))
        conn.execute("""INSERT INTO document_current(meeting_id,source_document_id,version_id) VALUES (%s,%s,%s)
            ON CONFLICT(meeting_id,source_document_id) DO UPDATE SET version_id=excluded.version_id""", (meeting_id, doc_id, version_id))

    incoming_items = []
    for item in row.get("agenda_items", row.get("items", [])):
        source_key = str(item.get("source_key") or item.get("source_number") or f"unnumbered-{item['order']}")
        citations = []
        for ref in item.get("source_pages", []):
            doc_id = str(ref["document_id"])
            version_id = version_ids.get(doc_id)
            if not version_id:
                raise ValueError(f"item {source_key} references missing document {doc_id}")
            page_num = int(ref.get("page_number") or ref.get("page"))
            page_text = page_rows.get((doc_id, page_num), "")
            # A placeholder for a skipped number has no source text; never search the page for its title.
            quote = ref.get("quote") or ("" if is_placeholder(item) else _first_quote(page_text, item["title"]))
            if quote:
                citations.append(_citation(version_id, page_num, quote))
        incoming_items.append((item, source_key, citations))
    stored_items = {r["id"]: r for r in conn.execute("SELECT * FROM agenda_items WHERE meeting_id=%s", (meeting_id,))}
    page_texts = stored_page_texts(conn, [c for _, _, citations in incoming_items for c in citations])
    seen_items = set()
    legacy = []  # (item ID, fingerprint stored under legacy_fingerprint, its item_fingerprint)
    moves = []  # (item ID, stored citations, the same pages and quotes cited from other document versions)
    for item, source_key, citations in incoming_items:
        item_id = stable_id(meeting_id, source_key)
        # A repeated key sees the row its first occurrence just wrote.
        existing = (conn.execute("SELECT * FROM agenda_items WHERE id=%s", (item_id,)).fetchone()
                    if item_id in seen_items else stored_items.get(item_id))
        seen_items.add(item_id)
        fingerprint = item_fingerprint(item["title"], item.get("section"), citations)
        stored_fingerprint = existing["source_fingerprint"] if existing else None
        if existing and stored_fingerprint != fingerprint and \
                stored_fingerprint == legacy_fingerprint(item["title"], item.get("section"), citations):
            # Stored under the old formula from these same citations (migrate_fingerprints could not recompute
            # it, or an older worker wrote it): the same item, whose derived rows move to the new fingerprint.
            legacy.append((item_id, stored_fingerprint, fingerprint))
            stored_fingerprint = fingerprint
        refreshed = (not existing or stored_fingerprint != fingerprint or
                     existing["item_order"] != int(item["order"]) or not existing["active"])
        changed = refreshed
        changed_items |= changed
        item_change = "new" if not existing else "revised" if changed else existing["change"]
        try:
            validate_citations(conn, citations, page_texts)
            valid_citations = citations
            summary_status, summary_error = "published", None
        except ValueError as exc:
            valid_citations = []
            summary_status, summary_error = "validation_failed", str(exc)
        stored_citations = _json(existing["citations"]) if existing else []
        if existing and valid_citations and stored_citations != valid_citations:
            if stored_citations and cited_text(stored_citations) == cited_text(valid_citations):
                # The same pages and quotes, cited from another version or copy of the agenda: not a change, but
                # what cited the old version follows (_move_citations).
                moves.append((item_id, stored_citations, valid_citations))
            else:
                # Restore evidence after a document-only refresh invalidated this
                # item's citations, even when its source fingerprint is unchanged.
                changed = True
                changed_items = True
                item_change = "revised"
        number = item.get("source_number") or ""
        # The whole text of a title the parser cut short. It is display text derived from the same rows as the
        # title, so it stays out of the fingerprint: adding it never re-maps, re-summarizes, or re-classifies an item.
        full_title = item.get("full_title") or None
        unchanged_row = (existing and not changed and existing["number"] == number and existing["title"] == item["title"]
                         and existing["full_title"] == full_title and existing["source_fingerprint"] == fingerprint
                         and existing["section"] == item.get("section") and existing["change"] == item_change
                         and stored_citations == valid_citations)
        if not unchanged_row:
            conn.execute("""INSERT INTO agenda_items(id,meeting_id,source_key,number,item_order,title,section,source_fingerprint,citations,change,full_title)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET number=excluded.number,item_order=excluded.item_order,title=excluded.title,
                full_title=excluded.full_title,section=excluded.section,source_fingerprint=excluded.source_fingerprint,citations=excluded.citations,active=true,
                change=excluded.change,updated_at=CASE WHEN %s THEN clock_timestamp() ELSE agenda_items.updated_at END""",
                (item_id, meeting_id, source_key, number, int(item["order"]), item["title"], item.get("section"),
                 fingerprint, json.dumps(valid_citations), item_change, full_title, refreshed))
        if is_placeholder(item):
            # No summary, topics, or citation check for a number the agenda skips (decision 0032).
            conn.execute("DELETE FROM summaries WHERE item_id=%s", (item_id,))
            conn.execute("DELETE FROM item_topics WHERE item_id=%s", (item_id,))
            conn.execute("UPDATE agenda_items SET classifier_version=%s WHERE id=%s AND classifier_version IS DISTINCT FROM %s",
                         (CLASSIFIER_VERSION, item_id, CLASSIFIER_VERSION))
            continue
        if changed:
            summary_text = _summary_from_citations(citations) if summary_status == "published" else ""
            conn.execute("""INSERT INTO summaries(item_id,text,status,method,generated_at,source_fingerprint,citations,error)
                VALUES (%s,%s,%s,'deterministic-source-excerpt-v1',now(),%s,%s,%s)
                ON CONFLICT(item_id) DO UPDATE SET text=excluded.text,status=excluded.status,method=excluded.method,
                generated_at=excluded.generated_at,source_fingerprint=excluded.source_fingerprint,citations=excluded.citations,error=excluded.error""",
                (item_id, summary_text, summary_status, fingerprint, json.dumps(valid_citations), summary_error))
            conn.execute("""INSERT INTO summary_versions(item_id,text,status,method,source_fingerprint,citations,error)
                VALUES (%s,%s,%s,'deterministic-source-excerpt-v1',%s,%s,%s)""",
                (item_id, summary_text, summary_status, fingerprint, json.dumps(valid_citations), summary_error))
        if changed or existing["classifier_version"] != CLASSIFIER_VERSION:
            topic_set_changed = _replace_topics(conn, item_id, aid, item["title"], valid_citations)
            changed_topics |= topic_set_changed and not changed
            if topic_set_changed and not changed:
                conn.execute("UPDATE agenda_items SET updated_at=clock_timestamp() WHERE id=%s", (item_id,))
    rebind_fingerprints(conn, legacy)
    if moves:
        document_of = {version: document for document, version in version_ids.items()}
        _move_citations(conn, moves, lambda version, page: page_rows.get((document_of.get(version), page)))
    items_complete = row.get("items_complete", bool(row.get("documents")))
    withdrawn_count = 0
    if items_complete and any(r["active"] and r["id"] not in seen_items for r in stored_items.values()):
        if seen_items:
            withdrawn_count = conn.execute("""UPDATE agenda_items SET active=false,updated_at=clock_timestamp()
                WHERE meeting_id=%s AND active=true AND NOT (id=ANY(%s))""",
                (meeting_id, list(seen_items))).rowcount
        else:
            withdrawn_count = conn.execute("""UPDATE agenda_items SET active=false,updated_at=clock_timestamp()
                WHERE meeting_id=%s AND active=true""", (meeting_id,)).rowcount
    incoming_document_ids = {str(d["id"]) for d in row.get("documents", [])}
    source_set_changed = bool(incoming_document_ids - previous_document_ids or
                              (row.get("documents_complete", bool(row.get("documents"))) and
                               previous_document_ids - incoming_document_ids))
    if row.get("documents_complete", bool(row.get("documents"))) and previous_document_ids - incoming_document_ids:
        conn.execute("""DELETE FROM document_current WHERE meeting_id=%s AND NOT (source_document_id=ANY(%s))""",
                     (meeting_id, list(incoming_document_ids)))
    if (revised_documents or source_set_changed) and not items_complete:
        # A PDF changed before its items were parsed. Never leave its old summary published.
        invalidated = 0
        for item_row in conn.execute("SELECT id,citations,active FROM agenda_items WHERE meeting_id=%s", (meeting_id,)):
            if item_row["id"] in seen_items:
                # These items were just processed against incoming source text.
                # A partial item list only leaves the other items unprocessed.
                continue
            refs = _json(item_row["citations"])
            if source_set_changed or any(any(c["documentId"].startswith(stable_id(meeting_id, d, "")) for d in revised_documents) for c in refs):
                conn.execute("""UPDATE summaries SET text='',status='pending',citations='[]'::jsonb,
                    error='Source PDF revised; item requires reprocessing' WHERE item_id=%s""", (item_row["id"],))
                conn.execute("""INSERT INTO summary_versions(item_id,text,status,method,source_fingerprint,citations,error)
                    SELECT item_id,'','pending',method,source_fingerprint,'[]'::jsonb,
                    'Source PDF revised; item requires reprocessing' FROM summaries WHERE item_id=%s""", (item_row["id"],))
                conn.execute("UPDATE agenda_items SET change='revised',updated_at=now(),citations='[]'::jsonb WHERE id=%s", (item_row["id"],))
                invalidated += bool(item_row["active"])
        if invalidated and REPROCESSING_WARNING not in meeting_warnings:
            # Only when a published item lost its summary; a meeting without items has nothing awaiting reprocessing.
            conn.execute("UPDATE meetings SET warnings=warnings || %s::jsonb WHERE id=%s", (json.dumps([REPROCESSING_WARNING]), meeting_id))
    if old and (revised_documents or source_set_changed or changed_items or withdrawn_count or changed_topics):
        _touch_meeting(conn, meeting_id)
    if mode == "live":
        conn.execute("UPDATE agencies SET status='ok',error=NULL,last_checked_at=%s WHERE id=%s", (ts, aid))
    else:
        conn.execute("""UPDATE agencies SET last_checked_at=(SELECT max(last_checked_at) FROM meetings WHERE agency_id=%s),
            status=CASE WHEN (SELECT max(last_checked_at) FROM meetings WHERE agency_id=%s) IS NULL
                THEN 'never_checked' ELSE 'ok' END,error=NULL WHERE id=%s""", (aid, aid, aid))
    conn.execute("""INSERT INTO processing_runs(agency_id,meeting_id,stage,status,finished_at,detail)
        VALUES (%s,%s,'ingest','succeeded',now(),%s)""", (aid, meeting_id, json.dumps({"mode": mode, "revised_documents": sorted(revised_documents)})))


def _move_citations(conn, moves: list[tuple[str, list, list]], page_text) -> None:
    """Point what cited an unchanged item's old document versions at the ones it cites now (three statements at most).

    ``moves`` holds (item ID, stored citations, current citations) with equal pages and quotes. The item's agenda
    excerpt follows, and so does a Claude summary's citation of an old version (the requested action it leads
    with) once its quote is on the same page of the new version (``page_text(version, page)``). A summary whose
    citation cannot follow is unbound from the fingerprint, which hides it until ``item_summaries`` rebinds or
    rewrites it, as after any agenda change; the export never cites a version it does not carry.
    """
    conn.execute("""UPDATE summaries s SET citations=r.new FROM jsonb_to_recordset(%s::jsonb) AS r(item_id text,old jsonb,new jsonb)
        WHERE s.item_id=r.item_id AND s.citations=r.old""",
                 (json.dumps([{"item_id": i, "old": old, "new": new} for i, old, new in moves]),))
    versions = {}
    for item_id, old, new in moves:
        pairs = {(a["documentId"], b["documentId"]) for a, b in zip(old, new) if a["documentId"] != b["documentId"]}
        sources = [a for a, _ in pairs]
        # One old version now cited from two new ones cannot be followed.
        versions[item_id] = dict(pairs) if len(sources) == len(set(sources)) else dict.fromkeys(sources)
    updates, unbound = [], []
    for r in conn.execute("SELECT item_id,citations FROM ai_item_summaries WHERE item_id=ANY(%s)", (list(versions),)):
        mapping, followed = versions[r["item_id"]], []
        for citation in _json(r["citations"]) or []:
            if citation.get("documentId") in mapping:
                version = mapping[citation["documentId"]]
                text = page_text(version, int(citation["page"])) if version else None
                if not text or not citation.get("quote") or citation["quote"] not in text:
                    unbound.append(r["item_id"])
                    break
                citation = {**citation, "documentId": version}
            followed.append(citation)
        else:
            if followed != (_json(r["citations"]) or []):
                updates.append({"item_id": r["item_id"], "citations": followed})
    if updates:
        conn.execute("""UPDATE ai_item_summaries x SET citations=r.citations
            FROM jsonb_to_recordset(%s::jsonb) AS r(item_id text,citations jsonb) WHERE x.item_id=r.item_id""", (json.dumps(updates),))
    if unbound:
        conn.execute("UPDATE ai_item_summaries SET source_fingerprint='moved:'||source_fingerprint WHERE item_id=ANY(%s)", (unbound,))


def _page_links(page: dict) -> list[str]:
    """A page's embedded web links (``source.link_annotations``): a list of http(s) URLs."""
    links = page["links"]
    if not isinstance(links, list) or not all(isinstance(u, str) and re.fullmatch(r"https?://\S+", u) for u in links):
        raise ValueError(f"page links must be a list of http(s) URLs: {page['document_id']} page {page['page_number']}")
    return links


def _json(value):
    if value is None:
        return None
    return value if isinstance(value, (dict, list)) else json.loads(value)


def _touch_meeting(conn, meeting_id: str) -> None:
    _touch_meetings(conn, [meeting_id])


def _touch_meetings(conn, meeting_ids) -> None:
    conn.execute("""UPDATE meetings SET updated_at=clock_timestamp(),
        change=CASE WHEN change IN ('canceled','rescheduled') THEN change
                    WHEN status='canceled' THEN 'canceled'
                    WHEN status='rescheduled' THEN 'rescheduled'
                    ELSE 'revised' END
        WHERE id = ANY(%s)""", (sorted(meeting_ids),))


def reclassify_topics(conn, *, dry_run=False) -> dict:
    """Upgrade generated topic tags without altering source or summary versions.

    Any topic catalog edit changes CLASSIFIER_VERSION and so upgrades every item;
    writes therefore go in bulk, a fixed number of statements whatever the archive
    size (decision 0020). An agency's topicHints are not in the version: every item's
    stored evidence is compared instead, so a hint edit rewrites only the items whose
    tags or their provenance (rule name, matched text) change.
    """
    rows = list(conn.execute(f"""SELECT i.id,i.meeting_id,i.title,i.citations,i.classifier_version,m.agency_id
        FROM agenda_items i JOIN meetings m ON m.id=i.meeting_id WHERE i.active AND {NOT_PLACEHOLDER_SQL} ORDER BY i.id"""))
    before = {t["id"]: 0 for t in TOPICS}
    after = dict(before)
    changed = 0
    upgraded = 0
    evidence_only = 0
    stored = {}
    for r in conn.execute("SELECT item_id,topic_id,source,evidence FROM item_topics"):
        stored.setdefault(r["item_id"], {})[r["topic_id"]] = r
    pending = []
    for item in rows:
        old = set(stored.get(item["id"], {}))
        evidence = classify(item["agency_id"], item["title"], _json(item["citations"]))
        new = set(evidence)
        for topic in old:
            before[topic] = before.get(topic, 0) + 1
        for topic in new:
            after[topic] += 1
        stale = _tags_stale(stored.get(item["id"], {}), evidence)
        if old != new:
            changed += 1
        if item["classifier_version"] != CLASSIFIER_VERSION:
            upgraded += 1
        elif stale and old == new:
            evidence_only += 1
        if stale or item["classifier_version"] != CLASSIFIER_VERSION:
            pending.append((item, evidence))
    if pending and not dry_run:
        with conn.transaction():
            # Ingestion rewrites a meeting's items under that meeting's lock; take every lock needed, in order, first.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(m,0)) FROM unnest(%s::text[]) AS m",
                         (sorted({item["meeting_id"] for item, _ in pending}),))
            previous = {}
            for r in conn.execute("SELECT item_id,topic_id,source,evidence FROM item_topics WHERE item_id = ANY(%s)",
                                  ([item["id"] for item, _ in pending],)):
                previous.setdefault(r["item_id"], {})[r["topic_id"]] = r
            stale = [(item, evidence) for item, evidence in pending if _tags_stale(previous.get(item["id"], {}), evidence)]
            retagged = [item for item, evidence in pending if set(previous.get(item["id"], {})) != set(evidence)]
            if stale:
                conn.execute("DELETE FROM item_topics WHERE item_id = ANY(%s)", ([item["id"] for item, _ in stale],))
                conn.execute("""INSERT INTO item_topics(item_id,topic_id,source,evidence)
                    SELECT item_id,topic_id,%s,evidence FROM jsonb_to_recordset(%s::jsonb) AS t(item_id text,topic_id text,evidence jsonb)""",
                    (CLASSIFIER_VERSION, json.dumps([{"item_id": item["id"], "topic_id": topic, "evidence": detail}
                                                     for item, evidence in stale for topic, detail in sorted(evidence.items())])))
            conn.execute("UPDATE agenda_items SET classifier_version=%s WHERE id = ANY(%s) AND classifier_version IS DISTINCT FROM %s",
                         (CLASSIFIER_VERSION, [item["id"] for item, _ in pending], CLASSIFIER_VERSION))
            if retagged:
                conn.execute("UPDATE agenda_items SET updated_at=clock_timestamp() WHERE id = ANY(%s)", ([item["id"] for item in retagged],))
                _touch_meetings(conn, {item["meeting_id"] for item in retagged})
    return {"classifierVersion": CLASSIFIER_VERSION, "dryRun": dry_run, "itemsScanned": len(rows),
            "itemsChanged": changed, "itemsUpgraded": upgraded, "itemsEvidenceChanged": evidence_only,
            "automaticTopicsBefore": before, "automaticTopicsAfter": after}


def agency_freshness(agency: dict, status: str, error: str | None) -> tuple[str, str | None]:
    """The source status the export shows for a registry entry.

    An agency whose discovery index or source page is in config/skipped-endpoints.json
    is ``blocked``, with the skip reason as its error: the worker no longer requests
    that source, so an older "ok" would claim a freshness no check supports. The
    stored check results are left as they are, so removing the skip restores them.
    """
    for url in ((agency.get("discovery") or {}).get("indexUrl"), agency.get("sourceUrl")):
        if url and (reason := skipped_endpoint(url)):
            return "blocked", reason
    return status, error


def public_agency(row, fallback_url: str) -> dict:
    """One `agencies[]` entry: the stored row's source state plus the registry's own coverage note, if any."""
    status, error = agency_freshness(AGENCIES[row["id"]], row["status"], row["error"])
    agency = {"id": row["id"], "name": row["name"], "shortName": row["short_name"], "officialUrl": row["official_url"] or fallback_url,
              "sourceUrl": row["source_url"] or fallback_url, "lastCheckedAt": iso(row["last_checked_at"]), "status": status, "error": error}
    if note := AGENCIES[row["id"]].get("coverageNote"):
        agency["coverageNote"] = note
    return agency


def export_public(conn, *, mode="live", forecast_days: int = 0, today: date | None = None) -> dict:
    """Export stored records, plus derived forecasts when ``forecast_days`` > 0.

    The CLI export defaults to ``DEFAULT_FORECAST_DAYS``. Forecasts are never
    written to the database; see ``schedules.forecast_meetings``.
    """
    stamp = now()
    generated_at = iso(stamp)
    from .comment_periods import export_comment_periods
    comment_periods = export_comment_periods(conn)
    comment_agencies = {p["agencyId"] for p in comment_periods}
    # Every hosted query is a billed operation, so each table is read once in bulk
    # and grouped here; the query count does not grow with the archive.
    first_url = {r["agency_id"]: r["official_url"] for r in conn.execute(
        """SELECT DISTINCT ON (agency_id) agency_id,official_url FROM meetings
        WHERE quarantine_reason IS NULL ORDER BY agency_id,id""")}
    agencies = []
    for a in conn.execute("SELECT * FROM agencies ORDER BY id"):
        # Keep unimplemented watchlist entries out of public coverage claims.
        if a["id"] not in AGENCIES or (a["id"] not in comment_agencies and a["id"] not in first_url):
            continue
        fallback = first_url.get(a["id"]) or next(p["officialUrl"] for p in comment_periods if p["agencyId"] == a["id"])
        agencies.append(public_agency(a, fallback))
    pages_by_version = {}
    first_page_links = {}  # embedded links on each current document's page 1, for the join link rule
    for p in conn.execute("""SELECT p.document_version_id,p.page_number,p.text,p.links FROM document_pages p
            WHERE p.document_version_id IN (SELECT version_id FROM document_current) ORDER BY p.document_version_id,p.page_number"""):
        pages_by_version.setdefault(p["document_version_id"], []).append({"number": p["page_number"], "text": p["text"]})
        if p["page_number"] == 1:
            first_page_links[p["document_version_id"]] = _json(p["links"]) or []
    docs_by_meeting = {}
    for d in conn.execute("""SELECT c.meeting_id,v.* FROM document_current c JOIN document_versions v ON v.id=c.version_id
            ORDER BY c.meeting_id,v.source_document_id"""):
        docs_by_meeting.setdefault(d["meeting_id"], []).append({"id": d["id"], "title": d["title"], "url": d["url"], "sha256": d["sha256"],
            "mediaType": d["media_type"], "fetchedAt": iso(d["fetched_at"]), "pageCount": d["page_count"], "status": d["status"],
            "warnings": _json(d["warnings"]), "pages": pages_by_version.get(d["id"], [])})
    history_by_meeting = document_history(conn.execute(HISTORY_SQL), iso)
    items_by_meeting = {}
    for i in conn.execute("""SELECT i.*,s.text AS summary_text,s.status AS summary_status,s.method,
            s.generated_at,s.citations AS summary_citations,e.suppressed,e.correction
            FROM agenda_items i LEFT JOIN summaries s ON s.item_id=i.id LEFT JOIN editorial e ON e.item_id=i.id
            WHERE i.active ORDER BY i.meeting_id,i.item_order,i.id"""):
        items_by_meeting.setdefault(i["meeting_id"], []).append(i)
    claude_topics = {r["item_id"]: r["topics"] for r in conn.execute(
        """SELECT a.item_id,a.topics FROM ai_topic_classifications a
        JOIN agenda_items i ON i.id=a.item_id AND i.source_fingerprint=a.source_fingerprint""")}
    agenda_participation = {r["meeting_id"]: r for r in conn.execute("SELECT meeting_id,version_ids,fields FROM ai_participation")}
    rule_topics = {}
    for r in conn.execute("SELECT item_id,topic_id FROM item_topics"):
        rule_topics.setdefault(r["item_id"], set()).add(r["topic_id"])
    overrides = {}
    for r in conn.execute("SELECT item_id,topic_id,included FROM topic_overrides"):
        overrides.setdefault(r["item_id"], []).append(r)
    from .item_summaries import Supporting
    from .requested_actions import for_meeting as requested_actions
    from . import previews as preview_store
    from .short_titles import Headlines
    headlines = Headlines(conn)  # short headlines for long titles, by agenda text hash (decision 0035; operator overrides applied)
    supporting = Supporting(conn)  # each item's staff report, its summary, and its attachments (IDEA-013, IDEA-016)
    current_previews = preview_store.current_previews(conn)  # one query (IDEA-018 R8)
    local_today = today or stamp.astimezone(ZoneInfo(PACIFIC)).date()
    page_text = {(version, p["number"]): p["text"] for version, pages in pages_by_version.items() for p in pages}
    meetings = []
    record_dates = {}
    for m in conn.execute("SELECT * FROM meetings WHERE quarantine_reason IS NULL ORDER BY local_date NULLS LAST, starts_at NULLS LAST, id"):
        docs = docs_by_meeting.get(m["id"], [])
        # A planned meeting that no current preview supports and nothing was captured for is left out (R6), and it
        # no longer stands in for a forecast.
        if preview_store.withdrawn(m, has_previews=(m["agency_id"], m["local_date"]) in current_previews,
                                   has_documents=bool(docs), has_items=bool(items_by_meeting.get(m["id"]))):
            continue
        if m["local_date"]:
            record_dates.setdefault(m["agency_id"], []).append(m["local_date"])
        if m["agency_id"] not in AGENCIES:
            continue
        items = []
        actions = requested_actions([{"id": i["id"], "citations": _json(i["citations"])} for i in items_by_meeting.get(m["id"], [])],
                                    page_text)
        for i in items_by_meeting.get(m["id"], []):
            # A current Claude classification replaces the rule tags, including an empty one for items outside Tahoe.
            topics = set(_json(claude_topics[i["id"]])) if i["id"] in claude_topics else set(rule_topics.get(i["id"], ()))
            for override in overrides.get(i["id"], []):
                (topics.add if override["included"] else topics.discard)(override["topic_id"])
            summary = None if i["summary_status"] is None else {"text": i["summary_text"], "status": i["summary_status"],
                "method": i["method"], "generatedAt": iso(i["generated_at"]),
                "citations": _json(i["summary_citations"])}
            if summary and summary["method"] == EXCERPT_METHOD and summary["status"] == "published" and summary["citations"]:
                # Rebuilt from its citation, so a change to excerpt_text reaches excerpts stored before it.
                summary["text"] = _summary_from_citations(summary["citations"])
            # A current staff report summary, else the agenda's requested action, replaces the excerpt; a correction replaces any.
            summary = supporting.summary(i["id"], summary, iso, actions.get(i["id"]))
            correction = _json(i["correction"]) if i["correction"] else None
            if summary and correction and summary["status"] == "published":
                # An operator correction simply replaces the displayed text; no attribution or history is exported.
                summary["text"] = correction["text"]
            items.append({"id": i["id"], "number": i["number"], "order": i["item_order"], "title": i["title"],
                "section": i["section"], "topics": sorted(expand_topics(topics)), "summary": summary, "citations": _json(i["citations"]),
                "change": i["change"], "updatedAt": iso(i["updated_at"]), "suppressed": bool(i["suppressed"])})
            if i["full_title"]:
                items[-1]["fullTitle"] = i["full_title"]  # present only when the title is cut short
            if short_title := headlines.for_item(i["title"], i["full_title"]):
                items[-1]["shortTitle"] = short_title  # a validated headline for a long title; the whole text stays in title/fullTitle
            if staff_report := supporting.report(i["id"], summary, iso):
                items[-1]["staffReport"] = staff_report
            if attachments := supporting.attachments(i["id"]):
                items[-1]["attachments"] = attachments
        stored_docs, docs = docs, one_per_url(docs, {c["documentId"] for i in items for c in [*i["citations"], *((i["summary"] or {}).get("citations") or [])]})
        timed = time_certain(docs, items)
        for item in items:
            item["timeCertain"] = timed.get(item["id"])
        # Agenda-read details count only while the documents they came from are still current.
        extracted = agenda_participation.get(m["id"])
        extracted = _json(extracted["fields"]) if extracted and set(_json(extracted["version_ids"])) <= {d["id"] for d in stored_docs} else None
        if join := annotated_join_url([{"title": d["title"], "links": first_page_links.get(d["id"], [])} for d in stored_docs]):
            # One Zoom or Teams link behind the agenda's first page outranks the model's reading of the text.
            extracted = {k: v for k, v in (extracted or {}).items() if v != join} | {"remoteUrl": join}
        location, participation = merged_participation(m["location"], _json(m["participation"]), extracted,
                                                       AGENCIES[m["agency_id"]].get("participation"))
        start, warnings = exported_start(m, docs, _json(m["warnings"]))
        meetings.append({"id": m["id"], "agencyId": m["agency_id"], "sourceId": m["source_id"], "kind": "confirmed", "title": m["title"],
            "topics": sorted(expand_topics(set(_json(m["declared_topics"])))),
            "startsAt": iso(start), "date": m["local_date"].isoformat() if m["local_date"] else None,
            "timezone": m["timezone"], "status": m["status"], "location": location, "host": m["host"],
            "officialUrl": m["official_url"], "participation": participation,
            "minutesUrl": m["minutes_url"], "minutesStatus": (m["minutes_status"] or "approved") if m["minutes_url"] else None,
            "minutesNote": m["minutes_note"] if m["minutes_url"] else None,
            "recordingUrl": m["recording_url"], "lastCheckedAt": iso(m["last_checked_at"]),
            "updatedAt": iso(m["updated_at"]), "change": m["change"], "warnings": warnings, "reviewNotes": _json(m["review_notes"]) or [], "documents": docs, "items": items,
            "history": history_by_meeting.get(m["id"], []),
            "duration": meeting_duration(m, [{"title": i["title"], "section": i["section"]} for i in items])})
        if preview_store.is_planned(m) and not agenda_posted(meetings[-1]):
            meetings[-1]["planned"] = True  # until the captured agenda adopts the record (R6)
    attach_previews(meetings, current_previews, comment_periods, local_today)
    # Expected meetings follow the stored records; the website sorts by date.
    for agency in agencies:
        if AGENCIES[agency["id"]].get("participationTypes") == ["public-comment-period"]:
            continue
        meetings.extend(forecast_meetings(agency["id"], AGENCIES[agency["id"]].get("schedule"), record_dates.get(agency["id"], []),
                                          today=local_today, horizon_days=forecast_days,
                                          official_url=agency["officialUrl"], generated_at=generated_at))
    for meeting in meetings:
        if meeting.get("kind") == "forecast":
            meeting["duration"] = meeting_duration({"agency_id": meeting["agencyId"]}, [])
            meeting["host"] = None
            meeting["location"], meeting["participation"] = merged_participation(
                meeting["location"], meeting["participation"], None, AGENCIES[meeting["agencyId"]].get("participation"))
    return {"schemaVersion": 2, "generatedAt": generated_at, "mode": mode,
            "coverageNote": "Meetings and agenda items shown are limited to the official source documents processed so far.",
            "agencies": agencies, "topics": TOPICS, "meetings": meetings, "commentPeriods": comment_periods}


AGENDA_DOCUMENT = re.compile(r"agenda|packet", re.I)
SUPPLEMENT = re.compile(r"supplement", re.I)


# Start-time caveats discovery stored before decision 0038; they describe no known problem, so they are never exported.
RETIRED_START_WARNINGS = ("Archive playback timestamps are not used as scheduled start times",
                          "Confirm the scheduled start in the agenda")


def exported_start(m: dict, docs: list[dict], warnings: list[str]) -> tuple:
    """The start time to publish, and the warnings without retired start-time caveats.

    A start time printed on the agenda's first page wins over the stored one (decision 0038). It fills in a meeting
    stored without one; it replaces the time a listing gave when a revised agenda moved it (a TCPUD board meeting
    listed at 4:00 p.m. whose revised agenda says 3:45 p.m.), since a past meeting's archive row states no time and
    the upcoming row's time stays stored; and it replaces the stored start of a planned meeting, which came from a
    planning calendar ("Nov 3, 9:30 a.m."). Without an agenda time, or when agendas disagree, the stored start stays.
    """
    warnings = [w for w in warnings if not w.startswith(RETIRED_START_WARNINGS)]
    start = m["starts_at"]
    if agenda_time := agenda_start_time(m["title"], m["local_date"], docs):
        try:
            start = starts_at({"date": m["local_date"].isoformat(), "start_time": agenda_time, "timezone": m["timezone"]})
        except ValueError:  # A daylight-saving gap or overlap; the agenda cannot say which.
            pass
    return start, warnings


def agenda_posted(meeting: dict) -> bool:
    """Items are listed, or a current document is the agenda or its packet (a supplement alone is not)."""
    return bool(meeting["items"]) or any(AGENDA_DOCUMENT.search(d["title"] or "") and not SUPPLEMENT.search(d["title"] or "")
                                         for d in meeting["documents"])


def _collapsed(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _public_preview(row: dict) -> dict:
    return {"subject": row["subject"], "section": row["section"], "topics": sorted(expand_topics(set(row["topics"]))),
            "source": {"kind": row["source_kind"], "title": row["source_title"], "url": row["source_url"],
                       "publishedOn": row["published_on"].isoformat(), "quote": row["quote"], "page": row["page"]}}


def attach_previews(meetings: list[dict], current: dict, comment_periods: list[dict], today: date) -> None:
    """Show each body's current previews on its stored meeting that date (IDEA-018 R7), and link comment periods.

    Previews keyed (agency, date) go to the one meeting ``records.match_meeting`` picks among that body's meetings
    that day (qualifiers from the calendar's "Special"), else to none; a captured meeting is preferred to a planned
    one. They are shown only on a future meeting whose agenda is not posted, and their topics join the meeting's
    while shown. Once the agenda is posted, a notice preview whose project no item title contains is listed in
    ``previewNotOnAgenda``. A planned meeting left with no preview shown and no document (past, or beside a
    captured meeting that day) is dropped. Each comment period lists the meetings that came from it: one-offs its
    notice announced and meetings its notice previews.
    """
    from .records import match_meeting
    by_day: dict[tuple, list[dict]] = {}
    for meeting in meetings:
        if meeting["date"]:
            by_day.setdefault((meeting["agencyId"], date.fromisoformat(meeting["date"])), []).append(meeting)
    projects = {p["id"]: p["projectTitle"] for p in comment_periods}
    from_period: dict[str, set[str]] = {}
    for (agency_id, day), rows in current.items():
        same_day = by_day.get((agency_id, day), [])
        captured = [m for m in same_day if not m.get("planned")]
        same_day = captured or same_day
        quals = {"special"} if any(r["special"] for r in rows) else set()
        chosen = match_meeting([{"local_date": day, "title": m["title"], "status": m["status"], "meeting": m}
                                for m in same_day], day, quals)
        if not chosen:
            continue
        meeting = chosen["meeting"]
        for row in rows:
            if row["source_kind"] == "notice":
                from_period.setdefault(row["source_key"], set()).add(meeting["id"])
        if not agenda_posted(meeting):
            if day > today:
                meeting["previews"] = [_public_preview(r) for r in rows]
                meeting["topics"] = sorted(expand_topics(set(meeting["topics"]).union(
                    *(p["topics"] for p in meeting["previews"]))))
            continue
        titles = [_collapsed(i["title"]) for i in meeting["items"]]
        missing = []
        for row in rows:
            project = projects.get(row["source_key"]) if row["source_kind"] == "notice" else None
            if titles and project and project not in missing and not any(_collapsed(project) in t for t in titles):
                missing.append(project)
        if missing:
            meeting["previewNotOnAgenda"] = missing
    meetings[:] = [m for m in meetings if not m.get("planned") or m.get("previews") or m["documents"]]
    for period in comment_periods:
        prefix = f"one-off-{period['id'].split(':', 1)[1]}-"
        linked = from_period.get(period["id"], set())
        period["meetingIds"] = [m["id"] for m in meetings if m["id"] in linked or m["sourceId"].startswith(prefix)]


def edit_item(conn, item_id: str, *, correction=None, suppressed=None, topic=None):
    item = conn.execute("SELECT meeting_id FROM agenda_items WHERE id=%s", (item_id,)).fetchone()
    if not item:
        raise ValueError(f"unknown item {item_id}")
    with conn.transaction():
        conn.execute("INSERT INTO editorial(item_id) VALUES (%s) ON CONFLICT DO NOTHING", (item_id,))
        if correction is not None:
            text, reason, author = correction
            if not all((text.strip(), reason.strip(), author.strip())):
                raise ValueError("correction requires text, reason, and author")
            conn.execute("UPDATE editorial SET correction=%s,updated_at=now() WHERE item_id=%s",
                         (json.dumps({"text": text, "reason": reason, "author": author, "updatedAt": iso(now())}), item_id))
        if suppressed is not None:
            conn.execute("UPDATE editorial SET suppressed=%s,updated_at=now() WHERE item_id=%s", (suppressed, item_id))
        if topic is not None:
            topic_id, included, author = topic
            if topic_id not in topic_ids() or not author.strip():
                raise ValueError("topic override requires known topic and author")
            conn.execute("""INSERT INTO topic_overrides(item_id,topic_id,included,author) VALUES (%s,%s,%s,%s)
                ON CONFLICT(item_id,topic_id) DO UPDATE SET included=excluded.included,author=excluded.author,updated_at=now()""",
                (item_id, topic_id, included, author))
        if any(value is not None for value in (correction, suppressed, topic)):
            conn.execute("UPDATE agenda_items SET updated_at=clock_timestamp() WHERE id=%s", (item_id,))
            _touch_meeting(conn, item["meeting_id"])
