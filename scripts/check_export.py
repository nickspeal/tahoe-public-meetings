#!/usr/bin/env python3
"""Check the actual worker-to-website JSON boundary, including evidence integrity."""
import argparse
from datetime import date, datetime
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PACIFIC = ZoneInfo("America/Los_Angeles")
SUPPRESSION_DAYS = 3  # Matches tahoe_ingest.schedules: a nearby real record replaces a forecast.
CATALOG = json.loads((ROOT / "packages/domain/topics.json").read_text())
PUBLIC_TOPICS = [{key: topic[key] for key in ("id", "name", "description")} for topic in CATALOG]


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
    followed = {a["id"] for a in json.loads((ROOT / "packages/domain/agencies.json").read_text()) if a["follow"]}
    require(agencies <= followed, "exported agencies must be followed")
    require(data.get("topics") == PUBLIC_TOPICS, "topics must match the ordered canonical catalog")
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
        url(meeting.get("minutesUrl"), f"{mid}.minutesUrl", nullable=True)
        url(meeting.get("recordingUrl"), f"{mid}.recordingUrl", nullable=True)
        require(meeting.get("minutesStatus") in (("approved", "draft") if meeting.get("minutesUrl") else (None,)),
                f"{mid}: minutesStatus must be approved or draft with minutesUrl, else absent")
        require(not meeting.get("minutesNote") or meeting.get("minutesStatus") == "draft", f"{mid}: minutesNote is only for draft minutes")
        for key in ("url", "remoteUrl", "watchUrl"):
            url((meeting.get("participation") or {}).get(key), f"{mid}.participation.{key}", nullable=True)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=ROOT / "apps/web/src/data/public.json")
    args = parser.parse_args()
    data = json.loads(args.path.read_text())
    errors, stats = check(data)
    kind_errors, expected = check_kinds(data)
    errors += kind_errors
    stats += f"; {expected} of those meetings are expected dates derived from agency schedules"
    if errors:
        raise SystemExit("Export validation failed:\n" + "\n".join(f"- {e}" for e in errors))
    print(f"Public export valid: {stats}.")


if __name__ == "__main__":
    main()
