# 0013: Estimated meeting durations are exported and labelled
Status: accepted
Date: 2026-10-20
Related ideas: IDEA-003

## Context

Calendar downloads previously omitted `DTEND` when an agency published no end time, on the rule that an unknown duration is never invented. The maintainer's Round 2 feedback is that downloadable meetings need a duration, estimated from agency pacing and agenda content when none is published, and biased long.

## Decision

Every exported meeting carries `duration: { minutes, source, basis }`, and every calendar event has a `DTEND`. `source` is `published` (agency stated), `observed` (recorded from minutes text, minutes, a recording, or a manual entry), or `estimated`. Published and observed values always win. Estimates come from the declarative model in `config/meeting-durations.json` (fixed-length bodies, or base time plus weighted agenda items), are rounded up and biased long, and are explained in `basis`. Estimated events say so in the calendar description and on the meeting page. The maintainer tunes the model from `tahoe-ingest duration-report`.

This replaces the previous "an unknown duration is never invented" rule for calendar exports. The start-time rule is unchanged: a missing start time is still never invented.

## Alternatives and consequences

An all-day or zero-length event was rejected because it misrepresents the meeting and breaks reminders. A single global default was rejected because pacing differs sharply by body. Estimates can be wrong; the long bias, explicit labelling, and observed-value override limit the harm. Initial per-agency values are guesses until observations accumulate.

## Revisit when

Calibration data shows persistent error for an agency, an agency begins publishing end times, or the deferred recording-length research step is built.

## Update 2026-10-28: per-agency settings in the registry
An agency's own settings moved from the `agencies` map of `config/meeting-durations.json` to a `duration` block on its entry in `packages/domain/agencies.json` ([decision 0027](0027-agency-definitions-in-configuration.md)); the file keeps the defaults. Estimates are unchanged, and a sibling body that `extends` another entry does not inherit its meeting length.
