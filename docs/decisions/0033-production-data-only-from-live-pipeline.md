# 0033: Production meetings come only from the live pipeline

Status: accepted (maintainer direction); the cleanup rules and the warning ownership model are proposed defaults
Date: 2026-11-03
Related decisions: [0006](0006-local-proof-of-concept.md), [0018](0018-scheduled-publication.md), [0027](0027-agency-definitions-in-configuration.md) (amended), [0030](0030-automatic-image-rollout.md) (amended)

## Context
The scheduled run began as a replay of the local proof of concept. Besides live discovery it loaded two hand-built sources into production:

- `seed-fixtures`: the 19-meeting bundle `tests/fixtures/meetings.json`, written by hand on 2026-10-19 and 20 ([source notes](../research/local-poc-sources.md)), inserted when a meeting was not yet stored.
- `verified-sources`: 132 candidate meetings typed into `config/verified-agency-meetings-*.json` during the October 20 source review, each with a hand-written title, date, and usually a warning, captured when not yet stored and retried daily for missing documents.

Their warnings were dated or described the state of one check ("No agenda document is linked from the official event page as of 2026-10-20"). A discovery capture replaces a meeting's warnings, but a meeting discovery never lists again, or only sees and does not download again (a settled past meeting), kept them. Truckee's November 10 and 24 Council meetings still carried the October 20 text on November 3, and the website hid such text with special cases. The maintainer asked to remove this hand-built material and simplify the system around the production pipeline.

## Decision
- **Meetings come only from live discovery** (and `backfill`, which uses the same capture). `run-scheduled` drops `seed-fixtures` and `verified-sources`; the image no longer contains `tests/fixtures/meetings.json`, and a change to it no longer rolls out the image. The CLI drops `ingest-fixtures` and `ingest-manifest`, and `export` always writes mode `live`. The verified-candidate lists move to [`docs/research/verified-agency-meetings/`](../research/verified-agency-meetings/) as research records. The bundle stays as a test input.
- **Configuration with verification stays.** The registry, agenda layouts, skipped endpoints, quarantine records, meeting-record feeds, verified comment periods (each notice fetched and its quote checked), and one-off notices under decision 0009 are configuration, not meeting data. A one-off notice no longer gets a default "check the official page for updates" warning.
- **Agenda availability is state, not a warning.** A meeting with no captured document is "Agenda not posted yet" (upcoming) or "Agenda not available here" (past), derived at export and in the website from its documents and date. Capture and discovery stop storing "No agenda PDF is linked", "No agenda file is listed … yet", and "The agenda is not posted yet …".
- **Discovery capture owns a meeting's warnings.** Each capture writes the whole list from the listing and that download. Between captures, live steps may add what they find (a revised source awaiting reprocessing, an event withdrawn from a revised notice) and `reparse` replaces its own parser notes; the next capture replaces those too. One-off and planned meetings, which discovery does not capture, carry the warnings their own step writes. A document version captured again with more of its pages takes that capture's warnings.
- **Local development follows production.** `scripts/project.py setup` starts the website from the export the production site publishes; `scripts/project.py ingest` runs `run-scheduled --no-publish` against the local database.
- **What the hand-built records left is cleaned up once**, by `tahoe-ingest clean-hand-built` (dry run unless `--apply`; idempotent). It removes each record's own warnings from its meeting, corrects document warnings the bundle wrote once a live capture has stored the whole file, and deletes meetings that exist only because of a hand-built record and hold no source content (no document, item, minutes or recording link, or discovery alias). The maintainer decides when to run it in production. Afterwards the command and the research lists can be removed.

## Alternatives and consequences
Keeping the seed but clearing its warnings on the first live sighting was rejected: the seed would still create records nobody checks, and "sighting" is not recorded for meetings discovery skips. Deleting every hand-built meeting was rejected: many hold verbatim agenda text from official documents, and some predate what an agency's index still lists, so `backfill` could not restore them.

A database rebuilt from scratch now holds only what discovery and backfill find. Entries without live discovery (`ctc`, `california-state-parks`, `nevada-state-parks`; the Placer County bodies are blocked) gain no meetings until they get a discovery adapter. The cleanup's dry run against production on 2026-11-03 would delete 12 empty meetings, including all of `ctc`'s and `placer-tahoe-design-review`'s, and strip hand-written warnings from 58 meetings.

## Revisit when
An agency that matters has no discoverable index and its meetings must be entered by hand again: that would need its own decision and a verified format, like comment periods.
