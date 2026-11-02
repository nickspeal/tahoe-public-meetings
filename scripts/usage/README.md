# Usage dashboard

A local, on-demand report of this project's compute and quota usage. It reads each
service with the operator's existing command-line logins and writes only to the
gitignored `private/usage/` directory, so no usage figures are published.

```sh
python3 scripts/usage/usage.py                     # collect everything, then write the reports
python3 scripts/usage/usage.py --open              # ... and open the dashboard
python3 scripts/usage/usage.py --only prisma,database
python3 scripts/usage/usage.py --report-only       # rebuild reports from stored data
```

Outputs in `private/usage/`:

- `dashboard.html`: quotas, failures, weekly and monthly charts, the past 7 and 30 days, and breakdowns.
  Open it without collecting: `open private/usage/dashboard.html`.
- `summary.md`: the same figures as text. Agents should read this before decisions that spend a quota.
- `summary.json`: machine-readable, with the same fields.
- `usage.sqlite`: one value per metric, per breakdown, per UTC day, plus point-in-time snapshots.

## Sources

| Collector | Reads | Needs |
| --- | --- | --- |
| `prisma` | Daily operations and storage for every database in the Prisma workspace (the quota is workspace-wide) | `npx prisma` signed in, or `PRISMA_SERVICE_TOKEN` |
| `database` | `ai_usage` (cost, tokens, calls, and failed calls grouped by error, by day, purpose, model) and database and table sizes. Source `cloud` is the production database (the Cloud Run jobs, or any command pointed at production); `local` is the local database, if it is running | `gcloud` access to the `tahoe-database-url` secret, and `psql` |
| `cloudrun` | Job executions, turned into vCPU-seconds and GiB-seconds from each execution's duration and configured limits | `gcloud` |
| `github` | Workflow runs and their wall-time minutes | `gh` |
| `vercel` | Team usage (edge requests, data transfer, functions, builds) with this project's share | The Vercel CLI login (refreshed automatically), or `VERCEL_TOKEN` / `VERCEL_ADMIN_TOKEN` |

A collection sends two read-only statements to the production database, which count toward the
Prisma operations quota. Prisma usage takes one CLI call per database per day. The first run
backfills every day since `start`; later runs refetch only the last three days and any missing day.

## What can be rebuilt

Deleting `private/usage/` loses little. The next run backfills daily history for Prisma operations,
AI usage (from the `ai_usage` tables), GitHub runs, and Vercel usage from the services themselves.
Some data cannot be recovered:

- Storage, database size, and table sizes are point-in-time readings, kept only from past collections.
- Cloud Run execution history: Cloud Run keeps a limited history, so the store keeps every execution it has seen.
- AI usage from the local database exists only while that database does.

## Configuration

Everything has a default. Optional overrides go in `private/usage/config.toml`:

```toml
start = "2026-10-01"                 # first day to backfill

[prisma]
projects = ["<project id or name>"]  # default: every project in the signed-in workspace

[gcp]
project = "<gcp project>"
region = "<region>"

[github]
repo = "<owner>/<repo>"              # default: the origin remote

[vercel]
team_id = "<team id>"                # default: .vercel/project.json
project_id = "<project id>"

[ai]
include_local = true

[quotas."vercel.edge_requests"]      # override any metric's quota or window
quota = 1000000
window = "rolling30"
```

Quotas default to the free-tier figures recorded in `docs/operations/deployment.md` and
`docs/architecture/free-tier-sizing.md`. The AI quota is the ingestion job's own
`DEFAULT_MONTHLY_BUDGET`. Recheck the vendors' pages when a plan changes.
