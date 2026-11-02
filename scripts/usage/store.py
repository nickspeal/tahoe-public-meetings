"""Local usage store and the catalog of tracked metrics.

The store is a SQLite file in the gitignored private/usage directory. Collectors write
one value per metric, per breakdown, per UTC day; reports derive every window from that.
"""
from pathlib import Path
import datetime as dt
import os
import re
import sqlite3
import tomllib

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("TAHOE_USAGE_DIR", ROOT / "private/usage"))
CONFIG_PATH = DATA_DIR / "config.toml"


def ai_monthly_budget():
    """The ingestion job's own monthly AI spend guard, so the dashboard quota cannot drift from it."""
    source = (ROOT / "workers/ingestion/tahoe_ingest/ai_topics.py").read_text()
    match = re.search(r"^DEFAULT_MONTHLY_BUDGET\s*=\s*([0-9.]+)", source, re.M)
    return float(match.group(1)) if match else None


# window: "month" quotas reset on the 1st (UTC); "rolling30" quotas are judged on the last 30 days.
# kind: "sum" metrics add up across days; "gauge" metrics are point-in-time snapshots.
# Free-tier figures are the ones recorded in docs/operations/deployment.md and
# docs/architecture/free-tier-sizing.md; override them in private/usage/config.toml.
METRICS = {
    ("prisma", "operations"): dict(label="Prisma Postgres operations", unit="count", quota=200_000, window="month",
                                   primary=True, note="Free plan, shared by every database in the Prisma workspace. "
                                   "Queries are blocked once the quota is used, until the 1st (UTC)."),
    ("prisma", "storage_bytes"): dict(label="Prisma Postgres storage", unit="bytes", kind="gauge", quota=2e9,
                                      primary=True, note="As reported by Prisma; Free plan limit 2 GB (Prisma Console figure; see docs/operations/deployment.md)."),
    ("postgres", "database_bytes"): dict(label="Production database size (pg_database_size)", unit="bytes",
                                         kind="gauge"),
    ("ai", "cost_usd"): dict(label="AI cost (estimated)", unit="usd", quota=ai_monthly_budget(), window="month",
                             primary=True, note="From the ai_usage table, priced by the ingestion code. The quota is the "
                             "job's own monthly budget guard (DEFAULT_MONTHLY_BUDGET)."),
    ("ai", "calls"): dict(label="AI calls", unit="count"),
    ("ai", "failed_calls"): dict(label="AI calls that failed", unit="count"),
    ("ai", "input_tokens"): dict(label="AI uncached input tokens", unit="count"),
    ("ai", "output_tokens"): dict(label="AI output tokens", unit="count"),
    ("ai", "cache_write_tokens"): dict(label="AI cache-write tokens", unit="count"),
    ("ai", "cache_read_tokens"): dict(label="AI cache-read tokens", unit="count"),
    ("cloudrun", "vcpu_seconds"): dict(label="Cloud Run vCPU-seconds", unit="count", quota=240_000, window="month",
                                       primary=True, note="Monthly free allowance, shared across the billing account. "
                                       "Computed from execution duration x configured vCPU."),
    ("cloudrun", "gib_seconds"): dict(label="Cloud Run GiB-seconds", unit="count", quota=450_000, window="month",
                                      primary=True, note="Monthly free allowance, shared across the billing account."),
    ("cloudrun", "executions"): dict(label="Cloud Run executions", unit="count"),
    ("cloudrun", "failed_executions"): dict(label="Cloud Run failed executions", unit="count"),
    ("cloudrun", "runtime_minutes"): dict(label="Cloud Run runtime minutes", unit="count"),
    ("github", "minutes"): dict(label="GitHub Actions minutes", unit="count", primary=True,
                                note="Standard runners are free and unmetered on public repositories. "
                                "Minutes are each run's wall time rounded up."),
    ("github", "runs"): dict(label="GitHub Actions runs", unit="count"),
    ("github", "failed_runs"): dict(label="GitHub Actions failed runs", unit="count"),
    ("vercel", "edge_requests"): dict(label="Vercel edge requests", unit="count", quota=1_000_000, window="rolling30",
                                      primary=True, note="Hobby allowance, shared by every project in the team."),
    ("vercel", "fast_data_transfer_bytes"): dict(label="Vercel fast data transfer", unit="bytes", quota=100e9,
                                                 window="rolling30", primary=True, note="Hobby allowance (100 GB)."),
    ("vercel", "incoming_bytes"): dict(label="Vercel incoming transfer", unit="bytes"),
    ("vercel", "function_invocations"): dict(label="Vercel function invocations", unit="count", quota=1_000_000,
                                             window="rolling30"),
    ("vercel", "function_gb_hours"): dict(label="Vercel function memory GB-hours", unit="count", quota=360,
                                          window="rolling30"),
    ("vercel", "build_minutes"): dict(label="Vercel build minutes", unit="count", primary=True),
    ("vercel", "builds"): dict(label="Vercel builds", unit="count"),
    ("vercel", "failed_builds"): dict(label="Vercel failed builds", unit="count"),
}
SERVICES = {"prisma": "Prisma Postgres", "postgres": "Production database", "ai": "AI processing",
            "cloudrun": "Google Cloud Run", "github": "GitHub Actions", "vercel": "Vercel"}
# Collector name -> what it reads, for the collection status table.
SOURCES = {"prisma": "Prisma Postgres usage (operations, storage)", "database": "Production database (AI usage, size)",
           "cloudrun": "Google Cloud Run executions", "github": "GitHub Actions runs", "vercel": "Vercel team usage"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily(service TEXT NOT NULL, metric TEXT NOT NULL, dims TEXT NOT NULL DEFAULT '',
  day TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY(service, metric, dims, day));
CREATE TABLE IF NOT EXISTS snapshots(service TEXT NOT NULL, metric TEXT NOT NULL, dims TEXT NOT NULL DEFAULT '',
  taken_at TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY(service, metric, dims, taken_at));
-- Raw executions, kept because Cloud Run does not retain execution history forever.
CREATE TABLE IF NOT EXISTS cloud_run_executions(name TEXT PRIMARY KEY, job TEXT NOT NULL, started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL, succeeded INTEGER NOT NULL, task_count INTEGER NOT NULL, vcpu REAL NOT NULL,
  gib REAL NOT NULL);
CREATE TABLE IF NOT EXISTS collections(run_at TEXT NOT NULL, service TEXT NOT NULL, ok INTEGER NOT NULL,
  message TEXT NOT NULL);
"""


def now():
    return dt.datetime.now(dt.timezone.utc)


def config():
    raw = tomllib.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    for key, override in raw.get("quotas", {}).items():
        service, metric = key.split(".", 1)
        METRICS[(service, metric)].update(override)
    return raw


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "usage.sqlite")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def dims(**parts):
    return "|".join(f"{k}={v}" for k, v in parts.items())


def replace_daily(conn, service, start, end, rows, metrics=None):
    """Replace the service's daily rows for start..end (inclusive, YYYY-MM-DD) with rows.

    rows: iterable of (metric, dims, day, value). Days missing from rows become absent,
    which reports treat as zero, so a collector must only call this after a full fetch.
    """
    params = [service, start, end]
    where = "service=? AND day BETWEEN ? AND ?"
    if metrics:
        where += f" AND metric IN ({','.join('?' * len(metrics))})"
        params += list(metrics)
    conn.execute(f"DELETE FROM daily WHERE {where}", params)
    totals = {}
    for metric, dim, day, value in rows:
        totals[(metric, dim, day)] = totals.get((metric, dim, day), 0) + value
    conn.executemany("INSERT INTO daily(service, metric, dims, day, value) VALUES (?,?,?,?,?)",
                     [(service, m, d, day, v) for (m, d, day), v in totals.items()])


def snapshot(conn, service, metric, value, dim="", taken_at=None):
    taken_at = taken_at or now().isoformat(timespec="seconds")
    conn.execute("INSERT OR REPLACE INTO snapshots(service, metric, dims, taken_at, value) VALUES (?,?,?,?,?)",
                 (service, metric, dim, taken_at, value))


def log_collection(conn, service, ok, message):
    conn.execute("INSERT INTO collections(run_at, service, ok, message) VALUES (?,?,?,?)",
                 (now().isoformat(timespec="seconds"), service, int(ok), message))
