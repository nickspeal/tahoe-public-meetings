# 0012: Forecast meetings from agency schedule patterns
Status: accepted
Date: 2026-10-20
Related ideas: [IDEA-003 calendar](../ideas/IDEA-003-calendar.md), [IDEA-012 coverage admin](../ideas/IDEA-012-coverage-admin.md)
Source: maintainer feedback, Round 2

## Context

The maintainer asked for three related changes. Canceled meetings should be greyed out in the meeting list. The site should learn each agency's regular meeting pattern, such as TTD board meetings on the first Wednesday of each month at 3 PM, and use it as a default before meetings are scheduled. Predicted meetings must be visually separate from meetings with a published agenda, and a filter should hide predictions, hidden by default.

Earlier configuration rules said the unverified 2023 CSV schedule cells must never create meeting dates. Most agencies also have only two to five captured meetings, so any learned pattern rests on thin evidence.

## Decision

- **Schedules are declarative registry data.** Each agency entry in `packages/domain/agencies.json` can have an optional, validated `schedule`. Its rules are a weekday plus a monthly ordinal (1st–4th or last), or a week cadence (`every` + `anchor`). Each rule has a local time, the Pacific time zone, a label, and optional `months`. `source` is `configured` (operator-verified) or `learned`. TTD is configured from the maintainer's statement: first Wednesday, 3:00 PM Pacific. The legacy CSV cells remain unread research input.
- **Learning is an explicit, reviewable operator step.** `tahoe-ingest learn-schedules` reports patterns from stored, non-canceled, timed meetings. It excludes one-off notices and titles naming special, joint, workshop, hearing, retreat, or study session meetings. A schedule is proposed only with at least 3 matching meetings and a match rate of at least 60%; otherwise the result is "insufficient data". `--write` stores learned schedules and never replaces a configured one. The October 20 run learned schedules for 9 agencies, and the learner independently agreed with TTD's configured schedule ([findings](../research/meeting-schedule-patterns-2026-10-20.md)).
- **Forecasts are derived at export time and never stored.** `export_public` adds expected meetings for agencies with stored records. They cover tomorrow through a configurable horizon: 90 days by default, set with `--forecast-days` or `TAHOE_FORECAST_DAYS`. Expected meetings have stable IDs (`forecast:<agency>:<YYYY-MM-DD>`), status `scheduled`, the agency's official URL, and no documents, items, or topics. They are never generated for past dates. The database, ingestion state, and change history are unaffected.
- **Real records replace forecasts.** Any stored record from the same agency within three days of an expected date suppresses that forecast. This includes an agenda, a date-only notice, a one-off notice, or a cancellation. A canceled regular meeting therefore shows as canceled, never as also expected.
- **The public boundary distinguishes the two kinds.** Every exported meeting has `kind: 'confirmed' | 'forecast'`; forecasts add `forecast: { ruleLabel, basis, source }`. Older consumers would otherwise read forecasts as real meetings, so `schemaVersion` moves to **2**. `scripts/check_export.py` validates the invariants: IDs, no evidence or topics, future dates only, the agency link, and suppression near real records.
- **The UI hides predictions unless asked.** **Show expected meetings** is off by default and bookmarkable (`?expected=1`). Expected cards have a dashed outline, an "Expected · no agenda yet" label, and a plain-language basis ("Usually the first Wednesday at 3:00 PM"). They link to the agency's official page and have no meeting page or calendar download. They never appear among earlier meetings and match no topic filter. Meeting counts include them only when shown. Confirmed meetings without a posted agenda keep their existing presentation.
- **Canceled meetings recede but stay readable.** Canceled cards use a grey surface and a struck-through, muted title with at least 4.5:1 text contrast. They keep the colored "Canceled" pill, and their link text includes "Canceled:" for screen readers. This applies on the timeline and topic pages.

## Alternatives and consequences

Storing forecasts as meeting rows was rejected. It would mix predictions into ingestion state, change history, and freshness. Generating forecasts in the website was rejected so the public JSON stays the single checked boundary. Expected meetings were not shown by default, because a wrong prediction looks like a real commitment.

Learned patterns can be wrong. Small samples are common, holidays and summer recesses are not modeled, and an agency can change its time, as South Lake Tahoe did in September 2026. The bar, visible support counts, operator review of the Git diff, and default hiding limit that risk. Agencies with insufficient data simply get no expected meetings. Forecasts do not imply live coverage or a checked agency calendar, and they add no services, jobs, or cost.

## Revisit when

More captures make learning more reliable, or an agency publishes an annual calendar that can be configured directly. Also revisit if readers mistake expected meetings for confirmed ones, or if holiday and recess exceptions become necessary.
