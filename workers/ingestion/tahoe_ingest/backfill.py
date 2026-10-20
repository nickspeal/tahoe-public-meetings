"""One-time history backfill: capture every meeting dated in a range, then publish.

`tahoe-ingest backfill --since YYYY-MM-DD [--until YYYY-MM-DD] [--agency ID ...]`
enumerates each followed agency's official index for meetings dated in the range
(full listings, date-range queries, or paging, depending on the source), captures
the ones not yet stored with documents through the same capture path as the
scheduled run, and then runs the scheduled run's post-steps (quarantine, reparse,
reclassify, classify-topics, export, validate, store, deploy hook).

It is resumable: a meeting already stored with documents is skipped, so a rerun
continues where a stopped run left off. It takes the same PostgreSQL advisory
lock as `run-scheduled`, so the two never overlap: the backfill waits for a
running scheduled run, and a scheduled run that starts during a backfill skips.

Per-agency enumeration is layered on the adapter modules. Each adapter has a
date-range variant next to its bounded one:

    ttd      discovery.discover_ttd_range       WordPress posts, paged until older than --since
    slt      discovery.discover_slt_range       Granicus publisher (full archive)
    truckee  discovery.discover_truckee_range   calendar, month by month
    configured  source_discovery.discover_configured_range
             granicus, link-index, trpa: the whole index page
             dated-table: one yearly page per year in the range
             civicplus with a Municode embed: Municode date-range query
             legistar: web API date-range query, plus department-page rows
             civicclerk: event list paged until older than --since
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import ai_topics, scheduled
from .capture import capture_candidates, source_identity
from .catalog import followed_agencies
from .core import export_public, init_db, record_failure, stable_id
from .discovery import discover_slt_range, discover_truckee_range, discover_ttd_range
from .scheduled import RUN_LOCK, log
from .source import download_extract
from .source_discovery import discover_configured_range

DEFAULT_FUTURE_DAYS = 120
DEFAULT_LOCK_WAIT_SECONDS = 45 * 60
LOCK_POLL_SECONDS = 30


class Options:
    def __init__(self, *, since: date, until: date | None = None, agencies=None, root=".", max_documents=2, delay=1.0,
                 forecast_days=None, min_meetings=1, max_cost=ai_topics.DEFAULT_MAX_RUN_COST,
                 monthly_budget=ai_topics.DEFAULT_MONTHLY_BUDGET, out=None, publish=True, dry_run=False,
                 lock_wait_seconds=DEFAULT_LOCK_WAIT_SECONDS, today: date | None = None):
        today = today or datetime.now(scheduled.PACIFIC).date()
        self.since = since
        self.until = until or today + timedelta(days=DEFAULT_FUTURE_DAYS)
        if self.since > self.until:
            raise ValueError("--since must not be after --until")
        self.agencies = list(agencies or [])
        self.root = Path(root)
        self.max_documents = max_documents
        self.delay = delay
        self.forecast_days = (int(os.getenv("TAHOE_FORECAST_DAYS", scheduled.DEFAULT_FORECAST_DAYS))
                              if forecast_days is None else forecast_days)
        self.min_meetings = min_meetings
        self.max_cost = max_cost
        self.monthly_budget = monthly_budget
        self.out = out
        self.publish = publish
        self.dry_run = dry_run
        self.lock_wait_seconds = lock_wait_seconds


def discover_agency_range(agency_id: str, config: dict, *, since: date, until: date) -> dict:
    """All candidates from one agency's adapter dated since..until.

    Returns ``candidates``, ``earliestListed`` (the oldest date the source showed,
    or for date-range APIs the oldest meeting returned), and ``queryByDate`` (the
    source was asked for the range directly, so it reaches ``since`` by design).
    """
    adapter = config.get("discoveryAdapter")
    if not adapter:
        raise ValueError(f"{agency_id}: no discovery adapter")
    query_by_date = False
    if adapter == "configured":
        candidates, earliest, query_by_date = discover_configured_range(agency_id, config["discovery"], since=since, until=until)
    elif adapter == "ttd":
        candidates, earliest = discover_ttd_range(since=since, until=until)
    elif adapter == "slt":
        candidates, earliest = discover_slt_range(since=since, until=until)
    elif adapter == "truckee":
        candidates, earliest = discover_truckee_range(since=since, until=until)
        query_by_date = True
    else:
        raise ValueError(f"{agency_id}: unknown discovery adapter {adapter}")
    for candidate in candidates:
        candidate["agency"] = agency_id
    return {"candidates": candidates, "earliestListed": earliest, "queryByDate": query_by_date}


class Downloads:
    """Wraps the document fetcher to count successful downloads and their bytes."""

    def __init__(self, fetch):
        self.fetch, self.documents, self.bytes = fetch, 0, 0

    def __call__(self, url):
        result = self.fetch(url)
        self.documents += 1
        self.bytes += int(result.get("bytes") or 0)
        return result


def acquire_lock(conn, wait_seconds: float, *, poll: float = LOCK_POLL_SECONDS, sleep=time.sleep,
                 clock=time.monotonic) -> bool:
    """Take the scheduled run's advisory lock, waiting up to ``wait_seconds`` for a running run."""
    deadline = clock() + wait_seconds
    while True:
        ok = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (RUN_LOCK,)).fetchone()["ok"]
        conn.commit()
        if ok:
            return True
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        log("INFO", "backfill: another run holds the lock; waiting", waitSeconds=round(remaining))
        sleep(min(poll, remaining))


def database_size(conn) -> int | None:
    try:
        size = conn.execute("SELECT pg_database_size(current_database()) AS bytes").fetchone()["bytes"]
        conn.commit()
        return int(size)
    except Exception:
        conn.rollback()
        return None


def stored_counts(conn) -> dict:
    row = conn.execute("""SELECT (SELECT count(*) FROM meetings) AS meetings,
        (SELECT count(*) FROM agenda_items) AS items,
        (SELECT count(*) FROM document_current) AS documents,
        (SELECT count(*) FROM document_pages) AS pages""").fetchone()
    conn.commit()
    return {key: int(value) for key, value in row.items()}


def _selected_agencies(options: Options) -> dict[str, dict]:
    followed = followed_agencies()
    adapters = {aid: config for aid, config in followed.items() if config.get("discoveryAdapter")}
    unknown = [aid for aid in options.agencies if aid not in adapters]
    if unknown:
        raise ValueError(f"not a followed agency with a discovery adapter: {', '.join(unknown)}")
    return {aid: adapters[aid] for aid in sorted(options.agencies or adapters)}


def enumerate_agencies(conn, run: scheduled.Run, options: Options, agencies: dict, discover) -> dict:
    """Discover every agency's in-range candidates and split them into to-capture and already stored."""
    quarantined = scheduled._quarantine_keys(options.root)
    plan = {}
    for aid, config in agencies.items():
        report = {"found": 0, "alreadyStored": 0, "quarantined": 0, "toCapture": 0}
        began = time.monotonic()
        try:
            listed = discover(aid, config, since=options.since, until=options.until)
            candidates = listed["candidates"]
            earliest = listed.get("earliestListed")
            report.update(found=len(candidates), earliestListed=earliest,
                          earliestInRange=min((c["date"] for c in candidates if c.get("date")), default=None),
                          undated=sum(1 for c in candidates if not c.get("date")),
                          reachesSince=bool(listed.get("queryByDate") or (earliest and earliest <= options.since.isoformat())))
            pending = []
            for candidate in candidates:
                if (aid, str(candidate.get("source_id"))) in quarantined:
                    report["quarantined"] += 1
                    continue
                meeting_id = stable_id(aid, source_identity(conn, candidate))
                stored = conn.execute("SELECT 1 FROM meetings WHERE id=%s", (meeting_id,)).fetchone()
                if stored and (not candidate.get("documents") or scheduled._has_documents(conn, meeting_id)):
                    report["alreadyStored"] += 1
                    continue
                pending.append(candidate)
            conn.commit()
            report["toCapture"] = len(pending)
            plan[aid] = (report, pending)
        except Exception as exc:  # One index failing never stops the backfill.
            conn.rollback()
            report["error"] = scheduled._short(exc)
            run.fail(f"backfill-discover:{aid}", exc)
            record_failure(conn, aid, "discovery", "discover", scheduled._short(exc, 1000))
            conn.commit()
            plan[aid] = (report, [])
        report["discoverSeconds"] = round(time.monotonic() - began, 1)
        log("WARNING" if report.get("error") else "INFO", f"backfill discover {aid}", agency=aid, result=report)
    return plan


def capture_plan(conn, run: scheduled.Run, options: Options, plan: dict, fetch) -> None:
    for aid, (report, pending) in plan.items():
        if not pending:
            report.update(captured=0, failed=0)
            continue
        began = time.monotonic()
        result = capture_candidates(conn, pending, max_documents=options.max_documents, fetch=fetch,
                                    incremental=True, delay=options.delay)
        report.update(captured=result["processed"], failed=result["failed"],
                      captureSeconds=round(time.monotonic() - began, 1))
        if result["captureFailures"]:
            report["documentFailures"] = len(result["captureFailures"])
        for error in result["errors"] + result["captureFailures"]:
            run.fail(f"backfill-capture:{aid}", error)
        conn.commit()
        log("WARNING" if result["failed"] or result["captureFailures"] else "INFO",
            f"backfill capture {aid}", agency=aid, result=report)


def _agency_status(plan: dict) -> str:
    bad = any(r.get("error") or r.get("failed") or r.get("documentFailures") for r, _ in plan.values())
    return "degraded" if bad else "ok"


def backfill(conn, options: Options, *, discover=discover_agency_range, fetch=download_extract,
             post=scheduled.post_deploy_hook, sleep=time.sleep) -> dict:
    started_at = datetime.now(timezone.utc)
    agencies = _selected_agencies(options)
    run = scheduled.Run(conn)
    window = {"since": options.since.isoformat(), "until": options.until.isoformat(), "agencies": list(agencies),
              "dryRun": options.dry_run}
    if not acquire_lock(conn, options.lock_wait_seconds, sleep=sleep):
        return {"severity": "WARNING", "message": "backfill skipped: another run held the lock", "status": "skipped",
                "window": window, "steps": [], "failures": []}
    scratch = scheduled._scratch_dir()
    previous_tempdir, tempfile.tempdir = tempfile.tempdir, str(scratch)
    downloads = Downloads(fetch)
    payload, published, plan, measured = None, {}, {}, {}
    try:
        run.step("init-db", lambda: init_db(conn), fatal=True)
        if run.fatal:
            return _summary(run, started_at, payload, published, plan, downloads, measured, window)
        measured["dbBytesBefore"] = database_size(conn)
        measured["storedBefore"] = stored_counts(conn)

        def enumerate_step():
            plan.update(enumerate_agencies(conn, run, options, agencies, discover))
            totals = {"found": sum(r["found"] for r, _ in plan.values()),
                      "alreadyStored": sum(r["alreadyStored"] for r, _ in plan.values()),
                      "toCapture": sum(r["toCapture"] for r, _ in plan.values())}
            return _agency_status(plan), totals
        run.step("backfill-discover", enumerate_step)
        if options.dry_run:
            return _summary(run, started_at, payload, published, plan, downloads, measured, window)

        def capture_step():
            capture_plan(conn, run, options, plan, downloads)
            return _agency_status(plan), {"captured": sum(r.get("captured", 0) for r, _ in plan.values()),
                                          "documents": downloads.documents, "bytes": downloads.bytes}
        run.step("backfill-capture", capture_step)
        # The scheduled run's post-steps, reused unchanged.
        run.step("quarantine", lambda: scheduled.apply_quarantine(conn, options.root))
        run.step("reparse", lambda: scheduled._reparse(conn))
        run.step("reclassify", lambda: scheduled._reclassify(conn))
        run.step("classify-topics", lambda: scheduled.classify(conn, options))

        def export():
            nonlocal payload
            payload = export_public(conn, mode="live", forecast_days=options.forecast_days)
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            if options.out:
                dest = Path(options.out)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            measured["exportBytes"] = len(encoded)
            return {"meetings": len(payload["meetings"]), "bytes": len(encoded)}
        run.step("export", export, fatal=True)
        if payload is not None and options.publish:
            published = scheduled.publish(conn, payload, run, min_meetings=options.min_meetings,
                                          hook_url=os.getenv("VERCEL_DEPLOY_HOOK_URL"), post=post)
        elif payload is not None:
            run.step("validate", lambda: scheduled.validated(payload, options.min_meetings), fatal=True)
    finally:
        tempfile.tempdir = previous_tempdir
        shutil.rmtree(scratch, ignore_errors=True)
        try:
            measured["dbBytesAfter"] = database_size(conn)
            measured["storedAfter"] = stored_counts(conn)
        except Exception:
            conn.rollback()
        try:
            conn.execute("SELECT pg_advisory_unlock(%s)", (RUN_LOCK,))
            conn.commit()
        except Exception:
            pass
    return _summary(run, started_at, payload, published, plan, downloads, measured, window)


def _summary(run, started_at, payload, published, plan, downloads, measured, window) -> dict:
    summary = scheduled._summary(run, started_at, payload, published)
    summary["message"] = f"backfill {summary['status']}"
    by_agency = {aid: report for aid, (report, _) in plan.items()}
    summary.update(
        window=window,
        byAgency=by_agency,
        totals={"found": sum(r.get("found", 0) for r in by_agency.values()),
                "alreadyStored": sum(r.get("alreadyStored", 0) for r in by_agency.values()),
                "captured": sum(r.get("captured", 0) for r in by_agency.values()),
                "failedMeetings": sum(r.get("failed", 0) for r in by_agency.values()),
                "documentFailures": sum(r.get("documentFailures", 0) for r in by_agency.values()),
                "agenciesWithErrors": sorted(a for a, r in by_agency.items() if r.get("error")),
                "agenciesNotReachingSince": sorted(a for a, r in by_agency.items()
                                                   if not r.get("error") and not r.get("reachesSince"))},
        downloads={"documents": downloads.documents, "bytes": downloads.bytes},
        phaseSeconds={s["name"]: s.get("seconds") for s in run.steps},
        database={"bytesBefore": measured.get("dbBytesBefore"), "bytesAfter": measured.get("dbBytesAfter"),
                  "storedBefore": measured.get("storedBefore"), "storedAfter": measured.get("storedAfter")},
        exportBytes=measured.get("exportBytes"),
    )
    return summary
