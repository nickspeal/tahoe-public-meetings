# Tahoe Public Meetings

A local Tahoe-area meeting timeline with configurable agencies and six shared topics: **Biking, Transit, Housing, Short Term Rentals, Environment, and Hot Topics**. The October 20, 2026 local capture contains 123 confirmed meetings across 35 agencies/bodies. The [source review](docs/operations/source-review.md) records the reviewed sources and remaining gaps; [source coverage](docs/operations/source-coverage.md) lists every registry entry and how it is read. Public comment periods have a [separate page](http://127.0.0.1:4321/comment-periods/) and verified-source import workflow.

The Astro website reads a versioned export from a Python/PostgreSQL ingestion worker. A visible Today marker separates earlier meetings, which open ten at a time, from upcoming dates. Topic and agency filters expose related agenda items on that same timeline. Each meeting shows its covered topics and counts, tags on collapsed agenda items, and multi-select topic filters. Meeting pages also include page-level citations, a direct link from each agenda item to its cited page in the official document, participation links, calendar downloads, and cancellations. The included source bundle has 19 verified meetings and 243 agenda items, spanning May through November 2026. Upcoming listings without posted agendas or confirmed start times preserve those missing details. Canceled meetings stay listed, greyed out. By default the timeline lists upcoming meetings with posted agendas. One button reveals the rest: confirmed meetings without an agenda yet, plus dashed placeholders for regular meetings predicted from each agency's usual schedule (for example, TTD's first Wednesday at 3 PM) before an agenda or notice exists. Any real record replaces the placeholder.

Coverage is limited to the records actually captured; it is not a comprehensive live feed. Item summaries repeat official text verbatim: the requested action the agenda prints under an item, plus sentences Claude selects and trims from the item's own staff report, each checked against the stored page text before publishing ([decision 0024](docs/decisions/0024-verbatim-item-summaries.md)). Items link to their staff reports for every agency that publishes them in a verifiable way ([decision 0026](docs/decisions/0026-staff-reports-for-every-agency.md)) and, for South Lake Tahoe and TRPA, to each item's listed attachments. Claude is the selected provider for future automated analysis ([decision 0011](docs/decisions/0011-claude-for-automated-analysis.md)); the [topic classification evaluation](docs/research/topic-classification-evaluation-2026-10-20.md) compares Claude Haiku with the current rules. No API key or cloud account is needed to run locally.

## Run locally

Prerequisites: Node.js 22.12+ (Node 24 recommended), npm, [uv](https://docs.astral.sh/uv/), PostgreSQL command-line tools, curl with system CA certificates, and Poppler (`pdftotext`, `pdfinfo`). Setup uses uv to obtain Python 3.13 if needed.

From the repo root:

```sh
python3 scripts/project.py setup
python3 scripts/project.py dev
```

Open **http://127.0.0.1:4321/**. The server binds to loopback only. In a normal terminal, Ctrl-C stops the website. Astro may run in the background when started by a coding agent; use `npm --prefix apps/web run dev -- stop` to stop that server. The database uses a dedicated cluster in ignored `data/postgres/`, port **55432**, and does not modify other PostgreSQL installations. Stop it with `python3 scripts/project.py stop`.

Setup installs locked dependencies, starts the dedicated database, initializes its schema, ingests the fixtures, and validates the public JSON export. It can be rerun without duplicate records. Fixture ingestion preserves operator edits, but reapplies the captured source state; use `refresh`, rather than `seed`, after editing or live capture.

For Docker instead of a native PostgreSQL installation:

```sh
docker compose up -d db
DATABASE_URL=postgresql://tahoe:tahoe-local@localhost:55432/tahoe python3 scripts/project.py setup
```

The `localhost` URL selects an existing database instead of starting the native cluster. Choose one database approach at a time. The sample password is for loopback development only. Copy `.env.example` to `.env` to use another local database; environment variables take precedence. `POSTGRES_BIN` can locate PostgreSQL tools if they are not on PATH.

## Develop and operate

| Command | Purpose |
| --- | --- |
| `python3 scripts/project.py test` | PostgreSQL tests, evidence validation, Astro checks, build, agenda/timeline interaction tests, calendar tests, and internal links |
| `python3 scripts/project.py refresh` | Export current database state plus 90 days of expected meetings, and validate citations; the dev website reloads |
| `python3 scripts/project.py build` | Refresh data and build static HTML in `apps/web/dist/` |
| `python3 scripts/project.py status` | Inspect source state, processing failures, and invalid summaries |
| `python3 scripts/project.py worker --help` | All ingestion and operator commands |
| `python3 scripts/project.py worker reclassify --dry-run` | Preview changes from the current topic rules |
| `python3 scripts/project.py worker reclassify` | Refresh generated tags while preserving human overrides |
| `python3 scripts/project.py worker evaluate-topics` | Compare rule tags with Claude Haiku (read-only; a dry run with cost projection unless `ANTHROPIC_API_KEY` is set) |
| `python3 scripts/project.py worker learn-schedules --dry-run` | Report each agency's regular meeting pattern, support, and exceptions from stored meetings |
| `python3 scripts/project.py worker learn-schedules --write` | Store learned schedules (3+ matches, 60%+ match rate); never replaces configured ones |
| `python3 scripts/project.py seed` | Reapply the original source fixtures; preserves human edits |
| `python3 scripts/storage_report.py [--json]` | Read-only database size report: per-table bytes, page text, per-agency counts, and share of pages cited |

Local operator edits are separate from generated facts. Corrections require an author and reason for the internal audit trail; a correction simply replaces the displayed summary text, and topic overrides and suppression are likewise internal steering. The site shows only the latest summary, with no revision history or attribution. After an edit, run `refresh`.

Topic rules use each item's title and limited agency context, with recorded rule evidence. They exclude ambiguous uses such as a funding "Cycle B" from Biking, and recognize ridership, transit services, mobility hubs, and relevant fleet actions. They do not scan neighboring agenda items for keywords. Supporting-report topics that are absent from a title can still be missed; use a topic override for those cases.

The canonical [topic catalog](packages/domain/topics.json) defines IDs, names, descriptions, and examples for ingestion, overrides, exports, and every public view. It currently includes six topics. Title-matching rules and the STR → Housing relationship are configured there too. Keep IDs stable when adjusting display names. After changing classification rules, run `worker reclassify --dry-run`, `worker reclassify`, and `refresh`. The [configuration guide](config/README.md) explains topic boundaries, the 42-entry agency/body registry (41 imported CSV rows plus Truckee), adding one-off workshops through `config/one-off-meetings.json`, and agency meeting schedules for expected meetings. Imported agency metadata is unverified; automated discovery still covers the three existing agencies.

Meeting filters match **any** selected topic, preserve agenda order, and can be shared through the page URL. **All items** clears the filters. A deep link keeps its referenced item visible even if it falls outside the selection. Calendar downloads are `.ics` snapshots with stable event IDs and explicit cancellations; they do not update automatically. A meeting with no verified start time has no downloadable event. Every event has an end time: a published or observed length when known, otherwise a long-biased estimate that is labelled as an estimate ([decision 0013](docs/decisions/0013-estimated-meeting-durations.md); tune the defaults in `config/meeting-durations.json` and a body's own length in its registry `duration`, with `worker duration-report`).

Discover official meetings without changing stored data:

```sh
mkdir -p data
python3 scripts/project.py worker discover truckee --limit 5 --out data/truckee-candidates.json
```

See the [worker guide](workers/ingestion/README.md) for capture, document refresh, and correction commands, and the [operations guide](docs/operations.md) for backup, recovery, and failed-job procedures. Live access can be rate-limited or blocked; the reproducible fixture workflow works offline after dependencies are installed.

## Data and reliability

- Original PDFs are temporary inputs. PostgreSQL retains page text, source URLs, hashes, source versions, summary history, topic assignments, and editorial edits.
- Repeated ingestion is idempotent. A revised source invalidates affected summaries, and preserves corrections and topic overrides. Failed jobs preserve previously published records and expose source errors.
- Invalid or missing evidence blocks a summary, while verified logistics remain visible. Agenda proposals do not establish adopted decisions. Empty extraction and unknown parsing formats are explicit review tasks.
- The public JSON boundary is [packages/domain/public.ts](packages/domain/public.ts). Generated exports, local databases, backups, dependencies, credentials, and PDFs are ignored by Git.
- Integration tests use temporary schemas in `TEST_DATABASE_URL` and remove only those schemas. They do not reset the application database.

The [source audit](docs/research/local-poc-sources.md) documents genuine dates, source IDs, known inconsistencies, and selected-page coverage. The [large PDF benchmark](docs/research/local-pdf-benchmark.md) measures the 505-page SLT packet. The [implementation status](docs/planning/implementation-status.md) is the resume handoff, with verified work and remaining limitations.

## Project direction

Selected hosting is Vercel, Prisma Postgres, and Python on Google Cloud Run Jobs with Cloud Scheduler. Alerts and resident accounts are deferred; recurring budget remains below $20/month.

Read the [implementation handoff](docs/planning/implementation-handoff.md) before older planning documents. See the [build plan](docs/planning/mvp-build-plan.md), [product brief](docs/planning/product-brief.md), [decisions](docs/decisions/README.md), and [idea backlog](docs/ideas/README.md). Local defaults are in [decision 0006](docs/decisions/0006-local-proof-of-concept.md).

## License

[MIT](LICENSE). Source: [github.com/nickspeal/tahoe-public-meetings](https://github.com/nickspeal/tahoe-public-meetings). Agenda text in `tests/fixtures/` is excerpted from public agency documents for testing; the official sources remain authoritative.
