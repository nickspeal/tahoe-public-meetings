"""A meeting's agenda history: when each official document, and each changed copy of it, was first found.

Every distinct file the pipeline downloads is kept as a `document_versions` row whose
`fetched_at` is the first time those bytes were seen, so the history is read from
stored rows at export time and needs no schema change.

The dates are when this site first found a version, not when the agency posted it;
for meetings collected after the fact they can fall long after the meeting.

A new file whose extracted text matches the previous version (a re-rendered webpage
or a re-saved PDF) is not a change a reader could see, so it is left out.
"""
from __future__ import annotations

HISTORY_SQL = """SELECT v.meeting_id,v.source_document_id,v.title,v.fetched_at,
    (SELECT md5(string_agg(p.text, E'\\f' ORDER BY p.page_number)) FROM document_pages p
     WHERE p.document_version_id=v.id) AS text_md5
    FROM document_versions v ORDER BY v.meeting_id,v.source_document_id,v.fetched_at,v.id"""


def document_history(rows, iso) -> dict[str, list[dict]]:
    """Group `HISTORY_SQL` rows into each meeting's events, oldest first."""
    history: dict[str, list[dict]] = {}
    previous = None
    for row in rows:
        key = (row["meeting_id"], row["source_document_id"])
        if previous and previous[0] == key and previous[1] == row["text_md5"]:
            continue
        change = "revised" if previous and previous[0] == key else "added"
        previous = (key, row["text_md5"])
        history.setdefault(row["meeting_id"], []).append(
            {"at": iso(row["fetched_at"]), "change": change, "document": row["title"]})
    for events in history.values():
        events.sort(key=lambda event: event["at"])
    return history
