# 0009: Configurable coverage and one-off meetings
Status: accepted
Date: 2026-10-20
Supersedes: topic selection and three-agency-only scope in earlier decisions/handoff

## Context

The maintainer requested editable topic/agency configuration, supplied a 41-row agency research CSV with potentially stale metadata, and requested a code-based way to add workshops and other meetings outside regular boards.

## Decision

Use six shared topics: Biking (including pedestrian and road repair items), Transit, Housing, Short Term Rentals, Environment, and Hot Topics. STR/VHR/vacation rental items also count as Housing. Hot Topics starts with the maintainer's named examples: Glyphosate, Incline Village Mobility Hub / 771 Southwood Blvd, Vacancy Tax, and Palisades redevelopment. It is an evolving interest watchlist; tagging does not assert verified turnout.

Keep topic metadata, title rules, and inclusion relationships in `packages/domain/topics.json`. Keep a followed-agency/body registry in `packages/domain/agencies.json`, preserving each of the 41 imported entries and retaining Truckee (42 entries total). The spreadsheet's schedules, notification details, and links are unverified historical research, separate from operational source settings. Registry selection expands desired coverage; automated discovery still has adapters for only the three existing agencies. Do not imply comprehensive live coverage for newly listed entries.

Use a checked-in `config/one-off-meetings.json` and local CLI import for one-off notices with stable IDs, official links, optional verified time, meeting-level topics, and existing status/history behavior. Agenda ingestion remains the same evidence-backed pipeline. No hypothetical example meeting is published.

## Alternatives and consequences

An authenticated admin UI is deferred unless frequent usage or additional admin tasks justify it. Config changes are reviewable through Git. Imported metadata needs verification and additional adapters before broad automatic ingestion; the recurring budget remains below $20/month. No new services, hosting, accounts, or scheduled jobs are provisioned by this change. Automatic summaries and separate the maintainer-authored perspective notes retain the existing editorial rules.

Removing an agency from followed coverage hides its exported records without deleting history. Retired topic overrides stay stored and are hidden publicly. One-off notices can match topic filters before an agenda is posted, and never invent agenda items or meeting times.

## Revisit when

The maintainer adjusts the watchlist, sources are verified, new adapters are built, or one-off/admin work becomes frequent enough to justify a protected interface.
