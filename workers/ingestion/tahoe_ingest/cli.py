"""Operator CLI. All writes use a local PostgreSQL DATABASE_URL."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .core import connect, edit_item, export_public, ingest_bundle, init_db, reclassify_topics, record_failure
from .reparse import reparse_meetings
from .capture import capture_candidates, discover_agency, source_identity
from .source import download_extract
from .comment_periods import ingest_comment_periods
from .catalog import agency_catalog_path, followed_agencies
from .one_offs import load_one_offs
from .duration_ops import detect_from_minutes, duration_report, format_report, set_duration
from . import ai_topics, alerts, backfill, evaluate_topics, item_summaries, participation, scheduled, staff_reports
from .schedules import DEFAULT_FORECAST_DAYS, describe, learn_schedules_report, write_schedules


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tahoe-ingest")
    p.add_argument("--database-url", default=os.getenv("DATABASE_URL"), help="PostgreSQL URL (or DATABASE_URL)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    ingest = sub.add_parser("ingest-fixtures", help="Ingest representative source fixture JSON")
    ingest.add_argument("path", nargs="?", default="tests/fixtures/meetings.json")
    manifest = sub.add_parser("ingest-manifest", help="Ingest an independently captured JSON source bundle")
    manifest.add_argument("path")
    one_offs = sub.add_parser("ingest-one-offs", help="Add/update configured workshops and other one-off notices")
    one_offs.add_argument("path", nargs="?", default="config/one-off-meetings.json")
    one_offs.add_argument("--dry-run", action="store_true")
    comments = sub.add_parser("ingest-comment-periods", help="Verify and import public comment windows from official notices")
    comments.add_argument("path", nargs="?", default="config/public-comment-periods.json")
    sub.add_parser("agencies", help="List followed agencies and discovery readiness")
    export = sub.add_parser("export")
    export.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    export.add_argument("--out", required=True)
    export.add_argument("--forecast-days", type=int, default=int(os.getenv("TAHOE_FORECAST_DAYS", DEFAULT_FORECAST_DAYS)),
                        help=f"Days of expected meetings to derive from agency schedules (0 disables; default {DEFAULT_FORECAST_DAYS} or TAHOE_FORECAST_DAYS)")
    learn = sub.add_parser("learn-schedules", help="Infer regular meeting patterns from stored meetings; dry run unless --write")
    learn.add_argument("--agency", help="Only this followed agency ID")
    mode = learn.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Report only (default)")
    mode.add_argument("--write", action="store_true", help="Store proposals in the registry; never replaces source: configured")
    learn.add_argument("--registry", type=Path, help="Agency registry to update (default: packages/domain/agencies.json)")
    learn.add_argument("--json", action="store_true", help="Print the full machine-readable report")
    quarantine = sub.add_parser("quarantine-meeting", help="Hide a misattributed record while preserving source history")
    quarantine.add_argument("meeting_id")
    quarantine.add_argument("--reason", required=True)
    sub.add_parser("status")
    classify = sub.add_parser("reclassify", help="Refresh generated topic tags without changing source history or the operator's overrides")
    classify.add_argument("--dry-run", action="store_true")
    correction = sub.add_parser("correct")
    correction.add_argument("item_id")
    correction.add_argument("--text", required=True)
    correction.add_argument("--reason", required=True)
    correction.add_argument("--author", required=True)
    suppress = sub.add_parser("suppress")
    suppress.add_argument("item_id")
    suppress.add_argument("--off", action="store_true", help="Remove suppression")
    tag = sub.add_parser("tag")
    tag.add_argument("item_id")
    tag.add_argument("topic_id")
    tag.add_argument("--exclude", action="store_true")
    tag.add_argument("--author", required=True)
    pdf = sub.add_parser("refresh-document", help="Fetch an official PDF for an existing meeting")
    pdf.add_argument("meeting_id")
    pdf.add_argument("source_document_id")
    pdf.add_argument("url")
    pdf.add_argument("--title", default="Official PDF")
    setd = sub.add_parser("set-duration", help="Record an observed meeting length (overrides the estimate)")
    setd.add_argument("meeting_id")
    setd.add_argument("--minutes", type=int, required=True)
    setd.add_argument("--source", required=True, choices=["minutes", "recording", "manual", "published"])
    detect = sub.add_parser("detect-durations", help="Find called-to-order/adjourned times in stored minutes text (dry run unless --apply)")
    detect.add_argument("--apply", action="store_true")
    detect.add_argument("--json", action="store_true")
    report = sub.add_parser("duration-report", help="Observed durations vs. the model's estimate, per agency")
    report.add_argument("--json", action="store_true")
    discover = sub.add_parser("discover", help="List bounded candidates from official meeting indexes")
    discover.add_argument("agency", choices=list(followed_agencies()))
    discover.add_argument("--limit", type=int, default=5)
    discover.add_argument("--out", required=True)
    reparse = sub.add_parser("reparse", help="Re-run agenda parsers over stored document text (no source fetch)")
    reparse.add_argument("--agency", help="Only this agency ID")
    reparse.add_argument("--meeting", help="Only this meeting ID")
    reparse.add_argument("--force", action="store_true", help="Also reparse meetings that already have items")
    reparse.add_argument("--dry-run", action="store_true")
    capture = sub.add_parser("capture", help="Fetch PDFs for discovered candidates; agenda items need review")
    capture.add_argument("path")
    capture.add_argument("--max-meetings", type=int, default=5)
    capture.add_argument("--max-documents", type=int, default=2)
    ai = sub.add_parser("classify-topics", help="Tag new or changed agenda items with Claude (Haiku, then Sonnet); rule tags remain the fallback")
    ai.add_argument("--agency", help="Only this agency ID")
    ai.add_argument("--max-meetings", type=int, help="Classify at most this many pending meetings")
    ai.add_argument("--force", action="store_true", help="Reclassify every meeting, even unchanged ones")
    ai.add_argument("--dry-run", action="store_true", help="Report pending meetings and estimated cost; no API calls")
    ai.add_argument("--max-cost", type=float, default=ai_topics.DEFAULT_MAX_RUN_COST, help="USD limit for this run")
    ai.add_argument("--monthly-budget", type=float, default=ai_topics.DEFAULT_MONTHLY_BUDGET,
                    help="USD; refuse to run once month-to-date AI spend reaches it")
    part = sub.add_parser("extract-participation", help="Read location, remote access, and public comment details from agendas with Claude")
    part.add_argument("--agency", help="Only this agency ID")
    part.add_argument("--meeting", help="Only this meeting ID")
    part.add_argument("--max-meetings", type=int, help="Extract at most this many pending meetings")
    part.add_argument("--force", action="store_true", help="Re-extract meetings whose agendas have not changed")
    part.add_argument("--dry-run", action="store_true", help="Report pending meetings and estimated cost; no API calls")
    part.add_argument("--max-cost", type=float, default=ai_topics.DEFAULT_MAX_RUN_COST, help="USD limit for this run")
    part.add_argument("--monthly-budget", type=float, default=ai_topics.DEFAULT_MONTHLY_BUDGET,
                      help="USD; refuse to run once month-to-date AI spend reaches it")
    reports = sub.add_parser("map-staff-reports", help="Link agenda items to their own staff reports for every agency with a "
                             "mapping rule (staff_report_rules/); downloads PDFs temporarily")
    reports.add_argument("--agency", choices=staff_reports.SUPPORTED, metavar="AGENCY",
                         help="Only this agency ID (any agency with a staff report rule)")
    reports.add_argument("--meeting", help="Only this meeting ID")
    reports.add_argument("--max-meetings", type=int, help="Scan at most this many pending meetings, nearest to today first")
    reports.add_argument("--force", action="store_true", help="Rescan meetings whose items and documents have not changed")
    reports.add_argument("--dry-run", action="store_true", help="List pending meetings; no downloads or writes")
    reports.add_argument("--delay", type=float, default=1.0, help="Seconds between downloads")
    summaries = sub.add_parser("summarize-items", help="Summarize items from their staff reports with Claude (Haiku, then Sonnet); the agenda excerpt remains the fallback")
    summaries.add_argument("--agency", help="Only this agency ID")
    summaries.add_argument("--meeting", help="Only this meeting ID")
    summaries.add_argument("--max-items", type=int, help="Summarize at most this many pending items")
    summaries.add_argument("--force", action="store_true", help="Summarize again even when nothing the model sees has changed")
    summaries.add_argument("--dry-run", action="store_true", help="Report pending items and estimated cost; no API calls")
    summaries.add_argument("--max-cost", type=float, default=item_summaries.DEFAULT_MAX_RUN_COST, help="USD limit for this run")
    summaries.add_argument("--monthly-budget", type=float, default=ai_topics.DEFAULT_MONTHLY_BUDGET,
                           help="USD; refuse to run once month-to-date AI spend reaches it")
    scheduled_run = sub.add_parser("run-scheduled", help="Full scheduled pipeline: seed, discover, capture, classify, export, validate, publish")
    scheduled_run.add_argument("--root", default=os.getenv("TAHOE_SOURCE_ROOT", "."),
                               help="Directory holding config/ and tests/fixtures/ (default: TAHOE_SOURCE_ROOT or the working directory)")
    scheduled_run.add_argument("--discovery-limit", type=int, default=scheduled.INDEX_LIMIT,
                               help="Rows read from each agency index before the window is applied (1..20)")
    scheduled_run.add_argument("--past-days", type=int, default=scheduled.PAST_DAYS, help="Capture discovered meetings this many days back")
    scheduled_run.add_argument("--future-days", type=int, default=scheduled.FUTURE_DAYS, help="Capture discovered meetings this many days ahead")
    scheduled_run.add_argument("--max-documents", type=int, default=2, help="Documents fetched per meeting (1..10)")
    scheduled_run.add_argument("--delay", type=float, default=1.0, help="Seconds between source downloads")
    scheduled_run.add_argument("--forecast-days", type=int, default=int(os.getenv("TAHOE_FORECAST_DAYS", DEFAULT_FORECAST_DAYS)))
    scheduled_run.add_argument("--min-meetings", type=int, default=1, help="Refuse to publish an export with fewer meetings")
    scheduled_run.add_argument("--max-cost", type=float, default=ai_topics.DEFAULT_MAX_RUN_COST, help="USD limit for Claude classification this run")
    scheduled_run.add_argument("--monthly-budget", type=float, default=ai_topics.DEFAULT_MONTHLY_BUDGET, help="USD month-to-date AI budget")
    scheduled_run.add_argument("--summary-max-cost", type=float, default=item_summaries.DEFAULT_MAX_RUN_COST,
                               help="USD limit for staff report summaries this run")
    scheduled_run.add_argument("--staff-report-meetings", type=int, default=staff_reports.DEFAULT_MAX_MEETINGS,
                               help="Meetings scanned for staff reports this run (0 disables)")
    scheduled_run.add_argument("--out", help="Also write the export JSON to this path")
    scheduled_run.add_argument("--skip-discovery", action="store_true", help="Use tracked config only; no live index checks")
    scheduled_run.add_argument("--no-publish", action="store_true", help="Validate but do not store the export or call the deploy hook")
    history = sub.add_parser("backfill", help="One-time history backfill: capture every meeting dated in a range, then publish")
    history.add_argument("--since", required=True, type=_iso_date, help="First meeting date to capture (YYYY-MM-DD)")
    history.add_argument("--until", type=_iso_date, help=f"Last meeting date (default: {backfill.DEFAULT_FUTURE_DAYS} days ahead)")
    history.add_argument("--agency", action="append", default=[], help="Only this followed agency ID (repeatable)")
    history.add_argument("--root", default=os.getenv("TAHOE_SOURCE_ROOT", "."), help="Directory holding config/")
    history.add_argument("--max-documents", type=int, default=2, help="Documents fetched per meeting (1..10)")
    history.add_argument("--delay", type=float, default=1.0, help="Seconds between source downloads (at least 1)")
    history.add_argument("--forecast-days", type=int, default=int(os.getenv("TAHOE_FORECAST_DAYS", DEFAULT_FORECAST_DAYS)))
    history.add_argument("--min-meetings", type=int, default=1)
    history.add_argument("--max-cost", type=float, default=ai_topics.DEFAULT_MAX_RUN_COST, help="USD limit for Claude classification this run")
    history.add_argument("--monthly-budget", type=float, default=ai_topics.DEFAULT_MONTHLY_BUDGET, help="USD month-to-date AI budget")
    history.add_argument("--lock-wait-minutes", type=float, default=backfill.DEFAULT_LOCK_WAIT_SECONDS / 60,
                         help="Wait this long for a running scheduled run to release the lock, then exit")
    history.add_argument("--out", help="Also write the export JSON to this path")
    history.add_argument("--dry-run", action="store_true", help="Discover and report only; no downloads, export, or publish")
    history.add_argument("--no-publish", action="store_true", help="Validate but do not store the export or call the deploy hook")
    links = sub.add_parser("meeting-records", help="Match recordings and minutes (YouTube, Laserfiche, draft minutes in later agendas) to stored meetings")
    links.add_argument("--root", default=os.getenv("TAHOE_SOURCE_ROOT", "."), help="Directory holding config/")
    links.add_argument("--since", type=_iso_date, help="With YOUTUBE_API_KEY set, read YouTube uploads back to this date through the Data API (one-time backfill)")
    links.add_argument("--skip-feeds", action="store_true", help="Only draft minutes from stored agendas; no network requests")
    links.add_argument("--dry-run", action="store_true", help="Report matches without storing them")
    publish = sub.add_parser("publish-export", help="Validate an export file, store it in public_exports, and call the deploy hook if set")
    publish.add_argument("--file", required=True)
    publish.add_argument("--min-meetings", type=int, default=1)
    publish.add_argument("--no-hook", action="store_true", help="Do not call VERCEL_DEPLOY_HOOK_URL")
    evaluate = sub.add_parser("evaluate-topics", help="Compare rule tags with a Claude classifier; read-only, dry run without ANTHROPIC_API_KEY")
    evaluate_topics.add_arguments(evaluate)
    return p


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "agencies":
        print(json.dumps(list(followed_agencies().values()), indent=2))
        return 0
    if args.command == "ingest-one-offs":
        bundle = load_one_offs(args.path)
        if args.dry_run:
            print(json.dumps(bundle, indent=2))
            return 0
    if args.command == "discover":
        if not followed_agencies()[args.agency].get("discovery"):
            raise SystemExit(f"{args.agency}: no discovery source yet; use ingest-one-offs or ingest-manifest")
        candidates = discover_agency(args.agency, followed_agencies()[args.agency], limit=args.limit)
        dest = Path(args.out)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({"captured_at": datetime.now(timezone.utc).isoformat(), "candidates": candidates}, indent=2) + "\n")
        print(f"Discovered {len(candidates)} candidates in {dest}")
        return 0
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")
    with connect(args.database_url) as conn:
        if args.command == "evaluate-topics":
            conn.read_only = True  # The evaluation never writes source or tag records.
            evaluate_topics.run(conn, args)
            return 0
        if args.command == "classify-topics":
            print(json.dumps(ai_topics.classify_topics(
                conn, agency=args.agency, max_meetings=args.max_meetings, force=args.force, dry_run=args.dry_run,
                max_cost=args.max_cost, monthly_budget=args.monthly_budget), indent=2))
            return 0
        if args.command == "extract-participation":
            print(json.dumps(participation.extract_participation(
                conn, agency=args.agency, meeting=args.meeting, max_meetings=args.max_meetings, force=args.force,
                dry_run=args.dry_run, max_cost=args.max_cost, monthly_budget=args.monthly_budget), indent=2))
            return 0
        if args.command == "map-staff-reports":
            init_db(conn)
            print(json.dumps(staff_reports.map_staff_reports(
                conn, agency=args.agency, meeting=args.meeting, max_meetings=args.max_meetings, force=args.force,
                dry_run=args.dry_run, delay=args.delay), indent=2))
            return 0
        if args.command == "summarize-items":
            init_db(conn)
            print(json.dumps(item_summaries.summarize_items(
                conn, agency=args.agency, meeting=args.meeting, max_items=args.max_items, force=args.force,
                dry_run=args.dry_run, max_cost=args.max_cost, monthly_budget=args.monthly_budget), indent=2))
            return 0
        if args.command == "run-scheduled":
            return _run_scheduled(conn, args)
        if args.command == "publish-export":
            return _publish_export(conn, args)
        if args.command == "backfill":
            return _backfill(conn, args)
        if args.command == "meeting-records":
            return _meeting_records(conn, args)
        if args.command == "init-db":
            init_db(conn)
            print("PostgreSQL schema ready")
        elif args.command == "ingest-comment-periods":
            init_db(conn)
            result = ingest_comment_periods(conn, json.loads(Path(args.path).read_text()))
            print(json.dumps(result, indent=2))
            return 1 if result["failed"] else 0
        elif args.command == "ingest-one-offs":
            init_db(conn)
            result = ingest_bundle(conn, bundle, mode="fixture")
            print(json.dumps(result, indent=2))
            return 1 if result["failed"] else 0
        elif args.command in {"ingest-fixtures", "ingest-manifest"}:
            bundle = json.loads(Path(args.path).read_text())
            result = ingest_bundle(conn, bundle, mode="fixture" if args.command == "ingest-fixtures" else "live")
            print(json.dumps(result, indent=2))
            return 1 if result["failed"] else 0
        elif args.command == "learn-schedules":
            return _learn_schedules(conn, args)
        elif args.command == "export":
            if not 0 <= args.forecast_days <= 366:
                raise SystemExit("--forecast-days must be between 0 and 366")
            output = export_public(conn, mode=args.mode, forecast_days=args.forecast_days)
            dest = Path(args.out)
            dest.parent.mkdir(parents=True, exist_ok=True)
            temporary = dest.with_name(dest.name + ".tmp")
            temporary.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
            temporary.replace(dest)
            expected = sum(1 for m in output["meetings"] if m["kind"] == "forecast")
            print(f"Exported {len(output['meetings']) - expected} meetings and {expected} expected meetings "
                  f"({args.forecast_days}-day forecast) to {dest}")
        elif args.command == "quarantine-meeting":
            if not args.reason.strip():
                raise SystemExit("A quarantine reason is required")
            row = conn.execute("UPDATE meetings SET quarantine_reason=%s WHERE id=%s RETURNING id", (args.reason, args.meeting_id)).fetchone()
            if not row:
                raise SystemExit("Unknown meeting")
            print(f"Quarantined {row['id']}; source history retained. Run refresh.")
        elif args.command == "status":
            result = {"agencies": list(conn.execute("SELECT id,status,error,last_checked_at FROM agencies ORDER BY id")),
                      "recentRuns": list(conn.execute("SELECT id,agency_id,meeting_id,stage,status,attempts,error,started_at,finished_at FROM processing_runs ORDER BY id DESC LIMIT 20")),
                      "summaryFailures": list(conn.execute("SELECT item_id,error FROM summaries WHERE status='validation_failed' ORDER BY item_id LIMIT 20"))}
            print(json.dumps(result, indent=2, default=str))
        elif args.command == "set-duration":
            try:
                set_duration(conn, args.meeting_id, args.minutes, args.source)
            except ValueError as exc:
                raise SystemExit(str(exc))
            print(f"Recorded {args.minutes} minutes ({args.source}) for {args.meeting_id}; run refresh to export")
        elif args.command == "detect-durations":
            result = detect_from_minutes(conn, apply=args.apply)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Scanned {result['meetingsScanned']} meetings with minutes documents; detected {result['detected']}; "
                      f"{'applied ' + str(result['applied']) if args.apply else 'dry run, nothing written'}")
                for r in result["results"]:
                    print(f"  {r['meeting']}: {r['minutes'] or r['reason']}")
        elif args.command == "duration-report":
            report = duration_report(conn)
            print(json.dumps(report, indent=2) if args.json else format_report(report))
        elif args.command == "reparse":
            result = reparse_meetings(conn, agency=args.agency, meeting=args.meeting, force=args.force, dry_run=args.dry_run)
            print(json.dumps(result, indent=2))
            return 1 if result["failed"] and args.meeting else 0
        elif args.command == "reclassify":
            print(json.dumps(reclassify_topics(conn, dry_run=args.dry_run), indent=2))
        elif args.command == "correct":
            edit_item(conn, args.item_id, correction=(args.text, args.reason, args.author))
        elif args.command == "suppress":
            edit_item(conn, args.item_id, suppressed=not args.off)
        elif args.command == "tag":
            edit_item(conn, args.item_id, topic=(args.topic_id, not args.exclude, args.author))
        elif args.command == "refresh-document":
            meeting = conn.execute("SELECT * FROM meetings WHERE id=%s", (args.meeting_id,)).fetchone()
            if not meeting:
                raise SystemExit(f"unknown meeting {args.meeting_id}")
            fetched = download_extract(args.url)
            doc = {"id": args.source_document_id, "title": args.title, "url": args.url,
                   "sha256": fetched["sha256"], "fetched_at": datetime.now(timezone.utc).isoformat(),
                   "page_count": fetched["page_count"], "media_type": fetched.get("media_type", "application/pdf"), "warnings": fetched["warnings"]}
            bundle = {"meetings": [{"agency": meeting["agency_id"], "id": meeting["source_id"], "title": meeting["title"],
                "starts_at": meeting["starts_at"].isoformat() if meeting["starts_at"] else None,
                "date": meeting["local_date"].isoformat() if meeting["local_date"] else None,
                "status": meeting["status"],
                "source_meeting_url": meeting["official_url"], "timezone": meeting["timezone"],
                "location": meeting["location"], "participation": meeting["participation"],
                "documents": [doc], "pages": [{"document_id": doc["id"], **page} for page in fetched["pages"]],
                "agenda_items": [], "items_complete": False}]}
            result = ingest_bundle(conn, bundle, mode="live")
            print(json.dumps(result, indent=2))
            return 1 if result["failed"] else 0
        elif args.command == "capture":
            if not 1 <= args.max_meetings <= 20 or not 1 <= args.max_documents <= 10:
                raise SystemExit("capture limits must be 1..20 meetings and 1..10 documents")
            candidates = json.loads(Path(args.path).read_text())["candidates"][:args.max_meetings]
            result = capture_candidates(conn, candidates, max_documents=args.max_documents,
                                        fetch=lambda url: download_extract(url))
            print(json.dumps(result, indent=2))
            return 1 if result["failed"] or result["captureFailures"] else 0
    return 0


_source_identity = source_identity  # Kept for callers of the former CLI helper.


def _run_scheduled(conn, args) -> int:
    if not 1 <= args.discovery_limit <= 20 or not 1 <= args.max_documents <= 10 or not 0 <= args.forecast_days <= 366:
        raise SystemExit("run-scheduled limits: --discovery-limit 1..20, --max-documents 1..10, --forecast-days 0..366")
    options = scheduled.Options(
        root=args.root, discovery_limit=args.discovery_limit, past_days=args.past_days, future_days=args.future_days,
        max_documents=args.max_documents, delay=args.delay, forecast_days=args.forecast_days,
        min_meetings=args.min_meetings, max_cost=args.max_cost, monthly_budget=args.monthly_budget,
        summary_max_cost=args.summary_max_cost, staff_report_meetings=args.staff_report_meetings,
        out=args.out, skip_discovery=args.skip_discovery, publish=not args.no_publish)
    summary = scheduled.run_scheduled(conn, options, fetch=lambda url: download_extract(url))
    summary["alerts"] = alerts.alert_on_summary(summary)
    print(json.dumps(summary, default=str), flush=True)
    return 1 if summary["status"] == "failed" else 0


def _iso_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _backfill(conn, args) -> int:
    if not 1 <= args.max_documents <= 10 or args.delay < 1 or not 0 <= args.forecast_days <= 366 or args.lock_wait_minutes < 0:
        raise SystemExit("backfill limits: --max-documents 1..10, --delay >= 1, --forecast-days 0..366, --lock-wait-minutes >= 0")
    try:
        options = backfill.Options(
            since=args.since, until=args.until, agencies=args.agency, root=args.root, max_documents=args.max_documents,
            delay=args.delay, forecast_days=args.forecast_days, min_meetings=args.min_meetings, max_cost=args.max_cost,
            monthly_budget=args.monthly_budget, out=args.out, publish=not args.no_publish, dry_run=args.dry_run,
            lock_wait_seconds=args.lock_wait_minutes * 60)
        summary = backfill.backfill(conn, options, fetch=lambda url: download_extract(url))
    except ValueError as exc:
        raise SystemExit(str(exc))
    summary["alerts"] = alerts.alert_on_summary(summary)
    print(json.dumps(summary, default=str), flush=True)
    return 1 if summary["status"] == "failed" else 0


def _meeting_records(conn, args) -> int:
    """Run the scheduled run's meeting-records and draft-minutes steps once; the next export publishes them."""
    from . import records
    from .draft_minutes import link_draft_minutes
    init_db(conn)
    today = datetime.now(scheduled.PACIFIC).date()
    report = {}
    if not args.skip_feeds:
        key = records.youtube_api_key()
        if args.since and not key:
            raise SystemExit("--since reads the YouTube Data API: set YOUTUBE_API_KEY")
        report["feeds"] = records.update_meeting_records(conn, args.root, today=today, api_key=key if args.since else None,
                                                         since=args.since, dry_run=args.dry_run)
    report["draftMinutes"] = link_draft_minutes(conn, since=scheduled.COVERAGE_START, dry_run=args.dry_run)
    if args.dry_run:
        conn.rollback()
    else:
        conn.commit()
    print(json.dumps(report, indent=2, default=str))
    return 1 if report.get("feeds", {}).get("errors") else 0


def _publish_export(conn, args) -> int:
    run = scheduled.Run(conn)
    run.step("init-db", lambda: init_db(conn), fatal=True)
    payload = json.loads(Path(args.file).read_text())
    hook = None if args.no_hook else os.getenv("VERCEL_DEPLOY_HOOK_URL")
    published = scheduled.publish(conn, payload, run, min_meetings=args.min_meetings, hook_url=hook) if not run.fatal else {}
    status = "failed" if run.fatal else "ok"
    print(json.dumps({"message": f"publish-export {status}", "status": status, "steps": run.steps,
                      "exportId": published.get("exportId"), "failures": run.failures}, default=str), flush=True)
    return 1 if run.fatal else 0


def _learn_schedules(conn, args) -> int:
    followed = followed_agencies()
    if args.agency and args.agency not in followed:
        raise SystemExit(f"{args.agency}: not a followed agency")
    registry_path = args.registry or agency_catalog_path()
    if args.write and not args.registry and registry_path.parent.name == "tahoe_ingest":
        raise SystemExit("This install uses a bundled registry; pass --registry PATH to the checkout's agencies.json")
    registry = json.loads(registry_path.read_text())
    rows: dict[str, list[dict]] = {}
    for row in conn.execute("""SELECT id,agency_id,source_id,title,status,starts_at,local_date,timezone
            FROM meetings WHERE quarantine_reason IS NULL ORDER BY agency_id,local_date NULLS LAST,starts_at NULLS LAST,id"""):
        if row["agency_id"] in followed and (not args.agency or row["agency_id"] == args.agency):
            rows.setdefault(row["agency_id"], []).append(row)
    for entry in registry:
        if entry["id"] in followed and entry.get("schedule") and (not args.agency or entry["id"] == args.agency):
            rows.setdefault(entry["id"], [])
    reports = learn_schedules_report(rows, registry)
    written = write_schedules(registry_path, reports) if args.write else []
    if args.json:
        print(json.dumps({"dryRun": not args.write, "registry": str(registry_path), "written": written,
                          "agencies": reports}, indent=2, default=str))
        return 0
    labels = {"keep configured": "configured (kept)", "unchanged": "learned (unchanged)",
              "write learned": "written" if args.write else "would write",
              "keep existing learned": "insufficient; kept existing", "none": "insufficient data"}
    print(f"{'Agency':<28} {'Regular':>7} {'Matched':>7} {'Rate':>5}  {'Result':<28} Pattern")
    for report in sorted(reports, key=lambda r: (r["schedule"] is None and r["action"] != "keep configured", r["agency"])):
        pattern = report["pattern"] or "-"
        if report.get("reason"):
            pattern += f" ({report['reason']})"
        if report["action"] == "keep configured":
            pattern = f"{pattern}; configured: {describe(report['existing']['rules'])}"
        print(f"{report['agency']:<28} {report['considered']:>7} {report['matched']:>7} {report['matchRate']:>5.0%}  "
              f"{labels[report['action']]:<28} {pattern}")
        if report["excluded"]:
            print(f"{'':<4}not evidence: {', '.join(f'{n} {reason}' for reason, n in report['excluded'].items())}")
        for exception in report["exceptions"]:
            print(f"{'':<4}exception {exception['date']} {exception['time']} {exception['reason']}: {exception['title']}")
        if report["unrecordedDates"]:
            print(f"{'':<4}pattern dates with no stored record: {', '.join(report['unrecordedDates'])}")
    if args.write:
        print(f"Updated {len(written)} learned schedules in {registry_path}: {', '.join(written) or 'none'}. "
              "Run `refresh` to export forecasts.")
    else:
        print("Dry run: no files changed. Use --write to store proposals (configured schedules are never replaced).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
