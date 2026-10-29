# Deployment runbook

Updated October 20, 2026. This runbook covers the hosted pipeline from [decision 0005](../decisions/0005-selected-stack.md): a Cloud Run job ingests sources every six hours, stores a validated public export in Prisma Postgres, and calls a Vercel deploy hook, and Vercel then rebuilds the static site from that export. One idempotent script, [`scripts/deploy/gcp-ingestion.sh`](../../scripts/deploy/gcp-ingestion.sh), creates or updates everything on Google Cloud. Rerunning it is safe: each step checks for an existing resource before creating one, and a rerun never resumes a paused scheduler.

The scheduled job only keeps meetings current: by default it captures meetings from 60 days back to 120 days ahead, and never anything dated before the 2026-01-01 coverage floor. Earlier history comes from the separate one-time backfill, and stored meetings are kept whatever the window. See the worker's [scheduled pipeline](../../workers/ingestion/README.md#scheduled-pipeline-cloud-run-job) notes.

Nothing in this repository names the operator's accounts. The Google project, billing account, alert address, and Vercel scope are supplied when you run the commands.

## What the script creates

| Resource (default name) | Purpose |
| --- | --- |
| Project `tahoe-public-meetings` (`PROJECT_ID`), linked to `BILLING_ACCOUNT` | Project IDs are global; override the ID if it is taken |
| Artifact Registry repository `tahoe` in `us-west1` | Images. A cleanup policy keeps the newest 3 versions of each image and deletes older ones after a day |
| Cloud Run job `tahoe-ingest` | `tahoe-ingest run-scheduled`; 1 vCPU, 2 GiB (the `/tmp` filesystem is in memory, sized for the 505-page SLT packet), 60-minute task timeout, 1 retry |
| Service accounts `tahoe-ingest-job`, `tahoe-ingest-scheduler`, `tahoe-budget-guard` | No basic roles. The job account can read only its secrets. The scheduler account holds `run.invoker` on the job alone. The guard account holds a custom role with exactly `cloudscheduler.jobs.get` and `cloudscheduler.jobs.pause` |
| Secrets `tahoe-database-url`, `tahoe-vercel-deploy-hook-url`, `tahoe-ntfy-url` | Exposed to the job as `DATABASE_URL`, `VERCEL_DEPLOY_HOOK_URL`, and `NTFY_URL` at `:latest`. Only the database URL is required. Claude needs no secret: the job authenticates with workload identity federation (below) |
| Cloud Scheduler job `tahoe-ingest-every-6h` | `1 */6 * * *` in `America/Los_Angeles`: 00:01, 06:01, 12:01, and 18:01 Pacific, following PST and PDT. The 00:01 run rebuilds the site just after midnight so its Today marker is current. It calls the Cloud Run Admin API `jobs:run` endpoint with an OAuth token |
| Alert policy "Tahoe ingestion job failed" plus an email channel | Sends an email when any execution fails (requires `ALERT_EMAIL`) |
| Budget `tahoe-public-meetings-monthly` | $5 per month on this project. Emails go out at 50%, 90%, and 100%, and notifications go to Pub/Sub |
| Pub/Sub topic `tahoe-budget-alerts`, push subscription, and private Cloud Run service `tahoe-budget-guard` | The budget guard. It pauses the scheduler once the month's cost reaches the budget |

Settings are environment variables at the top of the script, such as `REGION`, `JOB_MEMORY`, `TASK_TIMEOUT`, `SCHEDULE`, `BUDGET_AMOUNT`, and `KEEP_IMAGES`. The script never accepts a secret value as an argument and never prints one. It reads values from a hidden prompt, or from stdin for `set-secret`.

## Prerequisites

1. **Google identity.** The operator signs in and chooses the billing account:
   ```sh
   gcloud auth login
   gcloud billing accounts list
   ```
   Budget creation needs Billing Account Administrator or Billing Account Costs Manager on that account. Docker with `buildx` builds images locally, which avoids Cloud Build. Pass `--cloud-build` to build remotely instead. Cloud Build uploads only the files allowlisted in the root `.gcloudignore` (about 0.3 MB), the same set the `.dockerignore` sends to a local build. Without that file `gcloud` would fall back to `.gitignore` and also upload untracked local folders.
2. **Vercel scope.** Verify the identity and the designated scope before doing anything in Vercel. Do not use whichever account happens to be logged in:
   ```sh
   vercel whoami
   vercel teams ls        # confirm the designated scope is listed
   vercel project ls --scope <designated-scope>
   ```
   Pass `--scope <designated-scope>` explicitly to every Vercel command. If the scope is missing, stop and ask for the correct sign-in.
3. **Prisma Postgres.** In the Vercel dashboard under the designated scope, open Storage, then Create Database, then Prisma Postgres (Marketplace), and connect it to the website project. Copy the **direct TCP** connection string (`postgres://…?sslmode=require`), not the `prisma+postgres://` Accelerate URL. Add `sslmode=require` if it is missing. The job applies the schema itself on every run (`init-db` is idempotent).

## First deploy

Follow this order: database, secrets, job, one run, deploy hook, then the Vercel build.

```sh
# 1. Infrastructure, image, secrets (prompts for DATABASE_URL; Enter skips the optional ones)
PROJECT_ID=<project-id> BILLING_ACCOUNT=<billing-account-id> ALERT_EMAIL=<operator-address> \
  scripts/deploy/gcp-ingestion.sh

# 2. Run once and wait; prints the execution's log URL
PROJECT_ID=<project-id> scripts/deploy/gcp-ingestion.sh run-now
```

The script prints the active account and target, then asks you to type the project ID. Pass `--yes` only after you have checked both. If you want the job to classify with Claude, set the four `ANTHROPIC_*` federation IDs (see [Claude credentials](#claude-credentials-workload-identity-federation)) in the environment when running the script. After the first run succeeds, the database holds a `public_exports` row.

3. **Deploy hook.** In the Vercel project (designated scope), open Settings, then Git, then Deploy Hooks, and create a hook for the production branch. Deploy hooks require a Git-connected project. Store the hook URL, which is itself a credential:
   ```sh
   PROJECT_ID=<project-id> scripts/deploy/gcp-ingestion.sh set-secret vercel-deploy-hook-url   # hidden prompt
   PROJECT_ID=<project-id> scripts/deploy/gcp-ingestion.sh --yes --skip-build                   # attach it to the job
   ```
4. **Vercel build.** Trigger the first production build (`vercel deploy --prod --scope <designated-scope>` or the dashboard). Run `run-now` again to confirm that the hook starts a build.

## Redeploy a new image

Commit the worker changes, then rerun the script. It tags the image with the Git SHA, deploys it by digest, and leaves secrets and the scheduler state untouched. Without the `ANTHROPIC_*` IDs in the environment it keeps the deployed job's IDs. Before October 24, 2026 a redeploy without them turned Claude classification off, which happened on October 20:

```sh
PROJECT_ID=<project-id> scripts/deploy/gcp-ingestion.sh --yes [--run-now]
```

The script refuses to build from uncommitted changes under `workers/ingestion`, `packages/domain`, or `config` unless you set `ALLOW_DIRTY=1`. To roll the job back to an earlier image that is still in the registry (the newest 3 are kept), run `IMAGE_TAG=<old-sha> scripts/deploy/gcp-ingestion.sh --yes --skip-build`.

## Rotate a secret

```sh
PROJECT_ID=<project-id> scripts/deploy/gcp-ingestion.sh set-secret database-url   # or vercel-deploy-hook-url
```

The command reads the value from a hidden prompt, or from the first line of stdin, and adds a new version. The next execution reads `:latest`, so the job does not need a redeploy. After a run succeeds, revoke the old credential with its provider. Then destroy the old version so it stops counting toward the six free active versions:

```sh
gcloud secrets versions list tahoe-database-url --project=<project-id>
gcloud secrets versions destroy <old-version> --secret=tahoe-database-url --project=<project-id>
```

## History backfill

The scheduled run looks only at the newest few listings per agency, so it cannot build history. The maintainer set coverage to start on 2026-01-01, with everything kept, and history filled once by `tahoe-ingest backfill` (see the [worker README](../../workers/ingestion/README.md#history-backfill)). It runs as a separate Cloud Run job, `tahoe-backfill`, with no scheduler, so the production `tahoe-ingest` job and its image stay untouched. It takes the same advisory lock as the scheduled run. If a scheduled run is in progress it waits up to 45 minutes. A scheduled run that starts while the backfill is running skips itself.

1. **Check storage headroom first.** Compare the current database size with the Prisma Postgres plan's storage limit (see [storage monitoring](../operations.md#check-database-storage)), and dry-run the range to see how many meetings it would add:
   ```sh
   DATABASE_URL=... tahoe-ingest backfill --since 2026-01-01 --dry-run   # discovery only; no downloads, export, or publish
   ```
2. **Build and push an image** from a committed revision, tagged `backfill-<sha>`. The registry's cleanup policy keeps only the newest 3 versions of each image, and the `ingestion` prefix also matches any `ingestion-*` name, so confirm that the image `tahoe-ingest` runs is still among the newest 3 after the push (`gcloud artifacts docker images list us-west1-docker.pkg.dev/<project-id>/tahoe/ingestion --include-tags --sort-by=~UPDATE_TIME`).
   ```sh
   docker buildx build --platform linux/amd64 -f workers/ingestion/Dockerfile \
     -t us-west1-docker.pkg.dev/<project-id>/tahoe/ingestion:backfill-<sha> --push .
   ```
3. **Create the job.** It uses the same service account, the two secrets, and the Claude federation IDs. 1 vCPU, 2 GiB, a 6-hour task timeout, and no retries (a rerun resumes anyway):
   ```sh
   gcloud run jobs create tahoe-backfill --project=<project-id> --region=us-west1 \
     --image=us-west1-docker.pkg.dev/<project-id>/tahoe/ingestion:backfill-<sha> \
     --service-account=tahoe-ingest-job@<project-id>.iam.gserviceaccount.com \
     --cpu=1 --memory=2Gi --task-timeout=6h --max-retries=0 \
     --set-secrets=DATABASE_URL=tahoe-database-url:latest,VERCEL_DEPLOY_HOOK_URL=tahoe-vercel-deploy-hook-url:latest \
     --set-env-vars=TAHOE_IDENTITY_TOKEN_SOURCE=gcp-metadata,ANTHROPIC_ORGANIZATION_ID=...,ANTHROPIC_SERVICE_ACCOUNT_ID=...,ANTHROPIC_FEDERATION_RULE_ID=...,ANTHROPIC_WORKSPACE_ID=... \
     --args=backfill,--since,2026-07-01
   ```
4. **Run it outside a scheduled slot.** The scheduler fires at 00, 06, 12, and 18 h Pacific. Check that no `tahoe-ingest` execution is running, then:
   ```sh
   gcloud run jobs executions list --job=tahoe-ingest --region=us-west1 --project=<project-id> --limit=3
   gcloud run jobs execute tahoe-backfill --region=us-west1 --project=<project-id> --wait
   ```
   For the full year, change the arguments and run the job again. Meetings already stored with documents are skipped, so a full-year run after a partial one only fetches what is missing:
   ```sh
   gcloud run jobs update tahoe-backfill --region=us-west1 --project=<project-id> --args=backfill,--since,2026-01-01
   gcloud run jobs execute tahoe-backfill --region=us-west1 --project=<project-id> --wait
   ```
5. **Check the result.** The last log line is a JSON summary. It has a `byAgency` entry for each agency (found, already stored, captured, failures, earliest date the source listed, and whether it reached `--since`), plus download volume, `phaseSeconds`, database size before and after, `exportBytes`, `aiCostUsd`, and `peakMemory`. The deploy hook starts a Vercel production build. Confirm that the build is Ready and that the site shows the larger meeting count.

**Recordings before the YouTube feed window** ([decision 0025](../decisions/0025-minutes-and-recording-links.md)). Each YouTube feed holds only the newest 15 videos, which reaches back a few weeks to a few months depending on the channel. To link older recordings, create a free YouTube Data API key (Google Cloud console, APIs and services, enable "YouTube Data API v3", create an API key restricted to that API), store it as a secret, and add it to the backfill job. The backfill then reads each configured channel's uploads back to `--since`, which uses a few dozen of the 10,000 free daily quota units. The scheduled job does not need the key.
```sh
gcloud secrets create tahoe-youtube-api-key --project=<project-id> --data-file=-   # paste the key, then Ctrl-D
gcloud secrets add-iam-policy-binding tahoe-youtube-api-key --project=<project-id> \
  --member=serviceAccount:tahoe-ingest-job@<project-id>.iam.gserviceaccount.com --role=roles/secretmanager.secretAccessor
gcloud run jobs update tahoe-backfill --region=us-west1 --project=<project-id> --update-secrets=YOUTUBE_API_KEY=tahoe-youtube-api-key:latest
```
Without the key, the backfill still links listing minutes and videos, draft minutes, and recordings within each feed's window.

Claude classification during a backfill uses the same `--max-cost` ($1 per run) and `--monthly-budget` guards. If the run stops at a cost limit, the remaining meetings keep rule tags and are classified by later scheduled runs.

## Logs and status

```sh
gcloud run jobs executions list --job=tahoe-ingest --region=us-west1 --project=<project-id>
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="tahoe-ingest"' \
  --project=<project-id> --freshness=1d --order=asc --format='value(textPayload)'
```

The job prints a JSON summary on stdout as its last output. The console's Cloud Run page lists the job, its executions, and per-execution logs, and `run-now` prints a direct link. A failed execution emails `ALERT_EMAIL`. Three further checks cover failures that email cannot see, all through ntfy; see [Silent-failure alerts](#silent-failure-alerts).

## Silent-failure alerts

The Cloud Monitoring email fires only when an execution fails. Two quiet failure modes are covered separately, both delivered to the operator's ntfy topic:

1. **Failed meetings or a cost-limit stop.** After each `run-scheduled`, the job sends an ntfy alert if the `classify-topics` step reports `failedMeetings`, `stoppedEarly` (per-run or monthly cost limit), or `stopped` (a fatal API or budget guard). The run still publishes, and the affected meetings keep rule tags. The alert lists counts and meeting ids and is also recorded in the summary's `alerts` field. Enable it by storing the topic URL, which is a secret:
   ```sh
   PROJECT_ID=<project-id> scripts/deploy/gcp-ingestion.sh set-secret ntfy-url   # hidden prompt
   PROJECT_ID=<project-id> scripts/deploy/gcp-ingestion.sh --yes --skip-build    # attach it to the job
   ```
2. **A scheduler that is paused or not firing.** The site publishes `/status.json` with the export's `generatedAt`, and the footer shows "Meeting data updated … (N hours ago)". The GitHub Actions workflow [`freshness.yml`](../../.github/workflows/freshness.yml) fetches it every 3 hours and sends a high-priority ntfy alert when the data is more than 14 hours old (two missed 6-hour runs plus build time) or the file cannot be fetched. Because it checks the published site, it also catches a broken deploy hook or Vercel build. Add the repository secrets `NTFY_URL` (and `NTFY_TOKEN` if the topic needs one) under Settings, then Secrets and variables, then Actions. Without `NTFY_URL` the workflow just fails and GitHub emails the repository owner.

A scheduler paused by the budget guard also trips the freshness alert after about 14 hours. That is expected: resume it as described below. GitHub disables scheduled workflows after 60 days without repository activity, so a long quiet period needs a manual re-enable.

## Pause and resume ingestion

```sh
gcloud scheduler jobs pause  tahoe-ingest-every-6h --location=us-west1 --project=<project-id>
gcloud scheduler jobs resume tahoe-ingest-every-6h --location=us-west1 --project=<project-id>
```

The public site keeps serving its last build while the scheduler is paused.

## Spend limit and the budget guard

Google budgets do not cap spending. They only send notifications. The project therefore has two layers:

1. **Emails.** The $5 monthly budget emails billing administrators, and `ALERT_EMAIL` when it is set, at 50%, 90%, and 100% of actual spend.
2. **Budget guard.** Cloud Billing sends the budget's status to the `tahoe-budget-alerts` topic several times a day. A push subscription forwards each message to the private `tahoe-budget-guard` Cloud Run service ([source](../../scripts/deploy/budget-guard/main.py), standard library only). When `costAmount >= budgetAmount` for this budget, the service pauses the ingestion scheduler, which stops all automatic runs. The guard does **not** disable project billing, because that would shut down and could delete resources. Manual `run-now` executions remain possible, so watch spending if you run the job by hand.

This is a safety stop, not an exact cap. Billing data lags actual usage by hours (sometimes longer), and the first notification after setup can take several hours. Charges already accrued, and any execution already running, continue. Pub/Sub delivers at least once, and the guard ignores duplicates because pausing an already paused job changes nothing.

Each decision appears in the guard's logs:

```sh
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="tahoe-budget-guard"' \
  --project=<project-id> --freshness=2d --format='value(jsonPayload.message,jsonPayload.cost,jsonPayload.budgetAmount)'
```

**Resuming after the guard trips.** Find out what spent the money first, using the Billing reports for this project. Then do one of the following:

- Wait for the new calendar month. The budget resets, and you resume the scheduler with the command above.
- Raise the limit deliberately (`BUDGET_AMOUNT=<n> scripts/deploy/gcp-ingestion.sh --yes --skip-build`), then resume. If you resume while the month's cost is still at or above the budget, the guard pauses the scheduler again on its next notification.

To test the guard without waiting for a real overrun, publish a synthetic notification:

```sh
gcloud pubsub topics publish tahoe-budget-alerts --project=<project-id> \
  --message='{"budgetDisplayName":"tahoe-public-meetings-monthly","costAmount":5.01,"budgetAmount":5,"currencyCode":"USD"}'
```

This pauses the scheduler, so resume it afterward. If the push fails with an authentication error in a project created before April 2021, grant the Pub/Sub service agent `roles/iam.serviceAccountTokenCreator` on `tahoe-budget-guard`. Set `BUDGET_GUARD=0` to deploy without the guard (emails only).

## Rollback

- **Website:** Vercel keeps previous deployments. Promote or instant-rollback to a known-good deployment in the dashboard (`vercel rollback --scope <designated-scope>`). A failed build leaves the current deployment live.
- **Data:** each run only appends a validated row to `public_exports` and keeps the latest 30, so an earlier export remains available for the site build to use. An invalid export is never stored or published.
- **Database unavailable:** if a Vercel build cannot read `public_exports` (for example, the Prisma operations quota is used up), it reuses the export the live site serves at `/data/public-export.json`, so code and UI changes still deploy with the last published data. The build log says so, and `/status.json` keeps that export's `generatedAt`, so the freshness alert still reports the stale data. Set `TAHOE_FALLBACK_EXPORT_URL` to read a different deployment's copy. The build fails only when neither source works.
- **Job image:** redeploy an earlier tag (see above). To stop ingestion entirely, pause the scheduler.

## Estimated monthly cost

These are list prices checked on October 20, 2026 for `us-west1` (a Tier 1 region). Free tiers are counted per billing account where noted. Recheck the linked pages before relying on them.

| Component | Expected use | Estimate |
| --- | --- | --- |
| [Cloud Run](https://cloud.google.com/run/pricing) jobs | About 120 runs per month. Assuming 10 minutes each at 1 vCPU and 2 GiB, that is 72k vCPU-s and 144k GiB-s, inside the 240k vCPU-s and 450k GiB-s monthly free tier | $0. Even if every run hit the 60-minute timeout, the cost would be about $4.30 |
| Cloud Run budget guard service | A few requests per day, scales to zero | $0 |
| [Artifact Registry](https://cloud.google.com/artifact-registry/pricing) | 3 versions per image with shared base layers, within the 0.5 GB free tier | $0 to $0.05 |
| [Cloud Scheduler](https://cloud.google.com/scheduler/pricing) | 1 job, with 3 free per billing account | $0 |
| [Secret Manager](https://cloud.google.com/secret-manager/pricing) | 3 active versions (6 free) and about 360 accesses (10k free) | $0 |
| [Pub/Sub](https://cloud.google.com/pubsub/pricing) | Budget notifications, well under 1 MB | $0 |
| [Cloud Logging and Monitoring](https://cloud.google.com/stackdriver/pricing) | Logs far below the 50 GiB per project free allotment. Alerting is charged at $0.10 per condition per month, starting no earlier than May 2026 | about $0.10 |
| Budgets, email notification channels | | $0 |
| Cloud Build (only with `--cloud-build`) | A few builds per month within the free build minutes | $0 |
| **Google total** | | **about $0.10 to $0.20 per month** |

Outside Google, Prisma Postgres runs on the Free plan and Vercel on the plan the designated scope already uses. Anthropic usage is about $0.02 per classification run plus a few cents for new item summaries, limited by the job's `--max-cost`, `--summary-max-cost`, and `--monthly-budget` guards and the Anthropic console limits ([decision 0011](../decisions/0011-claude-for-automated-analysis.md)).

### Prisma Postgres operations

The Free plan allows **2 GB of storage and 200,000 operations per month** for the whole Prisma workspace. Queries are blocked once the quota is used, until the start of the next calendar month. The Vercel integration page shows older plan text (100K operations, 500 MB); the Prisma Console figure is the one enforced. Every SQL statement sent over the direct connection is one operation, so the job's query count is a budget ([decision 0020](../decisions/0020-database-operation-budget.md)):

| Source | Operations |
| --- | --- |
| Scheduled run | About 260 (measured 2026-10-27; `BEGIN`/`COMMIT` appear not to count). Before October 24, 2026 a run used about 8,700 |
| Claude classification | About 2 per classified meeting, only when items are new or changed. A topic catalog or prompt edit reclassifies every meeting (about 220 for the October 2026 archive; [decision 0020](../decisions/0020-database-operation-budget.md)) |
| Vercel build | 1 (reads the newest export) |
| Feedback submission | 3 |
| Page views | 0 (the site is static) |

At four runs a day the job should use about 32,000 operations a month, plus classification of new items. Other databases in the same Prisma workspace share the quota. Check usage with the Prisma CLI after `npx prisma auth login`. The database is in the Prisma project `tahoe-public-meetings`:

```sh
npx prisma postgres usage tahoe-public-meetings-db --project tahoe-public-meetings          # month to date
npx prisma postgres usage tahoe-public-meetings-db --project tahoe-public-meetings \
  --from 2026-11-02T00:55:00Z --to 2026-11-02T01:10:00Z                                  # around one run
```

The period always ends at the end of the `--to` day, so a window that starts just before a run and ends on the same day measures that run plus anything later that day. Storage is far from its limit (0.06 GiB in October 2026); the job keeps only the latest 5 exports (about 21 MB each). See [storage monitoring](../operations.md#check-database-storage).

## Staff report links and item summaries

The image built from the commit that adds [decision 0024](../decisions/0024-verbatim-item-summaries.md) runs two more steps after `participation`; no new secret or environment variable is needed:

- `staff-reports` scans at most 6 meetings per run (`--staff-report-meetings`; 0 disables it), nearest to today first, so the first runs after deployment work through the archive over a few days. It downloads South Lake Tahoe executive summaries, Lahontan item reports, and Municode packets temporarily (in the run's scratch directory) and writes one statement per meeting. Packets already stored (TTD) are read from the database.
- `summarize-items` needs the same Claude credentials as `classify-topics` and is limited by `--summary-max-cost` ($0.50 per run) and the shared `--monthly-budget`. A backfill of a few hundred items costs well under $1; afterwards a run costs cents.

After deploying, check one run's summary line: `staff-reports.detail.itemsMapped`, `summarize-items.detail.published`, `validationFailed`, and `costUsd`. A stopped summary step sends the same ntfy alert as classification.

## Previews from notices and planning calendars

The image built from the commit that wires IDEA-018 runs three more steps; no new secret or environment variable is needed:

- `notices`, after `comment-periods`, reads each tracked comment period notice whose text changed with Claude (Haiku, then Sonnet), under the same credentials and budget guards as `classify-topics`. An unchanged notice costs one query. `one-offs` now runs after it, so a configured one-off with the same ID overrides extracted fields.
- `planning-calendars`, after `staff-reports`, reads new agenda planning calendars: South Lake Tahoe's from the attachment list the staff report step stores (one temporary PDF download per new calendar, at most `--max-calendars` (4) per run; 0 disables the step), TTD's from packet pages already stored. Unchanged calendars cost three queries and no download. A calendar that fails validation publishes nothing, keeps the previous calendar's previews, and appears in the run summary's `failures`; a calendar redesign stops that agency's previews until its format is updated.
- `classify-subjects` tags new preview subjects with Claude, under the same guards.

After deploying, check one run's summary line: `planning-calendars.detail.calendarsRead`, `rejected`, and `notices.detail.eventsRejected`.

## Claude credentials: workload identity federation

The job calls the Anthropic API without an API key. Its Google service account (`tahoe-ingest-job@<project>.iam.gserviceaccount.com`) fetches a Google ID token from the Cloud Run metadata server (audience `https://api.anthropic.com`, `format=full`) and the Anthropic SDK exchanges it for a short-lived token on every refresh.

One-time setup in the Claude Console, under the project's workspace:

1. **Workspace spend limit.** Settings → Workspaces → the workspace → **Spend limits**: $5/month. The job's own `--monthly-budget` guard is also $5.
2. **Settings → Workload identity → Connect workload → Google Cloud.** Issuer `https://accounts.google.com` (JWKS discovery). Service account with the developer organization role, a member of the workspace. Rule: audience `https://api.anthropic.com`; claims `sub` = the job service account's unique ID (`gcloud iam service-accounts describe <email> --format='value(uniqueId)'`) and `email` = its address; the single workspace; token lifetime 600 s. Never use a `subject_prefix` ending in `*` for Google tokens.
3. Pass the IDs (none are secret) when running the script; they become job environment variables:

```sh
ANTHROPIC_FEDERATION_RULE_ID=fdrl_... ANTHROPIC_ORGANIZATION_ID=<org uuid> \
ANTHROPIC_SERVICE_ACCOUNT_ID=svac_... ANTHROPIC_WORKSPACE_ID=wrkspc_... \
PROJECT_ID=<project-id> scripts/deploy/gcp-ingestion.sh --skip-build
```

The job also sets `TAHOE_IDENTITY_TOKEN_SOURCE=gcp-metadata`. Do not set `ANTHROPIC_API_KEY` on the job; it would take precedence over federation. Without the IDs, `classify-topics` is skipped and rule tags remain.
