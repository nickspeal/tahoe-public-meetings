# IDEA-012: Coverage and one-off meeting administration

Status: parked
Stage: later, if frequent operator work justifies it

## Problem and audience

The maintainer needs to tune followed agencies/topics and add public workshops that fall outside recurring board meetings. Other admin tasks may eventually make direct config edits cumbersome.

## Smallest useful version

Shipped locally: version-controlled catalogs and a one-off notice manifest with a CLI import. Deferred: a protected admin page and login to manage these same records.

## Evidence and dependencies

The maintainer requested a code-change workflow on 2026-10-20 and suggested an admin page/login if usage becomes frequent or admin tasks grow. Remote writes require authentication and authorization. Keep recurring costs under $20/month.

## Acceptance criteria

If promoted, an authorized operator can add/update/reschedule/cancel a notice with stable identity, official provenance, optional verified time, and topic tags; preserve source material and editorial history. Ordinary visitors have no write access. Agency and topic settings should stay shared across ingestion and website views.

## Open questions

How often are notices added? Which additional admin tasks warrant a UI? Which authentication approach fits the existing stack and budget?

## Links and updates

[Decision 0009](../decisions/0009-configurable-coverage-and-one-off-meetings.md), [configuration guide](../../config/README.md).
