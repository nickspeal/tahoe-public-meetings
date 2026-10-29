"""ntfy alerts for runs that succeed but leave something behind.

A failed Cloud Run execution already emails through Cloud Monitoring. These alerts cover
the quiet cases: Claude failed on some meetings, classification stopped at a cost
limit, or steps stopped at the run's time budget, so the run exits 0 and the
affected meetings keep rule tags or wait for the next run. The message names
counts and meeting ids, never the topic URL, which is a secret.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request


def run_problems(summary: dict) -> list[str]:
    """Plain-language problems in a run-scheduled summary that the exit code does not show."""
    step = next((s for s in summary.get("steps", []) if s.get("name") == "classify-topics"), None)
    detail = (step or {}).get("detail")
    if not isinstance(detail, dict) or (step or {}).get("status") == "skipped":
        return _summary_problems(summary)
    problems = []
    if detail.get("stopped"):
        problems.append(f"Claude classification stopped: {detail['stopped']}")
    if detail.get("stoppedEarly") and not detail.get("timeBudgetReached"):  # time stops are reported once, below
        problems.append(f"Claude classification hit a cost limit (${detail.get('costUsd', 0):.2f} this run, "
                        f"${detail.get('monthToDateUsd', 0):.2f} this month): {detail['stoppedEarly']}")
    failed = detail.get("failedMeetings") or []
    if failed:
        problems.append(f"Claude failed on {len(failed)} meeting(s): {', '.join(map(str, failed[:5]))}"
                        + (" …" if len(failed) > 5 else ""))
    return problems + _summary_problems(summary)


def _summary_problems(summary: dict) -> list[str]:
    """Staff report summaries stopped by a budget or fatal API guard (items keep their agenda text), and time-budget stops."""
    problems = []
    step = next((s for s in summary.get("steps", []) if s.get("name") == "summarize-items"), None)
    detail = (step or {}).get("detail")
    if isinstance(detail, dict) and detail.get("stopped"):
        problems.append(f"Staff report summaries stopped: {detail['stopped']}")
    if summary.get("timeBudgetReached"):
        budget = summary.get("timeBudgetSeconds")
        limit = f" ({budget / 60:g} min)" if budget else ""
        took = summary.get("durationSeconds")
        after = f" and finished after {took / 60:.0f} min" if isinstance(took, (int, float)) else ""
        problems.append(f"The run reached its time budget{limit}{after}: {', '.join(summary.get('timeBudgetSteps') or [])} "
                        "stopped early and left the rest for the next run")
    return problems


def send_ntfy(title: str, message: str, *, tags: str = "warning", priority: str = "default",
              env=os.environ, opener=urllib.request.urlopen) -> bool:
    """POST one notification to NTFY_URL. Returns False when ntfy is not configured; raises on delivery errors."""
    url = (env.get("NTFY_URL") or "").strip()
    if not url:
        return False
    if not url.startswith("https://"):
        raise ValueError("NTFY_URL must be an https URL")
    headers = {"Title": title, "Tags": tags, "Priority": priority, "User-Agent": "TahoePublicMeetings-ingest/0.1"}
    if env.get("NTFY_TOKEN"):
        headers["Authorization"] = f"Bearer {env['NTFY_TOKEN'].strip()}"
    request = urllib.request.Request(url, data=message.encode(), method="POST", headers=headers)
    try:
        with opener(request, timeout=10) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ntfy returned HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"ntfy request failed: {type(getattr(exc, 'reason', exc)).__name__}") from None
    if not 200 <= status < 300:
        raise RuntimeError(f"ntfy returned HTTP {status}")
    return True


def alert_on_summary(summary: dict, *, send=send_ntfy) -> list[str]:
    """Send one ntfy alert for a finished run's silent problems. Never raises; returns the problems found."""
    problems = run_problems(summary)
    if not problems:
        return []
    try:
        send("Tahoe ingestion needs attention", "\n".join(problems))
    except Exception as exc:  # The run itself succeeded; the failure shows in the job log.
        print(f'{{"severity": "ERROR", "message": "ntfy alert failed: {type(exc).__name__}"}}', flush=True)
    return problems
