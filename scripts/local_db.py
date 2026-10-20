#!/usr/bin/env python3
"""Manage this checkout's isolated, loopback-only PostgreSQL cluster."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "postgres"
SOCKET = ROOT / "data" / "pg-socket"
PORT = os.environ.get("TAHOE_DB_PORT", "55432")


def binary(name):
    configured = os.environ.get("POSTGRES_BIN")
    candidates = ([Path(configured) / name] if configured else []) + [
        Path("/Applications/Postgres.app/Contents/Versions/latest/bin") / name,
        Path("/opt/homebrew/opt/postgresql@17/bin") / name,
        Path("/opt/homebrew/opt/postgresql@16/bin") / name,
    ]
    found = shutil.which(name)
    if found:
        return found
    for path in candidates:
        if path.is_file():
            return str(path)
    raise SystemExit(f"{name} not found. Install PostgreSQL or set POSTGRES_BIN; alternatively use docker compose up -d db.")


def run(name, *args, **kwargs):
    return subprocess.run([binary(name), *args], check=True, **kwargs)


def start():
    DATA.parent.mkdir(exist_ok=True)
    SOCKET.mkdir(exist_ok=True)
    if not (DATA / "PG_VERSION").exists():
        with tempfile.NamedTemporaryFile(mode="w") as password:
            password.write("tahoe-local\n")
            password.flush()
            run("initdb", "-D", str(DATA), "-U", "tahoe", "--auth-host=scram-sha-256", "--auth-local=trust", "--pwfile", password.name, "--encoding=UTF8", "--locale=C")
    status = subprocess.run([binary("pg_ctl"), "-D", str(DATA), "status"], capture_output=True)
    if status.returncode:
        pid_file = DATA / "postmaster.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().splitlines()[0])
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            except PermissionError:
                raise SystemExit("Cannot inspect the existing PostgreSQL process in this sandbox. Run with local process/network access; do not remove its PID file or start a second server.") from None
            else:
                raise SystemExit("PostgreSQL's recorded process is running but pg_ctl could not inspect it. Refusing to start a second server; check process permissions.")
        # pg_ctl passes this option string to postgres; quote paths with spaces.
        import shlex
        options = shlex.join(["-h", "127.0.0.1", "-p", PORT, "-k", str(SOCKET)])
        run("pg_ctl", "-D", str(DATA), "-l", str(DATA.parent / "postgres.log"), "-o", options, "-w", "start")
    env = {**os.environ, "PGPASSWORD": "tahoe-local"}
    args = ["-h", "127.0.0.1", "-p", PORT, "-U", "tahoe"]
    exists = run("psql", *args, "-d", "postgres", "-Atc", "SELECT 1 FROM pg_database WHERE datname = 'tahoe'", env=env, capture_output=True, text=True)
    if exists.stdout.strip() != "1":
        run("createdb", *args, "tahoe", env=env)
    print(f"Local PostgreSQL ready on 127.0.0.1:{PORT}, database tahoe.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args()
    if args.action == "start":
        start()
    elif args.action == "stop":
        if (DATA / "postmaster.pid").exists():
            run("pg_ctl", "-D", str(DATA), "-m", "fast", "-w", "stop")
        else:
            print("This checkout's PostgreSQL cluster is stopped.")
    else:
        run("pg_ctl", "-D", str(DATA), "status")


if __name__ == "__main__":
    main()
