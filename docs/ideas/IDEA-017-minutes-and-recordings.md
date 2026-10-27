# IDEA-017: Minutes and recording links

Status: proposed
Stage: MVP polish

## Problem and audience
Every meeting page has an "After the meeting" card with Minutes and Recording links, and every one says "Not yet available" because no adapter sets `minutes_url` or `recording_url`. Residents who missed a meeting cannot find what happened without searching each agency's site. The accepted scope already calls for recordings and minutes when available.

## Smallest useful version
Fill the two existing fields from free sources, in this order of preference:

1. **Minutes.** Link the agency's posted minutes when the listing row has them (Granicus, Legistar `EventMinutesFile` with Final status, CivicClerk, STPUD, PCTPA, TRPA, Lahontan, NTCD, Nevada water board). Otherwise link the draft minutes in the same body's next packet, found through the "Approval of minutes of <date>" agenda item and its packet pages (the item-to-page mapping from IDEA-013/016), as `packet.pdf#page=N`. Most agencies never post approved minutes, so the draft link is the common case. Label it "Draft minutes (in the <date> packet)". An approved link replaces a draft one.
2. **Recordings.** Prefer YouTube when an agency also uses another host. A configured `youtube` matcher per body (channel or playlist ID, include and exclude title patterns) reads the free channel feed each run and matches by body and title date. Otherwise use the link on the listing row (Granicus MediaPlayer, CivicClerk media, Lahontan's CAL-SPAN link).
3. **TTD.** Clips cannot be discovered (see research). Add an operator-maintained file of meeting-to-clip links, checked against the clip's title and date and rechecked weekly, and scan TTD meeting posts for any recording link TTD adds later. Ask TTD to post clip links on its meeting pages, as it did in February 2026.

Draft-minutes matching: a dated item matches the same body's meeting on that date; an undated item matches the body's immediately preceding meeting only if exactly one exists; items naming another body are skipped. A meeting never takes minutes from a packet dated before it.

Deferred: summarizing outcomes from minutes or transcripts, using Zoom transcripts, and archiving recordings.

## Evidence and dependencies
See [minutes and recordings sources](../research/minutes-and-recordings-sources-2026-10-27.md). About 80 dated minutes-approval items are already in the 2026-10-27 export. YouTube channel feeds need no key; a one-time backfill to 2026-01-01 needs a free YouTube Data API key (a few dozen quota units) or a local scrape. The 60-day look-back in scheduled runs is too short for quarterly bodies and late uploads, so draft-minutes matching should run over stored agenda items rather than depend on re-scraping the earlier meeting. Placer County minutes are unreachable while placer.ca.gov serves a Cloudflare challenge.

The export would gain a minutes kind (draft or approved) so the page can label drafts. Recording and minutes links add no model cost.

## Acceptance criteria
Fixtures for SLT, Truckee, a Granicus body, a Legistar body and TTD show the expected links. No link attaches to the wrong body or date: ambiguous matches leave the field empty. Draft links are labeled as drafts. Coverage is reported per agency in the run summary.

## Open questions
Should the page show a separate "Action summary" (Douglas) when no minutes exist? Should a Granicus video remain as a fallback link when a YouTube match also exists?

## Links and updates
- 2026-10-27: proposed after the maintainer asked why the Minutes and Recording sections are blank. The maintainer confirmed that a draft-minutes link is useful because approved minutes are rarely posted.
