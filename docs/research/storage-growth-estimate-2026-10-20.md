# Database storage growth estimate — October 20, 2026

Question (feedback round 2): will storing full PDF text in Postgres fill the database, especially if most of it is never read? Measured on the shared local database (108 meetings, 55 documents, 549 pages, 278 agenda items) with `python3 scripts/storage_report.py`; projected with the models below. No ingestion behavior was changed.

Legend: **M** = measured here, **R** = from earlier research docs, **A** = assumption/extrapolation.

## 1. Measured now (M)

| Measure | Value |
| --- | ---: |
| Database total (`pg_database_size`) | 19.66 MB |
| All application tables combined | about 2.8 MB (the rest is fixed PostgreSQL catalog/system baseline of roughly 17 MB) |
| `document_pages` (heap + index + TOAST) | 1.20 MB, 549 rows |
| `document_pages` raw text | 1,523,900 B; avg 2,776 B/page, median 2,703, max 10,737 |
| `document_pages` on disk per page | 2,193 B (TOAST compresses text about 21%; PK index 194 B/page) |
| Everything else (all other app tables) | 1.62 MB: agenda_items 0.40, summary_versions 0.29, summaries 0.23, meetings 0.20, item_topics 0.15, processing_runs 0.11, document_versions 0.09, rest 0.15 |
| Summaries / summary_versions text | 30.0 KB / 32.6 KB (tables 0.23 / 0.29 MB: row and index overhead dominates) |
| Pages per document (55 docs) | min 1, median 3, p90 16, max 134 |
| Pages cited by any agenda item or summary | 50 of 549 = **9.1%** (9.8% of text bytes); 273 distinct quotes, 23.7 KB |
| Public export `public.json` | 2,136,619 B; page text is 1,523,900 B raw (1,548,445 B JSON-encoded) = **72.5%**. Without page text: 591 KB. Gzipped: 468 KB with text, 55 KB without |

Per agency (pages / text bytes): placer-transportation 181 / 465 KB, stpud 122 / 451 KB, california-fish-game 54 / 141 KB, slt 37 / 107 KB, all others under 50 KB each. 22 of 42 registry bodies have no document text yet. Full output: run the script.

Caveats on the measured data:

- Capture is already partial. Many documents are agenda text only ("Text retained for X of Y source pages"). The corpus averages 5.1 pages/meeting and 10 pages/document (median 3), far below the packet-heavy TTD/SLT references.
- The 9.1% cited share is therefore an upper bound for packet-heavy ingestion: most stored pages here are agenda pages, which agenda items cite directly.
- Small tables are dominated by 8 KB page granularity, so the 15 KB/meeting "other" cost below is conservative; marginal cost at scale will be lower.

Dated check of the export: of 2.1 MB, page text is the bulk. Page text is **not** present in the built site: a 30-character sample of a 2 KB page was absent from `apps/web/dist` (3.0 MB total; the largest meeting page is 63 KB). So `public.json` is a build-time input, not a browser download, but it grows with page text and is read whole at every build.

## 2. Growth model

| Parameter | Value | Basis |
| --- | ---: | --- |
| Page text on disk, local mix | 2,193 B/page | M |
| Page text on disk, used for projections | **2,700 B/page** | A: TTD Markdown averages 3,367 B/page (116.7 MB / 34,648 pages, R); times the 0.79 disk/raw ratio measured here |
| Non-page cost per meeting | **15 KB** | M: 1.62 MB / 108 meetings, includes empty registry rows; A: assumed to scale per meeting |
| Export JSON per page | 2,820 B (3,420 B for TTD-density pages) | M: 1,548,445 / 549 (x1.016 JSON escaping); TTD rate A |
| Export non-page per meeting | 5.5 KB | M: 591 KB / 108 |
| Fixed Postgres baseline | about 17 MB | M, not growth |

Scenario inputs (all per year):

| Scenario | Meetings | Pages | Basis |
| --- | ---: | ---: | --- |
| (a) Handoff baseline | 260 | 15,600 | R/A: 5 meetings/week, 300 pages/week (60 pages/meeting) |
| (b) 42-entry watchlist, about 3 agencies automated | 630 | 3,200 | A: 42 bodies x about 15 meetings; current measured 5.1 pages/meeting (agenda-level capture) |
| (c) About 24 agencies, full packets | 1,000 | 128,000 | A: 24 x 5,330 pages/yr, where 5,330 = TTD 34,648 pages / about 6.5 archive years (R); SLT's 505-page single packet (R) shows one body can alone exceed this |

## 3. Projection (extrapolated, MB decimal; baseline 17 MB not included)

| Scenario | Horizon | `document_pages` text | Everything else in Postgres | `public.json` (embeds all page text) |
| --- | --- | ---: | ---: | ---: |
| (a) 260 mtg, 15.6k pages | 1 yr | 42 MB | 4 MB | 45 MB |
| | 3 yr | 126 MB | 12 MB | 136 MB |
| (b) 630 mtg, 3.2k pages | 1 yr | 9 MB | 9 MB | 13 MB |
| | 3 yr | 26 MB | 28 MB | 38 MB |
| (c) 1,000 mtg, 128k pages | 1 yr | 346 MB | 15 MB | 443 MB |
| | 3 yr | 1,037 MB (1.0 GB) | 45 MB | 1,330 MB (1.3 GB) |

Total Postgres including baseline: (a) 63 MB / 155 MB; (b) 35 MB / 71 MB; (c) 378 MB / 1.1 GB at 1 / 3 years.

## 4. Against plan limits

Prisma Postgres, checked 2026-10-20 via [Prisma billing docs](https://www.prisma.io/docs/data-platform/billing/plans-and-quotas) and [Prisma pricing comparison](https://www.prisma.io/blog/prisma-postgres-vs-neon-pricing-2026) (search snippets; figures can change, and sources disagreed on the free operation allowance, 100k vs 200k):

| Plan | Price | Storage | Operations | Overage |
| --- | ---: | ---: | ---: | --- |
| Free | $0 | 500 MB | 100k–200k/month | none; no spend limit needed |
| Starter | $10/month | 10 GB | 1M/month | $2/GB, $0.008 per 1,000 ops |
| Pro | $49/month | 50 GB | 10M/month | $1.50/GB; over the <$20 ceiling |

- (a) and (b) fit the free 500 MB for 3 years. (c) fits the free tier for about 1 year and needs Starter ($10/month, within the <$20 ceiling) in year 2. Storage alone does not threaten the budget in any scenario; 1.1 GB of 10 GB is 11%.
- Whether Prisma meters total database size (including the roughly 17 MB baseline) or table data is unconfirmed; the baseline is small either way.
- Reads for the static build and Cloud Run Jobs count as operations, but at this volume are far under 200k/month. **Correction (2026-10-24):** this was wrong. Every SQL statement is an operation, and the job's per-item queries used about 35,000 operations a day; see [decision 0020](../decisions/0020-database-operation-budget.md).

Vercel, checked 2026-10-20 via [Vercel limits](https://vercel.com/docs/limits) (search snippet): static file uploads 100 MB on Hobby (1 GB Pro); CLI source upload 100 MB on Hobby; build cache 1 GB. The dist output today is 3.0 MB. `public.json` is gitignored and generated during build/refresh, so it is not a deployed asset, but at 443 MB (scenario c, year 1) it would break any flow that uploads or loads it whole (CLI source upload, Astro build memory), and in scenario (a) it passes 100 MB in about year 2.2. The export, not the database, is the first thing to break.

## 5. Do we need most of that content?

Measured: 90.9% of stored pages (and 90.2% of page text) are not cited by any item or summary, and this is with agenda-heavy capture. The export currently embeds every page (72.5% of `public.json`), though the built site does not ship it to browsers. Hypothesis in feedback (a high-level agenda alone suffices for classification) is consistent with the data above but not proven: README notes supporting-report topics absent from titles can be missed by title-based rules.

| Option | Postgres effect | Trade-offs |
| --- | --- | --- |
| 1. Keep agenda document pages plus cited pages; link official PDF for the rest | Scenario (c) 1 yr: 346 MB to about 22 MB (A: about 8 retained pages/meeting) | Smallest. Loses ability to re-classify or search from supporting reports without refetching; depends on agency links staying up (decision 0004 already accepts this). Needs a per-document retention rule in ingestion. |
| 2. Keep full text in Postgres; export only cited quotes and metadata | Postgres unchanged (346 MB) ; export 443 MB to about 8 MB | No loss of data, tiny change (export schema drops `pages[].text`, check_export must still validate citations from quotes). Does not slow DB growth, so (c) hits the 500 MB free tier in year 1–2. |
| 3. Move full page text to object storage (e.g. Google Cloud Storage from Cloud Run) | Postgres about 20 MB; storage cost about $0.02/GB-month | Cheap and preserves text, but adds a second store, credentials, and cross-store consistency; the Google project is not yet provisioned. Over-built for scenarios (a)/(b). |
| 4. Time-based pruning of uncited packet pages (e.g. after 12 months) | Caps steady state to about 1 year of text: (c) stays about 350 MB | Keeps reprocessing window and recent searchability; adds a job and a visible "text expired" state; prune must not break citations (keep cited pages). |

**Recommendation (default): Option 2 now, with Option 1 as the ingestion policy only if scenario (c) is pursued.**

Rationale: the export is the first limit to break and Option 2 removes 72% of it (2.1 MB to about 0.6 MB today) with no data loss and no ingestion change. Postgres at full text is 63 MB (a) or 71 MB (b) after 3 years, comfortably inside the free tier, so pruning stored text would trade away re-classification ability for savings nobody needs yet. Revisit when full-packet ingestion for many agencies starts; at that point apply Option 1 (cited and agenda pages only), which shrinks (c) to about 22 MB for year 1 and 65 MB for 3 years, and exports about 8 MB per year.

Estimated storage under the default (Option 2, full text kept), Postgres unchanged from section 3; export becomes metadata plus cited quotes (A: about 8 KB/meeting): (a) 2 MB / 6 MB, (b) 5 MB / 15 MB, (c) 8 MB / 24 MB at 1 / 3 years.

## 6. Decision (2026-10-20)

The maintainer reviewed this estimate and chose to keep the current system: full page text in Postgres and in the build-time export. The measured scenarios last at least a year, so no storage engineering happens now. Monitor instead, and revisit when a review threshold in the [operations guide](../operations.md#check-database-storage) is crossed. The export-slimming option is tracked as [IDEA-014](../ideas/IDEA-014-export-slimming.md).

## 7. Rerun

```sh
python3 scripts/storage_report.py          # human-readable
python3 scripts/storage_report.py --json   # machine-readable
```

Rerun after each ingestion expansion and compare with section 2 parameters. Replace the assumed 2,700 B/page and 15 KB/meeting with newly measured values, especially once real packet-heavy documents are stored.
