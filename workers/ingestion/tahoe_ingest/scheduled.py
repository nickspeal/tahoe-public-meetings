"""The scheduled pipeline: populate or refresh the database from tracked config and live sources, then publish.

`tahoe-ingest run-scheduled` runs these steps in order; one source failing never
stops the run, but an invalid export is never published:

1. init-db           idempotent schema and agency registry
2. seed-fixtures     tracked source bundle; only meetings not yet stored (never reapplied)
3. one-offs          config/one-off-meetings.json (idempotent upsert)
4. comment-periods   config/public-comment-periods.json (fetches and verifies notices)
5. verified-sources  config/verified-agency-meetings-*.json; captures meetings not yet stored
6. discover          each followed agency with an adapter: recent and upcoming meetings, polite delays
7. quarantine        config/quarantined-meeting-candidates.json
8. reparse           stored text of meetings without items
9. reclassify        rule topic tags
10. classify-topics  Claude tags, only with Claude credentials, within cost guards
11. export           public JSON (mode "live")
12. validate         the same checks as scripts/check_export.py, plus a minimum size
13. store            insert into public_exports, keep the latest 5
14. deploy-hook      POST to VERCEL_DEPLOY_HOOK_URL when set

Only 11-14 failing makes the run fail.
"""
from __future__ import annotations

import glob
import json
import os
import resource
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import ai_topics
from .capture import StoredMeetings, capture_candidates, discover_agency
from .http import EndpointSkipped, skipped_endpoint
from .catalog import followed_agencies
from .comment_periods import ingest_comment_periods
from .core import export_public, ingest_bundle, init_db, reclassify_topics, record_failure, stable_id
from .export_check import validate_export
from .one_offs import load_one_offs
from .reparse import reparse_meetings
from .schedules import DEFAULT_FORECAST_DAYS
from .source import download_extract
from .source_discovery import date_in

PACIFIC = ZoneInfo("America/Los_Angeles")
RETAINED_EXPORTS = 5
RUN_LOCK = 0x7461686f65  # pg advisory lock key shared by every scheduled run
FIXTURE_BUNDLE = "tests/fixtures/meetings.json"
ONE_OFFS = "config/one-off-meetings.json"
COMMENT_PERIODS = "config/public-comment-periods.json"
VERIFIED_GLOB = "config/verified-agency-meetings-*.json"
QUARANTINED = "config/quarantined-meeting-candidates.json"
FAILED_RETRY_HOURS = 24
MAX_LISTED_FAILURES = 100

# Discovery policy. History is filled once by a separate backfill; the scheduled
# job only keeps recent and upcoming meetings current. Stored meetings are kept
# forever, whatever the window.
COVERAGE_START = date(2026, 1, 1)  # Never capture a dated meeting before this day (undated TTD posts excepted).
INDEX_LIMIT = 20                   # Rows requested from each index (the adapters' maximum); the window is applied afterwards.
UNDATED_INDEX_LIMIT = 6            # TTD posts are undated and each costs a page fetch, so they stay bounded by count.
PAST_DAYS = 60                     # Look-back for newly linked documents on past meetings (minutes, revised packets).
FUTURE_DAYS = 120                  # Horizon for posted notices; covers the 90-day forecast.
RECAPTURE_PAST_DAYS = 7            # Past meetings this recent are re-downloaded every run (same-URL corrections, cancellations).
MAX_CAPTURES_PER_AGENCY = 10       # Meetings captured per agency per run, nearest to today first.


class Options:
    def __init__(self, *, root=".", discovery_limit=INDEX_LIMIT, past_days=PAST_DAYS, future_days=FUTURE_DAYS,
                 recapture_past_days=RECAPTURE_PAST_DAYS, max_captures_per_agency=MAX_CAPTURES_PER_AGENCY,
                 coverage_start=COVERAGE_START, max_documents=2, delay=1.0, forecast_days=None, min_meetings=1,
                 max_cost=ai_topics.DEFAULT_MAX_RUN_COST, monthly_budget=ai_topics.DEFAULT_MONTHLY_BUDGET,
                 out=None, skip_discovery=False, publish=True):
        self.root = Path(root)
        self.discovery_limit = discovery_limit
        self.past_days = past_days
        self.future_days = future_days
        self.recapture_past_days = recapture_past_days
        self.max_captures_per_agency = max_captures_per_agency
        self.coverage_start = coverage_start
        self.max_documents = max_documents
        self.delay = delay
        self.forecast_days = (int(os.getenv("TAHOE_FORECAST_DAYS", DEFAULT_FORECAST_DAYS))
                              if forecast_days is None else forecast_days)
        self.min_meetings = min_meetings
        self.max_cost = max_cost
        self.monthly_budget = monthly_budget
        self.out = out
        self.skip_discovery = skip_discovery
        self.publish = publish


def log(severity: str, message: str, **fields) -> None:
    """One structured line on stderr; Cloud Logging reads `severity` and `message`."""
    print(json.dumps({"severity": severity, "message": message, **fields}, default=str), file=sys.stderr, flush=True)


def _short(text, limit=300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


class Run:
    def __init__(self, conn):
        self.conn = conn
        self.steps = []
        self.failures = []
        self.fatal = False
        self.started = time.monotonic()

    def fail(self, source: str, error) -> None:
        self.failures.append(f"{source}: {_short(error)}")

    def step(self, name: str, action, *, fatal: bool = False):
        began = time.monotonic()
        entry = {"name": name, "status": "ok"}
        try:
            detail = action()
            if isinstance(detail, tuple):
                entry["status"], detail = detail
            if detail is not None:
                entry["detail"] = detail
            self.conn.commit()
        except BaseException as exc:  # SystemExit from cost guards included; Ctrl-C is re-raised.
            self.conn.rollback()
            if isinstance(exc, KeyboardInterrupt):
                raise
            entry["status"] = "failed"
            entry["error"] = _short(exc, 1000)
            self.fail(name, exc)
            self.fatal |= fatal
        entry["seconds"] = round(time.monotonic() - began, 2)
        self.steps.append(entry)
        log("ERROR" if entry["status"] == "failed" else "WARNING" if entry["status"] == "degraded" else "INFO",
            f"step {name}: {entry['status']}", step=entry)
        return entry


def _read_json(path: Path):
    return json.loads(path.read_text())


def _existing(conn) -> set[str]:
    return {r["id"] for r in conn.execute("SELECT id FROM meetings")}


def _quarantined(root: Path) -> list[dict]:
    path = root / QUARANTINED
    return _read_json(path).get("records", []) if path.is_file() else []


def _quarantine_keys(root: Path) -> set[tuple[str, str]]:
    return {(r["candidate"]["agency"], str(r["candidate"]["source_id"])) for r in _quarantined(root)}


def seed_fixtures(conn, root: Path, run: Run):
    path = root / FIXTURE_BUNDLE
    if not path.is_file():
        return "skipped", {"reason": f"{FIXTURE_BUNDLE} not present"}
    bundle = _read_json(path)
    followed = followed_agencies()
    stored = _existing(conn)
    fresh = [m for m in bundle["meetings"] if m.get("agency") in followed
             and stable_id(m["agency"], str(m.get("id") or m.get("source_id"))) not in stored]
    result = ingest_bundle(conn, {**bundle, "meetings": fresh}, mode="fixture")
    for error in result["errors"]:
        run.fail("seed-fixtures", error)
    return ("degraded" if result["failed"] else "ok"), {"inBundle": len(bundle["meetings"]), "alreadyStored": len(bundle["meetings"]) - len(fresh),
                                                        "processed": result["processed"], "failed": result["failed"]}


def one_offs(conn, root: Path, run: Run):
    path = root / ONE_OFFS
    if not path.is_file():
        return "skipped", {"reason": f"{ONE_OFFS} not present"}
    result = ingest_bundle(conn, load_one_offs(path), mode="fixture")
    for error in result["errors"]:
        run.fail("one-offs", error)
    return ("degraded" if result["failed"] else "ok"), {"processed": result["processed"], "failed": result["failed"]}


def comment_periods(conn, root: Path, run: Run, fetch):
    path = root / COMMENT_PERIODS
    if not path.is_file():
        return "skipped", {"reason": f"{COMMENT_PERIODS} not present"}
    result = ingest_comment_periods(conn, _read_json(path), fetch=fetch)
    for error in result["errors"]:
        run.fail("comment-periods", error)
    return ("degraded" if result["failed"] else "ok"), {"processed": result["processed"], "failed": result["failed"]}


def documents_skipped(candidate: dict, max_documents: int) -> bool:
    """True when the candidate lists documents and every one a capture would fetch is a configured skip."""
    documents = candidate.get("documents", [])[:max_documents]
    return bool(documents) and all(skipped_endpoint(d["url"]) for d in documents)


def stored_meetings(conn) -> StoredMeetings:
    return StoredMeetings(conn, failure_hours=FAILED_RETRY_HOURS)


def verified_sources(conn, root: Path, run: Run, options: Options, fetch):
    """Capture reviewed candidates from tracked config once; retry missing documents at most daily."""
    files = sorted(glob.glob(str(root / VERIFIED_GLOB)))
    if not files:
        return "skipped", {"reason": f"no {VERIFIED_GLOB} files"}
    followed = followed_agencies()
    quarantined = _quarantine_keys(root)
    pending, counts = [], {"files": len(files), "candidates": 0, "alreadyStored": 0, "quarantined": 0, "unfollowed": 0}
    stored = stored_meetings(conn)
    for name in files:
        for candidate in _read_json(Path(name)).get("candidates", []):
            counts["candidates"] += 1
            key = (candidate.get("agency"), str(candidate.get("source_id")))
            if key[0] not in followed:
                counts["unfollowed"] += 1
                continue
            if key in quarantined:
                counts["quarantined"] += 1
                continue
            meeting_id = stable_id(key[0], stored.identity(candidate))
            if meeting_id in stored.meetings and (not candidate.get("documents") or meeting_id in stored.document_urls
                                                  or documents_skipped(candidate, options.max_documents)
                                                  or meeting_id in stored.failed):
                counts["alreadyStored"] += 1
                continue
            pending.append(candidate)
    conn.commit()
    log("INFO", f"verified-sources: capturing {len(pending)} candidates", counts=counts)
    result = capture_candidates(conn, pending, max_documents=options.max_documents, fetch=fetch,
                                incremental=True, delay=options.delay)
    for error in result["errors"] + result["captureFailures"]:
        run.fail("verified-sources", error)
    counts.update(captured=result["processed"], failed=result["failed"], documentFailures=len(result["captureFailures"]))
    return ("degraded" if result["failed"] or result["captureFailures"] else "ok"), counts


def _window_reason(candidate: dict, today: date, options: Options) -> str | None:
    """Why a candidate is outside the scheduled window, or None. Stored meetings are never removed for this."""
    if not candidate.get("date"):
        return None  # Undated candidates (TTD posts) cannot be windowed; their count is bounded by UNDATED_INDEX_LIMIT.
    day = date.fromisoformat(candidate["date"])
    if day < options.coverage_start:
        return "beforeCoverage"
    if not today - timedelta(days=options.past_days) <= day <= today + timedelta(days=options.future_days):
        return "outsideWindow"
    return None


def _skip_reason(stored: StoredMeetings, meeting_id: str, candidate: dict, today: date, options: Options) -> str | None:
    """Keep stored past meetings from being downloaded again on every run.

    Older than the recapture window, a meeting is "settled" once every document
    a capture would fetch is already stored under the same URL, so a newly
    linked document (minutes, a packet at a new address) is still picked up.
    Its failed downloads are retried at most once a day.
    """
    local_date = stored.local_date(meeting_id)
    if not local_date or local_date >= today - timedelta(days=options.recapture_past_days):
        return None
    urls = stored.document_urls.get(meeting_id)
    if urls and all(d["url"] in urls for d in candidate.get("documents", [])[:options.max_documents]):
        return "settled"
    if meeting_id in stored.failed:
        return "retryLater"
    return None


def _index_limit(config: dict, options: Options) -> int:
    if config.get("discoveryAdapter") == "ttd":
        return min(options.discovery_limit, UNDATED_INDEX_LIMIT)
    return options.discovery_limit


def _nearest_first(candidates: list[dict], today: date) -> list[dict]:
    """Recent and upcoming meetings before distant ones; undated posts keep their newest-first order."""
    return sorted(candidates, key=lambda c: abs((date.fromisoformat(c["date"]) - today).days) if c.get("date") else 0)


def discover_sources(conn, root: Path, run: Run, options: Options, discover, fetch, today: date):
    """Capture each followed agency's recent and upcoming meetings.

    Each index is read up to the adapters' maximum and only then filtered to the
    coverage floor and window, so far-future postings never crowd out recent past
    meetings. Settled meetings are skipped, and at most `max_captures_per_agency`
    of the rest are captured, nearest to today first; the remainder wait for the
    next run.
    """
    followed = followed_agencies()
    quarantined = _quarantine_keys(root)
    # Each agency's captures only add that agency's meetings, so one snapshot serves every agency's selection.
    stored = stored_meetings(conn)
    agencies, checked = {}, []
    for aid in sorted(a for a, config in followed.items() if config.get("discoveryAdapter")):
        report = agencies[aid] = {}
        try:
            candidates = discover(aid, followed[aid], limit=_index_limit(followed[aid], options))
            report["discovered"] = len(candidates)
            selected = []
            for candidate in candidates:
                if not candidate.get("date"):
                    candidate["date"] = date_in(candidate.get("title", ""))
                if (aid, str(candidate.get("source_id"))) in quarantined:
                    continue
                reason = _window_reason(candidate, today, options)
                if not reason:
                    meeting_id = stable_id(aid, stored.identity(candidate))
                    reason = _skip_reason(stored, meeting_id, candidate, today, options)
                    if not reason and documents_skipped(candidate, options.max_documents) and meeting_id in stored.meetings:
                        reason = "endpointSkipped"
                if reason:
                    report[reason] = report.get(reason, 0) + 1
                    continue
                selected.append(candidate)
            conn.commit()
            selected = _nearest_first(selected, today)
            if len(selected) > options.max_captures_per_agency:
                report["deferred"] = len(selected) - options.max_captures_per_agency
                selected = selected[:options.max_captures_per_agency]
            result = capture_candidates(conn, selected, max_documents=options.max_documents, fetch=fetch,
                                        incremental=True, delay=options.delay)
            report.update(captured=result["processed"], failed=result["failed"])
            if result["captureFailures"]:
                report["documentFailures"] = len(result["captureFailures"])
            for error in result["errors"] + result["captureFailures"]:
                run.fail(f"discover:{aid}", error)
            if not result["failed"] and not result["captureFailures"]:
                # The index was checked even when every candidate was already settled.
                checked.append(aid)
            conn.commit()
        except EndpointSkipped as exc:
            conn.rollback()
            report["skipped"] = _short(exc)
        except Exception as exc:  # A broken index never stops other agencies.
            conn.rollback()
            report["error"] = _short(exc)
            run.fail(f"discover:{aid}", exc)
            record_failure(conn, aid, "discovery", "discover", _short(exc, 1000))
            conn.commit()
        log("WARNING" if report.get("error") or report.get("failed") or report.get("documentFailures") else "INFO",
            f"discover {aid}", agency=aid, result=report)
    if checked:
        conn.execute("UPDATE agencies SET status='ok',error=NULL,last_checked_at=now() WHERE id=ANY(%s)", (checked,))
        conn.commit()
    failed = [aid for aid, r in agencies.items() if r.get("error") or r.get("failed") or r.get("documentFailures")]
    totals = {"agencies": len(agencies), "agenciesWithFailures": failed,
              "captured": sum(r.get("captured", 0) for r in agencies.values()), "byAgency": agencies}
    return ("degraded" if failed else "ok"), totals


def apply_quarantine(conn, root: Path):
    applied = 0
    for record in _quarantined(root):
        candidate = record["candidate"]
        applied += conn.execute("""UPDATE meetings SET quarantine_reason=%s WHERE agency_id=%s AND source_id=%s
            AND quarantine_reason IS NULL""", (f"{record.get('reviewId', 'review')}: {record['reason']}",
                                               candidate["agency"], str(candidate["source_id"]))).rowcount
    return {"records": len(_quarantined(root)), "applied": applied}


def classify(conn, options: Options):
    if not ai_topics.has_credentials():
        return "skipped", {"reason": "no Claude credentials (API key or workload identity federation); rule tags stay in place"}
    try:
        report = ai_topics.classify_topics(conn, max_cost=options.max_cost, monthly_budget=options.monthly_budget)
    except SystemExit as exc:  # Budget and fatal-API guards stop classification, not publication.
        conn.rollback()
        return "degraded", {"stopped": _short(exc)}
    report.pop("promptSha256", None)
    status = "degraded" if report.get("failedMeetings") or report.get("stoppedEarly") else "ok"
    return status, report


def store_export(conn, payload: dict) -> int:
    with conn.transaction():
        row = conn.execute("""INSERT INTO public_exports(schema_version,generated_at,payload)
            VALUES (%s,%s,%s::jsonb) RETURNING id""",
            (payload["schemaVersion"], datetime.fromisoformat(payload["generatedAt"].replace("Z", "+00:00")),
             json.dumps(payload, ensure_ascii=False))).fetchone()
        conn.execute("""DELETE FROM public_exports WHERE id NOT IN
            (SELECT id FROM public_exports ORDER BY id DESC LIMIT %s)""", (RETAINED_EXPORTS,))
    return row["id"]


def post_deploy_hook(url: str, *, timeout: float = 30.0, opener=urllib.request.urlopen) -> dict:
    """POST to the Vercel deploy hook. The URL is a secret: it never appears in output or errors."""
    if not url.startswith("https://"):
        raise ValueError("VERCEL_DEPLOY_HOOK_URL must be an https URL")
    request = urllib.request.Request(url, data=b"", method="POST",
                                     headers={"User-Agent": "TahoePublicMeetings-ingest/0.1"})
    try:
        with opener(request, timeout=timeout) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"deploy hook returned HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"deploy hook request failed: {type(reason).__name__}") from None
    if not 200 <= status < 300:
        raise RuntimeError(f"deploy hook returned HTTP {status}")
    return {"httpStatus": status}


def validated(payload: dict, min_meetings: int) -> dict:
    try:
        errors, stats = validate_export(payload)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        errors, stats = [f"malformed export: {exc}"], {}
    if not errors and stats["meetings"] < min_meetings:
        errors.append(f"export has {stats['meetings']} meetings; at least {min_meetings} required")
    if errors:
        raise ValueError(f"export validation failed ({len(errors)} errors): " + "; ".join(errors[:20]))
    return stats


def publish(conn, payload: dict, run: Run, *, min_meetings: int, hook_url: str | None, post=post_deploy_hook) -> dict:
    """Validate, store, then trigger a deploy. Each part is its own fatal step."""
    published = {}
    stats = run.step("validate", lambda: validated(payload, min_meetings), fatal=True)
    if stats["status"] != "ok":
        return published
    published["stats"] = stats["detail"]
    stored = run.step("store", lambda: {"exportId": store_export(conn, payload), "retained": RETAINED_EXPORTS}, fatal=True)
    if stored["status"] != "ok":
        return published
    published["exportId"] = stored["detail"]["exportId"]
    if hook_url:
        run.step("deploy-hook", lambda: post(hook_url), fatal=True)
    else:
        run.steps.append({"name": "deploy-hook", "status": "skipped", "detail": {"reason": "VERCEL_DEPLOY_HOOK_URL is not set"}})
    return published


def peak_memory() -> dict:
    scale = 1 if sys.platform == "darwin" else 1024  # ru_maxrss is bytes on macOS, KiB on Linux
    mib = lambda who: round(resource.getrusage(who).ru_maxrss * scale / 2**20, 1)
    report = {"workerMiB": mib(resource.RUSAGE_SELF), "largestChildMiB": mib(resource.RUSAGE_CHILDREN)}
    for path in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        try:
            report["containerMiB"] = round(int(Path(path).read_text().strip()) / 2**20, 1)
            break
        except (OSError, ValueError):
            continue
    return report


def _scratch_dir():
    base = Path(os.getenv("TAHOE_SCRATCH_DIR") or tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="tahoe-run-", dir=base))


def run_scheduled(conn, options: Options, *, discover=discover_agency, fetch=download_extract,
                  post=post_deploy_hook, today: date | None = None) -> dict:
    started_at = datetime.now(timezone.utc)
    run = Run(conn)
    today = today or started_at.astimezone(PACIFIC).date()
    root = options.root
    if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (RUN_LOCK,)).fetchone()["ok"]:
        conn.rollback()
        return {"severity": "WARNING", "message": "another scheduled run holds the lock; nothing done",
                "status": "skipped", "steps": [], "failures": []}
    conn.commit()
    # PDFs and curl bodies go to one run directory that is always removed (in-memory /tmp on Cloud Run).
    scratch = _scratch_dir()
    previous_tempdir, tempfile.tempdir = tempfile.tempdir, str(scratch)
    payload, published = None, {}
    try:
        run.step("init-db", lambda: init_db(conn), fatal=True)
        if run.fatal:
            return _summary(run, started_at, payload, published)
        run.step("seed-fixtures", lambda: seed_fixtures(conn, root, run))
        run.step("one-offs", lambda: one_offs(conn, root, run))
        run.step("comment-periods", lambda: comment_periods(conn, root, run, fetch))
        run.step("verified-sources", lambda: verified_sources(conn, root, run, options, fetch))
        if options.skip_discovery:
            run.steps.append({"name": "discover", "status": "skipped", "detail": {"reason": "--skip-discovery"}})
        else:
            run.step("discover", lambda: discover_sources(conn, root, run, options, discover, fetch, today))
        run.step("quarantine", lambda: apply_quarantine(conn, root))
        run.step("reparse", lambda: _reparse(conn))
        run.step("reclassify", lambda: _reclassify(conn))
        run.step("classify-topics", lambda: classify(conn, options))

        def export():
            nonlocal payload
            payload = export_public(conn, mode="live", forecast_days=options.forecast_days)
            if options.out:
                dest = Path(options.out)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            return {"meetings": len(payload["meetings"]), "bytes": len(json.dumps(payload, ensure_ascii=False).encode())}
        run.step("export", export, fatal=True)
        if payload is not None and options.publish:
            published = publish(conn, payload, run, min_meetings=options.min_meetings,
                                hook_url=os.getenv("VERCEL_DEPLOY_HOOK_URL"), post=post)
        elif payload is not None:
            run.step("validate", lambda: validated(payload, options.min_meetings), fatal=True)
    finally:
        tempfile.tempdir = previous_tempdir
        shutil.rmtree(scratch, ignore_errors=True)
        try:
            conn.execute("SELECT pg_advisory_unlock(%s)", (RUN_LOCK,))
            conn.commit()
        except Exception:
            pass
    return _summary(run, started_at, payload, published)


def _reparse(conn):
    result = reparse_meetings(conn)
    return {"parsed": len(result["parsed"]), "unparsed": len(result["failed"]), "skipped": result["skipped"]}


def _reclassify(conn):
    result = reclassify_topics(conn)
    return {"itemsScanned": result["itemsScanned"], "itemsChanged": result["itemsChanged"]}


def _summary(run: Run, started_at: datetime, payload, published: dict) -> dict:
    degraded = any(s["status"] in ("degraded", "failed") for s in run.steps)
    status = "failed" if run.fatal else "degraded" if degraded else "ok"
    ai = next((s.get("detail", {}) for s in run.steps if s["name"] == "classify-topics"), {})
    counts = {}
    if payload is not None:
        meetings = payload["meetings"]
        counts = {"meetings": sum(1 for m in meetings if m.get("kind") != "forecast"),
                  "expectedMeetings": sum(1 for m in meetings if m.get("kind") == "forecast"),
                  "items": sum(len(m.get("items", [])) for m in meetings),
                  "agencies": len(payload.get("agencies", [])),
                  "agenciesWithMeetings": len({m["agencyId"] for m in meetings if m.get("kind") != "forecast"}),
                  "commentPeriods": len(payload.get("commentPeriods", []))}
    return {"severity": "ERROR" if status == "failed" else "WARNING" if status == "degraded" else "INFO",
            "message": f"run-scheduled {status}", "status": status,
            "startedAt": started_at.isoformat(), "durationSeconds": round(time.monotonic() - run.started, 1),
            "steps": run.steps, "counts": counts, "exportId": published.get("exportId"),
            "aiCostUsd": ai.get("costUsd", 0.0) if isinstance(ai, dict) else 0.0,
            "failureCount": len(run.failures), "failures": run.failures[:MAX_LISTED_FAILURES],
            "peakMemory": peak_memory()}
