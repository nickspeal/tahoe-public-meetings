# Meeting duration and pacing — October 20, 2026

Purpose: give every calendar export a defensible end time (decision [0013](../decisions/0013-estimated-meeting-durations.md)). Agencies rarely publish an end time, so the system estimates, labels the estimate, and improves it from observed lengths.

## Methodology

1. **Observed lengths** are stored per meeting (`observed_duration_minutes`, `duration_source` = `minutes`, `recording`, `manual`, `published`, or `minutes-text`). Operators add them with `tahoe-ingest set-duration`; `tahoe-ingest detect-durations` finds them automatically in stored minutes text.
2. **Detector.** Only documents whose title contains "minutes" or "synopsis" are scanned (agendas state scheduled times, not actual ones). It needs exactly one distinct "called to order / convened / commenced ... H:MM a.m." time and exactly one "adjourned / adjournment ... H:MM p.m." time, and a span of 10 minutes to 16 hours. Anything ambiguous (several start times, a recess and reconvening, only one end) records nothing. `detect-durations` is a dry run unless `--apply`, and never overwrites an existing value.
3. **Model** (`config/meeting-durations.json`, see [config/README.md](../../config/README.md)). Two modes:
   - `fixed`: the body keeps a roughly constant length regardless of agenda; `fixedMinutes` times `longBiasFactor`, rounded up.
   - `estimated`: `baseMinutes` plus a weight per agenda item, times `longBiasFactor`, clamped to `minMinutes`..`maxMinutes`, rounded up to `roundUpToMinutes`. An item's weight is the heaviest matching keyword in `sectionWeights` (matched case-insensitively against the item's section and title together), else `perItemMinutes`. Consent items weigh 1 minute, reports 5, presentations and workshops 15, closed session 10, public hearings 25.
4. **Calibration.** `tahoe-ingest duration-report` compares observed lengths with the model's estimate for the same meetings: count, median, p90, mean error (positive means the estimate ran long, the intended direction), and how many were too short.

## What the minutes-text detector found (dry run, shared local database)

The local database holds 108 meetings, none with a stored minutes URL or recording URL, and no document titled "minutes". The only minutes-like documents are three IVGID Board of Trustees meeting synopses. Dry run results (nothing was written):

| Agency | Meetings with minutes/synopsis text | Detected | Notes |
| --- | --- | --- | --- |
| ivgid | 3 | 1 | 2026-10-14: called to order 4:00 p.m., adjourned 5:24 p.m. = **84 minutes** (correct on inspection). 2026-09-09 and 2026-09-23: an adjournment time exists (6:15 p.m. on 09-09, "followed by closed session") but no called-to-order time, so nothing is recorded. |
| all others | 0 | 0 | No minutes documents are captured yet. |

Three Truckee Tahoe Airport agendas contain "called to order at ... p.m." text; they are scheduled times, which is why agendas are excluded. One detection is not enough to tune any agency. The detector will become useful once minutes are captured (minutes URLs are not yet ingested as documents).

## Initial configuration and why

All values are unverified starting guesses biased long. None is calibrated against observed data (the single observation, IVGID 84 minutes, is below the default estimate and consistent with the long bias).

| Agency | Mode | Setting | Exported estimate | Reasoning |
| --- | --- | --- | --- | --- |
| ttd | fixed | 150 min, x1.25 | 210 min (3.5 h) | Board meetings tend to fill a set morning slot regardless of agenda. |
| lahontan-board | fixed | 240 min, x1.25 | 300 min (5 h) | Regional Water Board business meetings run long. |
| trpa-governing | fixed | 240 min, x1.25 | 300 min (5 h) | Governing Board meetings are a long session. |
| slt | estimated | base 120 min | 210-360 min for current agendas | City Council agendas with 20-40 items, closed session, and hearings. Likely the most over-estimated; the first to tune. |
| truckee | estimated | base 90 min | about 120 min | Town Council guess; agendas are short. |
| every other agency | estimated | default base 90 min, 8 min/item | 120 min when no agenda is available | No data: default, a guess biased long. |

## Tuning

1. Record lengths: `tahoe-ingest set-duration <meeting-id> --minutes N --source minutes|recording|manual`, or `tahoe-ingest detect-durations` then `--apply`.
2. Run `tahoe-ingest duration-report` (add `--json` for scripts).
3. Edit `config/meeting-durations.json`: change an agency's mode or `fixedMinutes`/`baseMinutes`, adjust keyword weights, or tweak `longBiasFactor`. Overrides merge with the defaults.
4. Run `python3 scripts/project.py refresh` to re-export; no re-ingest is needed.

## Deferred: research agent over recordings

Not built. A later agent would collect actual lengths for past meetings and feed them to `set-duration`:

- **Recordings:** YouTube channel uploads and Granicus/other hosted video, where the video duration approximates meeting length (check for pre-roll, recess edits, and closed sessions held off camera). Source tag `recording`.
- **Minutes:** called-to-order and adjourned times in approved minutes PDFs, including those the text detector cannot match. Source tag `minutes`.
- **Output per agency:** observed count, median, p90, whether length varies with agenda size (estimated mode) or not (fixed mode), and a proposed config change. The maintainer approves config changes; the agent does not edit the model on its own. It should stop for review if a source requires login or the sample is under five meetings.
