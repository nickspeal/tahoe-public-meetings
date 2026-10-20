# 0014: Reviewed sources and public comment periods

Status: accepted
Date: 2026-10-20
Supersedes: selected source defaults in decision 0009

## Decision

The maintainer reviewed SR-001–034. Disable followed coverage for Douglas newsletter, FOWS E-News, Lahontan office, Tahoe Fund E-news, CalEPA, and USACE. Retain their registry entries and history. Keep LTBMU, Tahoe National Forest, and USFWS as sources of public comment opportunities as well as meetings. Keep statewide California/Nevada Parks sources because individual agenda items can concern Tahoe. Add separate South Lake Tahoe Parks and Recreation Commission and TCPUD Parks and Recreation Committee entries. The registry now has 44 entries, 38 followed.

Public comment windows are separate participation records with an opening date when known, closing date, optional verified exact deadline, instructions, topics, and official-source evidence. They appear on a dedicated Public comment page linked from navigation and the meeting timeline, not as multi-day meetings, forecasts, or estimated-duration calendar events. This interface choice is the local implementation default; the maintainer requested that comment periods count and left their presentation open. No current comment period is invented to fill an empty page.

Use the verified body-specific Granicus, Legistar, CivicClerk, Municode, and embedded public meeting-library sources identified during review. Prefer small agendas and structured accessible HTML to full packets. Keep page/section text, exact cited excerpts, source links, hashes, and source versions; PDFs stay temporary. Rolling WordPress agenda links are checked on later discovery runs, while earlier captured links and text remain stored. Archive reconstruction/Wayback search is optional later work, not a prerequisite.

Public fetches use verified TLS. Native curl is a bounded transport fallback for the certificate/403 failures observed in Python. This recovery is part of the worker and its container dependencies. Persistent Placer 403 failures are deferred in [SOURCE-001](../issues/SOURCE-001-placer-downloads.md); retain valid notices. Broad RSS feeds require body filtering and are not treated as approval to ingest unrelated boards. These are reusable local commands, not a newly provisioned subscription or cloud job.

## Reliability and corrections

Quarantine the wrongly attributed October 1 OVDRC cancellation; retain its provenance and restore the genuine August 25 TBDRC notice. Preserve distinct TNT/TMA Board and transit-workshop records. Persist aliases when an agenda's native ID corresponds to an existing calendar notice, so repeated acquisition updates the record. Playback timestamps and generic recurring schedules do not establish a verified scheduled start. Pending future agendas are routine follow-up, not evidence that an agency should be removed.

## Revisit when

An official source becomes inaccessible, an appropriate public comment feed is identified, the maintainer changes source scope, or publication requires a different presentation for participation opportunities.
