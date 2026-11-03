# 0030: Roll out the ingestion image from GitHub Actions

Status: accepted (maintainer request, 2026-11-02)
Date: 2026-11-02
Related decisions: [0005 selected stack](0005-selected-stack.md), [0018 scheduled publication](0018-scheduled-publication.md), [0020 database operation budget](0020-database-operation-budget.md)

## Context
Worker changes reached production only when someone built and deployed the image from a local checkout. On 2026-11-02 ingestion resumed on an image four days and about 30 commits behind `main`, which had to be noticed and deployed by hand.

## Decision
- Every push to `main` that changes the image's inputs (`workers/ingestion/`, `packages/domain/`, `config/`, `.dockerignore`; until decision 0033 also `tests/fixtures/meetings.json`) runs the worker tests against a throwaway Postgres, builds the image, and points `tahoe-ingest` and `tahoe-backfill` at it by digest ([`deploy-ingestion.yml`](../../.github/workflows/deploy-ingestion.yml)).
- The workflow changes only the image. Job resources, secrets, environment, the scheduler, alerts, and the budget stay with the deploy script, run by an operator.
- GitHub authenticates with workload identity federation, without a stored key. The provider accepts only push and manual runs on `refs/heads/main` of this repository, matched by numeric repository and owner IDs. The service account `tahoe-github-deploy` can push to the image repository and holds `run.developer` on the two jobs only, plus `serviceAccountUser` on the job's runtime account. `scripts/deploy/gcp-ingestion.sh github-deploy` creates all of it.
- A rollout does not start a run. The next scheduled run uses the new image.

## Alternatives and consequences
- Cloud Build triggers on the repository: would keep credentials inside Google Cloud, but need a GitHub App connection and a second place to read build results. Rejected for now.
- Building on every push to `main`, including website-only changes: rebuilds identical images and shortens the rollback window. Rejected; the path filter follows the `.dockerignore` allowlist.
- The registry keeps the newest 3 images, so rollback by tag reaches about three worker commits back. A rollback by hand lasts only until the next worker change is pushed, so revert the bad commit too.
- A change that raises database operations or Claude cost per run, such as a topic catalog edit that reclassifies the archive (decision 0020), now reaches production at the next scheduled run, without a separate deploy step. Check the month's remaining operations before pushing such a change.

## Revisit when
- Pushes to `main` start needing a staged rollout or a run-before-promote check.
- The registry's free storage or GitHub Actions minutes become a constraint.
