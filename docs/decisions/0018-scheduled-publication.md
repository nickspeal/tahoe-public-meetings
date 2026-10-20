# 0018: Scheduled publication pipeline

Status: accepted
Date: 2026-10-20

## Context
The maintainer wants production data to come only from the scheduled pipeline, with no manual data upload, and to confirm the pipeline works by looking at the live site at tahoepublicmeetings.org. Recurring cost must stay under $20/month; the maintainer set $5/month limits on Google Cloud and Claude usage.

## Decision
- One Cloud Run job (`tahoe-ingest run-scheduled`) runs every 6 hours from Cloud Scheduler. It fills an empty Prisma Postgres database from official sources and tracked config, classifies topics, exports, validates, stores the export in a `public_exports` table (latest 5 kept), and calls a Vercel deploy hook.
- The Vercel project (root `apps/web`, connected to the GitHub repository) reads the newest `public_exports` row at build time. A Vercel build without a valid export fails, so the previous deployment stays live. Pushes to `main` rebuild with the latest stored export.
- Claude is called through the Anthropic API with workload identity federation: the job's Google service account identity is exchanged for short-lived Anthropic tokens, so no API key is stored. The workspace has a $5/month spend limit and the job has a $5 monthly budget guard.
- Google Cloud has a $5/month budget with alerts; at 100% a small budget guard pauses the scheduler (it never disables billing). Budget data lags, so this is a safety stop, not an exact cap.

## Consequences
The site is a static snapshot refreshed at least every 6 hours, which also keeps the Today marker current. A failed run leaves the last good site live and raises an alert. See the [deployment runbook](../operations/deployment.md).
