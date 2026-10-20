# Concrete service options

**Historical research / sizing:** service selection is now accepted in [decision 0005](../decisions/0005-selected-stack.md). Implement Vercel, Prisma Postgres, Cloud Run Jobs, and Cloud Scheduler. Earlier recommendations below are comparison context. Apply [decision 0004](../decisions/0004-source-first-storage.md): no permanent PDF archive initially.

Status: proposed options, not accepted choices. Storage proposals below predate [decision 0004](../decisions/0004-source-first-storage.md): initial versions retain extracted text and application backups, not permanent PDFs. Researched 2026-10-19. No services provisioned. Local development and deployment do not require Git.

## Shared application stack proposal

- Python worker, reusing suitable TTD code; Poppler `pdftotext` for extraction and OCR fallback when necessary.
- Container image for reproducible native PDF dependencies. One scheduled pipeline with persisted job state and a lease preventing overlapping publication runs.
- Astro + TypeScript for public pages. Export versioned public JSON from the database, build HTML, and deploy the completed site. The browser never receives database credentials.
- Structured relational records; documents and extracted text stored separately. Keep human corrections and perspective notes separate from generated records.
- Model provider behind a small interface: input text/page references → structured summary and topic assignments. Persist model ID, prompt/schema version, usage, and source hashes.
- Local commands for corrections and commentary initially. No public admin backend, accounts, message queue, or vector database required for MVP.

Astro supports static prerendering; this is a good match for shared content updated in batches. [Astro documentation](https://v6.docs.astro.build/en/guides/on-demand-rendering/).

## Option A: small server, fewer managed components

| Concern | Proposed choice |
| --- | --- |
| Processing compute | Hetzner CX23 in a European region, Docker |
| Scheduling | systemd timer on the server |
| Database | SQLite on persistent local disk, one publishing writer |
| Website | Astro output on Cloudflare Pages |
| Documents | Local cache plus Cloudflare R2 canonical archive |
| Backups | Consistent SQLite snapshots in private R2 storage with retention |
| Secrets | Restricted environment file on server, outside source tree |
| Operations | systemd logs, job status table, external missed-run heartbeat |

The verified CX23 list price is $6.49/month excluding IPv4 and VAT for Germany/Finland; do not apply that price to US regions. [Hetzner pricing update](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/).

Benefit: direct reuse of filesystem-based Python code, few deployment concepts, predictable compute bill, easy local reproduction. Cost: the maintainer owns OS patching, disk capacity, recovery, and server availability. Public static pages remain available when the processing host is down.

Budget target: roughly $12–19/month total under modest processing, storage, and traffic, including an AI allocation, domain amortization, and contingency. This is an estimate, not a measured quote; taxes/addressing may require adjustment.

## Option B: managed scheduled containers

| Concern | Proposed choice |
| --- | --- |
| Processing compute | Google Cloud Run Jobs; Python/PDF tools in a container |
| Scheduling | Cloud Scheduler invokes the job with a service account |
| Container registry | Google Artifact Registry, retain only necessary image versions |
| Database | Neon Postgres, initially Free if measured usage fits |
| Website | Astro output on Cloudflare Pages |
| Documents and backups | Cloudflare R2; private database exports separate from public material |
| Secrets | Google Secret Manager; job service account gets required secrets only |
| Operations | Cloud Logging plus persisted source freshness/job status |

Cloud Run Jobs have a published monthly free allowance of 240,000 vCPU-seconds and 450,000 GiB-seconds, based on us-central1 pricing. These allowances are conditional on billing-account usage and region; registry, network, logging, secrets, and scheduling costs also need accounting. [Cloud Run pricing](https://cloud.google.com/run/pricing), [Scheduler pricing](https://cloud.google.com/scheduler/pricing).

Neon's Free plan currently lists 100 CU-hours/month and 1 GB database storage per project. Keep PDFs and bulk page text in R2, and query Postgres during batch publication rather than every public page view. Export backups independently; a free-tier history window is not a full backup policy. [Neon pricing](https://neon.com/pricing).

Benefit: no Linux host to patch; production scheduling does not depend on a personal computer. Cost: more accounts, permissions, networking, and usage quotas. Job-local storage is scratch space, not the authoritative database. Use a database lease to prevent concurrent scheduled/manual runs.

Budget target: $5–15/month at small measured usage with free allowances available, plus headroom up to $19. Not a guarantee; benchmark a representative month and include registry/transfer costs. This is the provisional preference if avoiding server maintenance matters most.

## Common hosting/storage choices

Cloudflare Pages supports direct uploads of prebuilt assets, so local CLI deployment and job-triggered publication can work without a Git repository. Start with a provider subdomain if a domain is not yet chosen. [Direct Upload documentation](https://developers.cloudflare.com/pages/get-started/direct-upload/).

R2 Standard includes 10 GB-month of storage, 1 million Class A operations, and 10 million Class B operations monthly; direct egress is free. Larger archives and operations above allowances are billed. Keep large PDFs out of the static site bundle. [R2 pricing](https://developers.cloudflare.com/r2/pricing/).

## AI candidate and budget framework

Benchmark Gemini 3.1 Flash-Lite as an initial low-cost candidate, not an accepted quality choice. Its standard text rates are $0.25 per million input tokens and $1.50 per million output tokens including thinking. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

Illustrative monthly usage of 5 million input tokens and 0.5 million output tokens costs $2 at those rates. This excludes retries, image/OCR processing, other models, and optional tools. Actual packet volumes have not been measured. Reserve $5–7 for analysis rather than treating $2 as a forecast.

Evaluate source fidelity, missed topic matches, handling of proposed versus adopted actions, and cost on the same representative agenda items. Choose a stronger model if the inexpensive candidate fails. AI provider preference is independent of hosting provider; keep provider-specific calls isolated. No model training or GPU hosting is proposed.

## Publication sequence

1. Acquire a database lease and discover source changes.
2. Save immutable document versions; extract and analyze only changed content.
3. Commit validated records, preserving independent human edits.
4. Export a consistent public snapshot and build the site in a temporary directory.
5. Deploy the complete output; mark its publication version successful only after deployment succeeds.
6. Release the lease and record usage, failures, and last-success timestamps.

A failed build leaves the old site live. A later run retries publication without repeating successful AI calls. Rebuild at least daily even without source changes so upcoming/recent grouping does not become stale. Initial source checks every six hours are a proposal, subject to source constraints and desired freshness.

## Preferences that decide the choice

1. Managed services versus responsibility for one Linux host.
2. Existing cloud accounts/credits, preferred AI provider, and any hosting-region preference.
3. Python ingestion plus Astro/TypeScript website versus an all-Python application.
4. Comfort with CLI/file-based editing initially versus a browser-based admin UI at launch.

If an all-Python stack is preferred, use generated templates for the static MVP or a Python web framework when a real admin UI becomes necessary. If a specific cloud is preferred, compare an equivalent architecture on that cloud before final selection. The recommendation is not tied to Git-based deployment.
