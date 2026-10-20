"""Budget guard: pause the ingestion scheduler when the monthly budget is spent.

Receives Cloud Billing budget notifications as Pub/Sub push requests. When
costAmount >= budgetAmount for the configured budget, it pauses the Cloud
Scheduler job through the Cloud Scheduler API using the service's own identity.
It never disables billing, so no resources are deleted. Standard library only.

Environment: PROJECT_ID, REGION, SCHEDULER_JOB, BUDGET_NAME (optional filter),
DRY_RUN=1 (log the decision without calling the API), PORT.
Runbook: docs/operations/deployment.md
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)


def log(severity, message, **fields):
    print(json.dumps({"severity": severity, "message": message, **fields}), flush=True)


def decide(envelope, budget_name=""):
    """Return (should_pause, details) for a Pub/Sub push envelope."""
    message = envelope.get("message") or {}
    data = json.loads(base64.b64decode(message.get("data") or b"e30=").decode("utf-8"))
    details = {
        "budget": data.get("budgetDisplayName"),
        "cost": data.get("costAmount"),
        "budgetAmount": data.get("budgetAmount"),
        "currency": data.get("currencyCode"),
        "interval": data.get("costIntervalStart"),
    }
    if budget_name and data.get("budgetDisplayName") != budget_name:
        return False, {**details, "reason": "other budget"}
    cost, amount = data.get("costAmount"), data.get("budgetAmount")
    if not isinstance(cost, (int, float)) or not isinstance(amount, (int, float)) or amount <= 0:
        return False, {**details, "reason": "missing cost or budget amount"}
    return cost >= amount, details


def access_token():
    request = urllib.request.Request(METADATA_TOKEN_URL, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)["access_token"]


def scheduler_call(method, path, token):
    request = urllib.request.Request(
        "https://cloudscheduler.googleapis.com/v1/" + path,
        method=method,
        data=b"{}" if method == "POST" else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def pause_scheduler():
    job = "projects/{}/locations/{}/jobs/{}".format(
        os.environ["PROJECT_ID"], os.environ["REGION"], os.environ["SCHEDULER_JOB"]
    )
    token = access_token()
    state = scheduler_call("GET", job, token).get("state")
    if state == "PAUSED":
        return "already paused"
    scheduler_call("POST", job + ":pause", token)
    return "paused"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (http.server naming)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            envelope = json.loads(self.rfile.read(length) or b"{}")
            should_pause, details = decide(envelope, os.environ.get("BUDGET_NAME", ""))
        except (ValueError, KeyError, TypeError) as exc:
            # Acknowledge malformed messages so they are not redelivered forever.
            log("WARNING", "ignored malformed budget notification", error=str(exc))
            return self.reply(204)
        if not should_pause:
            log("INFO", "budget below limit; no action", **details)
            return self.reply(204)
        if os.environ.get("DRY_RUN") == "1":
            log("WARNING", "budget exhausted; DRY_RUN set, scheduler not paused", **details)
            return self.reply(204)
        try:
            result = pause_scheduler()
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            # Non-2xx makes Pub/Sub retry with backoff.
            log("ERROR", "budget exhausted but pausing the scheduler failed", error=str(exc), **details)
            return self.reply(500)
        log("WARNING", "budget exhausted; ingestion scheduler " + result,
            scheduler_job=os.environ["SCHEDULER_JOB"], **details)
        return self.reply(204)

    def reply(self, status):
        self.send_response(status)
        self.end_headers()

    def log_message(self, *args):  # silence default access log; Cloud Run logs requests
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    log("INFO", "budget guard listening", port=port)
    try:
        HTTPServer(("", port), Handler).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
