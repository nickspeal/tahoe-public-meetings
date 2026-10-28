# Adapter expansion

Updated October 20, 2026. This brief has been worked once (ten entries now have live discovery; see [source coverage](source-coverage.md) for what remains: Tahoe Conservancy and State Lands adapters, the statewide parks bodies, and the federal comment-period sources). The original wording follows for reuse. This page is the task brief for adding adapters to the ones that are not known to be blocked, and for documenting the blockage where an adapter cannot be built. It is written to be pasted into a fresh agent session.

## Prompt

```text
Read AGENTS.md, docs/planning/implementation-handoff.md, docs/operations/source-coverage.md,
config/README.md (sections "Agencies and bodies" and "Reviewed source configuration"), and
docs/decisions/0014-reviewed-sources-and-public-comment-periods.md. If private/AGENTS.private.md
exists, read it too and follow it. Do not deploy anything or touch cloud resources.

Goal: give live discovery to as many of the "Snapshot only" followed entries in
docs/operations/source-coverage.md as can be done legitimately, and document precisely why
for any that cannot.

Entries to work, in this order (cheapest first):
1. douglas-commissioners, douglas-planning: Douglas County Granicus publisher
   (https://douglascountynv.granicus.com/ViewPublisher.php?view_id=1). douglas-transportation
   already uses this with a bodyPattern; add the same adapter config with the right body filters.
2. el-dorado-supervisors: El Dorado County Legistar; el-dorado-planning is the working model.
3. washoe-commissioners: Washoe County; washoe-planning already uses Legistar, so check
   whether the Board of County Commissioners is on the same Legistar site.
4. south-shore-tma (ss-tma.org/board-meetings/), ntpud (ntpud.org/category/meetings/),
   stpud, truckee-tahoe-airport, ndep, california-state-parks, nevada-state-parks,
   placer-transportation: find the official meetings source, identify its platform, and use an
   existing discovery type (granicus, legistar, civicplus, link-index, civicclerk, trpa,
   dated-table, rss) if one fits. Only write a new discovery type if several entries or an
   important one need it, and keep it small with fixtures.
5. ltbmu, tahoe-usfs, usfws are federal newsrooms kept for public comment periods. Do not build
   meeting adapters for them. Check whether they publish meetings at all; if not, record in
   source-coverage.md that they are comment-period-only and say whether they should keep
   participationTypes "meeting".

Method for each entry:
- Verify the official source by fetching it with the project's own fetcher (verified TLS, polite
  delays, bounded requests, no bursts). Use `python3 scripts/project.py worker discover <id>
  --limit 3 --out <scratch file>` then `worker capture` on a few results to test end to end.
  Put scratch output outside the repo or under ignored data/.
- Add a `discovery` object (a supported `type`, `indexUrl`, and `bodyPattern`) to packages/domain/agencies.json
  with a precise `bodyPattern` so unrelated commissions are not pulled in. Verify the result is
  only meetings of the intended body.
- Add or update tests with a saved fixture of the index page. Run `python3 scripts/project.py test`.
- Item parsing is separate. If an agenda layout has no parser, leave it as a notice plus official
  link and note that in the coverage table; do not guess at items.
- Do not duplicate meetings that already exist from the tracked snapshots. Use stable existing IDs
  and confirm that re-running does not create duplicates or change published meetings wrongly.

Hard rules:
- Never evade bot protection: no browser impersonation, no header spoofing to dodge blocks, no
  proxy rotation, no disabling TLS verification. A 403, Cloudflare block page, CAPTCHA, login
  wall, or robots disallow means "blocked", not "try harder". Retry politely at most once or
  twice, then document.
- Do not follow an entry's `legacy` fields; they are unverified research input.
- Keep requests low-volume. The job runs every 6 hours across all agencies.

If an adapter cannot be built, document the blockage instead:
- Add the entry to the "Blocked" table in docs/operations/source-coverage.md with the exact
  symptom (status code, block page, missing data), what you tried, and a likely cause stated as a
  hypothesis unless proven. Remove it from the "Snapshot only" table.
- Create docs/issues/SOURCE-NNN-short-name.md (next free number) in the style of SOURCE-001 and
  SOURCE-002: affected registry entries, observed behavior with example URLs, current behavior,
  next investigation (for example an agency-supported feed, a different host, or asking the
  agency), and a "Done when" line. Link it from source-coverage.md.
- Not every "cannot" is a block. If the source simply publishes no machine-discoverable meetings
  (for example PDFs mailed or uploaded irregularly), say that and recommend keeping snapshot
  coverage or unfollowing, without making that decision yourself.

When done:
- Update the counts and tables in docs/operations/source-coverage.md, the coverage summary in
  docs/planning/implementation-status.md, and the "Current coverage update" in
  docs/planning/implementation-handoff.md.
- Run the full tests and `python3 scripts/project.py worker agencies` to confirm adapter readiness.
- Commit in small logical commits and push to origin/main. Everything committed is public: no
  secrets, personal notes, or names (say "the maintainer"/"the operator"). Never commit private/.
- Report per entry: automatic now, blocked (with issue link), or comment-period-only, and anything
  you could not verify.
```

## Why it is split this way

Douglas, El Dorado, and Washoe sit on platforms the worker already supports, so they are configuration plus fixtures. The small districts and state parks need research to find a source at all. The federal newsrooms are not meeting bodies, so the useful outcome there is a documented decision, not an adapter.
