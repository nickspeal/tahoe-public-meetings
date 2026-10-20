# Free-tier sizing

**Historical research / sizing:** service selection is now accepted in [decision 0005](../decisions/0005-selected-stack.md). Implement Vercel, Prisma Postgres, Cloud Run Jobs, and Cloud Scheduler. Earlier recommendations below are comparison context. Apply [decision 0004](../decisions/0004-source-first-storage.md): no permanent PDF archive initially.

Researched 2026-10-19. Estimates, not measured load tests. Vercel and Prisma are familiar to the maintainer; this is a proposed refinement, not an accepted vendor decision.

## Workload supplied by the maintainer

- 5 meetings/week; 1–100 PDFs per meeting, with about 300 total PDF pages/week across the workload.
- 100 users visiting 5 times/week.
- At 52/12 weeks/month: approximately 22 meetings, 1,300 PDF pages, and 2,167 visits monthly.
- Visits are not page views. Assuming 5 pages/visit gives about 10,833 page views/month.

Document count and page count are independent sizing inputs. The upper document-count bound (500/week) cannot coincide with 300 total pages if every PDF has at least one page; treat the file range as burst guidance, not a simultaneous average.

## Website: Vercel Hobby

Current allowances include 100 GB Fast Data Transfer, 1M CDN requests, 1M function invocations, 4 active CPU-hours and 360 GB-hours provisioned memory. At an illustrative 1 MB transferred per page view, monthly page traffic would be about 10.8 GB, excluding PDF downloads, bots and unusually large assets. Static generation/caching should keep server and database work low. Hobby is intended for personal noncommercial projects; confirm eligibility if monetization or organizational ownership changes.

Sources: [Hobby](https://vercel.com/docs/plans/hobby), [plans](https://vercel.com/docs/plans).

## Database: Prisma Postgres Free

The live pricing page currently lists 200,000 operations/month and 2 GB storage. Older indexed results listed lower limits; use the live page and account console. Limits are shared within the workspace. With 5 database queries per illustrative page view, public reads would be about 54,167 operations/month before ingestion and administration. Cached or generated pages reduce that further. Store PDF bytes in object storage, batch writes, avoid per-item query loops, and keep logs on a retention policy.

Prisma Postgres supports ordinary Postgres clients, so a Python worker does not require Prisma ORM. Free has no automated backups; schedule exports. Exhausting query quota stops reads and writes until reset or upgrade.

Sources: [pricing](https://www.prisma.io/pricing), [Free-plan documentation](https://www.prisma.io/docs/full-stack-tutorial), [quota behavior](https://www.prisma.io/docs/postgres/faq).

## PDF archive: prefer R2 over Vercel Blob

R2 Standard includes 10 GB-month with operation allowances; Vercel Blob Hobby includes 1 GB-month and 10 GB transfer. PDFs vary enormously in byte size. At an assumed average 0.1–1 MB/page, 1,300 pages adds 0.13–1.3 GB/month, or 1.56–15.6 GB/year, before revisions, OCR derivatives, and backups. Therefore storage can eventually exceed a free tier even with flat traffic. R2 Standard paid storage is $0.015/GB-month beyond its free allowance; operations are separately metered. Keep originals content-addressed and avoid storing identical downloads repeatedly.

Sources: [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [Vercel Blob](https://vercel.com/docs/vercel-blob/usage-and-pricing).

## Processing: volume is small, per-run duration is the constraint

Vercel Hobby functions with Fluid Compute have a 300-second maximum and 2 GB memory. Native cron runs at most daily per job and has coarse timing. Small resumable batches may fit; a single invocation that downloads up to 100 documents, extracts them, calls AI, and publishes is not a reliable design. Vercel Workflows are another orchestration candidate; benchmark native dependencies and per-step limits before choosing it.

A Python container on Cloud Run Jobs remains a practical alternative for existing PDF tooling. A hypothetical 30 runs/month × 10 minutes × 1 vCPU/1 GiB uses 18,000 vCPU-seconds and 18,000 GiB-seconds, below the published 240,000/450,000 monthly allowances, assuming those allowances are available. Actual memory, job duration, OCR, network transfer, registry, scheduling, and logs must be measured. Free compute allowance does not mean every associated service is free.

Sources: [function limits](https://vercel.com/docs/functions/limitations), [cron limits](https://vercel.com/docs/cron-jobs/usage-and-pricing), [Cloud Run pricing](https://cloud.google.com/run/pricing).

## AI

Illustrative extracted-text sizing: 500–1,000 input tokens/page gives 0.65–1.3M input tokens/month for a single pass. Allow 3× for prompts, classification, summaries and retries: 1.95–3.9M input tokens. With an assumed 0.1–0.3M billable output tokens and Gemini 3.1 Flash-Lite's $0.25/M input and $1.50/M output, that is roughly $0.64–$1.43/month. This is a text-processing estimate; scanned images, stronger models, verbose reasoning, backfills, or repeated whole-packet context can change it substantially. Reserve $2–5 initially and measure quality and actual usage. Free model quotas may work for experiments, but reliable publication should not depend on them.

Source: [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

## Updated proposal

Vercel Hobby for the website, Prisma Postgres Free for structured data, R2 for documents/backups, and a paid low-cost AI API are a credible fit. Benchmark a large representative meeting before deciding whether processing runs in bounded Vercel jobs/workflows or a Cloud Run container. No paid VPS is required by the stated traffic or page volume.

Expected starting envelope: $0 hosting/database within quotas, $0 or small document-storage charges, roughly $2–5 reserved for AI, plus domain and any incidental job-hosting costs. This is a near-free design, not a guarantee of a permanently zero bill. Confirm existing account usage, sample PDF sizes, OCR frequency, source check cadence, and burst processing time before committing.

## Actual sample measurements supersede generic byte/page assumptions

See [document retention](document-retention.md): measured TTD and SLT ratios imply approximately 0.80–1.18 GB of PDFs per year at 300 pages/week. Larger packets can change the page-volume assumption itself. The earlier 0.1–1 MB/page range remains a generic sensitivity scenario, not the best estimate from the measured samples.
