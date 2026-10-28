# Budget and operations

Confirmed ceiling: **less than $20/month**, funded and operated by the maintainer. Revised 2026-10-28 from two weeks of production runs; earlier allocation targets (before services were chosen) are replaced by the estimate below.

## Expected monthly cost

| Item | Expected | Bad month | Basis |
| --- | ---: | ---: | --- |
| Domain (`tahoepublicmeetings.org`) | $1.67 | $1.67 | $20/year renewal |
| Claude API (classification, participation, summaries) | $0.25–1.50 | $5.00 | Measured costs below; capped by the $5 workspace limit and the job's `--monthly-budget` |
| Google Cloud (Cloud Run job, Scheduler, Artifact Registry, Secret Manager, logging, budget guard) | $0 | $5.00 | Inside free allowances at current run length; capped by the $5 budget guard |
| Vercel Hobby (static site, feedback function, 4 builds/day) | $0 | $0 | Far below Hobby allowances |
| Prisma Postgres Free | $0 | $0 | About 43,000 of 200,000 operations a month after [decision 0020](../decisions/0020-database-operation-budget.md); quota exhaustion stops the job rather than billing |
| GitHub Actions freshness check | $0 | $0 | Public repository; standard runners are free |
| ntfy alerts | $0 | $0 | A few messages a day at most |
| **Total** | **about $2–3** | **about $12** | |

The bad-month column is what the caps allow, not a forecast. Reaching it would mean a runaway loop or repeated full reprocessing that the guards then stop.

## Measured usage

- **Cloud Run.** Ordinary scheduled runs take 5–8 minutes on 1 vCPU and 2 GiB; four a day is about 120 runs, roughly 50,000 vCPU-seconds and 100,000 GiB-seconds a month. That is about a fifth of the published Cloud Run free allowance, which is shared by every project on the same billing account. Occasional 25-minute runs (first run, backfills, full reclassification) add little. Even with no free allowance, a month at this length would cost on the order of $1.
- **Artifact Registry.** About 77 MB per ingestion image; the cleanup policy keeps a few versions, under the 0.5 GB free storage.
- **Cloud Scheduler and Secret Manager.** One scheduler job and three secrets, within the free counts.
- **Claude.** Classifying all 1,738 first-run items cost $0.11 on Haiku 5.5; an incremental run with new items costs about $0.02. Participation details cost about $0.0006 per meeting ([decision 0021](../decisions/0021-participation-details-from-agendas.md)); item summaries about $0.0015 per item, $0.10–0.25 a month ([decision 0024](../decisions/0024-verbatim-item-summaries.md)). A topic catalog edit reclassifies the whole archive, about $0.10–0.20 each as the archive grows.
- **Prisma Postgres.** October exceeded the free quota (about 8,700 statements per run before the rewrite). The rewritten job sends about 240 statements per run, plus about 120 transaction statements if counted.

## Risks that would change the estimate

| Trigger | Effect |
| --- | --- |
| Prisma Postgres quota outgrown (more agencies, more frequent runs, repeated backfills) | Starter plan, $10/month: total about $12–14 |
| Vercel Hobby no longer eligible (organizational ownership, donations, or other commercial use) | Pro plan, $20/month per member: exceeds the ceiling, so the hosting choice would need revisiting |
| Larger AI scope (Sonnet as the default, whole-packet summaries, more agencies) | AI could reach the $5 cap; the Message Batches API halves the cost if delay is acceptable |
| Run length grows several-fold (OCR, many more sources) | Cloud Run could leave the free allowance; a few dollars a month at most before the budget guard pauses the scheduler |
| Billing country | Taxes and currency conversion may apply on top of list prices |

## Controls

Use provider hard caps where supported; a dashboard warning alone does not enforce a budget. Current controls: the $5 Claude workspace limit and the job's own cost limits, the $5 Google budget whose guard pauses the scheduler (budget data lags, so this is a safety stop, not an exact cap), and the Prisma Free plan, which blocks queries rather than billing.

Cache extraction and model results by content hash and prompt/model version. Process only changed material; avoid repeatedly summarizing full historical packets. Apply bounded retries and token limits.

When processing is paused, retain source-backed titles and links, show degraded freshness, and notify the operator. Do not pretend summaries are up to date.

Check actual spend about once a month: the Google billing report, the Claude console, the `ai_usage` table, and Prisma operations (see the [deployment runbook](../operations/deployment.md)).

## Selected services

Vercel, Prisma Postgres Free, Cloud Run Jobs, and Cloud Scheduler are accepted ([decision 0005](../decisions/0005-selected-stack.md)). No paid VPS, Firestore, Firebase Hosting, or permanent PDF archive is required. R2 was evaluated, not selected. Claude is the AI provider ([decision 0011](../decisions/0011-claude-for-automated-analysis.md)). The [free-tier assessment](../architecture/free-tier-sizing.md) holds the original sizing research.
