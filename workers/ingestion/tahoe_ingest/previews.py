"""Agenda previews: subjects an official source says are planned for a future meeting (IDEA-018, R5 and R6).

A *preview source* is one version of a tracked notice or of one agency's agenda planning calendar, stored in
``preview_sources`` keyed by (kind, key, sha256). Its subjects are rows of ``meeting_previews``, keyed on body
and date rather than meeting ID, so they reattach when the captured agenda adopts a planned meeting.

Replacement: for each (kind, key) the valid version with the latest ``published_on`` is current (ties: the
latest ``captured_at``), whatever order versions were captured in, so re-capturing an older calendar during a
backfill never displaces a newer one. Only the current version is shown, and only for dates after its
publication date. The current version, the newest version older than it, and any rejected versions newer than
it are kept; older versions are deleted with their rows.

Planned meetings: a valid, dated preview of the current version that names a future date with no stored
meeting for that body creates one (``<agency>:planned-<YYYY-MM-DD>``) through ``core.ingest_bundle``. It is
withdrawn from the export, never deleted, once no current preview supports it and nothing was captured for it.

Topics: rows get the rule classifier's tags at insert time, so previews have tags without credentials.
``classify_subjects`` then asks Claude with the same prompt and schema as agenda items (one editorial voice,
decision 0010) and caches the result per agency and normalized subject in ``ai_subject_topics``; every new
calendar repeats most subjects, so most are never sent again. Claude's tags replace the rule tags, as the
export does for agenda items; notice previews keep their comment period's topics as well.

Database cost (decision 0020): an unchanged source costs one lookup and no writes. A new version costs six
statements (lookup, source upsert, topic cache read, one bulk row insert, one prune, stored-meeting lookup) plus
about seven per new planned meeting (one ``ingest_bundle`` record). ``current_previews`` is one query.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time

from . import ai_topics, records
from .catalog import expand_topics, followed_agencies, topic_catalog, topic_ids
from .evaluate_topics import (
    AgendaItem, AnthropicTopicModel, ModelError, build_requests, cost_usd, estimated_usage, has_credentials,
    ordered, parse_reply, render_system_prompt, response_schema,
)
from .topics import classify

KINDS = ("notice", "planning-calendar")
PLANNED_PREFIX = "planned-"
PLANNED_WARNING = "Subject to change."
PURPOSE = "subject-classification"
CHAIN = ai_topics.CHAIN
ATTEMPTS_PER_MODEL = ai_topics.ATTEMPTS_PER_MODEL
SUBJECTS_PER_REQUEST = 40  # keeps one reply well under max_tokens; a calendar has about 10-20 new subjects
SUBJECT_MEETING_TITLE = "Planned agenda items for upcoming meetings"
TIME = re.compile(r"\d{2}:\d{2}")


@dataclass
class PreviewSource:
    """One version of a notice or planning calendar.

    ``published_on`` is the calendar's "(Updated MM DD YYYY)", else the date of the meeting whose agenda carries
    it; for a notice, the comment period's opening date (or the checked date when absent). ``pages`` holds the
    calendar's page text, ``[{"page_number": int, "text": str}]`` (decision 0004); None for notices, whose text
    is already in ``comment_periods``.
    """
    kind: str  # 'notice' | 'planning-calendar'
    key: str  # comment period ID | f'{agency}:planning-calendar'
    sha256: str  # of the source bytes; for packet pages, the packet's
    url: str  # official document URL
    title: str
    published_on: date
    pages: list[dict] | None = None


@dataclass(kw_only=True)
class Preview:
    """One planned subject for one body, on a date, in a month (month-only entries), or pending.

    ``subject`` is as written, without requester initials, motion numbers, conditional notes, or a trailing
    parenthetical naming a person; ``quote`` is the verbatim source line(s). ``start_time`` ('HH:MM' local),
    ``location`` and ``remote_quote`` are set only when the source states them for that date. ``rejection`` is
    set when a deterministic check failed: the row is stored, never shown. ``topics`` may be pre-seeded by the
    caller (a notice's comment period topics); storage adds the classifier's tags.
    """
    agency_id: str
    local_date: date | None
    month: date | None
    pending: bool = False
    subject: str
    section: str | None = None
    start_time: str | None = None
    location: str | None = None
    remote_quote: str | None = None
    quote: str
    page: int | None = None
    meeting_title: str | None = None
    special: bool = False
    rejection: str | None = None
    topics: list[str] = field(default_factory=list)


# --- Small helpers -----------------------------------------------------------------------------------------

def planned_source_id(day: date) -> str:
    return f"{PLANNED_PREFIX}{day.isoformat()}"


def is_planned(row: dict) -> bool:
    """A stored meeting created from a preview (also after the captured agenda adopted it; its ID stays)."""
    return str(row.get("source_id") or "").startswith(PLANNED_PREFIX)


def withdrawn(row: dict, *, has_previews: bool, has_documents: bool, has_items: bool) -> bool:
    """A planned meeting that no current preview supports and nothing was captured for: left out of the export.

    "Nothing captured" means no documents, no items, status still as created ('scheduled' or 'unknown'), and
    change still 'new'. A cancellation follows the normal path, so a canceled planned meeting is not withdrawn.
    """
    return (is_planned(row) and not has_previews and not has_documents and not has_items
            and row.get("status") not in ("canceled", "rescheduled") and row.get("change") == "new")


def subject_key(text: str) -> str:
    """Normalized subject for reusing topic results across calendar versions.

    NFKC, casefold, every punctuation character replaced by a space, whitespace collapsed. Nothing else is
    stripped (no section or action suffixes), so "Bike Lanes (Consent)" and "Bike Lanes" are different keys.
    """
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in text)
    return " ".join(text.split())


def planned_title(preview: Preview, agency_name: str) -> str:
    """The format's per-body title, led by "Special" when the calendar says "Special Mtg".

    South Lake Tahoe titles its special meetings "Special City Council Meeting"; the word also makes the same-date
    bridge's qualifiers (``records.qualifiers``) agree when the captured agenda adopts the record.
    """
    title = preview.meeting_title or f"{agency_name} meeting"
    return f"Special {title}" if preview.special and "special" not in records.qualifiers(title) else title


def _shown(preview, published_on: date) -> bool:
    return not preview["rejection"] and preview["local_date"] is not None and preview["local_date"] > published_on


def _validate(source: PreviewSource, previews: list[Preview]) -> None:
    if source.kind not in KINDS:
        raise ValueError(f"unknown preview source kind {source.kind}")
    if not re.fullmatch(r"[0-9a-f]{64}", source.sha256 or ""):
        raise ValueError(f"invalid SHA-256 for {source.key}")
    agencies = followed_agencies()
    for preview in previews:
        if preview.agency_id not in agencies:
            raise ValueError(f"preview for unknown agency {preview.agency_id}")
        if preview.start_time is not None:
            if not TIME.fullmatch(preview.start_time):
                raise ValueError(f"preview start time must be HH:MM: {preview.start_time!r}")
            time.fromisoformat(preview.start_time)
        if not (preview.subject or "").strip() or not (preview.quote or "").strip():
            raise ValueError("preview needs a subject and a quote")


def _record(preview: Preview, n: int, cached: dict) -> dict:
    """The stored row: rule tags (or Claude's cached tags, which replace them) plus the caller's pre-seeded topics."""
    tags = cached.get((preview.agency_id, subject_key(preview.subject)))
    if tags is None:
        tags = set(classify(preview.agency_id, preview.subject, []))
    topics = ordered(expand_topics(set(tags) | set(preview.topics)))
    return {"n": n, "agency_id": preview.agency_id, "local_date": _iso(preview.local_date), "month": _iso(preview.month),
            "pending": bool(preview.pending), "start_time": preview.start_time, "location": preview.location,
            "remote_quote": preview.remote_quote, "subject": preview.subject, "section": preview.section,
            "topics": topics, "quote": preview.quote, "page": preview.page, "meeting_title": preview.meeting_title,
            "special": bool(preview.special), "rejection": preview.rejection}


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _first(block: list, name: str):
    return next((r[name] for r in block if r[name]), None)


def _order_key(version: dict):
    # A version captured in this call has no stored captured_at yet; it is the latest capture.
    return (version["published_on"], version["captured_at"] or datetime.max.replace(tzinfo=None))


def _naive(value):
    return value.replace(tzinfo=None) if isinstance(value, datetime) and value.tzinfo else value


# --- Storage ------------------------------------------------------------------------------------------------

def replace_source(conn, source: PreviewSource, previews: list[Preview], *, today: date) -> dict:
    """Store this version and its previews, keep the key's current version and one previous, create planned meetings.

    Rows are inserted only when this (kind, key, sha256) has none yet, so a re-run with the same source costs
    the one version lookup and no writes. Planned meetings (R6) are created only when this version is current.
    Returns ``{"inserted", "current", "planned", "unchanged", "pruned"}`` (plus ``errors`` when an ingest failed).
    """
    _validate(source, previews)
    pages = source.pages if source.pages is None else [
        {"page_number": int(p["page_number"]), "text": p["text"]} for p in source.pages]
    with conn.transaction():
        versions = [dict(r) for r in conn.execute(
            """SELECT s.sha256,s.status,s.published_on,s.captured_at,
                CASE WHEN s.sha256=%(sha)s THEN jsonb_build_object('url',s.url,'title',s.title,'pages',s.pages,'error',s.error) END AS mine,
                EXISTS(SELECT 1 FROM meeting_previews p WHERE p.source_kind=s.source_kind AND p.source_key=s.source_key
                       AND p.source_sha256=s.sha256) AS has_rows
            FROM preview_sources s WHERE s.source_kind=%(kind)s AND s.source_key=%(key)s""",
            {"sha": source.sha256, "kind": source.kind, "key": source.key})]
        for v in versions:
            v["captured_at"] = _naive(v["captured_at"])
        mine = next((v for v in versions if v["sha256"] == source.sha256), None)
        same_metadata = bool(mine and mine["status"] == "valid" and mine["published_on"] == source.published_on
                             and mine["mine"] == {"url": source.url, "title": source.title, "pages": pages, "error": None})
        has_rows = bool(mine and mine["has_rows"])
        result = {"inserted": 0, "current": False, "planned": [], "unchanged": False, "pruned": 0}
        # Rank the key's versions as they will stand once this one is stored as valid.
        if mine:
            mine.update(status="valid", published_on=source.published_on)
        else:
            versions.append({"sha256": source.sha256, "status": "valid", "published_on": source.published_on,
                             "captured_at": None})
        ranked = sorted(versions, key=_order_key, reverse=True)
        current = next(i for i, v in enumerate(ranked) if v["status"] == "valid")
        result["current"] = ranked[current]["sha256"] == source.sha256
        # Keep the current version, rejected versions newer than it, and the newest version older than it.
        keep = {v["sha256"] for v in ranked[:current + 2]}
        if source.sha256 not in keep:
            # Older than both kept versions (a backfill re-capture): storing it would only delete it again.
            result.update(unchanged=True, superseded=True)
            return result
        if same_metadata and (has_rows or not previews):
            result["unchanged"] = True
            return result

        if not same_metadata:
            conn.execute("""INSERT INTO preview_sources(source_kind,source_key,sha256,url,title,published_on,pages,status,error)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'valid',NULL)
                ON CONFLICT(source_kind,source_key,sha256) DO UPDATE SET url=excluded.url,title=excluded.title,
                published_on=excluded.published_on,pages=excluded.pages,status='valid',error=NULL""",
                (source.kind, source.key, source.sha256, source.url, source.title, source.published_on,
                 json.dumps(pages) if pages is not None else None))

        stored = None  # the rows planned meetings are made from: the ones inserted now, or those stored before
        if not has_rows and previews:
            keys = sorted({(p.agency_id, subject_key(p.subject)) for p in previews})
            cached = {(r["agency_id"], r["subject_key"]): r["topics"] for r in conn.execute(
                """SELECT c.agency_id,c.subject_key,c.topics FROM ai_subject_topics c
                JOIN unnest(%s::text[],%s::text[]) AS k(agency_id,subject_key)
                  ON k.agency_id=c.agency_id AND k.subject_key=c.subject_key""",
                ([a for a, _ in keys], [k for _, k in keys]))}
            rows = [_record(p, n, cached) for n, p in enumerate(previews)]
            result["inserted"] = conn.execute(
                """INSERT INTO meeting_previews(source_kind,source_key,source_sha256,agency_id,local_date,month,pending,
                    start_time,location,remote_quote,subject,section,topics,quote,page,meeting_title,special,rejection)
                SELECT %(kind)s,%(key)s,%(sha)s,r.agency_id,r.local_date,r.month,r.pending,r.start_time,r.location,
                    r.remote_quote,r.subject,r.section,r.topics,r.quote,r.page,r.meeting_title,r.special,r.rejection
                FROM jsonb_to_recordset(%(rows)s::jsonb) AS r(n int,agency_id text,local_date date,month date,pending boolean,
                    start_time text,location text,remote_quote text,subject text,section text,topics jsonb,quote text,
                    page int,meeting_title text,special boolean,rejection text)
                ORDER BY r.n""",
                {"kind": source.kind, "key": source.key, "sha": source.sha256, "rows": json.dumps(rows)}).rowcount
            stored = [{"agency_id": p.agency_id, "local_date": p.local_date, "start_time": p.start_time,
                       "location": p.location, "remote_quote": p.remote_quote, "meeting_title": p.meeting_title,
                       "special": p.special, "rejection": p.rejection} for p in previews]

        doomed = sorted({v["sha256"] for v in ranked} - keep)
        if doomed:
            result["pruned"] = conn.execute(
                "DELETE FROM preview_sources WHERE source_kind=%s AND source_key=%s AND sha256=ANY(%s)",
                (source.kind, source.key, doomed)).rowcount

        if result["current"]:
            if stored is None:
                stored = list(conn.execute(
                    """SELECT agency_id,local_date,start_time,location,remote_quote,meeting_title,special,rejection
                    FROM meeting_previews WHERE source_kind=%s AND source_key=%s AND source_sha256=%s ORDER BY id""",
                    (source.kind, source.key, source.sha256)))
            planned, errors = _create_planned(conn, source, stored, today=today)
            result["planned"] = planned
            if errors:
                result["errors"] = errors
    return result


def _create_planned(conn, source: PreviewSource, rows: list, *, today: date) -> tuple[list[str], list[str]]:
    """One planned meeting per body and future date that no stored meeting of that body already covers."""
    from .core import ingest_bundle  # core imports this module's neighbors; import late to avoid a cycle

    agencies = followed_agencies()
    blocks: dict[tuple[str, date], list] = {}
    for row in rows:
        if (_shown(row, source.published_on) and row["local_date"] >= today
                and agencies[row["agency_id"]].get("meetingHost") != "per-record"):
            blocks.setdefault((row["agency_id"], row["local_date"]), []).append(row)
    if not blocks:
        return [], []
    keys = sorted(blocks)
    stored: dict[tuple[str, date], list] = {}
    for m in conn.execute(
            """SELECT m.id,m.agency_id,m.source_id,m.title,m.local_date,m.status FROM meetings m
            JOIN unnest(%s::text[],%s::date[]) AS k(agency_id,local_date) ON k.agency_id=m.agency_id AND k.local_date=m.local_date
            WHERE m.quarantine_reason IS NULL""", ([a for a, _ in keys], [d for _, d in keys])):
        stored.setdefault((m["agency_id"], m["local_date"]), []).append(dict(m))
    bundle = []
    for (agency_id, day) in keys:
        block = blocks[(agency_id, day)]
        special = any(r["special"] for r in block)
        quals = {"special"} if special else set()
        # A canceled meeting on that date counts as covering it too: match_meeting skips canceled meetings,
        # and a planned meeting beside a cancellation would contradict it.
        same_day = [{**m, "status": "scheduled"} for m in stored.get((agency_id, day), [])]
        if records.match_meeting(same_day, day, quals):
            continue
        preview = Preview(agency_id=agency_id, local_date=day, month=None, subject="", quote="",
                          meeting_title=_first(block, "meeting_title"), special=special)
        row = {"agency": agency_id, "id": planned_source_id(day), "title": planned_title(preview, agencies[agency_id]["name"]),
               "date": day.isoformat(), "location": _first(block, "location"),
               # The agency's own site, where the agenda will be posted; the source line on the page links the document.
               "source_meeting_url": agencies[agency_id].get("officialUrl") or agencies[agency_id].get("sourceUrl") or source.url,
               "status": "scheduled", "participation": {"instructions": _first(block, "remote_quote"), "url": None,
                                                        "remoteUrl": None, "deadline": None},
               "warnings": [PLANNED_WARNING], "items_complete": False, "documents_complete": False}
        if _first(block, "start_time"):
            row["start_time"] = _first(block, "start_time")
        bundle.append(row)
    if not bundle:
        return [], []
    report = ingest_bundle(conn, {"meetings": bundle}, mode="fixture")
    failed = {e.split(": ", 1)[0] for e in report["errors"]}
    return [m for m in (f"{r['agency']}:{r['id']}" for r in bundle) if m not in failed], report["errors"]


def reject_source(conn, source: PreviewSource, reason: str) -> None:
    """Store this version as rejected with the reason; it is never current, so the previous previews stay shown.

    Rows an earlier valid capture of the same file stored are removed; a later valid capture may store them again.
    """
    if source.kind not in KINDS:
        raise ValueError(f"unknown preview source kind {source.kind}")
    pages = source.pages if source.pages is None else [
        {"page_number": int(p["page_number"]), "text": p["text"]} for p in source.pages]
    with conn.transaction():
        conn.execute("""WITH stored AS (
                INSERT INTO preview_sources(source_kind,source_key,sha256,url,title,published_on,pages,status,error)
                VALUES (%(kind)s,%(key)s,%(sha)s,%(url)s,%(title)s,%(published)s,%(pages)s,'rejected',%(reason)s)
                ON CONFLICT(source_kind,source_key,sha256) DO UPDATE SET url=excluded.url,title=excluded.title,
                published_on=excluded.published_on,pages=excluded.pages,status='rejected',error=excluded.error
                WHERE preview_sources.status<>'rejected' OR preview_sources.error IS DISTINCT FROM excluded.error
                  OR preview_sources.url<>excluded.url OR preview_sources.title<>excluded.title
                  OR preview_sources.published_on<>excluded.published_on
                  OR preview_sources.pages IS DISTINCT FROM excluded.pages
                RETURNING 1)
            DELETE FROM meeting_previews WHERE source_kind=%(kind)s AND source_key=%(key)s AND source_sha256=%(sha)s""",
            {"kind": source.kind, "key": source.key, "sha": source.sha256, "url": source.url, "title": source.title,
             "published": source.published_on, "pages": json.dumps(pages) if pages is not None else None,
             "reason": reason[:1000]})


CURRENT_SOURCES = """SELECT DISTINCT ON (source_kind,source_key) source_kind,source_key,sha256,url,title,published_on
    FROM preview_sources WHERE status='valid' ORDER BY source_kind,source_key,published_on DESC,captured_at DESC"""


def current_previews(conn) -> dict[tuple[str, date], list[dict]]:
    """Every shown preview, in one query: valid, dated rows of each key's current version, dated after it was published.

    Keyed by (agency_id, local_date); rows in insertion order.
    """
    found: dict[tuple[str, date], list[dict]] = {}
    for r in conn.execute(f"""SELECT p.agency_id,p.local_date,p.subject,p.section,p.topics,p.quote,p.page,
            p.source_kind,p.source_key,s.url AS source_url,s.title AS source_title,s.published_on,
            p.start_time,p.location,p.remote_quote,p.special,p.meeting_title
        FROM meeting_previews p JOIN ({CURRENT_SOURCES}) s
          ON s.source_kind=p.source_kind AND s.source_key=p.source_key AND s.sha256=p.source_sha256
        WHERE p.rejection IS NULL AND p.local_date IS NOT NULL AND p.local_date>s.published_on
        ORDER BY p.id"""):
        row = dict(r)
        key = (row.pop("agency_id"), row.pop("local_date"))
        row["topics"] = list(row["topics"] or [])
        found.setdefault(key, []).append(row)
    return found


# --- Claude topic tags for subjects --------------------------------------------------------------------------

def _record_usage(conn, model: str, usage: dict, *, ok: bool, error: str | None) -> float:
    counts = {name: int(usage.get(name, 0) or 0) for name in ai_topics.USAGE_FIELDS}
    cost = cost_usd(model, uncached_input=counts["input_tokens"], output=counts["output_tokens"],
                    cache_write=counts["cache_creation_input_tokens"], cache_read=counts["cache_read_input_tokens"])
    conn.execute("""INSERT INTO ai_usage(purpose,model,meeting_id,ok,input_tokens,output_tokens,
        cache_creation_input_tokens,cache_read_input_tokens,cost_usd,error) VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s)""",
        (PURPOSE, model, ok, *(counts[name] for name in ai_topics.USAGE_FIELDS), cost, error))
    return cost


def _reconcile(conn, rows: list[dict], cache: dict) -> int:
    """Set each row's topics from Claude's cached tags (plus a notice's comment period topics); one statement."""
    changes = []
    for row in rows:
        tags = cache.get((row["agency_id"], subject_key(row["subject"])))
        if tags is None:
            continue  # not classified yet: the rule tags stand
        seeded = set(row["period_topics"] or []) if row["source_kind"] == "notice" else set()
        topics = ordered(expand_topics(set(tags) | seeded))
        if list(row["topics"] or []) != topics:
            changes.append({"id": row["id"], "topics": topics})
    if not changes:
        return 0
    return conn.execute("""UPDATE meeting_previews p SET topics=r.topics
        FROM jsonb_to_recordset(%s::jsonb) AS r(id bigint,topics jsonb) WHERE p.id=r.id""",
        (json.dumps(changes),)).rowcount


def classify_subjects(conn, *, max_cost: float = ai_topics.DEFAULT_MAX_RUN_COST,
                      monthly_budget: float = ai_topics.DEFAULT_MONTHLY_BUDGET, dry_run: bool = False,
                      clients: dict | None = None, force: bool = False) -> dict:
    """Claude tags for current preview subjects not yet classified under the current prompt, then rows updated in bulk.

    Same prompt, schema, model chain, retries, and budget guards as ``ai_topics.classify_topics``; each batch is
    one agency's subjects presented as one meeting's items. An unchanged run sends two queries and no model call.
    """
    catalog = list(topic_catalog())
    system, schema = render_system_prompt(catalog=catalog), response_schema(catalog)
    prompt_hash = ai_topics.prompt_sha256(system, schema)
    rows = [dict(r) for r in conn.execute(f"""SELECT p.id,p.agency_id,a.name AS agency_name,p.subject,p.section,p.topics,
            p.source_kind,p.local_date,s.published_on,cp.topics AS period_topics,(c.sha256 IS NOT NULL) AS current
        FROM meeting_previews p
        JOIN preview_sources s ON s.source_kind=p.source_kind AND s.source_key=p.source_key AND s.sha256=p.source_sha256
          AND s.status='valid'
        JOIN agencies a ON a.id=p.agency_id
        LEFT JOIN ({CURRENT_SOURCES}) c ON c.source_kind=p.source_kind AND c.source_key=p.source_key AND c.sha256=p.source_sha256
        LEFT JOIN comment_periods cp ON p.source_kind='notice' AND cp.id=p.source_key
        WHERE p.rejection IS NULL ORDER BY p.id""")]
    agency_ids = sorted({r["agency_id"] for r in rows})
    cache = {(r["agency_id"], r["subject_key"]): r for r in conn.execute(
        "SELECT agency_id,subject_key,prompt_sha256,topics FROM ai_subject_topics WHERE agency_id=ANY(%s)",
        (agency_ids,))} if rows else {}

    pending: dict[str, dict[str, dict]] = {}  # agency -> subject key -> first row
    for row in rows:
        if not row["current"] or (row["local_date"] is not None and row["local_date"] <= row["published_on"]):
            continue  # never shown, so never worth a model call
        key = subject_key(row["subject"])
        stored = cache.get((row["agency_id"], key))
        if force or not stored or stored["prompt_sha256"] != prompt_hash:
            pending.setdefault(row["agency_id"], {}).setdefault(key, row)
    items, batches = [], {}
    for agency_id, subjects in pending.items():
        for start in range(0, len(subjects), SUBJECTS_PER_REQUEST):
            batch_id = f"{agency_id}:planned-subjects:{start // SUBJECTS_PER_REQUEST + 1}"
            for order, (key, row) in enumerate(list(subjects.items())[start:start + SUBJECTS_PER_REQUEST], 1):
                item = AgendaItem(id=f"{agency_id}\x1f{key}", meeting_id=batch_id, agency_id=agency_id,
                                  agency_name=row["agency_name"], meeting_title=SUBJECT_MEETING_TITLE, meeting_date=None,
                                  number="", order=order, title=row["subject"], section=row["section"])
                items.append(item)
                batches.setdefault(batch_id, []).append((agency_id, key))
    requests = build_requests(items, [ai_topics.MODE], system)
    primary = CHAIN[0][0]
    estimate = sum(cost_usd(primary, uncached_input=u["sharedPrefixTokens"] * u["requests"] + u["itemInputTokens"],
                            output=u["outputTokens"], requests=u["requests"]) for u in estimated_usage(requests, schema).values())
    spent_this_month = ai_topics.month_to_date_spend(conn) if requests else 0.0
    report = {"promptSha256": prompt_hash, "chain": [m for m, _ in CHAIN], "subjectsPending": len(items),
              "requests": len(requests), "estimatedCostUsd": round(estimate, 4), "monthToDateUsd": round(spent_this_month, 4),
              "monthlyBudgetUsd": monthly_budget, "dryRun": dry_run, "classified": {}, "failed": [], "costUsd": 0.0,
              "previewsUpdated": 0}
    if dry_run:
        return report
    if requests and clients is None and not has_credentials():
        report["skipped"] = "no Claude credentials (API key or workload identity federation); rule tags stay in place"
        requests = []
    elif requests:
        if spent_this_month >= monthly_budget:
            raise SystemExit(f"Month-to-date AI spend ${spent_this_month:.2f} has reached the ${monthly_budget:.2f} budget")
        if estimate > max_cost:
            raise SystemExit(f"Estimated ${estimate:.4f} exceeds --max-cost ${max_cost:.2f}; raise it to classify subjects")
    clients = {} if clients is None else clients
    allowed = topic_ids()
    for request in requests:
        if report["costUsd"] >= max_cost or spent_this_month + report["costUsd"] >= monthly_budget:
            report["stoppedEarly"] = "cost limit reached; remaining subjects keep rule tags until the next run"
            break
        stored = False
        for model, effort in CHAIN:
            client = clients.get(model) or clients.setdefault(model, AnthropicTopicModel(model, effort))
            for _ in range(ATTEMPTS_PER_MODEL):
                try:
                    reply = client.complete(system=request.system, user=request.user, schema=schema, max_tokens=request.max_tokens)
                except ModelError as exc:
                    if exc.fatal:
                        raise SystemExit(f"Subject classification stopped: {exc}") from exc
                    _record_usage(conn, model, {}, ok=False, error=str(exc))
                    continue
                results, problems = parse_reply(reply, request, allowed)
                ok = not any(r["error"] for r in results.values())
                report["costUsd"] += _record_usage(conn, model, reply.usage, ok=ok, error=None if ok else "; ".join(problems))
                if ok:
                    incoming = []
                    for item_id, result in results.items():
                        agency_id, key = item_id.split("\x1f", 1)
                        incoming.append({"agency_id": agency_id, "subject_key": key, "topics": result["topics"],
                                         "reason": result["reason"]})
                        cache[(agency_id, key)] = {"prompt_sha256": prompt_hash, "topics": result["topics"]}
                    # One statement per batch, however many subjects it has (decision 0020).
                    conn.execute("""INSERT INTO ai_subject_topics(agency_id,subject_key,prompt_sha256,model,topics,reason)
                        SELECT r.agency_id,r.subject_key,%(prompt)s,%(model)s,r.topics,r.reason
                        FROM jsonb_to_recordset(%(rows)s::jsonb) AS r(agency_id text,subject_key text,topics jsonb,reason text)
                        ON CONFLICT(agency_id,subject_key) DO UPDATE SET prompt_sha256=excluded.prompt_sha256,
                        model=excluded.model,topics=excluded.topics,reason=excluded.reason,classified_at=now()""",
                        {"rows": json.dumps(incoming), "prompt": prompt_hash, "model": reply.model or model})
                    report["classified"][model] = report["classified"].get(model, 0) + len(results)
                    stored = True
                    break
            if stored:
                break
        if not stored:
            report["failed"].append(request.meeting_id)
        conn.commit()
    # Rows of every version follow the cache, so a version that becomes current never shows stale tags.
    report["previewsUpdated"] = _reconcile(conn, rows, {k: v["topics"] for k, v in cache.items()})
    conn.commit()
    report["costUsd"] = round(report["costUsd"], 6)
    return report
