"""One-time removal of stored meeting warnings that no code writes any more (decision 0038).

A meeting's warnings are rewritten only when a capture or reparse touches it, and
settled past meetings are never captured again, so a caveat the pipeline has
stopped writing stays in the public export for good. ``clean-retired-warnings``
removes them: every exact string in ``RETIRED``, and the "await reprocessing"
note from meetings that have no active agenda item (nothing awaits it). Other
warnings, and the order of those kept, are left as stored.

It is a dry run unless ``apply`` is set; running it again changes nothing.
Reading is one SQL statement and applying is one more (the hosted database
bills per operation). ``updated_at`` is left alone: dropping a caveat is not a
change in what the source says. Remove this module once production has been
cleaned.
"""
from __future__ import annotations

import json

from .core import REPROCESSING_WARNING

RETIRED = (
    # Capture's stored availability note; availability is derived since decision 0033.
    "No agenda PDF is linked; agenda items require review.",
    # Early notice extractions; removed in decision 0028's 2026-10-29 amendment.
    "Check the notice for changes.",
    # Blanket discovery caveats on every WordPress post and Granicus publisher row.
    "Meeting date and agenda items require source review.",
    "Publisher row date/time and agenda items require source review.",
    # Planned meetings; the website derives "Subject to change" from the export's `planned` flag.
    "Subject to change.",
    # Start-time caveats retired by decision 0037; the export already drops them (core.RETIRED_START_WARNINGS).
    "Archive playback timestamps are not used as scheduled start times; see the official agenda.",
    "Confirm the scheduled start in the agenda; CivicClerk wall-clock timestamps are not treated as UTC instants.",
)


def plan(conn) -> list[dict]:
    """Each affected meeting: the warnings removed and the ones kept."""
    rows = conn.execute("""SELECT m.id, m.warnings,
            EXISTS (SELECT 1 FROM agenda_items i WHERE i.meeting_id = m.id AND i.active) AS has_items
        FROM meetings m
        WHERE m.warnings ?| %s::text[] OR m.warnings ? %s
        ORDER BY m.id""", (list(RETIRED), REPROCESSING_WARNING)).fetchall()
    changes = []
    for row in rows:
        warnings = row["warnings"] or []
        removed = [w for w in warnings if w in RETIRED or (w == REPROCESSING_WARNING and not row["has_items"])]
        if removed:
            changes.append({"meeting": row["id"], "remove": removed, "keep": [w for w in warnings if w not in removed]})
    return changes


def clean_retired_warnings(conn, *, dry_run: bool = True) -> dict:
    changes = plan(conn)
    if changes and not dry_run:
        with conn.transaction():
            conn.execute("""UPDATE meetings m SET warnings = s.warnings::jsonb
                FROM unnest(%s::text[], %s::text[]) AS s(id, warnings) WHERE m.id = s.id""",
                         ([c["meeting"] for c in changes], [json.dumps(c["keep"]) for c in changes]))
    removed: dict[str, int] = {}
    for change in changes:
        for warning in change["remove"]:
            removed[warning] = removed.get(warning, 0) + 1
    return {"summary": {"dryRun": dry_run, "meetings": len(changes), "removed": dict(sorted(removed.items()))},
            "meetings": changes}
