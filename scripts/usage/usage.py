#!/usr/bin/env python3
"""Collect this project's compute and quota usage, then write a local dashboard and summaries.

    python3 scripts/usage/usage.py                 # collect everything, write reports
    python3 scripts/usage/usage.py --only prisma,vercel
    python3 scripts/usage/usage.py --report-only   # rebuild reports from stored data
    python3 scripts/usage/usage.py --open          # ... and open the dashboard

Uses the operator's existing logins (npx prisma, gcloud, gh, vercel) and writes only to the
gitignored private/usage/ directory: usage.sqlite, dashboard.html, summary.md, summary.json.
Optional overrides live in private/usage/config.toml (see scripts/usage/README.md).
"""
import argparse
import datetime as dt
import json
import subprocess
import sys

import collect
import dashboard
import report
from store import DATA_DIR, config, connect, log_collection


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help=f"comma-separated collectors: {', '.join(collect.COLLECTORS)}")
    parser.add_argument("--report-only", action="store_true", help="skip collection")
    parser.add_argument("--open", action="store_true", help="open the dashboard afterwards")
    parser.add_argument("--json", action="store_true", help="print summary.json to stdout")
    args = parser.parse_args()
    cfg = config()
    start = dt.date.fromisoformat(str(cfg.get("start", "2026-10-01")))
    conn = connect()
    failed = []
    if not args.report_only:
        names = args.only.split(",") if args.only else list(collect.COLLECTORS)
        for name in names:
            if name not in collect.COLLECTORS:
                parser.error(f"unknown collector {name!r}")
            print(f"collecting {name}...", file=sys.stderr, flush=True)
            try:
                message = collect.COLLECTORS[name](conn, cfg, start)
                log_collection(conn, name, True, message)
                print(f"  ok: {message}", file=sys.stderr)
            except collect.CollectError as error:
                conn.rollback()
                log_collection(conn, name, False, str(error))
                failed.append(name)
                print(f"  FAILED: {error}", file=sys.stderr)
            conn.commit()
    summary = report.summarize(conn)
    (DATA_DIR / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    (DATA_DIR / "summary.md").write_text(report.markdown(summary))
    (DATA_DIR / "dashboard.html").write_text(dashboard.render(summary))
    if args.json:
        print(json.dumps(summary, indent=1))
    print(f"wrote {DATA_DIR / 'dashboard.html'} and summary.md/.json", file=sys.stderr)
    if args.open:
        subprocess.run(["open", str(DATA_DIR / "dashboard.html")])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
