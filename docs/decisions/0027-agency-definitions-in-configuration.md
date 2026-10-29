# 0027: Agency definitions live in the registry

Status: accepted (maintainer request, 2026-10-28); implemented; deployed in the worker image of 2026-10-29 (tag `a7140adc4582`), first production run on 2026-11-01
Date: 2026-10-28
Related decisions: [0009 configurable coverage](0009-configurable-coverage-and-one-off-meetings.md), [0013 estimated meeting durations](0013-estimated-meeting-durations.md), [0014 reviewed sources](0014-reviewed-sources-and-public-comment-periods.md), [0015 deterministic agenda layouts](0015-deterministic-agenda-layouts.md), [0020 database operation budget](0020-database-operation-budget.md), [0026 staff reports for every agency](0026-staff-reports-for-every-agency.md)

## Context

Following the TTD Regional Transit and Capital Programs Committee (commit 09ecd18) changed 17 files: seven worker modules that branched on the agency ID (discovery, capture, backfill, the catalog, agenda parsing, the staff report rule, and topic hints) and three documents that repeated agency counts, besides the registry entry, tests, and the source review. The maintainer asked that agency definitions be driven by configuration: adding an agency whose formats are already supported should need only its registry entry, a fixture test, and a source review record, and code should register reusable formats by name, never by agency ID.

## Decision

**The registry is the one place an agency is defined.** An entry in `packages/domain/agencies.json` holds the body's identity and aliases, whether it is followed, its URLs and standing participation details, and how its publisher is read: `discovery` (a discovery type and its index), `agenda` (the agenda layouts it prints), `staffReports` (a staff report rule and its parameters, or why there is none), and `topicHints`. It also holds when and how long the body meets (`schedule`, and `duration`, moved from `config/meeting-durations.json`, which keeps only the defaults) and the imported research record (`legacy`). The worker validates every entry when it loads the registry and rejects unknown keys.

**Code registers formats by name.** Discovery types are `source_discovery.TYPES`; agenda layouts are data in `config/agenda-layouts.json`, with hand-written parsers named in `agenda_custom.PARSERS`; staff report rules register under a format name in `staff_report_rules/`. Layouts sit in their own file because a layout describes a document format that several bodies can share, carries many keys, and has its own fixtures; entries name the layouts they use. No code branches on an agency ID. A few format names start with the publisher's name (`ttd-sections`, `slt-council`, `ntpud-packet`) because only that publisher prints the format; they are still format names, and another body that prints the same format can use them.

**Sibling bodies inherit.** An entry may name `"extends": "<parent id>"`. It starts from the parent's resolved entry and overrides what differs: objects merge key by key, recursively, the child's value winning; lists and other values replace. `id`, `name`, `aliases`, `follow`, `participation`, `schedule`, `duration`, and `legacy` are never inherited, because they describe the body itself rather than its publisher: a committee does not meet when its board does, and disabling a board does not disable its committees. The parent must exist, chains resolve parent first, and cycles stop the load. The catalog resolves inheritance when it loads, so every consumer reads complete entries; the website reads only `id` and `aliases`, which never inherit. The TTD committee, the TRPA Advisory Planning Commission, and the Douglas County Planning Commission now extend their boards, and the resolved registry is identical to the entries they replaced. Bodies whose blocks differ in shape keep full entries, since a child cannot delete an inherited key.

**The file has one canonical form**, two-space JSON with each entry's keys in a fixed order (`catalog.AGENCY_KEYS`); a test checks it, and `learn-schedules --write` writes it.

**Coverage documentation is generated.** The tables of [source coverage](../operations/source-coverage.md) (counts, discovery type and body filter per entry, blocked endpoints, entries without discovery, staff report rules) are rewritten from the registry and `config/skipped-endpoints.json` by `tahoe-ingest coverage-doc`, between marked blocks, and a test fails when they are stale. Other documents link to that page instead of repeating counts. Notes on individual sources stay hand-written beside the tables.

**What stays in separate files.** Logs and dated snapshots (`source-reviews.json`, `verified-agency-meetings-*.json`, `one-off-meetings.json`, `public-comment-periods.json`, `quarantined-meeting-candidates.json`) record events, not definitions. `meeting-records.json` (YouTube and Laserfiche feeds) stays separate for now: it is read from the source root at run time, entry by entry in file order, with several bodies sharing one channel's feed, so moving it would change the order and the configuration seam the tests and the operator use. It already rejects agencies that are not followed when it loads, and a test checks that every agency named anywhere under `config/` exists in the registry.

**Topic hints do not version the classifier.** `CLASSIFIER_VERSION` hashes only `topics.json`, and its value is unchanged. A new version rewrites every item's tags; instead, `reclassify` (which runs in every scheduled run) compares each item's stored tags and evidence with the rules, so a hint edit rewrites only the items whose tags or provenance change, in the same fixed number of statements (decision 0020). Before this, a hint edit that changed only provenance (a renamed rule) left stale evidence.

**What still needs code:** a format the worker cannot read yet, meaning a new discovery type, an agenda the outline engine cannot describe (a parser in `agenda_custom.py`), or a new staff report rule module, each registered by format name; and a new kind of registry key.

## Alternatives and consequences

- Abstract template entries that are not agencies were rejected: the website, `init-db`, and the export would each have to skip them. Siblings extend a real entry instead.
- A way to delete an inherited key (for example `null`) was left out: no current family needs it, and `null` is already a meaningful value (`officialUrl` of the State Lands Commission).
- Hashing `topicHints` into `CLASSIFIER_VERSION` was rejected: it would change today's value and rewrite every item's tags after any hint edit, during a month whose Prisma operations quota is already exceeded.
- An edit to a parent changes its children. That is the point when they share a publisher, but a reviewer should read a parent's diff with its children in mind; `worker agencies` prints the resolved entries.

## Revisit when

- Several sibling families need to remove inherited keys, or inheritance chains grow beyond one level.
- Recording and minutes feeds need per-body settings that belong with the rest of the definition, which would justify moving `meeting-records.json` into the registry and adjusting its loader and tests.
- The generated coverage page needs information the registry does not hold, which would argue for adding it to the registry rather than to hand-written tables.
