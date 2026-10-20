#!/usr/bin/env python3
"""Create local PostgreSQL backups and verify them in a disposable database."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import local_db
from project import environment

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "data" / "backups"
TABLES = (
    "agencies", "meetings", "document_versions", "document_current",
    "document_pages", "agenda_items", "summaries", "summary_versions",
    "item_topics", "topic_overrides", "editorial", "processing_runs",
)


def connection_settings() -> tuple[dict[str, str], str, str]:
    url = environment().get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required in the environment or .env file.")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port or 5432
        username = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        database = unquote(parsed.path.lstrip("/"))
    except (TypeError, ValueError):
        raise SystemExit("DATABASE_URL is not a valid PostgreSQL URL; connection details were withheld.") from None
    if parsed.scheme not in ("postgresql", "postgres") or not host or not database or "/" in database:
        raise SystemExit("DATABASE_URL must name a PostgreSQL database; connection details were withheld.")
    query = parse_qs(parsed.query)
    if "hostaddr" in query:
        raise SystemExit("DATABASE_URL hostaddr is unsupported; connection details were withheld.")

    # Do not pass the original URL or its password in argv. Clear inherited libpq
    # overrides so commands use only the parsed URL host and credentials.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PG") and k not in ("DATABASE_URL", "TEST_DATABASE_URL")}
    env.update({
        "PGHOST": host,
        "PGPORT": str(port),
        "PGDATABASE": database,
        "PGCONNECT_TIMEOUT": "10",
        "PGCLIENTENCODING": "UTF8",
    })
    if username:
        env["PGUSER"] = username
    if password:
        env["PGPASSWORD"] = password
    if "sslmode" in query:
        env["PGSSLMODE"] = query["sslmode"][0]
    return env, host, database


def require_loopback(host: str) -> None:
    if host.lower() == "localhost":
        return
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise SystemExit("This local backup tool only connects to a loopback PostgreSQL host.")


def run_pg(name: str, args: list[str], env: dict[str, str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [local_db.binary(name), *args], env=env, check=False,
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit(f"{name} could not complete; connection details were withheld.") from None


def require_success(name: str, result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode:
        raise SystemExit(f"{name} failed with exit status {result.returncode}; database output was withheld.")


def backup(output: Path | None = None) -> Path:
    env, host, _ = connection_settings()
    require_loopback(host)
    if output is None:
        BACKUPS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        output = BACKUPS / f"tahoe-{stamp}-{secrets.token_hex(4)}.dump"
    else:
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise SystemExit("Backup destination already exists; choose a new path to avoid replacing an earlier backup.")

    handle = tempfile.NamedTemporaryFile(prefix=".tahoe-backup-", suffix=".tmp", dir=output.parent, delete=False)
    temporary = Path(handle.name)
    handle.close()
    os.chmod(temporary, 0o600)
    try:
        result = run_pg("pg_dump", ["--format=custom", "--no-owner", "--no-privileges", "--no-password", "--file", str(temporary)], env, timeout=1800)
        require_success("pg_dump", result)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise SystemExit("pg_dump produced an empty backup.")
        # A hard link gives no-clobber publication because the temporary file is
        # created in the destination directory (and therefore on the same FS).
        os.link(temporary, output)
        temporary.unlink()
        os.chmod(output, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def row_digest(env: dict[str, str], database: str, query: str) -> tuple[int, str]:
    command_env = dict(env, PGDATABASE=database)
    cmd = [local_db.binary("psql"), "-X", "-qAt", "--no-password", "-c", query]
    proc = None
    try:
        proc = subprocess.Popen(cmd, env=command_env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        digest = hashlib.sha256()
        count = 0
        assert proc.stdout is not None
        for line in proc.stdout:
            row = json.loads(line)
            digest.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            digest.update(b"\n")
            count += 1
        if proc.wait(timeout=300):
            raise SystemExit("psql could not read the database snapshot; connection details were withheld.")
    except subprocess.TimeoutExpired:
        if proc is not None:
            proc.kill()
            proc.wait()
        raise SystemExit("psql snapshot verification timed out; connection details were withheld.") from None
    except (OSError, json.JSONDecodeError):
        raise SystemExit("psql could not read the database snapshot; connection details were withheld.") from None
    return count, digest.hexdigest()


def snapshot(env: dict[str, str], database: str) -> dict[str, object]:
    union = " UNION ALL ".join(f"SELECT '{table}' AS table_name, count(*) AS n FROM public.{table}" for table in TABLES)
    counts_proc = run_pg("psql", ["-X", "-qAt", "-F", "|", "--no-password", "-c", f"SELECT table_name, n FROM ({union}) AS counts ORDER BY table_name"], dict(env, PGDATABASE=database))
    require_success("psql", counts_proc)
    counts = {}
    for line in counts_proc.stdout.splitlines():
        name, count = line.split("|", 1)
        counts[name] = int(count)

    source_pages = row_digest(env, database,
        "SELECT row_to_json(r)::text FROM (SELECT document_version_id, page_number, text FROM public.document_pages ORDER BY document_version_id, page_number) r")
    editorial = row_digest(env, database,
        "SELECT row_to_json(r)::text FROM (SELECT item_id, suppressed, correction FROM public.editorial ORDER BY item_id) r")
    overrides = row_digest(env, database,
        "SELECT row_to_json(r)::text FROM (SELECT item_id, topic_id, included, author, updated_at FROM public.topic_overrides ORDER BY item_id, topic_id) r")
    return {"counts": counts, "source_pages": source_pages, "editorial": editorial, "topic_overrides": overrides}


def restore_check(backup_file: Path) -> None:
    env, host, source_db = connection_settings()
    require_loopback(host)
    backup_file = backup_file.expanduser().resolve()
    if not backup_file.is_file() or backup_file.stat().st_size == 0:
        raise SystemExit("Backup file is missing or empty.")

    before = snapshot(env, source_db)
    scratch_db = "tahoe_restore_check_" + secrets.token_hex(8)
    admin_env = dict(env, PGDATABASE="postgres")
    created = False
    try:
        createdb = run_pg("createdb", ["--template=template0", "--maintenance-db=postgres", scratch_db], admin_env)
        require_success("createdb", createdb)
        created = True
        restore_env = dict(env, PGDATABASE=scratch_db)
        restored = run_pg("pg_restore", ["--dbname", scratch_db, "--exit-on-error", "--no-owner", "--no-privileges", "--no-password", str(backup_file)], restore_env, timeout=1800)
        require_success("pg_restore", restored)
        after = snapshot(env, source_db)
        if before != after:
            raise SystemExit("The source database changed during restore-check; rerun while local ingestion and edits are idle.")
        scratch = snapshot(env, scratch_db)
        if scratch != after:
            raise SystemExit("Restore verification failed: table counts, source text, or editorial data differ from the source.")
        counts = scratch["counts"]
        print("Restore verified in a disposable local database.")
        print(f"Tables checked: {len(counts)}; source pages: {scratch['source_pages'][0]}; editorial rows: {counts['editorial']}; topic overrides: {counts['topic_overrides']}.")
    finally:
        if created:
            dropped = run_pg("dropdb", ["--if-exists", "--maintenance-db=postgres", scratch_db], admin_env)
            if dropped.returncode:
                raise SystemExit(f"Could not drop the scratch database {scratch_db}; remove only that database after checking it is disposable.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    backup_parser = sub.add_parser("backup", help="write a custom-format dump under data/backups/")
    backup_parser.add_argument("--out", type=Path, help="optional destination path")
    check_parser = sub.add_parser("restore-check", help="restore a dump into a new scratch database and compare it")
    check_parser.add_argument("backup", type=Path)
    args = parser.parse_args(argv)
    if args.action == "backup":
        output = backup(args.out)
        print(f"Backup written: {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output} ({output.stat().st_size} bytes).")
    else:
        restore_check(args.backup)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        raise SystemExit("Interrupted; any created scratch database is cleaned up on exit.") from None
