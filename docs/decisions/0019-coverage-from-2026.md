# 0019: Coverage from January 1, 2026, kept forever

Status: accepted
Date: 2026-10-20

## Context
The first production run's history was uneven: agencies listed in the October 2026 research files reached back several months, while agencies covered only by live discovery started about 45 days back. The 6-hourly job's discovery reads only each source's newest listings, so it cannot build history.

## Decision
- Coverage starts on **January 1, 2026** for every followed agency. Earlier meetings are out of scope unless a later decision extends it.
- Stored meetings are **kept forever**; nothing is pruned or hidden because of age.
- History is filled by a separate, resumable one-time **backfill** command run as its own Cloud Run job. It runs first from July 1, 2026 to measure duration and size, then for the whole year if those are acceptable.
- The 6-hourly job only keeps up with new and changed meetings and never captures meetings dated before the coverage start.

## Consequences
Database and export size grow with the archive; watch Prisma storage and the export the Vercel build reads ([IDEA-014](../ideas/IDEA-014-export-slimming.md)). Where an official source cannot reach back to January, the backfill reports the earliest date it reached, and coverage pages should say so.
