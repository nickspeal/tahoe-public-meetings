# 0008: Shared topics and filters within a meeting

Status: accepted product direction; catalog details are implementation defaults
Date: 2026-10-20
Related ideas: [IDEA-002](../ideas/IDEA-002-topics-alerts.md), [IDEA-003](../ideas/IDEA-003-calendar.md)

## Context

The maintainer requested a consistent global topic list that can grow beyond the initial four topics. On each meeting page, residents should see the topics covered by that meeting, see tags without opening every agenda item, and filter the agenda by selected topics.

## Decision

Keep one version-controlled catalog of stable topic IDs, public names, definitions, and examples. Ingestion, operator overrides, exports, topic pages, timeline controls, and meeting controls all use that catalog. Preserve the existing `biking`, `transit`, `housing`, and `environment` IDs and their URLs.

The initial expanded catalog adds Roads & traffic, Land use & development, Parks & recreation, Public safety, Budgets & funding, Water & utilities, Health & community services, and Government & administration. These names and boundaries are implementation defaults that the maintainer can refine. Adding a catalog entry does not by itself justify assigning it to an item: automatic assignments still require item-specific evidence, and operator overrides remain authoritative.

Meeting filters allow multiple topics and show items matching any selected topic. Clearing the selection restores the full agenda. Counts reflect unique agenda items; a multi-topic item appears once. Preserve official item order and numbers. Display topics on collapsed agenda rows, keep filter state shareable in the URL, and ensure links to individual agenda items remain usable when filters are active. Items without a confident assignment remain untagged.

This supersedes the fixed four-topic limit in decisions 0002 and 0007. It does not change the three-agency scope or the deferral of accounts and alerts.

## Next local improvement

The maintainer also authorized advancing useful ideas from the repository. The next bounded addition is a single-meeting calendar download from IDEA-003. Use verified meeting start times, stable event identifiers, official links, and explicit cancellation status. Do not guess a duration or turn an unknown start time into an all-day event. Downloads are snapshots; they do not subscribe a resident to updates. *Updated 2026-10-29: the download became an **Add to calendar** menu that also offers prefilled Google Calendar and Outlook.com links, and canceled meetings now get no calendar event at all instead of one marked canceled.* No calendar account access, cloud deployment, or background scheduling is needed.

Archive keyword search and broader source capture remain useful follow-ups. Subscription alerts, accounts, polling, and advocacy workflows remain deferred.
