# Meeting schedule patterns — October 20, 2026

`tahoe-ingest learn-schedules --dry-run` was run against the shared local database (108 stored meetings across 33 followed agencies/bodies, captured October 20, 2026). Learned schedules were then written with `--write`, and TTD was hand-configured from the maintainer's statement. See [decision 0012](../decisions/0012-forecast-meetings-from-schedule-patterns.md) and the [configuration guide](../../config/README.md#meeting-schedules-and-expected-meetings).

## Method

Evidence is each agency's stored, non-canceled meetings with a verified start time. One-off notices (`one-off-` IDs) and titles naming a special, joint, workshop, hearing, retreat, or study session are excluded. Canceled and excluded records still count as "a record exists" when checking pattern dates. The learner compares monthly ordinal rules (1st–4th or last weekday), the ordinal pairs 1st/3rd and 2nd/4th, and every-other-week cadences, each at the most common start time. A rule loses half a point for every pattern date in the observed span with no stored record within three days. That stops one stray third-week meeting from turning "first Wednesday" into "first and third Wednesday". If several rules tie, the simpler monthly form wins. Quarterly or bimonthly months are inferred only when every matched month falls on a regular step.

A proposal requires **at least 3 matching meetings and a match rate of at least 60%** among regular timed meetings. Most agencies have only two to five captured meetings, so learned rules rest on thin evidence. The table records "regular" (meetings used as evidence) and "matched" so readers can judge that support.

## Findings

| Agency | Pattern | Regular | Matched | Result | Legacy CSV cell (2023, unverified) |
| --- | --- | ---: | ---: | --- | --- |
| ttd | First Wednesday, 3:00 PM | 4 | 4 | **Configured** by the maintainer; learner agrees | 1st Wednesday |
| truckee | Second and fourth Tuesday, 5:00 PM | 5 | 5 | Learned | — |
| ctc | Third Thursday of Mar/Jun/Sep/Dec, 9:30 AM | 3 | 3 | Learned (quarterly inferred) | Second Tuesday 10am |
| douglas-commissioners | First and third Thursday, 10:00 AM | 3 | 3 | Learned | 1st and 3rd Thursdays |
| douglas-planning | Second Tuesday, 1:00 PM | 3 | 3 | Learned | 2nd Tuesdays |
| el-dorado-planning | Second and fourth Thursday, 9:00 AM | 3 | 3 | Learned | 2nd and 4th Tuesdays 8:30am |
| placer-transportation | Fourth Wednesday, 9:00 AM | 3 | 3 | Learned | 4th Wednesdays 9am |
| slt-planning | Third Thursday, 3:00 PM | 3 | 3 | Learned | — |
| south-shore-tma | Third Friday, 8:30 AM | 3 | 3 | Learned | 3rd Friday 8:30am |
| washoe-planning | First Tuesday, 6:00 PM | 3 | 3 | Learned | 1st Tuesday of each month 6:30pm |
| slt | Second Tuesday, 9:00 AM (best) | 4 | 2 | Insufficient: start moved from 9:00 AM to 5:30 PM in September | 1st and 3rd Tuesdays |
| washoe-commissioners | Second Tuesday, 10:00 AM (best) | 3 | 2 | Insufficient; two regular meetings canceled | 2nd, 3rd, and 4th Tuesdays at 10am |
| el-dorado-supervisors | Every other Tuesday, 9:00 AM (best) | 3 | 2 | Insufficient | 2-3 Tuesday per month |
| ntpud | Second Tuesday, 2:00 PM (best) | 3 | 2 | Insufficient; September met on a Wednesday | — |
| lahontan-board | First Thursday, 9:00 AM (best) | 3 | 2 | Insufficient; three canceled | Monthly, dates vary |
| california-state-parks | First Thursday, 9:00 AM (best) | 4 | 2 | Insufficient; two commissions share the entry | n/a |
| truckee-tahoe-airport | Fourth Wednesday, 4:30 PM (best) | 2 | 2 | Insufficient; September was a special meeting | — |
| trcd | Third Thursday, 1:00 PM (best) | 2 | 2 | Insufficient | Monthly |
| california-fish-game | No consistent weekday | 3 | 1 | Insufficient; multi-day meetings | Monthly |
| state-lands | No consistent time | 4 | 1 | Insufficient; 10:00 AM, 12:00 PM, 1:00 PM | Bi-Monthly |
| ndep, ntcd, truckee-north-shore-tma | — | 1 | 1 | Insufficient | various |
| ivgid, placer-planning, placer-supervisors, stpud, tcpud, trpa-advisory, trpa-governing | — | 0 | 0 | Insufficient: no stored start times | various |
| douglas-transportation, nevada-state-parks, placer-tahoe-design-review | — | 0 | 0 | Insufficient: only special, joint, hearing, or canceled records | various |

Pattern dates in the observed span with no stored record (possible capture gaps or skipped months, not proof of a missed meeting): TTD July 1 and September 2, Douglas planning September 8, Washoe planning June 2.

Several learned patterns match the 2023 CSV cells. El Dorado planning (Thursday rather than Tuesday), CTC (quarterly Thursday rather than second Tuesday), and Washoe planning (6:00 PM rather than 6:30 PM) differ from them, which supports learning from current records instead of the stale spreadsheet. Agencies listed as insufficient get no forecasts until more timed regular meetings are captured, or until an operator configures a verified schedule.

## Result

On October 20, the 90-day export produced 30 expected meetings across these 10 agencies. TTD expects December 2 and January 6. Its stored November 4 listing (date only, no agenda yet) replaces that forecast. Re-run `learn-schedules --dry-run` after new captures; holiday cancellations and summer recesses are not modeled.
