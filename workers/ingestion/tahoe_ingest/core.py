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

from .catalog import public_topics, topic_ids, agency_catalog, followed_agencies, expand_topics
from .topics import CLASSIFIER_VERSION, classify
from .durations import meeting_duration
from .time_certain import time_certain
from .history import HISTORY_SQL, document_history
from .schedules import PACIFIC, forecast_meetings


TOPICS = public_topics()
AGENCIES = followed_agencies()


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


def _summary_from_citations(citations: list[dict]) -> str:
    # This is a plainly labeled source excerpt, not an inferred account of an outcome.
    return f"Agenda excerpt: “{citations[0]['quote']}”"


def _replace_topics(conn, item_id: str, agency_id: str, title: str, citations: list[dict]) -> bool:
    """Replace generated tags only; operator overrides are an independent layer."""
    evidence = classify(agency_id, title, citations)
    previous = {r["topic_id"]: r for r in conn.execute(
        "SELECT topic_id,source,evidence FROM item_topics WHERE item_id=%s", (item_id,))}
    changed = set(previous) != set(evidence)
    if (changed or any(previous[topic]["source"] != CLASSIFIER_VERSION or
                       _json(previous[topic]["evidence"]) != detail
                       for topic, detail in evidence.items())):
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
    minutes_url = row.get("minutes_url") or (old["minutes_url"] if partial and old else None)
    recording_url = row.get("recording_url") or (old["recording_url"] if partial and old else None)
    meeting_warnings = list(row.get("warnings", []))
    if row.get("source_warning"):
        meeting_warnings.append(row["source_warning"])
    conn.execute("""INSERT INTO meetings(id,agency_id,source_id,title,starts_at,local_date,timezone,status,location,official_url,participation,minutes_url,recording_url,last_checked_at,change,warnings)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(id) DO UPDATE SET title=excluded.title, starts_at=excluded.starts_at, local_date=excluded.local_date, timezone=excluded.timezone,
        status=excluded.status, location=excluded.location, official_url=excluded.official_url,
        participation=excluded.participation, minutes_url=excluded.minutes_url, recording_url=excluded.recording_url,
        last_checked_at=GREATEST(excluded.last_checked_at,meetings.last_checked_at), change=excluded.change, warnings=excluded.warnings,
        updated_at=CASE WHEN meetings.title IS DISTINCT FROM excluded.title OR meetings.starts_at IS DISTINCT FROM excluded.starts_at
          OR meetings.local_date IS DISTINCT FROM excluded.local_date
          OR meetings.status IS DISTINCT FROM excluded.status OR meetings.participation IS DISTINCT FROM excluded.participation
          OR meetings.location IS DISTINCT FROM excluded.location OR meetings.official_url IS DISTINCT FROM excluded.official_url
          OR meetings.minutes_url IS DISTINCT FROM excluded.minutes_url OR meetings.recording_url IS DISTINCT FROM excluded.recording_url
          THEN now() ELSE meetings.updated_at END""",
        (meeting_id, aid, source_id, row["title"], start, local_date, zone_name, status,
         location, row.get("source_meeting_url") or row.get("official_url") or (old["official_url"] if old else None), json.dumps(participation),
         minutes_url, recording_url, checked_at, change, json.dumps(meeting_warnings)))

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
        """SELECT c.source_document_id,c.version_id,(SELECT count(*) FROM document_pages p WHERE p.document_version_id=c.version_id) AS pages
        FROM document_current c WHERE c.meeting_id=%s""", (meeting_id,))}
    previous_document_ids = set(current_versions)
    version_ids = {}
    revised_documents = set()
    changed_items = False
    changed_topics = False
    page_rows = {(p["document_id"], int(p["page_number"])): p["text"] for p in row.get("pages", [])}
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
        if current and current["version_id"] == version_id and current["pages"] >= len(pages):
            continue  # The same file with all of its pages is already current.
        conn.execute("""INSERT INTO document_versions(id,meeting_id,source_document_id,title,url,sha256,fetched_at,page_count,status,warnings,media_type)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'extracted',%s,%s) ON CONFLICT(id) DO NOTHING""",
            (version_id, meeting_id, doc_id, doc.get("title") or doc.get("kind") or "Official document", doc["url"], hash_value,
             datetime.fromisoformat(fetched_at.replace("Z", "+00:00")), page_count, json.dumps(warnings), doc.get("media_type", "application/pdf")))
        conn.execute("""INSERT INTO document_pages(document_version_id,page_number,text)
            SELECT %s,n,text FROM unnest(%s::int[],%s::text[]) AS p(n,text)
            ON CONFLICT(document_version_id,page_number) DO NOTHING""",
            (version_id, [n for n, _ in pages], [text for _, text in pages]))
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
            quote = ref.get("quote") or _first_quote(page_text, item["title"])
            if quote:
                citations.append(_citation(version_id, page_num, quote))
        incoming_items.append((item, source_key, citations))
    stored_items = {r["id"]: r for r in conn.execute("SELECT * FROM agenda_items WHERE meeting_id=%s", (meeting_id,))}
    page_texts = stored_page_texts(conn, [c for _, _, citations in incoming_items for c in citations])
    seen_items = set()
    for item, source_key, citations in incoming_items:
        item_id = stable_id(meeting_id, source_key)
        # A repeated key sees the row its first occurrence just wrote.
        existing = (conn.execute("SELECT * FROM agenda_items WHERE id=%s", (item_id,)).fetchone()
                    if item_id in seen_items else stored_items.get(item_id))
        seen_items.add(item_id)
        fingerprint = _hash_text({"title": item["title"], "section": item.get("section"), "citations": citations})
        changed = (not existing or existing["source_fingerprint"] != fingerprint or
                   existing["item_order"] != int(item["order"]) or not existing["active"])
        changed_items |= changed
        item_change = "new" if not existing else "revised" if changed else existing["change"]
        try:
            validate_citations(conn, citations, page_texts)
            valid_citations = citations
            summary_status, summary_error = "published", None
        except ValueError as exc:
            valid_citations = []
            summary_status, summary_error = "validation_failed", str(exc)
        if existing and valid_citations and _json(existing["citations"]) != valid_citations:
            # Restore evidence after a document-only refresh invalidated this
            # item's citations, even when its source fingerprint is unchanged.
            changed = True
            changed_items = True
            item_change = "revised"
        number = item.get("source_number") or ""
        unchanged_row = (existing and not changed and existing["number"] == number and existing["title"] == item["title"]
                         and existing["section"] == item.get("section") and existing["change"] == item_change
                         and _json(existing["citations"]) == valid_citations)
        if not unchanged_row:
            conn.execute("""INSERT INTO agenda_items(id,meeting_id,source_key,number,item_order,title,section,source_fingerprint,citations,change)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET number=excluded.number,item_order=excluded.item_order,title=excluded.title,
                section=excluded.section,source_fingerprint=excluded.source_fingerprint,citations=excluded.citations,active=true,
                change=excluded.change,updated_at=CASE WHEN agenda_items.source_fingerprint IS DISTINCT FROM excluded.source_fingerprint
                  OR agenda_items.item_order IS DISTINCT FROM excluded.item_order OR NOT agenda_items.active
                THEN clock_timestamp() ELSE agenda_items.updated_at END""",
                (item_id, meeting_id, source_key, number, int(item["order"]), item["title"], item.get("section"),
                 fingerprint, json.dumps(valid_citations), item_change))
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
        for item_row in conn.execute("SELECT id,citations FROM agenda_items WHERE meeting_id=%s", (meeting_id,)):
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
        conn.execute("UPDATE meetings SET warnings=warnings || %s::jsonb WHERE id=%s", (json.dumps(["A source PDF changed; affected agenda items await reprocessing."]), meeting_id))
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


def _json(value):
    if value is None:
        return None
    return value if isinstance(value, (dict, list)) else json.loads(value)


def _touch_meeting(conn, meeting_id: str) -> None:
    conn.execute("""UPDATE meetings SET updated_at=clock_timestamp(),
        change=CASE WHEN change IN ('canceled','rescheduled') THEN change
                    WHEN status='canceled' THEN 'canceled'
                    WHEN status='rescheduled' THEN 'rescheduled'
                    ELSE 'revised' END
        WHERE id=%s""", (meeting_id,))


def reclassify_topics(conn, *, dry_run=False) -> dict:
    """Upgrade generated topic tags without altering source or summary versions."""
    rows = list(conn.execute("""SELECT i.id,i.meeting_id,i.title,i.citations,i.classifier_version,m.agency_id
        FROM agenda_items i JOIN meetings m ON m.id=i.meeting_id WHERE i.active ORDER BY i.id"""))
    before = {t["id"]: 0 for t in TOPICS}
    after = dict(before)
    changed = 0
    upgraded = 0
    stored = {}
    for r in conn.execute("SELECT item_id,topic_id FROM item_topics"):
        stored.setdefault(r["item_id"], set()).add(r["topic_id"])
    with conn.transaction():
        for item in rows:
            old = stored.get(item["id"], set())
            new = set(classify(item["agency_id"], item["title"], _json(item["citations"])))
            for topic in old:
                before[topic] = before.get(topic, 0) + 1
            for topic in new:
                after[topic] += 1
            if old != new:
                changed += 1
            if item["classifier_version"] != CLASSIFIER_VERSION:
                upgraded += 1
            if not dry_run and (old != new or item["classifier_version"] != CLASSIFIER_VERSION):
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (item["meeting_id"],))
                if _replace_topics(conn, item["id"], item["agency_id"], item["title"], _json(item["citations"])):
                    conn.execute("UPDATE agenda_items SET updated_at=clock_timestamp() WHERE id=%s", (item["id"],))
                    _touch_meeting(conn, item["meeting_id"])
    return {"classifierVersion": CLASSIFIER_VERSION, "dryRun": dry_run, "itemsScanned": len(rows),
            "itemsChanged": changed, "itemsUpgraded": upgraded,
            "automaticTopicsBefore": before, "automaticTopicsAfter": after}


def export_public(conn, *, mode="fixture", forecast_days: int = 0, today: date | None = None) -> dict:
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
        agencies.append({"id": a["id"], "name": a["name"], "shortName": a["short_name"], "officialUrl": a["official_url"] or fallback,
                         "sourceUrl": a["source_url"] or fallback, "lastCheckedAt": iso(a["last_checked_at"]), "status": a["status"], "error": a["error"]})
    pages_by_version = {}
    for p in conn.execute("""SELECT p.document_version_id,p.page_number,p.text FROM document_pages p
            WHERE p.document_version_id IN (SELECT version_id FROM document_current) ORDER BY p.document_version_id,p.page_number"""):
        pages_by_version.setdefault(p["document_version_id"], []).append({"number": p["page_number"], "text": p["text"]})
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
    rule_topics = {}
    for r in conn.execute("SELECT item_id,topic_id FROM item_topics"):
        rule_topics.setdefault(r["item_id"], set()).add(r["topic_id"])
    overrides = {}
    for r in conn.execute("SELECT item_id,topic_id,included FROM topic_overrides"):
        overrides.setdefault(r["item_id"], []).append(r)
    meetings = []
    record_dates = {}
    for m in conn.execute("SELECT * FROM meetings WHERE quarantine_reason IS NULL ORDER BY local_date NULLS LAST, starts_at NULLS LAST, id"):
        if m["local_date"]:
            record_dates.setdefault(m["agency_id"], []).append(m["local_date"])
        if m["agency_id"] not in AGENCIES:
            continue
        docs = docs_by_meeting.get(m["id"], [])
        items = []
        for i in items_by_meeting.get(m["id"], []):
            # A current Claude classification replaces the rule tags, including an empty one for items outside Tahoe.
            topics = set(_json(claude_topics[i["id"]])) if i["id"] in claude_topics else set(rule_topics.get(i["id"], ()))
            for override in overrides.get(i["id"], []):
                (topics.add if override["included"] else topics.discard)(override["topic_id"])
            summary = None if i["summary_status"] is None else {"text": i["summary_text"], "status": i["summary_status"],
                "method": i["method"], "generatedAt": iso(i["generated_at"]),
                "citations": _json(i["summary_citations"])}
            correction = _json(i["correction"]) if i["correction"] else None
            if summary and correction and summary["status"] == "published":
                # An operator correction simply replaces the displayed text; no attribution or history is exported.
                summary["text"] = correction["text"]
            items.append({"id": i["id"], "number": i["number"], "order": i["item_order"], "title": i["title"],
                "section": i["section"], "topics": sorted(expand_topics(topics)), "summary": summary, "citations": _json(i["citations"]),
                "change": i["change"], "updatedAt": iso(i["updated_at"]), "suppressed": bool(i["suppressed"])})
        timed = time_certain(docs, items)
        for item in items:
            item["timeCertain"] = timed.get(item["id"])
        participation = _json(m["participation"])
        meetings.append({"id": m["id"], "agencyId": m["agency_id"], "sourceId": m["source_id"], "kind": "confirmed", "title": m["title"],
            "topics": sorted(expand_topics(set(_json(m["declared_topics"])))),
            "startsAt": iso(m["starts_at"]), "date": m["local_date"].isoformat() if m["local_date"] else None,
            "timezone": m["timezone"], "status": m["status"], "location": m["location"],
            "officialUrl": m["official_url"], "participation": {k: participation.get(k) for k in ("instructions", "url", "remoteUrl", "deadline")},
            "minutesUrl": m["minutes_url"], "recordingUrl": m["recording_url"], "lastCheckedAt": iso(m["last_checked_at"]),
            "updatedAt": iso(m["updated_at"]), "change": m["change"], "warnings": _json(m["warnings"]), "documents": docs, "items": items,
            "history": history_by_meeting.get(m["id"], []),
            "duration": meeting_duration(m, [{"title": i["title"], "section": i["section"]} for i in items])})
    # Expected meetings follow the stored records; the website sorts by date.
    local_today = today or stamp.astimezone(ZoneInfo(PACIFIC)).date()
    for agency in agencies:
        if AGENCIES[agency["id"]].get("participationTypes") == ["public-comment-period"]:
            continue
        meetings.extend(forecast_meetings(agency["id"], AGENCIES[agency["id"]].get("schedule"), record_dates.get(agency["id"], []),
                                          today=local_today, horizon_days=forecast_days,
                                          official_url=agency["officialUrl"], generated_at=generated_at))
    for meeting in meetings:
        if meeting.get("kind") == "forecast":
            meeting["duration"] = meeting_duration({"agency_id": meeting["agencyId"]}, [])
    return {"schemaVersion": 2, "generatedAt": generated_at, "mode": mode,
            "coverageNote": "Meetings and agenda items shown are limited to the official source documents processed so far. Check each agency's source and meeting warnings for gaps.",
            "agencies": agencies, "topics": TOPICS, "meetings": meetings, "commentPeriods": comment_periods}


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
