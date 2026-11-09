# 0040: Operator review notes are kept apart from resident warnings

Status: accepted by the maintainer on 2026-11-09 (separating the two kinds, item P11 of the meeting-warnings review); exporting the notes as `reviewNotes` rather than leaving them out of the export, and the new wording of the "refresh" warning, are proposed defaults.
Date: 2026-11-09
Related decisions: 0032, 0033, 0039

## Context

A meeting's `warnings` held two kinds of text. Some are for residents: a document that could not be retrieved, a multi-day meeting's span, an event withdrawn from a revised notice. Most were parser and capture outcomes for the operator: "No complete … agenda recognized in downloaded documents; review required.", "… numbering is not sequential near …; review required.", "Not every listed document was captured; agenda items require review." The website hid the second kind by matching regular expressions in `apps/web/src/lib/data.ts` (`residentWarning`), which had to track the worker's wording. It missed two failure messages ("Agenda structure or source quote could not be validated; items require review." and "… cover was not recognized; items require review."), which residents saw as written, and the hidden notes were still in the public JSON as `warnings`.

## Decision

- **Warnings are for residents, as written.** `meetings.warnings` and the export's `warnings` hold only text a resident should read. The website shows them without mapping them.
- **Review notes are written as such at the source.** A new column, `meetings.review_notes`, holds parser and capture outcomes. Capture writes a parse failure, a truncated or incomplete document set, and the notes on items published despite irregular numbering (decision 0032) there; `reparse` owns them (a failed or successful reparse replaces its own notes), and `backfill --recapture-unparsed` selects meetings by them. Like warnings, each capture replaces the list.
- **The export carries them as `reviewNotes`.** Every confirmed meeting has `reviewNotes: string[]`, possibly empty; forecasts have none. The field is additive and `warnings` keeps its type, so `schemaVersion` stays 2. The website reads two of them for the short explanation beside the agenda history ("One agenda document could not be read reliably…", `agendaSourceNote`) and shows none as written. `export_check` requires both lists to hold non-empty text.
- **The "refresh" warning is stored in residents' words.** `REPROCESSING_WARNING` becomes "Source documents changed; some agenda details still need to be refreshed.", the text the website used to substitute for "A source PDF changed; affected agenda items await reprocessing." Its rules are unchanged (decision 0039).
- **Stored strings are moved once.** `core.migrate_review_notes` runs in `init_db` the first time the new schema is applied (`ingest_migrations` name `meeting-review-notes-v1`): it moves stored warnings that match the patterns the website hid, plus the two failure messages it missed and the planned meetings' retired "Subject to change.", into `review_notes`, and rewrites the old "await reprocessing" text. It reads the meetings with warnings in one statement and writes the changed ones in one more, and leaves `updated_at` alone. `clean-retired-warnings` and `clean-hand-built` look in both columns.
- **The website bridges older exports.** A build that reads an export without `reviewNotes` (one stored before the new worker image has run) splits its warnings by the same patterns (`withReviewNotes` in `data.ts`), so a website deploy that lands first shows residents nothing new. `residentWarning` becomes `documentWarning` and keeps only the document-level mappings (HTML sources, retained pages, unknown fetch time, unreadable pages).

## Alternatives and consequences

Leaving review notes out of the public export entirely was considered. It would keep the JSON to resident text, but the website needs two of the notes for the agenda history explanation, and the export is where the operator and the meeting-warnings review read parse outcomes today; dropping them would need a separate structured field for the first and a run-summary listing for the second. Tagging strings inside the one `warnings` list (`{"text", "audience"}`) was rejected because it changes the field's type for every consumer and would need a `schemaVersion` bump.

The website's patterns now live in two bridges (the one-time migration and the older-export split) instead of the display path; new worker wording needs no website change. Remove the older-export split once every export a build can read (the database and the live site's fallback copy) carries `reviewNotes`. A note written by a step other than capture or reparse must go to `review_notes` explicitly; `export_check` cannot tell a misplaced note from a warning.

## Revisit when

Residents need to see a parse outcome in more than the two agenda-history explanations, or an export consumer asks for the notes to be left out.
