# Google Cloud option

**Historical research / sizing:** service selection is now accepted in [decision 0005](../decisions/0005-selected-stack.md). Implement Vercel, Prisma Postgres, Cloud Run Jobs, and Cloud Scheduler. Earlier recommendations below are comparison context. Apply [decision 0004](../decisions/0004-source-first-storage.md): no permanent PDF archive initially.

Status: candidate, not accepted. The later [storage decision](../decisions/0004-source-first-storage.md) removes permanent PDF archival from the initial scope; any object storage is for extracted text and application backups. Researched 2026-10-19 using the workload in [free-tier sizing](free-tier-sizing.md).

## Proposed mostly-Google deployment

| Component | Service |
| --- | --- |
| Static public website | Firebase Hosting (not Firebase App Hosting) |
| Scheduled Python/PDF processing | Cloud Run Jobs |
| Schedule | Cloud Scheduler |
| Original PDFs, extracted text, exports | Regional Cloud Storage Standard bucket |
| Database, if keeping everything on Google | Firestore Standard |
| Database, if preferring SQL | Prisma Postgres Free outside Google |
| Container images | Artifact Registry with image cleanup |
| Credentials | Secret Manager and service-account IAM |
| Monitoring | Cloud Logging and per-source last-success records |
| AI | Gemini Developer API initially; billed separately from infrastructure |

Use a single eligible region such as us-central1 for the worker and bucket. Jobs write structured records, export a public snapshot, build the static site, and deploy to Firebase Hosting. The public site needs no direct database access. Keep deployment credentials out of published artifacts. Local CLI/container deployment works without Git.

## Free-tier fit

- Cloud Run Jobs: 240,000 vCPU-seconds and 450,000 GiB-seconds monthly free allowances based on us-central1 pricing. Four 10-minute runs/day at 1 vCPU/1 GiB would consume about 72,000 of each per 30-day month, before retries. This is a sizing example, not a measured runtime. [Pricing](https://cloud.google.com/run/pricing).
- Cloud Scheduler: three job definitions free per billing account, not three executions. One schedule can check all agencies every few hours. [Pricing](https://cloud.google.com/scheduler/pricing).
- Cloud Storage: 5 GB-month regional storage, 5,000 Class A and 50,000 Class B operations, with eligibility restricted to us-west1, us-central1, and us-east1. The archive eventually grows beyond the allowance. Artifact Registry separately includes only 0.5 GB, so retained container images may create a small bill. [Free program](https://docs.cloud.google.com/free/docs/free-cloud-features).
- Firestore Standard: one free database/project, 1 GiB storage, 50,000 document reads/day and 20,000 writes/day. Backups/PITR are not included in free usage. This workload plausibly fits, especially with static publication. [Firestore pricing](https://firebase.google.com/docs/firestore/pricing).
- Firebase Hosting: current detailed quota documentation lists 10 GB hosting storage and 10 GB/month transfer. On Blaze, transfer beyond that is $0.15/GB. At the earlier illustrative 10,833 page views × 1 MB, transfer is about 10.8 GB/month before PDFs/bots; lean pages can be well below this. This is less transfer headroom than Vercel Hobby. Retained hosting releases consume storage. [Hosting quotas](https://firebase.google.com/docs/hosting/usage-quotas-pricing).

Free quotas are not proof of a zero-dollar bill. Include network, image registry, logging, secrets, backups, AI, and archive growth in the measurements. Budget $2–5 for AI and a few dollars of infrastructure headroom rather than optimizing every component for exactly $0.

## Main tradeoff: database

Cloud SQL's PostgreSQL free offering is a 30-day trial, not the ongoing free Postgres option we need. [Cloud SQL trial](https://docs.cloud.google.com/sql/docs/postgres/free-trial-instance).

Firestore can represent agencies, meetings, items, topic IDs, and versions as documents. Topic/date filters suit this model, but cross-agency joins, reporting, and consistency across duplicated fields require more application work. Choose it if fewer providers matters more than SQL portability. Prefer Prisma Postgres if SQL is important; Google compute does not require a Google database.

An e2-micro VM is another free-tier compute option but reintroduces patching, disk management, and networking costs. It is not the recommended route merely to obtain self-hosted SQL.

## Ongoing allowances versus introductory credits

The $300/90-day new-customer trial is distinct from recurring free allowances. Size the production system without relying on temporary credits. A billing-enabled project can incur charges beyond allowances. Google explicitly excludes Gemini API in AI Studio from the $300 credit, so do not treat that credit as the model budget. [Free program](https://docs.cloud.google.com/free/docs/free-cloud-features).

## Preference to resolve

Mostly Google with Firestore, or Google processing/storage plus Prisma Postgres? Vercel can remain the public host in either case. No vendor decision is accepted yet.
