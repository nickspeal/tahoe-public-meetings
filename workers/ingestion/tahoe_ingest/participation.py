"""Attendance and public comment details read from each meeting's own agenda with Claude.

Discovery adapters rarely expose where a meeting is held, how to join it
remotely, or how to comment, but nearly every agenda states these on its first
pages. ``extract_participation`` sends the opening pages and the last page of a
meeting's current agenda documents to Haiku (Sonnet as fallback) and keeps only
fields that pass deterministic checks against that text: links, emails, phone
numbers, locations, deadlines, and the quote behind the instructions must all
appear in the source. Rejected fields are stored with the reason, never shown.

Results live in ``ai_participation``, one row per meeting, keyed to the document
versions read. The export uses a row only while those versions are still the
meeting's current documents, and values from the source adapter take precedence.
Usage and cost go to ``ai_usage`` under the same monthly budget as topic
classification (decision 0011).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .ai_topics import DEFAULT_MAX_RUN_COST, DEFAULT_MONTHLY_BUDGET, month_to_date_spend, out_of_time, time_budget_stop
from .evaluate_topics import (
    AnthropicTopicModel, ModelError, USAGE_FIELDS, cost_usd, estimate_tokens, has_credentials,
)

PROMPT = Path(__file__).with_name("prompts") / "participation_extraction.md"
CHAIN = (("claude-haiku-5-5", "low"), ("claude-sonnet-5-5", "medium"))
ATTEMPTS_PER_MODEL = 2
PURPOSE = "participation-extraction"
MAX_TOKENS = 2048
EST_OUTPUT_TOKENS = 600  # JSON fields plus low-effort thinking
LEADING_PAGES = 3
MAX_INPUT_CHARS = 24_000  # about 6K tokens per meeting
LOOKBACK_DAYS = 14  # past meetings need no joining details; undated meetings are always included
FIELDS = ("location", "remoteUrl", "watchUrl", "phone", "commentUrl", "commentEmail", "instructions", "instructionsQuote", "deadline")
NOT_AGENDA = re.compile(r"minutes|synopsis|supplement|staff report|attachment|presentation|notice of decision", re.I)


def response_schema() -> dict:
    nullable = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {"type": "object", "properties": {name: nullable for name in FIELDS},
            "required": list(FIELDS), "additionalProperties": False}


def prompt_sha256(system: str) -> str:
    return hashlib.sha256((system + json.dumps(response_schema(), sort_keys=True)).encode()).hexdigest()


# --- Validation against the source text ---------------------------------------

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‑": "-"})


def normalized(text: str) -> str:
    """Case-folded text with curly quotes, dashes, and any spacing or invisible character made plain."""
    text = "".join(" " if unicodedata.category(c) in ("Zs", "Cf", "Co", "Cc") else c for c in text.translate(_QUOTES))
    return " ".join(text.split()).casefold()


def loose(text: str) -> str:
    """Normalized words only: punctuation and line breaks never decide whether a phrase is in the source."""
    return " ".join(re.sub(r"[^\w]+", " ", normalized(text)).split())


def compact(text: str) -> str:
    """Normalized text without any whitespace, so a link or number broken across PDF lines still matches."""
    return normalized(text).replace(" ", "")


def _url(value: str, source_compact: str) -> tuple[str | None, str | None]:
    value = value.strip().rstrip(".,;:)]>'\"")
    if not re.match(r"(?i)(https?://|www\.|[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(/\S*)?$)", value):
        return None, "not a web link"
    bare = re.sub(r"(?i)^https?://", "", value)
    if compact(bare) not in source_compact:
        return None, "link not found in the agenda text"
    full = value if re.match(r"(?i)https?://", value) else "https://" + value
    parsed = urlparse(full)
    if not parsed.netloc or "." not in parsed.netloc or " " in full:
        return None, "malformed link"
    return full, None


def _verbatim(value: str, source_loose: str, limit: int) -> tuple[str | None, str | None]:
    value = " ".join(value.split())
    if len(value) > limit:
        return None, f"longer than {limit} characters"
    if not loose(value) or f" {loose(value)} " not in f" {source_loose} ":
        return None, "not found verbatim in the agenda text"
    return value, None


def _location(value: str, source_loose: str) -> tuple[str | None, str | None]:
    """Agendas split a venue over several lines, so each comma-separated part must appear, close together."""
    value = " ".join(value.split())
    if len(value) > 200:
        return None, "longer than 200 characters"
    padded, starts = f" {source_loose} ", []
    for part in filter(None, (loose(p) for p in re.split(r"[,|;\n]", value))):
        found = [m.start() for m in re.finditer(re.escape(f" {part} "), padded)]
        if not found:
            return None, f"'{part}' not found in the agenda text"
        starts.append(found)
    if not starts:
        return None, "empty"
    # Some occurrence of every part must fall within one 400-character window.
    anchors = [pos for positions in starts for pos in positions]
    if not any(all(any(anchor <= pos <= anchor + 400 for pos in positions) for positions in starts) for anchor in anchors):
        return None, "parts are not near each other in the agenda text"
    return value, None


def _numbers_present(value: str, source_compact: str) -> bool:
    """Every run of three or more digits in ``value`` occurs in the source, ignoring separators between groups."""
    digits = re.sub(r"\D", "", source_compact)
    return all(run in digits for run in re.findall(r"\d{3,}", value)) and all(
        re.sub(r"\D", "", m) in digits for m in re.findall(r"\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}", value))


def _phone(value: str, source_compact: str) -> tuple[str | None, str | None]:
    value = " ".join(value.split())
    if len(value) > 160 or not re.search(r"\d{3}\D{0,3}\d{3}\D{0,3}\d{4}", value):
        return None, "not a phone number"
    first = re.search(r"\(?\d{3}\)?\D{0,3}\d{3}\D{0,3}\d{4}", value).group()
    pattern = r"\D{0,3}".join(re.findall(r"\d{3,4}", first))
    if not re.search(pattern, source_compact) or not _numbers_present(value, source_compact):
        return None, "number not found in the agenda text"
    return value, None


def _email(value: str, source_compact: str) -> tuple[str | None, str | None]:
    value = value.strip().rstrip(".,;:)").removeprefix("mailto:")
    if not re.fullmatch(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", value):
        return None, "not an email address"
    if value.casefold() not in source_compact:
        return None, "email not found in the agenda text"
    return value, None


def _instructions(value: str, source_normalized: str, source_compact: str) -> tuple[str | None, str | None]:
    value = " ".join(value.split())
    if len(value) > 400:
        return None, "longer than 400 characters"
    for url in re.findall(r"(?i)(?:https?://|www\.)\S+", value):
        if _url(url, source_compact)[0] is None:
            return None, "mentions a link not in the agenda text"
    for email in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", value):
        if email.casefold() not in source_compact:
            return None, "mentions an email not in the agenda text"
    if not _numbers_present(value, source_compact):
        return None, "mentions a number not in the agenda text"
    for clock in re.findall(r"\b\d{1,2}:\d{2}", value):
        if clock not in source_normalized:
            return None, "mentions a time not in the agenda text"
    return value, None


def validate(reply: dict, source: str) -> tuple[dict, list[dict]]:
    """Keep only fields the source text supports; report the rest with a reason."""
    source_normalized, source_loose, source_compact = normalized(source), loose(source), compact(source)
    kept, rejected = {}, []

    def check(name, result):
        value, problem = result
        if problem:
            rejected.append({"field": name, "value": reply[name], "reason": problem})
        elif value:
            kept[name] = value

    for name in FIELDS:
        if not isinstance(reply.get(name), str) or not reply[name].strip():
            continue
        if name in ("remoteUrl", "watchUrl", "commentUrl"):
            check(name, _url(reply[name], source_compact))
        elif name == "phone":
            check(name, _phone(reply[name], source_compact))
        elif name == "commentEmail":
            check(name, _email(reply[name], source_compact))
        elif name == "location":
            check(name, _location(reply[name], source_loose))
        elif name == "deadline":
            check(name, _verbatim(reply[name], source_loose, 200))
        elif name == "instructionsQuote":
            check(name, _verbatim(reply[name], source_loose, 2000))
        elif name == "instructions":
            check(name, _instructions(reply[name], source_normalized, source_compact))
    if "instructions" in kept and "instructionsQuote" not in kept:
        rejected.append({"field": "instructions", "value": kept.pop("instructions"), "reason": "no supporting quote in the agenda text"})
    if kept.get("remoteUrl") and kept.get("remoteUrl") == kept.get("watchUrl"):
        kept.pop("watchUrl")
    return kept, rejected


# --- Input selection ------------------------------------------------------------

def _pending(conn, *, agency: str | None, meeting: str | None, force: bool, today: date, prompt_hash: str) -> dict:
    """Meetings whose current agenda documents (or the prompt) changed since their last extraction."""
    since = today - timedelta(days=LOOKBACK_DAYS)
    current = {}
    for r in conn.execute("""SELECT c.meeting_id,v.id,v.title,v.page_count,m.title AS meeting_title,m.local_date,a.name AS agency_name
            FROM document_current c JOIN document_versions v ON v.id=c.version_id JOIN meetings m ON m.id=c.meeting_id
            JOIN agencies a ON a.id=m.agency_id
            WHERE m.quarantine_reason IS NULL AND (m.local_date IS NULL OR m.local_date >= %s)
              AND (%s::text IS NULL OR m.agency_id=%s) AND (%s::text IS NULL OR m.id=%s)
            ORDER BY c.meeting_id,v.source_document_id""", (since, agency, agency, meeting, meeting)):
        current.setdefault(r["meeting_id"], []).append(r)
    stored = {r["meeting_id"]: r for r in conn.execute("SELECT meeting_id,version_ids,prompt_sha256 FROM ai_participation")}
    pending = {}
    for meeting_id, docs in current.items():
        agendas = [d for d in docs if not NOT_AGENDA.search(d["title"])] or docs
        agendas = agendas[:2]
        old = stored.get(meeting_id)
        if force or not old or old["prompt_sha256"] != prompt_hash or sorted(old["version_ids"]) != sorted(d["id"] for d in agendas):
            pending[meeting_id] = agendas
    return pending


def _pages(conn, pending: dict) -> dict:
    wanted = {d["id"]: d["page_count"] for docs in pending.values() for d in docs}
    pages = {}
    if not wanted:
        return pages
    for p in conn.execute("""SELECT document_version_id,page_number,text FROM document_pages
            WHERE document_version_id = ANY(%s) ORDER BY document_version_id,page_number""", (list(wanted),)):
        count = wanted[p["document_version_id"]]
        if p["page_number"] <= LEADING_PAGES or p["page_number"] == count:
            pages.setdefault(p["document_version_id"], []).append((p["page_number"], p["text"]))
    return pages


def render_user_message(docs: list, pages: dict) -> tuple[str, str]:
    """The model's input and the exact source text that validation checks against."""
    first = docs[0]
    header = f"Agency: {first['agency_name']}\nMeeting: {first['meeting_title']}"
    if first["local_date"]:
        header += f" ({first['local_date'].isoformat()})"
    blocks, budget = [], MAX_INPUT_CHARS
    for doc in docs:
        for number, text in pages.get(doc["id"], []):
            text = text.strip()[:budget]
            if not text:
                continue
            blocks.append(f'<page document="{doc["title"]}" number="{number}">\n{text}\n</page>')
            budget -= len(text)
            if budget <= 0:
                break
    source = "\n\n".join(blocks)
    return header + "\n\nExtract the participation details from these agenda pages.\n\n" + source, source


# --- Run ------------------------------------------------------------------------

def extract_participation(conn, *, agency: str | None = None, meeting: str | None = None, max_meetings: int | None = None,
                          force: bool = False, dry_run: bool = False, max_cost: float = DEFAULT_MAX_RUN_COST,
                          monthly_budget: float = DEFAULT_MONTHLY_BUDGET, clients: dict | None = None,
                          today: date | None = None, deadline: float | None = None) -> dict:
    """Read participation details from each pending meeting's agenda; ``deadline`` (``time.monotonic()``) stops before the next meeting."""
    system = PROMPT.read_text().strip()
    schema = response_schema()
    prompt_hash = prompt_sha256(system)
    pending = _pending(conn, agency=agency, meeting=meeting, force=force, today=today or date.today(), prompt_hash=prompt_hash)
    if max_meetings is not None:
        pending = dict(list(pending.items())[:max_meetings])
    pages = _pages(conn, pending)
    requests = {mid: render_user_message(docs, pages) for mid, docs in pending.items()}
    shared = estimate_tokens(system) + estimate_tokens(json.dumps(schema))
    estimate = sum(cost_usd(CHAIN[0][0], uncached_input=shared + estimate_tokens(user), output=EST_OUTPUT_TOKENS)
                   for user, source in requests.values() if source)
    spent_this_month = month_to_date_spend(conn)
    report = {"meetingsPending": len(pending), "estimatedCostUsd": round(estimate, 4), "monthToDateUsd": round(spent_this_month, 4),
              "monthlyBudgetUsd": monthly_budget, "dryRun": dry_run, "extracted": {}, "fieldsKept": {}, "fieldsRejected": 0,
              "noText": 0, "failedMeetings": [], "costUsd": 0.0}
    if dry_run or not requests:
        return report
    if clients is None and not has_credentials():
        report["skipped"] = "no Claude credentials (API key or workload identity federation)"
        return report
    if spent_this_month >= monthly_budget:
        raise SystemExit(f"Month-to-date AI spend ${spent_this_month:.2f} has reached the ${monthly_budget:.2f} budget")
    if estimate > max_cost:
        raise SystemExit(f"Estimated ${estimate:.4f} exceeds --max-cost ${max_cost:.2f}; use --max-meetings or raise it")
    clients = clients or {}
    for done, (meeting_id, (user, source)) in enumerate(requests.items()):
        version_ids = [d["id"] for d in pending[meeting_id]]
        if not source:
            # Nothing to read (a scanned or empty document); remember it so the next run does not retry.
            _store(conn, meeting_id, version_ids, prompt_hash, "none", {}, [])
            report["noText"] += 1
            conn.commit()
            continue
        if report["costUsd"] >= max_cost or spent_this_month + report["costUsd"] >= monthly_budget:
            report["stoppedEarly"] = "cost limit reached; remaining meetings are extracted on the next run"
            break
        if out_of_time(deadline):
            time_budget_stop(report, "meetings are extracted", meetingsDeferred=len(requests) - done)
            break
        stored = False
        for model, effort in CHAIN:
            client = clients.get(model) or clients.setdefault(model, AnthropicTopicModel(model, effort))
            for _ in range(ATTEMPTS_PER_MODEL):
                try:
                    reply = client.complete(system=system, user=user, schema=schema, max_tokens=MAX_TOKENS)
                except ModelError as exc:
                    if exc.fatal:
                        raise SystemExit(f"Participation extraction stopped: {exc}") from exc
                    _record_usage(conn, model, meeting_id, {}, ok=False, error=str(exc))
                    continue
                parsed, problem = _parse(reply)
                report["costUsd"] += _record_usage(conn, model, meeting_id, reply.usage, ok=problem is None, error=problem)
                if problem is None:
                    kept, rejected = validate(parsed, source)
                    _store(conn, meeting_id, version_ids, prompt_hash, reply.model or model, kept, rejected)
                    report["extracted"][model] = report["extracted"].get(model, 0) + 1
                    for name in kept:
                        report["fieldsKept"][name] = report["fieldsKept"].get(name, 0) + 1
                    report["fieldsRejected"] += len(rejected)
                    stored = True
                    break
            if stored:
                break
        if not stored:
            report["failedMeetings"].append(meeting_id)
        conn.commit()
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
    if not isinstance(parsed, dict) or set(parsed) != set(FIELDS):
        return {}, "response was not the expected JSON object"
    return parsed, None


def _record_usage(conn, model: str, meeting_id: str, usage: dict, *, ok: bool, error: str | None) -> float:
    counts = {name: int(usage.get(name, 0) or 0) for name in USAGE_FIELDS}
    cost = cost_usd(model, uncached_input=counts["input_tokens"], output=counts["output_tokens"],
                    cache_write=counts["cache_creation_input_tokens"], cache_read=counts["cache_read_input_tokens"])
    conn.execute("""INSERT INTO ai_usage(purpose,model,meeting_id,ok,input_tokens,output_tokens,
        cache_creation_input_tokens,cache_read_input_tokens,cost_usd,error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (PURPOSE, model, meeting_id, ok, *(counts[name] for name in USAGE_FIELDS), cost, error))
    return cost


def _store(conn, meeting_id: str, version_ids: list, prompt_hash: str, model: str, fields: dict, rejected: list) -> None:
    conn.execute("""INSERT INTO ai_participation(meeting_id,version_ids,prompt_sha256,model,fields,rejected)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT(meeting_id) DO UPDATE SET version_ids=excluded.version_ids,prompt_sha256=excluded.prompt_sha256,
        model=excluded.model,fields=excluded.fields,rejected=excluded.rejected,extracted_at=now()""",
        (meeting_id, json.dumps(sorted(version_ids)), prompt_hash, model, json.dumps(fields), json.dumps(rejected)))


# --- Export ------------------------------------------------------------------------

def merged(meeting_location, participation: dict, extracted: dict | None, agency_defaults: dict | None) -> tuple:
    """Source adapter values first, then the agenda, then the agency's standing instructions."""
    extracted = extracted or {}
    defaults = agency_defaults or {}

    def pick(*values):
        return next((v for v in values if v), None)

    result = {
        "instructions": pick(participation.get("instructions"), extracted.get("instructions"), defaults.get("instructions")),
        "url": pick(participation.get("url"), extracted.get("commentUrl"), defaults.get("url")),
        "remoteUrl": pick(participation.get("remoteUrl"), extracted.get("remoteUrl")),
        "watchUrl": pick(participation.get("watchUrl"), extracted.get("watchUrl"), defaults.get("watchUrl")),
        "phone": pick(participation.get("phone"), extracted.get("phone")),
        "email": pick(participation.get("email"), extracted.get("commentEmail"), defaults.get("email")),
        "deadline": pick(participation.get("deadline"), extracted.get("deadline")),
    }
    from_agenda = any(extracted.get(f) and result.get(k) == extracted.get(f) for k, f in (
        ("instructions", "instructions"), ("url", "commentUrl"), ("remoteUrl", "remoteUrl"), ("watchUrl", "watchUrl"),
        ("phone", "phone"), ("email", "commentEmail"), ("deadline", "deadline"))) or (
        not meeting_location and bool(extracted.get("location")))
    result["fromAgenda"] = from_agenda
    return pick(meeting_location, extracted.get("location")), result
