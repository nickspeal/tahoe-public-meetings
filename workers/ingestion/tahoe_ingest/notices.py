"""Meetings announced in tracked public notices, read with Claude (IDEA-018 R1–R3).

A comment period's notice (a Notice of Preparation, for example) often announces a scoping open house or
says the project goes before a standing body on a given day. ``extract_notices`` sends each notice that the
current prompt has not read yet (tracked by the notice's hash) to Haiku, with Sonnet as fallback, and keeps
only events that pass deterministic checks against the notice text: the quote is word for word, the date
with its year and every time re-parse from the quote, places, hosts, the body, and the subject appear in
the text, and nothing is dated before the notice opens or before today. A failure rejects only that event,
stored with its reason in ``notice_extractions.rejected`` and never shown.

Accepted ``one-off`` events become stored meetings through ``core.ingest_bundle``, under the one followed
body the hosts name or else under the per-record-host entry (Other) with the hosts as written. Accepted
``regular-meeting`` events become previews of that body's meeting, with the notice as their source
(``tahoe_ingest.previews``). A revised notice is matched to the earlier reading by normalized title, so a
moved event keeps its meeting ID and is marked rescheduled; an event missing from the revision stays
published with "Not in the latest notice." Usage goes to ``ai_usage`` under the shared monthly budget.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from .ai_topics import DEFAULT_MAX_RUN_COST, DEFAULT_MONTHLY_BUDGET, month_to_date_spend, out_of_time, time_budget_stop
from .catalog import followed_agencies
from .evaluate_topics import (
    AnthropicTopicModel, ModelError, USAGE_FIELDS, cost_usd, estimate_tokens, has_credentials,
)
from .participation import _location, _verbatim, loose, normalized

PROMPT = Path(__file__).with_name("prompts") / "notice_extraction.md"
CHAIN = (("claude-haiku-5-5", "low"), ("claude-sonnet-5-5", "medium"))
ATTEMPTS_PER_MODEL = 2
PURPOSE = "notice-extraction"
MAX_TOKENS = 4096
EST_OUTPUT_TOKENS = 900  # a few events plus low-effort thinking
MAX_INPUT_CHARS = 48_000  # about 12K tokens; meeting details sit near a notice's start or end
PACIFIC = ZoneInfo("America/Los_Angeles")
EVENT_FIELDS = ("kind", "title", "format", "date", "startTime", "endTime", "locationLines", "remoteQuote",
                "hosts", "bodyName", "subject", "quote")
WITHDRAWN_WARNING = "Not in the latest notice."
MAX_INSTRUCTIONS = 400


def response_schema() -> dict:
    text = {"type": "string"}
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    strings = {"type": "array", "items": text}
    event = {"type": "object", "properties": {
        "kind": {"type": "string", "enum": ["one-off", "regular-meeting"]}, "title": text, "format": nullable,
        "date": text, "startTime": nullable, "endTime": nullable, "locationLines": strings, "remoteQuote": nullable,
        "hosts": strings, "bodyName": nullable, "subject": nullable, "quote": text,
    }, "required": list(EVENT_FIELDS), "additionalProperties": False}
    return {"type": "object", "properties": {"events": {"type": "array", "items": event}},
            "required": ["events"], "additionalProperties": False}


def prompt_sha256(system: str) -> str:
    return hashlib.sha256((system + json.dumps(response_schema(), sort_keys=True)).encode()).hexdigest()


# --- Registry names -------------------------------------------------------------

_EQUIVALENT = {"commission": "committee", "commissions": "committee", "committees": "committee"}


def name_key(text: str) -> str:
    """Normalized words of an organization's name. "Commission" and "Committee" are the same word here,
    because notices and the registry disagree on it ("TRPA Advisory Planning Commission" is the registry's
    "TRPA — Advisory Planning Committee"); a leading "the" and "&" versus "and" never decide a match."""
    words = [_EQUIVALENT.get(w, w) for w in loose(text.replace("&", " and ")).split()]
    if words[:1] == ["the"]:
        words = words[1:]
    return " ".join(words)


def _labels(agency: dict) -> set[str]:
    """Every way a notice may name this registry entry: its name, short name, and aliases, the part of an
    "Organization — Body" name after the dash, and that body part prefixed with any of the organization's
    names ("TRPA Advisory Planning Committee", "Tahoe Regional Planning Agency Advisory Planning Committee")."""
    name = agency.get("name") or ""
    names = {name, agency.get("shortName") or "", *(agency.get("aliases") or [])}
    head, dash, body = name.partition(" — ")
    if dash:
        names.add(body)
        for prefix in {head, agency.get("shortName") or "", *(agency.get("aliases") or [])}:
            if prefix:
                names.add(f"{prefix} {body}")
    return {key for key in map(name_key, names) if key}


def _registry() -> tuple[dict[str, set[str]], str]:
    """Followed entries' labels, and the per-record-host entry (Other) that takes everything else."""
    agencies = followed_agencies()
    other = [aid for aid, a in agencies.items() if a.get("meetingHost") == "per-record"]
    if len(other) != 1:
        raise RuntimeError("the registry needs exactly one followed entry with meetingHost 'per-record'")
    return {aid: _labels(a) for aid, a in agencies.items() if aid != other[0]}, other[0]


def matching_agencies(name: str) -> set[str]:
    """Followed entries (never the per-record one) that ``name`` names exactly, after ``name_key``."""
    labels, _ = _registry()
    key = name_key(name)
    return {aid for aid, keys in labels.items() if key in keys}


def join_hosts(hosts: list[str]) -> str:
    hosts = list(dict.fromkeys(" ".join(h.split()) for h in hosts if h.strip()))
    return hosts[0] if len(hosts) == 1 else ", ".join(hosts[:-1]) + " and " + hosts[-1]


def resolve_host(hosts: list[str]) -> tuple[str, str | None]:
    """The registry entry a one-off goes under, and its host text (None under a followed body).

    It goes under a followed body only when every host names a registry entry and together they name exactly
    one. "TRPA" names both TRPA bodies, and an organization outside the registry names none, so either sends
    the meeting to the per-record-host entry (Other) with the hosts as the notice writes them."""
    _, other = _registry()
    names = [h for h in hosts if h and h.strip()]
    matches = [matching_agencies(h) for h in names]
    found = set().union(*matches) if matches else set()
    if names and all(matches) and len(found) == 1:
        return found.pop(), None
    return other, join_hosts(names) if names else None


# --- Dates and times printed in a quote -------------------------------------------

_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december")
_MONTH = r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATES = (
    re.compile(rf"\b{_MONTH}\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>\d{{4}})\b"),
    re.compile(rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH}\.?,?\s+(?P<year>\d{{4}})\b"),
    re.compile(r"\b(?P<mon>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})\b"),
    re.compile(r"\b(?P<year>\d{4})-(?P<mon>\d{2})-(?P<day>\d{2})\b"),
)
_MERIDIEM = r"(?P<{0}>[ap])\.?\s?m\.?(?![a-z])"
_RANGE = re.compile(r"(?<![\d:.])(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*(?:" + _MERIDIEM.format("a1") + r")?\s*"
                    r"(?:-|to|until|through)\s*(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*" + _MERIDIEM.format("a2"))
_SINGLE = re.compile(r"(?<![\d:.])(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*" + _MERIDIEM.format("a"))
_TWENTY_FOUR = re.compile(r"(?<![\d:.])(?P<h>\d{1,2}):(?P<m>[0-5]\d)(?!\s*[ap]\.?\s?m)")


def quoted_dates(quote: str) -> set[date]:
    """Dates the quote prints with their year; a date without a year is never completed."""
    found, text = set(), normalized(quote)
    for pattern in _DATES:
        for m in pattern.finditer(text):
            groups = m.groupdict()
            month = int(groups["mon"]) if groups.get("mon") else next(
                i for i, n in enumerate(_MONTH_NAMES, 1) if n.startswith(groups["month"][:3]))
            try:
                found.add(date(int(groups["year"]), month, int(groups["day"])))
            except ValueError:
                pass
    return found


def _clock(hour: str, minute: str | None, meridiem: str | None) -> time | None:
    h, m = int(hour), int(minute or 0)
    if meridiem:
        if not 1 <= h <= 12 or m > 59:
            return None
        return time((h % 12) + (12 if meridiem == "p" else 0), m)
    return time(h, m) if 13 <= h <= 23 and m <= 59 else None  # "5:30" alone could be morning or evening


def quoted_times(quote: str) -> set[str]:
    """Clock times the quote prints unambiguously, as HH:MM. "5:30-7:00 PM" gives both times; a bare "5:30"
    gives none, since nothing says whether it is morning or evening, and a 24-hour "17:30" gives 17:30."""
    text, found = normalized(quote), set()
    if re.search(r"\bnoon\b", text):
        found.add("12:00")
    for m in _RANGE.finditer(text):
        end = _clock(m["h2"], m["m2"], m["a2"])
        if not end:
            continue
        start = _clock(m["h1"], m["m1"], m["a1"] or m["a2"])
        if start and not m["a1"] and start > end:  # "11:30-1:00 PM" starts in the morning
            start = _clock(m["h1"], m["m1"], "a" if m["a2"] == "p" else "p")
        found.update(t.strftime("%H:%M") for t in (start, end) if t)
    for m in _SINGLE.finditer(text):
        if t := _clock(m["h"], m["m"], m["a"]):
            found.add(t.strftime("%H:%M"))
    for m in _TWENTY_FOUR.finditer(text):
        if t := _clock(m["h"], m["m"], None):
            found.add(t.strftime("%H:%M"))
    return found


# --- Validation against the notice text ---------------------------------------------

REMOTE = re.compile(r"(?i)virtual|remote|zoom|teams|webex|online|teleconference|video ?conference|livestream|webinar|by phone|call[- ]in")


def _hhmm(value) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value.strip()):
        raise ValueError("not an HH:MM time")
    time.fromisoformat(value.strip())
    return value.strip()


def _check_event(event: dict, source_loose: str, period: dict, today: date) -> tuple[dict | None, str | None]:
    from .core import starts_at  # core loads the database driver; validation alone needs none of it
    kind = event.get("kind")
    if kind not in ("one-off", "regular-meeting"):
        return None, "unknown event kind"
    quote = " ".join(str(event.get("quote") or "").split())
    if not quote or _verbatim(quote, source_loose, 3000)[1]:
        return None, "quote not found word for word in the notice"
    title = " ".join(str(event.get("title") or "").split())
    if not title or _verbatim(title, source_loose, 300)[1]:
        return None, "title not found word for word in the notice"
    try:
        day = date.fromisoformat(str(event.get("date")))
    except ValueError:
        return None, "date is not YYYY-MM-DD"
    if day not in quoted_dates(quote):
        return None, "date with its year is not stated in the quote"
    try:
        start = _hhmm(event["startTime"]) if event.get("startTime") else None
        end = _hhmm(event["endTime"]) if event.get("endTime") else None
    except ValueError:
        return None, "time is not HH:MM"
    printed = quoted_times(quote)
    if start and start not in printed:
        return None, f"start time {start} is not stated in the quote"
    if end and not start:
        return None, "end time without a start time"
    if end and end not in printed:
        return None, f"end time {end} is not stated in the quote"
    if end and end <= start:
        return None, "end time is not after the start time"
    try:
        starts_at({"date": day.isoformat(), "start_time": start, "timezone": "America/Los_Angeles"})
    except ValueError as exc:
        return None, str(exc)
    opens = period.get("opens_on")
    if opens and day < opens:
        return None, "dated before the notice opens"
    if day < today:
        return None, "already past"
    lines = [" ".join(str(line).split()) for line in event.get("locationLines") or [] if str(line).strip()]
    if lines and (problem := _location(", ".join(lines), source_loose)[1]):
        return None, f"location: {problem}"
    remote = " ".join(str(event.get("remoteQuote") or "").split()) or None
    if remote and (_verbatim(remote, source_loose, 400)[1] or not REMOTE.search(remote)):
        return None, "remote option not stated word for word in the notice"
    fmt = " ".join(str(event.get("format") or "").split()) or None
    if fmt and _verbatim(fmt, source_loose, 80)[1]:
        return None, "format not found word for word in the notice"
    hosts = list(dict.fromkeys(" ".join(str(h).split()) for h in event.get("hosts") or [] if str(h).strip()))
    for host in hosts:
        if _verbatim(host, source_loose, 200)[1]:
            return None, f"host '{host}' not found in the notice"
    accepted = {"kind": kind, "title": title, "format": fmt, "date": day.isoformat(), "startTime": start,
                "endTime": end, "locationLines": lines, "remoteQuote": remote, "hosts": hosts, "quote": quote,
                "bodyName": None, "subject": None}
    if kind == "one-off":
        if not hosts:
            return None, "no host named"
        return accepted, None
    body = " ".join(str(event.get("bodyName") or "").split())
    if not body or _verbatim(body, source_loose, 200)[1]:
        return None, "body name not found in the notice"
    bodies = matching_agencies(body)
    if len(bodies) != 1:
        return None, f"body '{body}' names {len(bodies)} followed registry entries, not one"
    subject = " ".join(str(event.get("subject") or "").split())
    if not subject or _verbatim(subject, source_loose, 300)[1]:
        return None, "subject not found word for word in the notice"
    return {**accepted, "bodyName": body, "subject": subject, "agencyId": bodies.pop()}, None


def validate(reply: dict, period_row: dict, *, today: date) -> tuple[list[dict], list[dict]]:
    """Keep only events the notice text supports; report each other event with its reason."""
    source_loose = loose(period_row["source_text"])
    accepted, rejected = [], []
    for event in reply.get("events") or []:
        if not isinstance(event, dict):
            rejected.append({"event": event, "reason": "not an event object"})
            continue
        kept, problem = _check_event(event, source_loose, period_row, today)
        if problem:
            rejected.append({"event": event, "reason": problem})
        else:
            accepted.append(kept)
    return accepted, rejected


# --- Records ------------------------------------------------------------------------

class Records(NamedTuple):
    one_offs: list[dict]   # core.ingest_bundle meeting rows, plus "end_time" and "format"
    previews: list[dict]   # previews.Preview keyword arguments
    source: dict           # previews.PreviewSource keyword arguments
    withdrawn: list[dict]  # [{"meeting_id", "warning"}] for earlier events missing from this notice
    stored: list[dict]     # notice_extractions.events: accepted events with their assigned IDs, and withdrawn ones


def title_key(title: str) -> str:
    return loose(title)


def instructions_from(quote: str) -> str:
    """The quote, cut to MAX_INSTRUCTIONS characters at a sentence end (or a word, marked with an ellipsis)."""
    quote = " ".join(quote.split())
    if len(quote) <= MAX_INSTRUCTIONS:
        return quote
    head = quote[:MAX_INSTRUCTIONS]
    cut = max(head.rfind(". "), head.rfind("? "), head.rfind("! "))
    return head[:cut + 1] if cut > 0 else head[:MAX_INSTRUCTIONS - 1].rsplit(" ", 1)[0] + "…"


def published_on(period_row: dict) -> date:
    if period_row.get("opens_on"):
        return period_row["opens_on"]
    checked = period_row["checked_at"]
    return checked.astimezone(PACIFIC).date() if isinstance(checked, datetime) else date.fromisoformat(str(checked)[:10])


def meeting_title(body_name: str) -> str:
    return body_name if body_name.casefold().endswith("meeting") else f"{body_name} Meeting"


def _pair(new: list[dict], old: list[dict]) -> tuple[list[tuple[dict, dict | None]], list[dict]]:
    """Match events by normalized title; several with one title pair up in date order."""
    by_title: dict[str, list[dict]] = {}
    for event in sorted(old, key=lambda e: (e["date"], e.get("startTime") or "")):
        by_title.setdefault(event["titleKey"], []).append(event)
    matches = {}
    for i, event in sorted(enumerate(new), key=lambda p: (p[1]["date"], p[1].get("startTime") or "")):
        candidates = by_title.get(title_key(event["title"]), [])
        matches[i] = candidates.pop(0) if candidates else None
    # Pairs keep the notice's order, so new IDs on one day are numbered as the notice lists the events.
    return [(event, matches[i]) for i, event in enumerate(new)], [e for group in by_title.values() for e in group]


def events_to_records(period_row: dict, accepted: list[dict], *, previous: list[dict] | dict | None) -> Records:
    """Meeting rows, previews, and the preview source for one notice's accepted events.

    ``previous`` is the earlier ``notice_extractions.events`` of this comment period (a list, or a dict with an
    "events" list). A one-off matched to an earlier event keeps that event's source ID even when its date moved,
    and is marked rescheduled when its date or time changed; earlier one-offs missing from this notice come back
    in ``withdrawn``. Removal never deletes.
    """
    if isinstance(previous, dict):
        previous = previous.get("events")
    earlier = [e for e in previous or [] if e.get("kind") == "one-off" and e.get("sourceId")]
    period_id, source_id = period_row["id"], period_row["source_id"]
    topics = list(period_row.get("topics") or [])
    new_one_offs = [e for e in accepted if e["kind"] == "one-off"]
    pairs, missing = _pair(new_one_offs, earlier)
    used = {e["sourceId"] for e in earlier}
    rows, stored = [], []
    for event, match in pairs:
        agency, host = resolve_host(event["hosts"])
        if match:
            sid = match["sourceId"]
            moved = (match["date"], match.get("startTime")) != (event["date"], event["startTime"])
            status = "rescheduled" if moved or match.get("status") == "rescheduled" else "scheduled"
        else:
            base = f"one-off-{source_id}-{event['date']}"
            sid, n = base, 1
            while sid in used:
                n += 1
                sid = f"{base}-{n}"
            status = "scheduled"
        used.add(sid)
        meeting_id = f"{agency}:{sid}"
        row = {"agency": agency, "id": sid, "title": event["title"], "date": event["date"],
               "timezone": "America/Los_Angeles", "location": ", ".join(event["locationLines"]) or None,
               "host": host, "source_meeting_url": period_row["official_url"], "status": status, "topics": topics,
               "participation": {"instructions": instructions_from(event["quote"]), "url": None, "remoteUrl": None,
                                 "deadline": None},
               "warnings": [], "items_complete": False, "documents_complete": False,
               "end_time": event["endTime"], "format": event["format"]}
        if event["startTime"]:
            row["start_time"] = event["startTime"]
        rows.append(row)
        stored.append({**event, "agencyId": agency, "host": host, "sourceId": sid, "meetingId": meeting_id,
                       "titleKey": title_key(event["title"]), "status": status, "withdrawn": False})
    current_ids = {s["meetingId"] for s in stored}
    withdrawn = []
    for event in missing:
        if event["meetingId"] not in current_ids:
            withdrawn.append({"meeting_id": event["meetingId"], "warning": WITHDRAWN_WARNING})
            stored.append({**event, "withdrawn": True})
    previews = []
    for event in accepted:
        if event["kind"] != "regular-meeting":
            continue
        previews.append({"agency_id": event["agencyId"], "local_date": date.fromisoformat(event["date"]), "month": None,
                         "pending": False, "subject": event["subject"], "section": None,
                         "start_time": event["startTime"], "location": ", ".join(event["locationLines"]) or None,
                         "remote_quote": event["remoteQuote"], "quote": event["quote"], "page": None,
                         "meeting_title": meeting_title(event["bodyName"]), "special": False, "topics": list(topics)})
        stored.append({**event, "titleKey": title_key(event["title"])})
    source = {"kind": "notice", "key": period_id, "sha256": period_row["source_sha256"],
              "url": period_row["official_url"], "title": source_title(period_row), "published_on": published_on(period_row),
              "pages": None}
    return Records(rows, previews, source, withdrawn, stored)


def source_title(period_row: dict) -> str:
    """The source line's name for the notice: the verified project title plus "notice", else the period's title."""
    project = " ".join((period_row.get("project_title") or "").split())
    return f"{project} notice" if project else period_row["title"]


def _replace_previews(conn, source: dict, previews: list[dict], *, today: date) -> dict:
    # Imported here: the previews store is its own module, and tests stand in for it.
    from . import previews as store
    return store.replace_source(conn, store.PreviewSource(**source), [store.Preview(**p) for p in previews], today=today)


def apply_events(conn, period_row: dict, accepted: list[dict], previous, today: date) -> tuple[dict, Records]:
    """Store one notice's events: one-off meetings, their published durations, withdrawal notes, and previews."""
    from .core import ingest_bundle
    from .duration_ops import set_duration
    records = events_to_records(period_row, accepted, previous=previous)
    counts = {"oneOffs": 0, "previews": len(records.previews), "withdrawn": len(records.withdrawn), "errors": []}
    if records.one_offs:
        rows = [{k: v for k, v in row.items() if k not in ("end_time", "format")} for row in records.one_offs]
        result = ingest_bundle(conn, {"meetings": rows}, mode="fixture")
        counts["oneOffs"] = result["processed"]
        counts["errors"] += result["errors"]
        failed = {e.split(": ", 1)[0] for e in result["errors"]}
        for row in records.one_offs:
            meeting_id = f"{row['agency']}:{row['id']}"
            if row["end_time"] and row.get("start_time") and meeting_id not in failed:
                minutes = (datetime.combine(date.min, time.fromisoformat(row["end_time"]))
                           - datetime.combine(date.min, time.fromisoformat(row["start_time"]))).seconds // 60
                set_duration(conn, meeting_id, minutes, "published")
    if records.withdrawn:
        conn.execute("""UPDATE meetings SET warnings=warnings || to_jsonb(%s::text), updated_at=now()
            WHERE id = ANY(%s) AND NOT warnings ? %s""",
            (WITHDRAWN_WARNING, [w["meeting_id"] for w in records.withdrawn], WITHDRAWN_WARNING))
    had_previews = any(e.get("kind") == "regular-meeting" for e in _events(previous))
    if records.previews or had_previews:
        _replace_previews(conn, records.source, records.previews, today=today)
    return counts, records


def _events(previous) -> list[dict]:
    if isinstance(previous, dict):
        previous = previous.get("events")
    return list(previous or [])


# --- Run ------------------------------------------------------------------------

def _pending(conn, *, prompt_hash: str, force: bool) -> list[dict]:
    """Comment periods whose notice (or the prompt) changed since it was last read, with the earlier events."""
    return conn.execute("""SELECT p.id,p.agency_id,p.source_id,p.title,p.opens_on,p.closes_on,p.official_url,p.topics,
            p.source_text,p.source_sha256,p.project_title,p.checked_at,p.host,e.events AS previous_events
        FROM comment_periods p LEFT JOIN notice_extractions e ON e.comment_period_id=p.id
        WHERE %s OR e.comment_period_id IS NULL OR e.source_sha256<>p.source_sha256 OR e.prompt_sha256<>%s
        ORDER BY p.id""", (force, prompt_hash)).fetchall()


def render_user_message(period: dict) -> str:
    text = period["source_text"].strip()
    if len(text) > MAX_INPUT_CHARS:
        half = MAX_INPUT_CHARS // 2
        text = text[:half] + "\n\n[…]\n\n" + text[-half:]
    agencies = followed_agencies()
    agency = period.get("host") or (agencies.get(period["agency_id"]) or {}).get("name") or period["agency_id"]
    window = f"{period['opens_on'].isoformat()} to " if period.get("opens_on") else "until "
    return (f"Agency: {agency}\nComment period: {period['title']} ({window}{period['closes_on'].isoformat()})\n\n"
            f"List the meetings this notice announces.\n\n<notice>\n{text}\n</notice>")


def extract_notices(conn, *, dry_run: bool = False, force: bool = False, max_cost: float = DEFAULT_MAX_RUN_COST,
                    monthly_budget: float = DEFAULT_MONTHLY_BUDGET, clients: dict | None = None,
                    today: date | None = None, deadline: float | None = None) -> dict:
    """Read each changed notice; ``deadline`` (``time.monotonic()``) stops before the next notice once it passes."""
    system = PROMPT.read_text().strip()
    schema = response_schema()
    prompt_hash = prompt_sha256(system)
    today = today or datetime.now(PACIFIC).date()
    pending = _pending(conn, prompt_hash=prompt_hash, force=force)
    report = {"periodsPending": len(pending), "estimatedCostUsd": 0.0, "monthToDateUsd": None,
              "monthlyBudgetUsd": monthly_budget, "dryRun": dry_run, "extracted": {}, "eventsAccepted": 0,
              "eventsRejected": 0, "oneOffs": 0, "previews": 0, "withdrawn": 0, "costUsd": 0.0, "failedPeriods": [],
              "stoppedEarly": None}
    if not pending:
        return report  # an unchanged notice costs nothing beyond the query above
    requests = {p["id"]: render_user_message(p) for p in pending}
    shared = estimate_tokens(system) + estimate_tokens(json.dumps(schema))
    estimate = sum(cost_usd(CHAIN[0][0], uncached_input=shared + estimate_tokens(user), output=EST_OUTPUT_TOKENS)
                   for user in requests.values())
    spent_this_month = month_to_date_spend(conn)
    report.update(estimatedCostUsd=round(estimate, 4), monthToDateUsd=round(spent_this_month, 4))
    if dry_run:
        return report
    if clients is None and not has_credentials():
        report["skipped"] = "no Claude credentials (API key or workload identity federation)"
        return report
    if spent_this_month >= monthly_budget:
        raise SystemExit(f"Month-to-date AI spend ${spent_this_month:.2f} has reached the ${monthly_budget:.2f} budget")
    if estimate > max_cost:
        raise SystemExit(f"Estimated ${estimate:.4f} exceeds --max-cost ${max_cost:.2f}")
    clients = clients or {}
    for done, period in enumerate(pending):
        if report["costUsd"] >= max_cost or spent_this_month + report["costUsd"] >= monthly_budget:
            report["stoppedEarly"] = "cost limit reached; remaining notices are read on the next run"
            break
        if out_of_time(deadline):
            time_budget_stop(report, "notices are read", periodsDeferred=len(pending) - done)
            break
        parsed, model_used = None, None
        for model, effort in CHAIN:
            client = clients.get(model) or clients.setdefault(model, AnthropicTopicModel(model, effort))
            for _ in range(ATTEMPTS_PER_MODEL):
                try:
                    reply = client.complete(system=system, user=requests[period["id"]], schema=schema, max_tokens=MAX_TOKENS)
                except ModelError as exc:
                    if exc.fatal:
                        raise SystemExit(f"Notice extraction stopped: {exc}") from exc
                    _record_usage(conn, model, {}, ok=False, error=str(exc))
                    continue
                result, problem = _parse(reply)
                report["costUsd"] += _record_usage(conn, model, reply.usage, ok=problem is None, error=problem)
                if problem is None:
                    parsed, model_used = result, reply.model or model
                    report["extracted"][model] = report["extracted"].get(model, 0) + 1
                    break
            if parsed is not None:
                break
        conn.commit()  # usage is recorded even when storing the events fails below
        if parsed is None:
            report["failedPeriods"].append(period["id"])
            continue
        accepted, rejected = validate(parsed, period, today=today)
        try:
            counts, records = apply_events(conn, period, accepted, period["previous_events"], today)
            _store(conn, period, prompt_hash, model_used, records.stored, rejected)
            conn.commit()
        except Exception as exc:  # one notice's failure never blocks the others; it is read again next run
            conn.rollback()
            report["failedPeriods"].append(f"{period['id']}: {exc}")
            continue
        report["eventsAccepted"] += len(accepted)
        report["eventsRejected"] += len(rejected)
        report["oneOffs"] += counts["oneOffs"]
        report["previews"] += counts["previews"]
        report["withdrawn"] += counts["withdrawn"]
        if counts["errors"]:
            report.setdefault("ingestErrors", []).extend(counts["errors"])
    report["costUsd"] = round(report["costUsd"], 6)
    return report


def _parse(reply) -> tuple[dict, str | None]:
    if reply.stop_reason == "refusal":
        return {}, "model declined the request (refusal)"
    if reply.stop_reason == "max_tokens":
        return {}, "response truncated at max_tokens"
    try:
        parsed = json.loads(reply.text)
    except json.JSONDecodeError:
        return {}, "response was not the expected JSON object"
    if not isinstance(parsed, dict) or not isinstance(parsed.get("events"), list):
        return {}, "response was not the expected JSON object"
    return parsed, None


def _record_usage(conn, model: str, usage: dict, *, ok: bool, error: str | None) -> float:
    counts = {name: int(usage.get(name, 0) or 0) for name in USAGE_FIELDS}
    cost = cost_usd(model, uncached_input=counts["input_tokens"], output=counts["output_tokens"],
                    cache_write=counts["cache_creation_input_tokens"], cache_read=counts["cache_read_input_tokens"])
    conn.execute("""INSERT INTO ai_usage(purpose,model,meeting_id,ok,input_tokens,output_tokens,
        cache_creation_input_tokens,cache_read_input_tokens,cost_usd,error) VALUES (%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s)""",
        (PURPOSE, model, ok, *(counts[name] for name in USAGE_FIELDS), cost, error))
    return cost


def _store(conn, period: dict, prompt_hash: str, model: str, events: list, rejected: list) -> None:
    conn.execute("""INSERT INTO notice_extractions(comment_period_id,source_sha256,prompt_sha256,model,events,rejected)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT(comment_period_id) DO UPDATE SET source_sha256=excluded.source_sha256,
        prompt_sha256=excluded.prompt_sha256,model=excluded.model,events=excluded.events,rejected=excluded.rejected,
        extracted_at=now()""",
        (period["id"], period["source_sha256"], prompt_hash, model, json.dumps(events), json.dumps(rejected, default=str)))
