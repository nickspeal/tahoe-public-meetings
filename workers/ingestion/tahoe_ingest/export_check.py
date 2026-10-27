"""Validate the worker-to-website JSON boundary, including evidence integrity.

Ported from ``scripts/check_export.py`` so the installed worker (and its
container) can refuse to publish an invalid export. It reads the bundled
topic catalog and agency registry instead of the checkout's files.
"""
from __future__ import annotations

from datetime import date, datetime
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from .catalog import followed_agencies, public_topics

PACIFIC = ZoneInfo("America/Los_Angeles")
SUPPRESSION_DAYS = 3  # Matches tahoe_ingest.schedules: a nearby real record replaces a forecast.


def check(data):
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)

    def timestamp(value, label, nullable=False):
        if value is None and nullable:
            return
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            require(dt.tzinfo is not None, f"{label}: timezone missing")
        except (AttributeError, TypeError, ValueError):
            errors.append(f"{label}: invalid timestamp")

    def url(value, label, nullable=False):
        if value is None and nullable:
            return
        parsed = urlparse(value or "")
        require(parsed.scheme in ("http", "https") and bool(parsed.netloc), f"{label}: invalid public URL")

    require(data.get("schemaVersion") == 2, "schemaVersion must be 2")
    require(data.get("mode") in ("live", "fixture"), "mode must be explicit")
    require(bool(data.get("coverageNote")), "coverageNote missing")
    timestamp(data.get("generatedAt"), "generatedAt")
    agencies = {a["id"] for a in data.get("agencies", [])}
    topics = {t["id"] for t in data.get("topics", [])}
    followed = set(followed_agencies())
    require(agencies <= followed, "exported agencies must be followed")
    require(data.get("topics") == public_topics(), "topics must match the ordered canonical catalog")
    for agency in data.get("agencies", []):
        require(agency.get("status") in ("ok", "error", "never_checked"), f"{agency['id']}: invalid freshness status")
        timestamp(agency.get("lastCheckedAt"), f"{agency['id']}.lastCheckedAt", nullable=True)
        url(agency.get("officialUrl"), f"{agency['id']}.officialUrl")
    seen_meetings = set()
    seen_items = set()
    citation_count = 0
    for meeting in data.get("meetings", []):
        mid = meeting["id"]
        require(mid not in seen_meetings, f"duplicate meeting {mid}")
        seen_meetings.add(mid)
        require(meeting.get("agencyId") in agencies, f"{mid}: unknown agency")
        require(meeting.get("timezone") == "America/Los_Angeles", f"{mid}: Pacific timezone required")
        require(meeting.get("status") in ("scheduled", "rescheduled", "canceled", "unknown"), f"{mid}: unknown status")
        timestamp(meeting.get("startsAt"), f"{mid}.startsAt", nullable=True)
        if meeting.get("date"):
            try:
                require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", meeting["date"])), f"{mid}: invalid local date format")
                datetime.strptime(meeting["date"], "%Y-%m-%d")
            except (TypeError, ValueError):
                errors.append(f"{mid}: invalid local meeting date")
        timestamp(meeting.get("updatedAt"), f"{mid}.updatedAt")
        duration = meeting.get("duration") or {}
        require(isinstance(duration.get("minutes"), int) and duration["minutes"] > 0, f"{mid}: duration.minutes required")
        require(duration.get("source") in ("published", "observed", "estimated"), f"{mid}: invalid duration source")
        require(bool(duration.get("basis")), f"{mid}: duration basis required")
        url(meeting.get("officialUrl"), f"{mid}.officialUrl")
        require(set(meeting.get("topics", [])) <= topics, f"{mid}: unknown meeting topic")
        require("short-term-rentals" not in meeting.get("topics", []) or "housing" in meeting["topics"], f"{mid}: STR must include housing")
        docs = {d["id"]: d for d in meeting.get("documents", [])}
        require(len(docs) == len(meeting.get("documents", [])), f"{mid}: duplicate document IDs")
        for doc in docs.values():
            url(doc.get("url"), f"{doc['id']}.url")
            require(bool(re.fullmatch(r"[0-9a-f]{64}", doc.get("sha256", ""))), f"{doc['id']}: invalid hash")
            timestamp(doc.get("fetchedAt"), f"{doc['id']}.fetchedAt")
            require(doc.get("mediaType", "application/pdf") in ("application/pdf", "text/html", "text/plain"), f"{doc['id']}: unsupported source format")
            pages = doc.get("pages", [])
            require(len({p["number"] for p in pages}) == len(pages), f"{doc['id']}: duplicate page")
            require(all(0 < p["number"] <= doc["pageCount"] for p in pages), f"{doc['id']}: invalid page bounds")
        for index, event in enumerate(meeting.get("history", [])):
            timestamp(event.get("at"), f"{mid}.history[{index}].at")
            require(event.get("change") in ("added", "revised"), f"{mid}: invalid history change")
            require(bool(event.get("document")), f"{mid}: history event lacks a document title")
        for item in meeting.get("items", []):
            iid = item["id"]
            require(iid not in seen_items, f"duplicate item {iid}")
            seen_items.add(iid)
            require(set(item.get("topics", [])) <= topics, f"{iid}: unknown topic")
            require("short-term-rentals" not in item.get("topics", []) or "housing" in item["topics"], f"{iid}: STR must include housing")
            summary = item.get("summary")
            citations = list(item.get("citations", []))
            if summary:
                require(summary.get("status") in ("published", "pending", "validation_failed"), f"{iid}: invalid summary status")
                if summary["status"] == "published":
                    require(bool(summary.get("text")) and bool(summary.get("citations")), f"{iid}: published summary lacks evidence")
                    citations.extend(summary.get("citations", []))
            for citation in citations:
                citation_count += 1
                doc = docs.get(citation.get("documentId"))
                require(doc is not None, f"{iid}: citation document missing")
                if not doc:
                    continue
                page = next((p for p in doc["pages"] if p["number"] == citation.get("page")), None)
                require(page is not None, f"{iid}: citation page missing")
                quote = " ".join(citation.get("quote", "").split())
                require(bool(quote), f"{iid}: empty citation quote")
                if page:
                    require(quote in " ".join(page["text"].split()), f"{iid}: citation quote absent from source page")
    seen_periods = set()
    for period in data.get("commentPeriods", []):
        pid = period.get("id")
        require(pid not in seen_periods, f"duplicate comment period {pid}")
        seen_periods.add(pid)
        require(period.get("agencyId") in agencies, f"{pid}: unknown agency")
        url(period.get("officialUrl"), f"{pid}.officialUrl")
        url(period.get("participationUrl"), f"{pid}.participationUrl", nullable=True)
        require(bool(period.get("sourceQuote")), f"{pid}: missing source quote")
        require(bool(re.fullmatch(r"[0-9a-f]{64}", period.get("sourceSha256", ""))), f"{pid}: invalid source hash")
        require(set(period.get("topics", [])) <= topics, f"{pid}: unknown topic")
        timestamp(period.get("closesAt"), f"{pid}.closesAt", nullable=True)
        timestamp(period.get("lastCheckedAt"), f"{pid}.lastCheckedAt")
        timestamp(period.get("updatedAt"), f"{pid}.updatedAt")
        try:
            closes = date.fromisoformat(period['closesOn'])
            opens = date.fromisoformat(period['opensOn']) if period.get('opensOn') else None
            require(opens is None or opens <= closes, f"{pid}: invalid date range")
        except (KeyError, ValueError, TypeError):
            errors.append(f"{pid}: invalid comment window dates")
    return errors, f"{len(seen_meetings)} meetings, {len(seen_items)} items, {citation_count} citation references"


def _local_day(meeting):
    if meeting.get("date"):
        return date.fromisoformat(meeting["date"])
    if meeting.get("startsAt"):
        return datetime.fromisoformat(meeting["startsAt"].replace("Z", "+00:00")).astimezone(PACIFIC).date()
    return None


def check_kinds(data):
    """Confirmed records vs. expected meetings derived from agency schedules at export time."""
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)

    agency_urls = {a["id"]: a.get("officialUrl") for a in data.get("agencies", [])}
    try:
        export_day = datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00")).astimezone(PACIFIC).date()
    except (KeyError, AttributeError, ValueError):
        export_day = None
    confirmed_days = {}
    forecasts = []
    for meeting in data.get("meetings", []):
        mid = meeting.get("id")
        kind = meeting.get("kind")
        require(kind in ("confirmed", "forecast"), f"{mid}: kind must be confirmed or forecast")
        try:
            day = _local_day(meeting)
        except (TypeError, ValueError, AttributeError):
            continue  # Reported by check().
        if kind != "forecast":
            require(meeting.get("forecast") is None, f"{mid}: confirmed meeting must not carry forecast details")
            if day:
                confirmed_days.setdefault(meeting.get("agencyId"), []).append(day)
            continue
        forecasts.append((meeting, day))
        info = meeting.get("forecast") or {}
        require(mid == f"forecast:{meeting.get('agencyId')}:{meeting.get('date')}", f"{mid}: forecast ID must be forecast:<agency>:<date>")
        require(not meeting.get("documents") and not meeting.get("items") and not meeting.get("topics"),
                f"{mid}: forecasts carry no documents, agenda items, or topics")
        require(meeting.get("status") == "scheduled" and bool(meeting.get("startsAt")), f"{mid}: forecast needs scheduled status and an expected start")
        require(bool(info.get("ruleLabel")) and bool(info.get("basis")) and info.get("source") in ("learned", "configured"),
                f"{mid}: forecast needs ruleLabel, basis, and source")
        require(meeting.get("officialUrl") == agency_urls.get(meeting.get("agencyId")), f"{mid}: forecast must link to the agency page")
    for meeting, day in forecasts:
        mid = meeting["id"]
        if day is None or not meeting.get("startsAt"):
            continue
        try:
            starts = datetime.fromisoformat(meeting["startsAt"].replace("Z", "+00:00")).astimezone(PACIFIC).date()
        except (AttributeError, ValueError):
            continue
        require(starts == day, f"{mid}: expected start is not on the forecast date")
        require(export_day is None or day > export_day, f"{mid}: forecasts must be after the export date")
        require(not any(abs((recorded - day).days) <= SUPPRESSION_DAYS for recorded in confirmed_days.get(meeting["agencyId"], [])),
                f"{mid}: a real record within {SUPPRESSION_DAYS} days must replace the forecast")
    return errors, len(forecasts)


def validate_export(data: dict) -> tuple[list[str], dict]:
    """All export errors plus counts; an empty error list means the export may be published."""
    errors, _ = check(data)
    kind_errors, expected = check_kinds(data)
    meetings = data.get("meetings", [])
    stats = {"meetings": sum(1 for m in meetings if m.get("kind") != "forecast"), "expectedMeetings": expected,
             "items": sum(len(m.get("items", [])) for m in meetings),
             "agencies": len(data.get("agencies", [])), "commentPeriods": len(data.get("commentPeriods", []))}
    return errors + kind_errors, stats
