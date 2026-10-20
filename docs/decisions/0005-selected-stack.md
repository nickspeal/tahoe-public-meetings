# 0005: Vercel, Prisma Postgres, and Google processing

Status: accepted
Date: 2026-10-19

## Decision

- Public website: Vercel, under the project's designated account/scope. Never infer the target from the currently logged-in account.
- Structured database: Prisma Postgres, starting on Free if available quotas fit.
- Ingestion and analysis compute: Python container on Google Cloud Run Jobs.
- Scheduling: Google Cloud Scheduler.
- Storage: retain extracted text and source/version metadata; PDFs are temporary inputs, with official public links. Follow decision 0004.
- AI: low-cost external API, provider/model still subject to representative quality evaluation. Gemini is a candidate, not an accepted requirement. *Superseded 2026-10-20 by [decision 0011](0011-claude-for-automated-analysis.md): Claude is the default provider.*

This replaces the open vendor selection in the architecture comparison documents. Firestore, Firebase Hosting, Neon, and a VPS are alternatives that were considered, not the implementation direction.

## Rationale

The maintainer is familiar with Vercel and Prisma. Postgres fits the meeting/item/topic/version relationships. Cloud Run accommodates existing Python and native PDF tooling independently of web-request time limits. Infrastructure should fit free allowances where possible; the total recurring budget remains below $20/month.

## Deployment identity

Before linking a local directory, creating a project, or deploying, verify the authenticated Vercel identity and selected ownership scope resolve to the designated scope. Use explicit scope selection in deployment tooling. Do not create a project under another account/team or reuse a copied `.vercel` project link without checking its owner. If the scope is inaccessible, report the mismatch and request the appropriate sign-in; do not silently substitute another account. This instruction does not itself request a deployment now.

Prisma workspace and Google project/billing account are not specified. Confirm these from the user's environment before provisioning; do not guess based on Vercel identity.

## Still open

Frontend framework (Astro/TypeScript proposed), AI provider/model, exact scheduler cadence, domain, extracted-text storage backend, license, and exact boards/committees beyond the named agencies. Keep these distinct from accepted vendor choices.
