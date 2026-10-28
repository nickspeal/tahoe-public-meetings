---
name: skip-blocked-endpoint
description: Find sources that keep failing with HTTP 403 in the tahoe-ingest or tahoe-backfill Cloud Run job logs, reproduce the failure locally, and add them to config/skipped-endpoints.json so scheduled runs stop retrying them. Use when asked to check the ingestion logs for blocked or failing sources, or to add or remove a skipped endpoint.
---

# Skip persistently blocked endpoints

Background: `config/skipped-endpoints.json` lists URL prefixes the worker never requests (see "Skipped endpoints" in `config/README.md`). The job does not detect 403s itself; this skill is the manual loop that does. Everything committed is public, so keep notes factual and refer to people by role.

## 1. Find failures in the logs

Identify the Google project without guessing: run `gcloud config list` and confirm the account and project with the user before querying. Read-only commands only.

```sh
gcloud run jobs executions list --job=tahoe-ingest --region=us-west1 --project=<project-id> --limit=10
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name=("tahoe-ingest" OR "tahoe-backfill") AND (textPayload:"403" OR jsonPayload.message:"403" OR severity>=WARNING)' \
  --project=<project-id> --freshness=3d --limit=200 --format=json > "$SCRATCH/logs.json"
```

Use the session scratchpad for `$SCRATCH`. Log lines are JSON with `severity`, `message` (`discover <agency>` or `step ...`) and a `result` or `step` object. Look for `error`, `documentFailures`, and `failures` entries mentioning `HTTP Error 403` or `Public source download failed ... 403`. For each, record the agency id and the failing URL. Distinguish:

- **Repeats across several executions** over days: a candidate for skipping.
- **One-off or intermittent** (429, 5xx, timeouts, a single 403): not a candidate; leave it.
- **Already covered** by an entry in `config/skipped-endpoints.json`: nothing to do.

The failing URL may not be in the log line (discovery errors name the agency). Find it in the agency's resolved entry (`python3 scripts/project.py worker agencies` prints `discovery.indexUrl` and `sourceUrl`, including any an entry inherits through `extends` from `packages/domain/agencies.json`) or the `documents[].url` of the agency's candidates in `config/verified-agency-meetings-*.json`.

## 2. Reproduce locally

Make one request per suspect URL, with no bursts, proxies, or disabled TLS verification:

```sh
cd workers/ingestion && uv run python -c "
from tahoe_ingest.http import fetch_bytes
print(len(fetch_bytes('<url>', max_bytes=2_000_000)[0]))"
```

This exercises the same Python-then-curl path as the job. Also run `python3 scripts/project.py worker discover <agency-id> --limit 3` for index failures. A 403 here confirms it. If the request succeeds, the block was transient or the source recovered: report that and change nothing. Note whether the host's home page also fails, since that decides the prefix.

## 3. Update the source

Add an entry to `config/skipped-endpoints.json` using the narrowest prefix that covers the block (the whole host if even its home page fails; a path otherwise; keep the trailing slash):

```json
{"prefix": "https://example.gov/path/", "status": 403, "since": "YYYY-MM-DD", "reason": "What was observed; cause unknown unless established.", "issue": "docs/issues/SOURCE-00N-....md"}
```

Then:

1. Update or add the issue note under `docs/issues/`, then regenerate `docs/operations/source-coverage.md` with `python3 scripts/project.py worker coverage-doc` (its Blocked list comes from the skip file) and adjust the hand-written notes beside it.
2. Run `cd workers/ingestion && uv run pytest -q`.
3. Commit (the attribution trailer from the session reminder applies) and push to `origin/main`.
4. Tell the user the job image must be rebuilt and redeployed for the change to take effect (`docs/operations/deployment.md`); do not deploy without being asked.

## Removing an entry

To retry an endpoint, delete its entry, reproduce once locally to confirm it now works, update the docs above, commit, and tell the user to redeploy.
