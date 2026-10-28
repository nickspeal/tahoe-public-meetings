"""Agency meeting-schedule patterns: validation, learning, and export-time forecasts.

A schedule is a small declarative rule set stored on an agency registry entry.
Forecasts derived from it are a public-export view only: they are never stored
as meetings, never generated for past dates, and any real record from the same
agency within ``SUPPRESSION_DAYS`` replaces them (including a cancellation).
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = "America/Los_Angeles"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
ORDINALS = (1, 2, 3, 4, "last")
ORDINAL_WORDS = {1: "first", 2: "second", 3: "third", 4: "fourth", "last": "last"}
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December")
DEFAULT_FORECAST_DAYS = 90
SUPPRESSION_DAYS = 3
MIN_MATCHED = 3
MIN_MATCH_RATE = 0.6
# Model selection cost of a predicted date, within the observed span, that has no stored record.
GAP_PENALTY = 0.5
RULE_KEYS = {"ordinal", "every", "anchor", "weekday", "months", "time", "timezone", "label"}
SCHEDULE_KEYS = {"source", "rules", "confidence", "learnedFrom", "note"}
# Titles that identify meetings outside the regular cadence. They stay public,
# but they are not evidence for the regular pattern.
IRREGULAR_TITLE = re.compile(
    r"\b(special|workshops?|joint|retreat|study sessions?|hearings?|town halls?|tours?|orientation|emergency|adjourned)\b",
    re.I)
_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


# ---------------------------------------------------------------- validation

def validate_schedule(agency_id: str, schedule) -> None:
    """Reject malformed schedules when the registry loads, before any export."""
    def fail(message):
        raise ValueError(f"invalid schedule for {agency_id}: {message}")

    if not isinstance(schedule, dict):
        fail("must be an object")
    if set(schedule) - SCHEDULE_KEYS:
        fail(f"unknown keys {sorted(set(schedule) - SCHEDULE_KEYS)}")
    if schedule.get("source") not in ("learned", "configured"):
        fail("source must be learned or configured")
    confidence = schedule.get("confidence")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                                   or not 0 <= confidence <= 1):
        fail("confidence must be between 0 and 1")
    if "learnedFrom" in schedule and not isinstance(schedule["learnedFrom"], dict):
        fail("learnedFrom must be an object")
    rules = schedule.get("rules")
    if not isinstance(rules, list) or not rules:
        fail("rules must be a non-empty list")
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - RULE_KEYS:
            fail(f"unknown rule keys in {rule}")
        if rule.get("weekday") not in WEEKDAYS:
            fail("weekday must be a lowercase English weekday")
        if not isinstance(rule.get("time"), str) or not _TIME.fullmatch(rule["time"]):
            fail("time must be HH:MM (24-hour)")
        if rule.get("timezone") != PACIFIC:
            fail(f"timezone must be {PACIFIC}")
        if not isinstance(rule.get("label"), str) or not rule["label"].strip():
            fail("label is required")
        months = rule.get("months")
        if months is not None and (not isinstance(months, list) or not months or len(set(months)) != len(months)
                                   or any(isinstance(m, bool) or not isinstance(m, int) or not 1 <= m <= 12 for m in months)):
            fail("months must be a non-empty list of distinct month numbers 1-12")
        if "every" in rule:
            if "ordinal" in rule:
                fail("use either ordinal or every, not both")
            if isinstance(rule["every"], bool) or not isinstance(rule["every"], int) or not 1 <= rule["every"] <= 8:
                fail("every must be a whole number of weeks from 1 to 8")
            try:
                anchor = date.fromisoformat(rule.get("anchor", ""))
            except (TypeError, ValueError):
                fail("every requires an anchor date (YYYY-MM-DD)")
            if WEEKDAYS[anchor.weekday()] != rule["weekday"]:
                fail("anchor date must fall on the rule weekday")
        else:
            if "anchor" in rule:
                fail("anchor is only used with every")
            if rule.get("ordinal") not in ORDINALS or isinstance(rule.get("ordinal"), bool):
                fail("ordinal must be 1-4 or last")


# ----------------------------------------------------------- date arithmetic

def _ordinal_of(day: date):
    """Return (n, is_last) for this weekday's occurrence within its month."""
    following = day + timedelta(days=7)
    return (day.day - 1) // 7 + 1, following.month != day.month


def matches_date(rule: dict, day: date) -> bool:
    if WEEKDAYS[day.weekday()] != rule["weekday"]:
        return False
    if rule.get("months") and day.month not in rule["months"]:
        return False
    if "every" in rule:
        return (day - date.fromisoformat(rule["anchor"])).days % (7 * rule["every"]) == 0
    n, last = _ordinal_of(day)
    return last if rule["ordinal"] == "last" else n == rule["ordinal"]


def rule_dates(rule: dict, start: date, end: date) -> list[date]:
    """Every date in [start, end] matching the rule, in order."""
    if end < start:
        return []
    weekday = WEEKDAYS.index(rule["weekday"])
    if "every" in rule:
        step = 7 * rule["every"]
        anchor = date.fromisoformat(rule["anchor"])
        current = anchor + timedelta(days=math.ceil((start - anchor).days / step) * step)
        found = []
        while current <= end:
            if not rule.get("months") or current.month in rule["months"]:
                found.append(current)
            current += timedelta(days=step)
        return found
    found = []
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        if not rule.get("months") or month in rule["months"]:
            first = date(year, month, 1)
            first_match = first + timedelta(days=(weekday - first.weekday()) % 7)
            if rule["ordinal"] == "last":
                candidate = first_match + timedelta(days=28)
                while candidate.month != month:
                    candidate -= timedelta(days=7)
            else:
                candidate = first_match + timedelta(days=7 * (rule["ordinal"] - 1))
            if candidate.month == month and start <= candidate <= end:
                found.append(candidate)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return found


def local_start(day: date, clock: str, zone_name: str = PACIFIC) -> datetime:
    """An aware local start; fold=0 for the (unlikely) repeated DST hour."""
    zone = ZoneInfo(zone_name)
    local = datetime.combine(day, time.fromisoformat(clock)).replace(tzinfo=zone)
    # Normalize a nonexistent spring-forward time instead of exporting it.
    return local.astimezone(timezone.utc).astimezone(zone)


# ------------------------------------------------------------- descriptions

def clock_label(clock: str) -> str:
    hour, minute = (int(part) for part in clock.split(":"))
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])}{',' if len(words) > 2 else ''} and {words[-1]}"


def describe(rules: list[dict]) -> str:
    """Plain-language pattern, e.g. "Usually the first Wednesday at 3:00 PM"."""
    groups: dict[tuple, list] = {}
    for rule in rules:
        cadence = ("every", rule["every"], rule["anchor"]) if "every" in rule else ("monthly",)
        key = (cadence, rule["weekday"], tuple(rule.get("months") or ()), rule["time"])
        groups.setdefault(key, []).append(rule)
    parts = []
    for (cadence, weekday, months, clock), grouped in groups.items():
        day = weekday.capitalize()
        if cadence[0] == "every":
            phrase = f"every {day}" if cadence[1] == 1 else f"every other {day}" if cadence[1] == 2 else f"every {cadence[1]} weeks on {day}"
        else:
            ordinals = sorted({r["ordinal"] for r in grouped}, key=lambda o: 5 if o == "last" else o)
            phrase = f"the {_join([ORDINAL_WORDS[o] for o in ordinals])} {day}"
        if months:
            phrase += f"{' in' if cadence[0] == 'every' else ' of'} {_join([MONTH_NAMES[m - 1] for m in sorted(months)])}"
        parts.append(f"{phrase} at {clock_label(clock)}")
    return "Usually " + "; ".join(parts)


# --------------------------------------------------------------- forecasting

def forecast_meetings(agency_id: str, schedule: dict | None, record_dates: list[date], *, today: date,
                      horizon_days: int, official_url: str, generated_at: str) -> list[dict]:
    """Expected meetings in (today, today + horizon], in public-export shape.

    ``record_dates`` are local dates of every stored record from this agency,
    whatever its status or title; each suppresses forecasts within
    ``SUPPRESSION_DAYS`` so a published notice or cancellation wins.
    """
    if not schedule or horizon_days <= 0:
        return []
    start, end = today + timedelta(days=1), today + timedelta(days=horizon_days)
    forecasts: dict[date, dict] = {}
    for rule in schedule["rules"]:
        for day in rule_dates(rule, start, end):
            if day in forecasts or any(abs((day - recorded).days) <= SUPPRESSION_DAYS for recorded in record_dates):
                continue
            starts = local_start(day, rule["time"], rule["timezone"])
            same_label = [r for r in schedule["rules"] if r["label"] == rule["label"]]
            forecasts[day] = {
                "id": f"forecast:{agency_id}:{day.isoformat()}", "agencyId": agency_id,
                "sourceId": f"forecast-{day.isoformat()}", "kind": "forecast", "title": rule["label"], "topics": [],
                "startsAt": starts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "date": day.isoformat(), "timezone": rule["timezone"], "status": "scheduled", "location": None,
                "officialUrl": official_url,
                "participation": {"instructions": None, "url": None, "remoteUrl": None, "deadline": None},
                "minutesUrl": None, "minutesStatus": None, "minutesNote": None, "recordingUrl": None, "lastCheckedAt": None, "updatedAt": generated_at,
                "change": None, "warnings": [], "documents": [], "items": [],
                "forecast": {"ruleLabel": rule["label"], "basis": describe(same_label), "source": schedule["source"]},
            }
    return [forecasts[day] for day in sorted(forecasts)]


# ------------------------------------------------------------------ learning

def regular_title(title: str) -> str:
    """Strip dates and per-meeting suffixes so recurring titles compare equal."""
    months = "|".join(MONTH_NAMES)
    text = re.sub(r"\s*\([^)]*\d[^)]*\)", "", title)
    text = re.sub(rf"\s*[–—-]?\s*(?:{months})\s+\d{{1,2}}(?:\s*[–-]\s*\d{{1,2}})?,?\s+\d{{4}}", "", text)
    text = re.sub(r"\s*[–—-]?\s*\d{4}-\d{2}-\d{2}", "", text)
    text = re.sub(r"\s+Agenda\b", "", text)
    return re.sub(r"\s+", " ", text).strip(" –—-:,") or "Regular meeting"


def _exclusion(meeting: dict) -> str | None:
    if meeting["status"] == "canceled":
        return "canceled"
    if str(meeting["source_id"]).startswith("one-off-"):
        return "oneOff"
    if IRREGULAR_TITLE.search(meeting["title"]):
        return "special"
    if not meeting.get("starts_at"):
        return "noStartTime"
    return None


def _local(meeting: dict):
    start = meeting["starts_at"]
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00"))
    local = start.astimezone(ZoneInfo(meeting.get("timezone") or PACIFIC))
    return local.date(), local.strftime("%H:%M")


def _candidate_models(days: list[date]):
    """Date models to evaluate, simplest first; rank breaks equal-support ties."""
    seen = set()
    for weekday in sorted({WEEKDAYS[d.weekday()] for d in days}, key=WEEKDAYS.index):
        same_day = sorted(d for d in days if WEEKDAYS[d.weekday()] == weekday)
        models = [(0, [{"ordinal": n, "weekday": weekday}]) for n in (1, 2, 3, 4)]
        models.append((1, [{"ordinal": "last", "weekday": weekday}]))
        models += [(2, [{"ordinal": a, "weekday": weekday}, {"ordinal": b, "weekday": weekday}]) for a, b in ((1, 3), (2, 4))]
        for anchor in same_day:
            phase = (anchor - same_day[0]).days // 7 % 2
            if (weekday, phase) not in seen:
                seen.add((weekday, phase))
                models.append((3, [{"every": 2, "anchor": anchor.isoformat(), "weekday": weekday}]))
        yield from models


def _month_step(days: list[date]) -> int:
    indices = sorted({d.year * 12 + d.month - 1 for d in days})
    step = 0
    for a, b in zip(indices, indices[1:]):
        step = math.gcd(step, b - a)
    return step


def learn_schedule(agency_id: str, meetings: list[dict], *, learned_on: date | None = None) -> dict:
    """Infer the dominant regular pattern from stored meetings and report support.

    A proposal needs at least ``MIN_MATCHED`` meetings matching weekday,
    ordinal (or two-week cadence), and start time, and a match rate of at least
    ``MIN_MATCH_RATE`` among regular meetings with a verified start time.
    """
    excluded = Counter()
    considered = []
    for meeting in meetings:
        reason = _exclusion(meeting)
        if reason:
            excluded[reason] += 1
            continue
        day, clock = _local(meeting)
        considered.append({"day": day, "time": clock, "title": meeting["title"], "id": meeting["id"]})
    considered.sort(key=lambda m: (m["day"], m["time"]))
    report = {"agency": agency_id, "considered": len(considered), "excluded": dict(sorted(excluded.items())),
              "matched": 0, "matchRate": 0.0, "status": "insufficient", "pattern": None, "schedule": None,
              "exceptions": [], "unrecordedDates": []}
    if not considered:
        report["reason"] = "no regular meetings with a verified start time"
        return report

    all_dates = [m["local_date"] for m in meetings if m.get("local_date")]
    first_day, last_day = considered[0]["day"], considered[-1]["day"]

    def unrecorded(rules):
        return [d for d in rule_dates_all(rules, first_day, last_day)
                if not any(abs((d - recorded).days) <= SUPPRESSION_DAYS for recorded in all_dates)]

    best = None
    for rank, rules in _candidate_models([m["day"] for m in considered]):
        on_date = [m for m in considered if any(matches_date(rule, m["day"]) for rule in rules)]
        if not on_date:
            continue
        times = Counter(m["time"] for m in on_date)
        latest = {m["time"]: m["day"] for m in on_date}
        clock = max(times, key=lambda t: (times[t], latest[t]))
        matched = [m for m in on_date if m["time"] == clock]
        # A broader rule must explain enough extra meetings to justify the
        # pattern dates it predicts where no record exists; one stray 3rd-week
        # meeting must not turn "first Wednesday" into "first and third".
        score = (len(matched) - GAP_PENALTY * len(unrecorded(rules)), len(matched), -rank)
        if best is None or score > best[0]:
            best = (score, rank, rules, clock, matched, on_date)
    _, rank, rules, clock, matched, on_date = best
    rules = [dict(rule, time=clock, timezone=PACIFIC) for rule in rules]
    if rank < 3 and len(matched) >= MIN_MATCHED:
        step = _month_step([m["day"] for m in matched])
        if step >= 2:
            first = matched[0]["day"].month
            for rule in rules:
                rule["months"] = [m for m in range(1, 13) if (m - first) % step == 0]
    label = Counter(regular_title(m["title"]) for m in matched).most_common(1)[0][0]
    rules = [{**rule, "label": label} for rule in rules]
    rate = len(matched) / len(considered)
    report.update(matched=len(matched), matchRate=round(rate, 2), pattern=describe(rules))
    matched_ids = {m["id"] for m in matched}
    on_date_ids = {m["id"] for m in on_date}
    report["exceptions"] = [{"date": m["day"].isoformat(), "time": m["time"], "title": m["title"],
                             "reason": f"starts {m['time']} instead of {clock}" if m["id"] in on_date_ids else "off pattern"}
                            for m in considered if m["id"] not in matched_ids]
    report["unrecordedDates"] = [d.isoformat() for d in unrecorded(rules)]
    if len(matched) < MIN_MATCHED:
        report["reason"] = f"only {len(matched)} matching meeting{'' if len(matched) == 1 else 's'} (need {MIN_MATCHED})"
    elif rate < MIN_MATCH_RATE:
        report["reason"] = f"match rate {rate:.0%} is below {MIN_MATCH_RATE:.0%}"
    else:
        report["status"] = "proposed"
        report["reason"] = None
        report["schedule"] = {
            "source": "learned", "rules": [_ordered_rule(rule) for rule in rules], "confidence": round(rate, 2),
            "learnedFrom": {"meetings": len(considered), "matched": len(matched),
                            "firstDate": considered[0]["day"].isoformat(), "lastDate": considered[-1]["day"].isoformat(),
                            "learnedOn": (learned_on or datetime.now(ZoneInfo(PACIFIC)).date()).isoformat()},
        }
    return report


def rule_dates_all(rules: list[dict], start: date, end: date) -> list[date]:
    return sorted({d for rule in rules for d in rule_dates(rule, start, end)})


def _ordered_rule(rule: dict) -> dict:
    order = ("ordinal", "every", "anchor", "weekday", "months", "time", "timezone", "label")
    return {key: rule[key] for key in order if key in rule}


def _comparable(schedule: dict) -> dict:
    learned_from = {k: v for k, v in (schedule.get("learnedFrom") or {}).items() if k != "learnedOn"}
    return {**schedule, "learnedFrom": learned_from}


def learn_schedules_report(meetings_by_agency: dict[str, list[dict]], registry: list[dict], *,
                           learned_on: date | None = None) -> list[dict]:
    """Learner findings per agency, plus the action ``--write`` would take."""
    entries = {entry["id"]: entry for entry in registry}
    reports = []
    for agency_id, rows in meetings_by_agency.items():
        entry = entries.get(agency_id, {})
        existing = entry.get("schedule")
        report = learn_schedule(agency_id, rows, learned_on=learned_on)
        report["legacySchedule"] = (entry.get("legacy") or {}).get("meetingSchedule") or None
        report["existing"] = existing
        if existing and existing.get("source") == "configured":
            report["action"] = "keep configured"
        elif report["schedule"]:
            report["action"] = ("unchanged" if existing and _comparable(existing) == _comparable(report["schedule"])
                                else "write learned")
        else:
            report["action"] = "keep existing learned" if existing else "none"
        reports.append(report)
    return reports


def write_schedules(path: Path, reports: list[dict]) -> list[str]:
    """Store learned proposals in the registry file; configured schedules always win."""
    updates = {r["agency"]: r["schedule"] for r in reports if r["action"] == "write learned"}
    registry = json.loads(path.read_text())
    written = []
    for index, entry in enumerate(registry):
        schedule = updates.get(entry["id"])
        if not schedule or (entry.get("schedule") or {}).get("source") == "configured":
            continue
        validate_schedule(entry["id"], schedule)
        registry[index] = {**entry, "schedule": schedule}
        written.append(entry["id"])
    if written:
        from .catalog import registry_text  # the catalog imports this module
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(registry_text(registry))
        temporary.replace(path)
    return written
