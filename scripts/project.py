#!/usr/bin/env python3
"""One entry point for local setup, ingestion, checks, and the public website."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers/ingestion"
WEB = ROOT / "apps/web"
PYTHON = WORKER / ".venv/bin/python"
CLI = WORKER / ".venv/bin/tahoe-ingest"
EXPORT = WEB / "src/data/public.json"
LOCAL_URL = "postgresql://tahoe:tahoe-local@127.0.0.1:55432/tahoe"
PUBLISHED_EXPORT = "https://tahoepublicmeetings.org/data/public-export.json"  # fetch-export.mjs FALLBACK_EXPORT_URL


def environment():
    env = dict(os.environ)
    env.setdefault("ASTRO_TELEMETRY_DISABLED", "1")
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for raw in dotenv.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip("\"'"))
    env.setdefault("DATABASE_URL", LOCAL_URL)
    env.setdefault("TEST_DATABASE_URL", env["DATABASE_URL"])
    return env


def run(*args, cwd=ROOT):
    subprocess.run([str(a) for a in args], cwd=cwd, env=environment(), check=True)


def worker(*args):
    if not CLI.exists():
        raise SystemExit("Run python3 scripts/project.py setup first.")
    run(CLI, *args)


def refresh():
    worker("export", "--out", EXPORT)
    run(sys.executable, ROOT / "scripts/check_export.py")


def published_export():
    """Start the website from the export production publishes, unless one is already present."""
    if EXPORT.exists():
        return
    print(f"Downloading the published export from {PUBLISHED_EXPORT}")
    with urllib.request.urlopen(PUBLISHED_EXPORT, timeout=60) as response:
        payload = json.load(response)
    EXPORT.parent.mkdir(parents=True, exist_ok=True)
    EXPORT.write_text(json.dumps(payload) + "\n")
    run(sys.executable, ROOT / "scripts/check_export.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "ingest", "refresh", "test", "check", "dev", "build", "stop", "status", "worker"],
                        metavar="{setup,ingest,refresh,test,dev,build,stop,status,worker}")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "setup":
        for name in ("node", "npm", "uv", "pdftotext", "pdfinfo"):
            if not shutil.which(name):
                raise SystemExit(f"Missing prerequisite: {name}. See README.md.")
        run("uv", "sync", "--project", WORKER, "--extra", "test", "--python", "3.13", "--locked")
        run("npm", "ci", cwd=WEB)
        if environment()["DATABASE_URL"] == LOCAL_URL:
            run(sys.executable, ROOT / "scripts/local_db.py", "start")
        worker("init-db")
        published_export()
        print("Ready. Start the website: python3 scripts/project.py dev\n"
              "Fill the local database from official sources: python3 scripts/project.py ingest")
    elif args.action == "ingest":
        # The production pipeline against the local database: live sources only, nothing published.
        worker("run-scheduled", "--no-publish", "--out", EXPORT, *args.arguments)
        run(sys.executable, ROOT / "scripts/check_export.py")
    elif args.action == "refresh":
        refresh()
    elif args.action in ("test", "check"):  # "check" is a hidden alias
        run(PYTHON, "-m", "pytest", "workers/ingestion/tests", "-q")
        run(sys.executable, ROOT / "scripts/check_export.py")
        run("npm", "run", "check", cwd=WEB)
        run("npm", "test", cwd=WEB)
        run(sys.executable, ROOT / "scripts/check_site.py")
    elif args.action == "dev":
        if not EXPORT.exists():
            raise SystemExit("No source data exported. Run python3 scripts/project.py setup first.")
        run("npm", "run", "dev", cwd=WEB)
    elif args.action == "build":
        if not EXPORT.exists():
            raise SystemExit("No export to build from. Run python3 scripts/project.py setup, ingest, or refresh first.")
        run("npm", "run", "build", cwd=WEB)
    elif args.action == "stop":
        run(sys.executable, ROOT / "scripts/local_db.py", "stop")
    elif args.action == "status":
        worker("status")
    elif args.action == "worker":
        worker(*args.arguments)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
