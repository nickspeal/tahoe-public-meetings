"""Validate operator-maintained one-off notices before any database writes."""
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
import json

from .catalog import followed_agencies, topic_ids
from .core import starts_at


def load_one_offs(path: str | Path) -> dict:
    bundle = json.loads(Path(path).read_text())
    meetings = bundle["meetings"]
    if not isinstance(meetings, list):
        raise ValueError("meetings must be a list")
    seen = set()
    for row in meetings:
        key = (row["agency"], row["id"])
        if key in seen:
            raise ValueError(f"duplicate one-off meeting: {key}")
        seen.add(key)
        if row["agency"] not in followed_agencies():
            raise ValueError(f"unknown or unfollowed agency: {row['agency']}")
        if not row["id"].startswith("one-off-") or not row["title"].strip():
            raise ValueError("one-off requires a stable one-off- ID and title")
        url = urlparse(row["source_meeting_url"])
        if url.scheme not in ("https", "http") or not url.netloc:
            raise ValueError("one-off requires an official public meeting URL")
        if row.get("status", "unknown") not in {"scheduled", "canceled", "rescheduled", "unknown"}:
            raise ValueError("invalid one-off meeting status")
        if row.get("date"):
            date.fromisoformat(row["date"])
        if row.get("start_time") and not row.get("date"):
            raise ValueError("start_time requires a date")
        starts_at(row)  # Reject ambiguous or invalid times using normal ingestion rules.
        if row.get("timezone", "America/Los_Angeles") != "America/Los_Angeles":
            raise ValueError("one-off meetings must use America/Los_Angeles")
        tags = row.get("topics", [])
        if not isinstance(tags, list) or not set(tags) <= topic_ids():
            raise ValueError("unknown one-off topic")
        row.setdefault("topics", [])
        row.setdefault("documents_complete", False)
        row.setdefault("items_complete", False)
        row.setdefault("warnings", ["Manually registered meeting notice; check the official page for updates."])
    return bundle
