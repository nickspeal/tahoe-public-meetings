"""Collectors: each reads one service's usage with the operator's existing CLI logins.

Every collector is read-only. Against the production database it sends two statements
per collection (the Prisma operations quota counts each one).
"""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import math
import os
import shutil
import subprocess
import urllib.error
import urllib.request

from store import ROOT, dims, now, replace_daily, snapshot

GCP_PROJECT = "tahoe-public-meetings"  # as in scripts/deploy/gcp-ingestion.sh
GCP_REGION = "us-west1"
DATABASE_SECRET = "tahoe-database-url"
LOCAL_URL = "postgresql://tahoe:tahoe-local@127.0.0.1:55432/tahoe"


class CollectError(Exception):
    pass


def run(args, timeout=120, env=None):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        raise CollectError(f"`{args[0]}` is not installed or not on PATH.") from None
    except subprocess.TimeoutExpired:
        raise CollectError(f"`{' '.join(args[:3])}` timed out after {timeout}s.") from None
    if result.returncode:
        raise CollectError(f"`{' '.join(args[:3])}` failed: {(result.stderr or result.stdout).strip()[-400:]}")
    return result.stdout


def days(start, end):
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


# --- Prisma Postgres -------------------------------------------------------------------

def prisma(args):
    command = os.environ.get("TAHOE_USAGE_PRISMA", "npx -y prisma@latest").split()
    out = run(command + args + ["--json"], timeout=180)
    envelope = json.loads(out.strip().splitlines()[-1])["envelope"]
    if not envelope.get("ok"):
        error = envelope.get("error", {})
        hint = (" Sign in with `npx prisma auth login`, or set PRISMA_SERVICE_TOKEN to a workspace service token."
                if "AUTH" in str(error.get("code", "")).upper() else "")
        raise CollectError(f"prisma {' '.join(args[:2])}: {error.get('summary', error)}{hint}")
    return envelope["result"]


def collect_prisma(conn, cfg, start):
    """Daily operations for every database in the workspace (the quota is workspace-wide)."""
    projects = cfg.get("prisma", {}).get("projects") or [p["id"] for p in prisma(["project", "list"])["items"]]
    databases = []
    for project in projects:
        result = prisma(["postgres", "list", "--project", project])
        databases += [(project, db) for db in result["databases"]]
    today = now().date()
    stored = {r["day"] for r in conn.execute("SELECT DISTINCT day FROM daily WHERE service='prisma'")}
    # Refetch the last three days (a day's figure settles after it ends); fetch any missing day.
    wanted = [d for d in days(start, today) if d >= today - dt.timedelta(days=2) or d.isoformat() not in stored]

    def usage(job):
        (project, db), day = job
        created = parse_time(db["createdAt"]).date()
        if day < created:
            return db, day, None, None
        r = prisma(["postgres", "usage", db["id"], "--project", project, "--from", day.isoformat(),
                    "--to", day.isoformat()])
        return db, day, r["metrics"]["operations"]["used"], r["metrics"]["storage"]["used"]

    jobs = [(db, day) for db in databases for day in wanted]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(usage, jobs))
    if wanted:
        rows = [("operations", dims(db=db["name"]), day.isoformat(), ops) for db, day, ops, _ in results
                if ops is not None]
        # Replace day by day so days that were not refetched keep their stored values.
        for day in wanted:
            replace_daily(conn, "prisma", day.isoformat(), day.isoformat(), [r for r in rows if r[2] == day.isoformat()])
    for db, day, _, storage in results:
        if day == today and storage is not None:
            snapshot(conn, "prisma", "storage_bytes", storage * 1024 ** 3, dims(db=db["name"]))
    return f"{len(databases)} database(s), {len(wanted)} day(s) fetched"


# --- Production database: AI usage and size --------------------------------------------

AI_USAGE = """select (recorded_at at time zone 'UTC')::date::text as day, purpose, model, ok, count(*) as calls,
  sum(input_tokens) as input, sum(output_tokens) as output, sum(cache_creation_input_tokens) as cache_write,
  sum(cache_read_input_tokens) as cache_read, sum(cost_usd)::float8 as cost from ai_usage group by 1, 2, 3, 4"""
# Failed calls grouped by error, with numbers and quoted text masked so "sentence 2 ... page 3" variants group.
# A staff report summary's error carries the rejected reply after "\n\nreply: " (item_summaries.REPLY_MARK); only
# the part before it is grouped.
AI_ERRORS = """select purpose, regexp_replace(regexp_replace(left(regexp_replace(split_part(coalesce(error, 'unknown'), E'\\n\\nreply: ', 1), '\\s+', ' ', 'g'),
  300), '“[^”]*”', '“…”', 'g'), '[0-9]+', '#', 'g') as error, count(*) as calls, sum(cost_usd)::float8 as cost,
  max(recorded_at) as last_seen from ai_usage where not ok group by 1, 2"""
# One statement (one Prisma operation) for both.
AI_SQL = f"""select (select coalesce(json_agg(u), '[]') from ({AI_USAGE}) u) as usage,
  (select coalesce(json_agg(e), '[]') from ({AI_ERRORS}) e) as errors"""
SIZE_SQL = """select json_build_object('database', pg_database_size(current_database()), 'tables',
  (select json_object_agg(c.relname, pg_total_relation_size(c.oid)) from pg_class c
   join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relkind = 'r'))"""


def database_url():
    url = os.environ.get("TAHOE_USAGE_DATABASE_URL")
    if url:
        return url
    return run(["gcloud", "secrets", "versions", "access", "latest", "--secret", DATABASE_SECRET,
                "--project", GCP_PROJECT]).strip()


def query(url, sql, timeout=60):
    """One read-only statement through psql; returns parsed JSON rows. Never echoes the URL."""
    env = dict(os.environ, PGOPTIONS="-c default_transaction_read_only=on", PGCONNECT_TIMEOUT="10")
    wrapped = f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t"
    try:
        return json.loads(run(["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", url, "-c", wrapped], timeout, env))
    except CollectError as error:
        raise CollectError(str(error).replace(url, "<database url>")) from None


def ai_rows(rows, source):
    for r in rows:
        dim = dims(purpose=r["purpose"], model=r["model"], source=source)
        yield ("calls", dim, r["day"], r["calls"])
        if not r["ok"]:
            yield ("failed_calls", dim, r["day"], r["calls"])
        for metric, key in (("input_tokens", "input"), ("output_tokens", "output"),
                            ("cache_write_tokens", "cache_write"), ("cache_read_tokens", "cache_read"),
                            ("cost_usd", "cost")):
            yield (metric, dim, r["day"], r[key] or 0)


def collect_database(conn, cfg, start):
    """AI usage from the production database (written by the Cloud Run jobs) and the local one."""
    url, rows, errors = database_url(), [], []
    sources = [("cloud", url, 60)]
    if cfg.get("ai", {}).get("include_local", True):
        sources.append(("local", os.environ.get("TAHOE_USAGE_LOCAL_DATABASE_URL", LOCAL_URL), 15))
    notes = []
    for source, source_url, timeout in sources:
        try:
            result = query(source_url, AI_SQL, timeout)[0]
        except CollectError:
            if source == "cloud":
                raise
            notes.append("local database not reachable")  # local experiments are optional
            continue
        rows += list(ai_rows(result["usage"], source))
        errors += [(source, e["purpose"], e["error"], e["calls"], e["cost"] or 0, e["last_seen"]) for e in result["errors"]]
        notes.append(f"{len(result['usage'])} {source} AI groups")
    replace_daily(conn, "ai", "0000-00-00", "9999-99-99", rows)
    conn.execute("DELETE FROM ai_errors")
    conn.executemany("INSERT INTO ai_errors VALUES (?,?,?,?,?,?)", errors)
    sizes = query(url, SIZE_SQL)[0]["json_build_object"]
    snapshot(conn, "postgres", "database_bytes", sizes["database"])
    taken_at = now().isoformat(timespec="seconds")
    for table, size in (sizes["tables"] or {}).items():
        snapshot(conn, "postgres", "table_bytes", size, dims(table=table), taken_at)
    return ", ".join(notes)


# --- Google Cloud Run ------------------------------------------------------------------

def quantity(value, unit_scale):
    value = str(value)
    for suffix, scale in unit_scale.items():
        if value.endswith(suffix):
            return float(value[:-len(suffix)]) * scale
    return float(value)


CPU = {"m": 0.001}
MEMORY_GIB = {"Gi": 1, "Mi": 1 / 1024, "Ki": 1 / 1024 ** 2, "G": 1e9 / 1024 ** 3, "M": 1e6 / 1024 ** 3}


def collect_cloudrun(conn, cfg, start):
    gcp = cfg.get("gcp", {})
    project, region = gcp.get("project", GCP_PROJECT), gcp.get("region", GCP_REGION)
    executions = json.loads(run(["gcloud", "run", "jobs", "executions", "list", "--project", project,
                                 "--region", region, "--limit", "5000", "--format", "json"], timeout=180))
    kept = 0
    for e in executions:
        status = e.get("status", {})
        if not status.get("completionTime") or not status.get("startTime"):
            continue  # still running; picked up by the next collection
        limits = e["spec"]["template"]["spec"]["containers"][0].get("resources", {}).get("limits", {})
        completed = next((c for c in status.get("conditions", []) if c["type"] == "Completed"), {})
        conn.execute("INSERT OR REPLACE INTO cloud_run_executions VALUES (?,?,?,?,?,?,?,?,?)", (
            e["metadata"]["name"], e["metadata"]["labels"].get("run.googleapis.com/job", "?"), status["startTime"],
            status["completionTime"], int(not status.get("failedCount") and not status.get("cancelledCount")),
            int(e["spec"].get("taskCount", 1)), quantity(limits.get("cpu", "1"), CPU),
            quantity(limits.get("memory", "512Mi"), MEMORY_GIB), completed.get("message", "")))
        kept += 1
    rows = []
    for e in conn.execute("SELECT * FROM cloud_run_executions"):
        seconds = (parse_time(e["completed_at"]) - parse_time(e["started_at"])).total_seconds() * e["task_count"]
        day, dim = e["started_at"][:10], dims(job=e["job"])
        rows += [("executions", dim, day, 1), ("vcpu_seconds", dim, day, seconds * e["vcpu"]),
                 ("gib_seconds", dim, day, seconds * e["gib"]), ("runtime_minutes", dim, day, seconds / 60)]
        if not e["succeeded"]:
            rows.append(("failed_executions", dim, day, 1))
    replace_daily(conn, "cloudrun", "0000-00-00", "9999-99-99", rows)
    return f"{kept} completed execution(s) listed in {project}/{region}"


# --- GitHub Actions --------------------------------------------------------------------

def github_repo(cfg):
    repo = cfg.get("github", {}).get("repo")
    if repo:
        return repo
    url = run(["git", "-C", str(ROOT), "remote", "get-url", "origin"]).strip()
    return url.removesuffix(".git").split("github.com")[-1].lstrip(":/")


def collect_github(conn, cfg, start):
    repo = github_repo(cfg)
    jq = ".workflow_runs[] | {name, conclusion, status, run_started_at, updated_at, created_at, html_url}"
    out = run(["gh", "api", "--paginate", f"repos/{repo}/actions/runs?per_page=100&created=>={start.isoformat()}",
               "--jq", jq], timeout=180)
    runs = [json.loads(line) for line in out.splitlines() if line.strip()]
    rows, failures = [], []
    for r in runs:
        if r["status"] != "completed":
            continue
        day, dim = r["created_at"][:10], dims(workflow=r["name"])
        seconds = (parse_time(r["updated_at"]) - parse_time(r["run_started_at"] or r["created_at"])).total_seconds()
        rows += [("runs", dim, day, 1), ("minutes", dim, day, max(1, math.ceil(seconds / 60)))]
        if r["conclusion"] not in ("success", "skipped", "neutral"):
            rows.append(("failed_runs", dim, day, 1))
            failures.append((r["name"], r["conclusion"], r["created_at"], r["html_url"]))
    conn.execute("DELETE FROM github_failed_runs")
    conn.executemany("INSERT INTO github_failed_runs VALUES (?,?,?,?)", failures)
    replace_daily(conn, "github", start.isoformat(), "9999-99-99", rows)
    return f"{len(runs)} run(s) in {repo}"


# --- Vercel ----------------------------------------------------------------------------

VERCEL_AUTH = os.path.expanduser("~/Library/Application Support/com.vercel.cli/auth.json")


def dotenv(name):
    path = ROOT / ".env"
    for line in path.read_text().splitlines() if path.exists() else []:
        key, _, value = line.strip().partition("=")
        if key.strip() == name:
            return value.strip().strip("\"'")
    return None


def vercel_token():
    """The Vercel CLI's own login, refreshed if needed; else VERCEL_TOKEN or VERCEL_ADMIN_TOKEN (.env)."""
    fallback = os.environ.get("VERCEL_TOKEN") or os.environ.get("VERCEL_ADMIN_TOKEN") or dotenv("VERCEL_ADMIN_TOKEN")
    try:
        if not os.path.exists(VERCEL_AUTH):
            raise CollectError("No Vercel login found. Run `vercel login`, or set VERCEL_TOKEN.")
        auth = json.load(open(VERCEL_AUTH))
        if auth.get("expiresAt") and auth["expiresAt"] < now().timestamp() + 120:
            run(["vercel", "whoami"])  # refreshes the CLI's stored token
            auth = json.load(open(VERCEL_AUTH))
        return auth["token"]
    except (CollectError, KeyError, ValueError):
        if fallback:
            return fallback
        raise


def vercel_ids(cfg):
    v = cfg.get("vercel", {})
    linked = ROOT / ".vercel/project.json"
    if linked.exists():
        project = json.loads(linked.read_text())
        v = {"team_id": project["orgId"], "project_id": project["projectId"], "project_name": project["projectName"],
             **v}
    if not v.get("team_id"):
        raise CollectError("Unknown Vercel team. Run `vercel link` at the repository root, or set [vercel] team_id "
                           "in private/usage/config.toml.")
    return v["team_id"], (v.get("project_id"), v.get("project_name", "this project"))


def vercel_usage(token, team, kind, start, end):
    url = (f"https://api.vercel.com/v2/usage?teamId={team}&type={kind}"
           f"&from={start.isoformat()}T00:00:00.000Z&to={end.isoformat()}T23:59:59.999Z")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        raise CollectError(f"Vercel usage API ({kind}) returned {error.code}: {error.read()[:200].decode()}") from None
    if body.get("granularity") != "day":
        raise CollectError(f"Vercel returned {body.get('granularity')} granularity for {kind}; expected day.")
    return body["data"]


def split(value, breakdown, project):
    """Split a team total into this project and the rest using Vercel's per-project percentages."""
    project_id, name = project
    share = next((b["percent"] for b in breakdown or [] if b.get("id") == project_id), 0) / 100
    return [(dims(project=name), value * share), (dims(project="other projects"), value * (1 - share))]


def collect_vercel(conn, cfg, start):
    token, (team, project) = vercel_token(), vercel_ids(cfg)
    today, rows, window_start = now().date(), [], start
    while window_start <= today:
        window_end = min(window_start + dt.timedelta(days=29), today)
        for d in vercel_usage(token, team, "requests", window_start, window_end):
            day, b = d["date"][:10], d.get("breakdown", {})
            fn_calls = sum(d.get(f"function_invocation_{k}_count", 0) for k in ("successful", "error", "throttle", "timeout"))
            fn_gbh = sum(d.get(f"function_execution_{k}_gb_hours", 0) for k in ("successful", "error", "timeout"))
            for metric, value, key in (("edge_requests", d["request_hit_count"] + d["request_miss_count"], "requests"),
                                       ("fast_data_transfer_bytes", d["bandwidth_outgoing_bytes"], "bandwidth"),
                                       ("incoming_bytes", d["bandwidth_incoming_bytes"], "bandwidth"),
                                       ("function_invocations", fn_calls, None), ("function_gb_hours", fn_gbh, None)):
                parts = split(value, b.get(key), project) if key else [("", value)]
                rows += [(metric, dim, day, v) for dim, v in parts]
        for d in vercel_usage(token, team, "builds", window_start, window_end):
            day, b = d["date"][:10], d.get("breakdown", {})
            rows += [("build_minutes", dim, day, v)
                     for dim, v in split(d["build_build_seconds"] / 60, b.get("build_time"), project)]
            rows += [("builds", dim, day, v) for dim, v in
                     split(d["build_completed_count"] + d["build_failed_count"], b.get("build_count"), project)]
            rows.append(("failed_builds", "", day, d["build_failed_count"]))
        window_start = window_end + dt.timedelta(days=1)
    replace_daily(conn, "vercel", start.isoformat(), "9999-99-99", rows)
    return f"{len({r[2] for r in rows})} day(s) of team usage"


COLLECTORS = {"prisma": collect_prisma, "database": collect_database, "cloudrun": collect_cloudrun,
              "github": collect_github, "vercel": collect_vercel}
