# Budget and operations

Confirmed ceiling: **less than $20/month**, funded and operated by the maintainer. The figures below are allocation targets, not researched provider quotes or a claim that a deployment fits them yet.

| Category | Proposed monthly envelope |
| --- | ---: |
| Hosting, database, document storage, backups | $5 |
| AI summarization and topic classification | $7 |
| Additional processing/storage contingency | $3 |
| Domain amortization and contingency | $4 |
| Total target | $19 |

Before selecting providers, verify current pricing, free-tier limits, scheduled-job limits,  and billing controls. Include domain renewal, storage growth, retries, OCR, and transfer. Do not assume a chat subscription includes API processing.

Measure three representative agency packets, then estimate monthly document/token volume, page traffic, storage growth, and worst-case retries. Record actual usage during beta. If the result exceeds the envelope, reduce processing costs or propose a scope adjustment before deploying paid services.

Cache extraction and model results by content hash and prompt/model version. Process only changed material; avoid repeatedly summarizing full historical packets. Apply bounded retries and token limits. Track projected and actual spend across services. Reserve funds for fixed bills, and refuse new paid processing before its estimated maximum could exceed the remaining allocation. Use provider hard caps where supported; a dashboard warning alone does not enforce a budget.

When processing is paused, retain source-backed titles/links, show degraded freshness, and notify the operator. Do not pretend summaries or alerts are up to date. A regularly updated website must have reliable scheduled execution; running on the maintainer's machine is only viable if its availability is acceptable.

## Workload-based refinement

The maintainer estimates 5 meetings and 300 total PDF pages per week, plus 100 readers visiting 5 times weekly. The [free-tier assessment](../architecture/free-tier-sizing.md) now favors evaluating Vercel Hobby + Prisma Postgres Free + R2, with $2–5 reserved for AI. Earlier category allocations are envelopes, not required expenses. Benchmark burst processing before selecting the worker host.

## Selected services

Vercel, Prisma Postgres Free where usage fits, Cloud Run Jobs, and Cloud Scheduler are accepted. No paid VPS, Firestore, Firebase Hosting, or permanent PDF archive is required. R2 was evaluated, not selected. Extracted text can start in Postgres if quotas permit; application backups may require small object storage. Claude is the AI provider ([decision 0011](../decisions/0011-claude-for-automated-analysis.md)). Haiku 5.5 agenda-level classification is estimated at about $0.02/month for 22 meetings ([evaluation](../research/topic-classification-evaluation-2026-10-20.md)); measure actual usage before relying on it. Measure actual Google auxiliary costs and existing account quota usage before making a zero-cost claim.
