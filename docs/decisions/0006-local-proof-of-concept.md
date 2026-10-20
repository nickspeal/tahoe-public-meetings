# 0006: Local proof-of-concept implementation defaults

Status: implementation defaults for the authorized local prototype, subject to beta evaluation
Date: 2026-10-19

The maintainer requested a functional local proof of concept with regular commits directly to `main`. No service provisioning or cloud deployment is authorized by this work. (The local-only Git constraint was lifted by [decision 0016](0016-public-open-source-repository.md).)

- Use Astro and TypeScript for static public pages. A versioned JSON export separates the website from Python and database credentials.
- Use a dedicated local PostgreSQL cluster (or the supplied Docker Compose database). Prisma Postgres remains the selected hosted database; Prisma's ORM is not required to use that service and is not introduced for the Python worker.
- Scope source samples to the TTD main board, South Lake Tahoe City Council, and Truckee Town Council. This is prototype coverage, not a commitment to all committees or a comprehensive live feed.
- Preserve genuine source dates and label fixture coverage. Do not move historical meetings into the future to fill an upcoming list.
- Use a deterministic extractive summarizer with exact source quotations and page references by default. It runs without API credentials or recurring costs and is labeled as an automated excerpt. AI provider selection, quality benchmarks, and model cost measurement remain open; this default does not claim generative AI quality or human review.
- Store page text and source metadata in PostgreSQL. Downloaded PDFs are temporary; preserve the existing TTD archive untouched.
- Expose operator changes through a local CLI. No unauthenticated web write endpoint is added.

These defaults implement the local workflow without changing decisions 0002–0005 or claiming public launch readiness. PostgreSQL lifecycle tooling follows the [initdb](https://www.postgresql.org/docs/current/app-initdb.html) and [pg_ctl](https://www.postgresql.org/docs/current/app-pg-ctl.html) documentation.
