"""Operator CLI. All writes use a local PostgreSQL DATABASE_URL."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .core import connect, edit_item, export_public, ingest_bundle, init_db, reclassify_topics, record_failure
from .agenda import parse_agenda
from .reparse import reparse_meetings
from .discovery import discover_slt, discover_truckee, discover_ttd
from .source_discovery import discover_configured
from .source import download_extract
from .comment_periods import ingest_comment_periods
from .catalog import agency_catalog_path, followed_agencies
from .one_offs import load_one_offs
from .duration_ops import detect_from_minutes, duration_report, format_report, set_duration
from . import ai_topics, evaluate_topics
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
    discover.add_argument("--year", type=int, default=datetime.now().year)
    discover.add_argument("--month", type=int, default=datetime.now().month)
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
        adapter = followed_agencies()[args.agency].get("discoveryAdapter")
        if not adapter:
            raise SystemExit(f"{args.agency}: no discovery adapter yet; use ingest-one-offs or ingest-manifest")
        candidates = (discover_configured(args.agency, followed_agencies()[args.agency]["discovery"], limit=args.limit) if adapter == "configured" else
                      discover_ttd(limit=args.limit) if adapter == "ttd" else
                      discover_slt(limit=args.limit) if adapter == "slt" else
                      discover_truckee(year=args.year, month=args.month, limit=args.limit))
        for candidate in candidates:
            candidate["agency"] = args.agency
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
            rows = []
            failures = []
            failure_records = []
            for candidate in candidates:
                candidate = dict(candidate)
                candidate["source_id"] = _source_identity(conn, candidate)
                docs = []
                pages = []
                html_items = None
                try:
                    document_errors = []
                    for document in candidate["documents"][:args.max_documents]:
                        try:
                            fetched = download_extract(document["url"])
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
                                     "page_count": fetched["page_count"], "media_type": fetched.get("media_type", "application/pdf"), "warnings": fetched["warnings"]})
                        pages.extend({"document_id": document["id"], **page} for page in fetched["pages"])
                    complete_document_set = not document_errors and len(candidate["documents"]) <= args.max_documents
                    from .agenda import ParseResult
                    parsed = ParseResult(html_items, True) if html_items else (parse_agenda(candidate["agency"], docs, pages) if docs and complete_document_set else None)
                    warnings = list(candidate.get("warnings", []))
                    if document_errors:
                        warnings.append("Some official documents could not be retrieved; the meeting notice is retained and document capture will need a retry.")
                    if parsed and parsed.complete:
                        warnings = [w for w in warnings if "agenda items require" not in w.lower()]
                    else:
                        warnings.append(parsed.warning if parsed else
                            "Document limit omitted source PDFs; agenda items require review." if docs else
                            "No agenda PDF is linked; agenda items require review.")
                    rows.append({"agency": candidate["agency"], "id": candidate["source_id"], "title": candidate["title"],
                                 "source_meeting_url": candidate["official_url"], "status": candidate.get("status", "unknown"),
                                 "date": candidate.get("date"), "start_time": candidate.get("start_time"),
                                 "timezone": "America/Los_Angeles", "location": candidate.get("location"),
                                 "warnings": warnings, "partial_logistics": True,
                                 "documents": docs, "documents_complete": bool(candidate.get("documents_complete")) and complete_document_set,
                                 "pages": pages, "agenda_items": parsed.items if parsed and parsed.complete else [],
                                 "items_complete": bool(parsed and parsed.complete)})
                except Exception as exc:
                    failures.append(f"{candidate['agency']}:{candidate['source_id']}: {exc}")
                    failure_records.append((candidate["agency"], candidate["source_id"], str(exc)))
            result = ingest_bundle(conn, {"meetings": rows}, mode="live")
            for agency_id, source_id, error in failure_records:
                record_failure(conn, agency_id, source_id, "capture", error)
            result["captureFailures"] = failures
            print(json.dumps(result, indent=2))
            return 1 if result["failed"] or failures else 0
    return 0


def _source_identity(conn, candidate):
    """Bridge a previously captured calendar ID to the same official agenda.

    Only a unique same-date notice can be bridged, and workshops/special/joint
    meetings must agree. The alias is persisted for subsequent fetches.
    """
    aid, sid = candidate['agency'], str(candidate['source_id'])
    direct = conn.execute("SELECT source_id FROM meetings WHERE agency_id=%s AND source_id=%s", (aid,sid)).fetchone()
    if direct:return sid
    alias = conn.execute("SELECT m.source_id FROM meeting_source_aliases a JOIN meetings m ON m.id=a.meeting_id WHERE a.agency_id=%s AND a.alias=%s",(aid,sid)).fetchone()
    if alias:return alias['source_id']
    rows=list(conn.execute("SELECT id,source_id,title,official_url FROM meetings WHERE agency_id=%s AND local_date=%s AND quarantine_reason IS NULL",(aid,candidate.get('date'))))
    exact=[r for r in rows if r['official_url']==candidate['official_url']]
    import re
    def qualifiers(title):
        return {word for word in ('workshop','special','joint') if re.search(r'\b'+word+r'\b',title,re.I)}
    matching=exact or [r for r in rows if qualifiers(r['title'])==qualifiers(candidate['title'])]
    if len(matching)==1:
        row=matching[0]
        conn.execute("INSERT INTO meeting_source_aliases(agency_id,alias,meeting_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",(aid,sid,row['id']))
        return row['source_id']
    return sid


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
