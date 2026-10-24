# 0020: Stay within the Prisma Postgres free operations quota

Status: accepted
Date: 2026-10-24

## Context
Prisma Postgres bills every SQL statement sent over a direct connection as one operation. The Free plan allows 200,000 operations per month across the workspace, and queries are blocked once the quota is used until it resets at the start of the next calendar month.

Four days after launch, the database had used 197,593 operations, 98.8% of the October quota. Each scheduled run sent about 8,700 statements (measured in Prisma's usage metrics for the October 24 19:00 UTC run), roughly 35,000 a day. Most of them were per-item lookups that grew with the archive: the export read each agenda item's topics, overrides, and Claude classification separately (about 5,500 statements), and reclassification read each item's tags (about 2,050). The storage estimate had assumed operations would stay far under the quota and was wrong. The website itself is static: page views send no queries; each Vercel build reads one row, and each feedback submission sends three statements.

## Decision
- Stay on the Prisma Postgres Free plan. The maintainer prefers spending well under the $20/month ceiling.
- Treat database statements as a budget. Read tables in bulk and group rows in Python instead of querying per item, per page, or per candidate, and skip writes that would leave a row unchanged.
- After the rewrite, a scheduled run sends about 240 statements, plus about 120 `BEGIN`/`COMMIT` statements if Prisma counts those, measured locally against a copy of production-sized data. The export and reclassification now use a fixed number of queries whatever the archive size; the remaining cost scales with the meetings captured again in a run (about 25), not with the archive. At four runs a day that is about 30,000–45,000 operations a month, 15–23% of the quota.
- Tests fail if the export or reclassification query count grows with the archive, or if re-ingesting an unchanged meeting rewrites its items or pages.
- The scheduler was paused on 2026-10-24 with 2,407 operations left for October. Runs resume on November 1, after the new image is deployed; at most a verification run happens before then.

## Alternatives and consequences
- Starter plan ($10/month, 1M operations): would have absorbed the old code at about 1.1M operations a month, with the cost growing as the archive grows. Rejected for now.
- Running less often: four runs a day now fit easily, so frequency stays at every 6 hours.
- Other personal projects in the same Prisma workspace share the quota. Keep an eye on the workspace total, not only this database's.

## Revisit when
- Monthly usage passes 100,000 operations (half the quota), or the per-run count from `prisma postgres usage` rises well above the measured figure.
- More agencies or a shorter schedule raise the number of meetings captured per run.
