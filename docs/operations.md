# Local operations

The local PostgreSQL database holds meeting records, source hashes and extracted page text, generated summaries, topic assignments, corrections, suppressions, and topic overrides. Keep it backed up before schema changes and after editing published content.

## Create a backup

Start the checkout's local database if needed, then create a backup:

```sh
python3 scripts/local_db.py start
python3 scripts/backup_db.py backup
```

The script writes a PostgreSQL custom-format dump to `data/backups/` with a UTC timestamp and restrictive file permissions. `data/` is ignored by Git, so backups stay local and are not committed. To choose another path:

```sh
python3 scripts/backup_db.py backup --out /path/to/tahoe-backup.dump
```

The URL comes from `DATABASE_URL` in the process environment or the repository's `.env` file, with the same local default as `scripts/project.py`. This tool requires a loopback database host. It reads the URL into PostgreSQL's `PG*` environment variables, so credentials do not appear in command arguments or its output. PostgreSQL command-line tools are resolved using `scripts/local_db.py`'s binary lookup; set `POSTGRES_BIN` to the PostgreSQL `bin` directory if they are not on `PATH` or in a standard install location.

PDFs are not part of the database backup. Agenda PDFs remain temporary processing inputs under the accepted retention policy; stored extracted text and source/version metadata are included in the dump.

## Verify a backup can be restored

Pass a specific dump to the restore check:

```sh
python3 scripts/backup_db.py restore-check data/backups/tahoe-<timestamp>.dump
```

The check refuses non-loopback database URLs. It creates a randomly named database, restores the dump there, then drops only that database. It never drops or changes the database named in `DATABASE_URL`. The check compares row counts across all application tables, hashes all extracted source-page text, and compares the stored editorial corrections, and topic overrides. The source database must remain idle during the check; if its contents change while the backup is being checked, the command stops and asks you to rerun it when editing and ingestion are idle. Keep the dump even if the restore check fails so the failure can be investigated.

This verifies a backup without replacing the current database. It is not a command for restoring over a live database. For recovery, preserve the damaged database, restore into a separately named database, inspect it, and only then decide whether to change the local connection setting.

## Retry ingestion and retain corrections

Check job and validation failures first:

```sh
python3 scripts/project.py status
```

After resolving a source or connectivity issue, rerun the failed `capture` or `refresh-document` command. Ingestion is idempotent for the same document version. Use `refresh-document MEETING_ID DOC_ID HTTPS_PDF_URL` to fetch a revised official PDF for an existing meeting; changed source content invalidates affected generated summaries and preserves editorial corrections and notes.

Operator edits are local CLI actions. A correction requires text, reason, and author; topic inclusion/exclusion and suppression are recorded separately. These are internal steering tools, not a public editorial layer: a correction replaces the displayed summary text with no attribution, the author and reason stay in the database audit trail, and the public site shows only the latest summary.

```sh
python3 scripts/project.py worker correct ITEM_ID --text "Corrected text" --reason "Reason" --author "Your Name"
python3 scripts/project.py worker tag ITEM_ID biking --author "Your Name"
python3 scripts/project.py worker tag ITEM_ID biking --exclude --author "Your Name"
python3 scripts/project.py worker suppress ITEM_ID
python3 scripts/project.py refresh
```

Make a fresh backup after manual corrections, topic overrides, or suppressions.

Use an actual item ID from `apps/web/src/data/public.json`. To remove suppression, pass `suppress ITEM_ID --off`. Run `refresh` after any ingestion or editorial change so the public site reflects the database. For a background Astro server, use `npm --prefix apps/web run dev -- stop` to stop the site; the PostgreSQL server is stopped separately with `python3 scripts/project.py stop`.

The source fixture command reapplies the captured agenda state. Do not use it as a refresh command after live capture. Unknown layouts and OCR-required files remain review tasks; a retry cannot recover source content that an agency has removed.

## Reclassify agenda items

Topic-rule changes can be applied independently of source downloads and summary generation:

```sh
python3 scripts/project.py worker reclassify --dry-run
python3 scripts/project.py worker reclassify
python3 scripts/project.py refresh
```

The dry run reports generated topic changes. Applying them records the classifier version, matched title text, rule, and citation without rewriting source or summary history. Operator topic overrides remain authoritative. The rules are intentionally bounded to item titles and limited agency context; a supporting-report subject may require an explicit override.

## Source questions and access problems

Use the [source review queue](operations/source-review.md) and its canonical `config/source-reviews.json` to review questionable source identities, missing agenda archives, and download failures one at a time. Pending flags do not change followed coverage. Record the maintainer’s decision with the stable SR ID before disabling a source or changing its intended scope. Missing parsers and recovered acquisition problems are maintainer work and are labeled separately.

## Check database storage

```sh
python3 scripts/storage_report.py          # readable report
python3 scripts/storage_report.py --json   # machine-readable
```

The maintainer's decision on 2026-10-20 was to keep full page text as stored and monitor rather than engineer a solution ahead of need. Run the report about once a month, or after any large capture, and note the numbers in [implementation status](planning/implementation-status.md). Review thresholds, each of which should trigger a decision before the limit is reached: `public.json` above **40 MB** (Vercel's 100 MB upload limit is the first hard ceiling); the application tables above **250 MB** (half of the Prisma Postgres free tier); or a build that takes noticeably longer because of export size. The prepared options are in the storage growth estimate and [IDEA-014](ideas/IDEA-014-export-slimming.md).

The report is read-only. It uses `DATABASE_URL` from the environment or `.env`, with the same local default as `scripts/project.py`, and prints database size, per-table sizes, `document_pages` text bytes and bytes per page, per-agency meeting/document/page counts, pages per document, and the share of pages cited by agenda items or summaries. Use it to track growth against the Prisma Postgres plan limit; see the [storage growth estimate](research/storage-growth-estimate-2026-10-20.md). It uses `psycopg` when installed and otherwise the `psql` command.
