# 0037: The agenda's start time wins, and CivicClerk times are local

Status: accepted (maintainer approval of the meeting-warnings review, 2026-11-09)
Date: 2026-11-09
Related decisions: [0033](0033-production-data-only-from-live-pipeline.md)

## Context
A review of every meeting with warnings in the live export of 2026-11-09 found two start-time caveats shown to residents on 15 meetings, and neither named a problem a resident could act on:

- **"Archive playback timestamps are not used as scheduled start times; see the official agenda."** (13 meetings). A Granicus archive row shows when the recording starts, so discovery sets no start time from it and added this note. The meeting keeps the start stored from its upcoming row, or the export reads it from the agenda's first page, so 12 of the 13 showed the time their agenda prints. The 13th, a TCPUD board budget workshop on November 4, showed 4:00 p.m. while its revised agenda says 3:45 p.m.: the export read the agenda only for a meeting stored without a start. Past meetings are not captured again, so the stored note never went away.
- **"Confirm the scheduled start in the agenda; CivicClerk wall-clock timestamps are not treated as UTC instants."** (2 meetings, Incline Village GID). From the first IVGID adapter (October 2026), discovery set no start time from the CivicClerk API, because its `eventDate` values end in "Z" and might be UTC instants; the test said "Agenda must confirm wall-clock time." The values are local wall-clock times: on 2026-11-09, the API's `16:00:00Z` for events 745, 746, and 756 matched agendas printing 4:00 p.m., and `17:00:00Z` for event 747 matched 5:00 p.m. (4 of 4). Read as UTC, they would be 9:00 and 10:00 a.m.

## Decision
- **The agenda's printed start time wins.** At export, a start time read from the agenda's first page (`start_times.agenda_start_time`: printed beside the meeting's own date, and agreed by every agenda document) replaces the stored start whenever it is found, not only when none is stored or the meeting is planned. Without one, the stored start stays.
- **Archive rows add no note.** Discovery still sets no start time from a Granicus archive row, and adds no warning.
- **CivicClerk times are local.** Discovery reads the `HH:MM` of `eventDate` as America/Los_Angeles wall-clock time and ignores the "Z"; midnight means no time was entered. This reverses the October 2026 choice above. The agenda's time still wins at export if the two ever differ.
- **The two retired caveats are never exported.** The export drops them from stored warning lists, so settled past meetings lose them without a database write.

## Alternatives and consequences
Limiting the override to meetings whose stored start predates the newest agenda version was considered and not adopted: the agenda reader already needs the time beside the meeting's own date and agreement between agendas, and of the 144 exported meetings whose agenda prints a start, the published start matched it in 143 (the TCPUD workshop was the exception). An agenda whose first page prints an earlier session (a closed session) beside the meeting date could now replace a correct listing time; no current agency's agenda does this.

IVGID is the only CivicClerk agency. A new CivicClerk tenant that writes real UTC instants would need a per-agency option.

## Revisit when
An agenda's first page prints a time beside the meeting date that is not its start, or a CivicClerk tenant's API times disagree with its agendas.
