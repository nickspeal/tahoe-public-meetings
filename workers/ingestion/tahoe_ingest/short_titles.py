"""Short headlines for long agenda item titles, with Claude (decision 0035).

Some agendas print the whole recommended action as an item's title: El Dorado County items run to 500 or more
characters, Washoe and Douglas items to several hundred. For an item whose whole agenda text (``full_title`` when
the parser cut the title short, else the title) is longer than ``MIN_SOURCE_CHARS``, Haiku 5.5 (Sonnet 5.5 as
fallback, as in ``item_summaries``) writes a headline of 6 to 14 words. The website shows it in place of the title,
marked as shortened, with the agenda's whole text in the expanded item.

``validate`` checks the headline against the agenda text before anything is published (decision 0010; no human
approval): every word must be a word of the text, a simple inflection of one ("approve" -> "approval"), or one of a
few connecting words (``GLUE``); every number must appear in the text as printed; and a word that reverses or limits
something the headline names ("recommending the Board deny the appeal" in a headline about the appeal) must be kept
(``_required``). A rejected headline is retried with the reason; Haiku gets two tries, Sonnet one.

Results live in ``ai_short_titles``, keyed by a hash of the text the headline was written from (``source_sha256``),
not by item: an agenda posted again with the same text, a new item ID, or the same long title at the next meeting
(standing items) reuses the stored headline without a request, and a revised title simply has no headline until the
next run writes one. The export looks headlines up by the same hash. Usage goes to ``ai_usage`` under the shared
monthly budget, and writes are batched per meeting (decision 0020).

The operator can suppress a bad headline or replace it (``set_override``, the ``short-title`` command). The override
is stored in ``editorial.short_title`` with the hash of the agenda text it was made for, and it applies wherever that
text appears, as the headline does; a revised title leaves it behind. A suppressed headline leaves the item's own
title in place; a replacement is shown as written. Texts with an override are not sent to Claude.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

from .ai_topics import DEFAULT_MONTHLY_BUDGET, month_to_date_spend, out_of_time, time_budget_stop
from .evaluate_topics import AnthropicTopicModel, ModelError, USAGE_FIELDS, cost_usd, estimate_tokens, has_credentials
from .item_scope import in_scope
from .outline import NOT_PLACEHOLDER_SQL

PROMPT = Path(__file__).with_name("prompts") / "short_title.md"
METHOD = "claude-short-title-v1"
# Raise when ``validate`` accepts more than it did: headlines that failed validation are tried again once.
VALIDATOR_VERSION = 1
CHAIN = (("claude-haiku-5-5", "low"), ("claude-sonnet-5-5", "medium"))
ATTEMPTS = {"claude-haiku-5-5": 2, "claude-sonnet-5-5": 1}
PURPOSE = "short-title"
MAX_TOKENS = 2048
EST_OUTPUT_TOKENS = 300  # a one-line JSON answer and low-effort thinking
DEFAULT_MAX_RUN_COST = 0.05  # USD per run (about 180 titles, 6 minutes); separate from classification and summaries
MAX_SOURCE_CHARS = 4000  # the longest agenda text sent; longer text is cut (about 1,000 tokens)

# Items whose whole agenda text is longer than this get a headline (23% of items on 2026-11-03).
MIN_SOURCE_CHARS = 200
MIN_WORDS, MAX_WORDS, MAX_CHARS = 4, 16, 120

# Words a headline may use that the agenda text does not.
GLUE = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "with", "by", "from", "as", "into", "its"}
# Words that reverse or limit an action, grouped by meaning. One that governs something the headline names
# ("deny the appeal" in a headline about the appeal) must be kept in a word of the same group (``_required``).
POLARITY = [
    ("not", "cannot"), ("no",), ("never",), ("without",), ("unless",), ("except",),
    ("deny", "denies", "denied", "denying", "denial", "denials"),
    ("reject", "rejects", "rejected", "rejecting", "rejection"),
    ("repeal", "repeals", "repealed", "repealing"),
    ("rescind", "rescinds", "rescinded", "rescinding", "rescission"),
    ("revoke", "revokes", "revoked", "revoking", "revocation"),
    ("terminate", "terminates", "terminated", "terminating", "termination"),
    ("oppose", "opposes", "opposed", "opposing", "opposition"),
    ("prohibit", "prohibits", "prohibited", "prohibiting", "prohibition"),
    ("ban", "bans", "banned", "banning"),
    ("suspend", "suspends", "suspended", "suspending", "suspension"),
    ("veto", "vetoes", "vetoed"),
    ("withdraw", "withdraws", "withdrew", "withdrawn", "withdrawal"),
    ("cancel", "cancels", "canceled", "cancelled", "canceling", "cancelling", "cancellation"),
    ("disapprove", "disapproves", "disapproved", "disapproval"),
    ("overturn", "overturns", "overturned"),
    ("uphold", "upholds", "upheld", "upholding"),
]
# Phrases with a polarity word that reverse nothing: a cap ("not to exceed $81,000", "not more than 2 years", "at no
# cost", "with no change to the term"), a number ("Case No. 1234"), the routine list of a body's options ("approve,
# approve with conditions, or deny"), or the routine limit on delegated amendments.
_BENIGN = re.compile(r"\bnot[\s-]+(?:to[\s-]+)?exceed\b|\b(?:not|no)\s+(?:more|less|later|earlier|fewer|greater)\s+than\b"
                     r"|\bat\s+no\s+(?:additional\s+)?cost\b|\bnos?\.?\s*(?=\d)|\bwith\s+no\s+change\s+to\b"
                     # El Dorado's delegation clause: "execute amendments ... which do not increase the maximum dollar amount"
                     r"|\b(?:which|that)\s+(?:do|does|would|will)\s+not\s+increase\b"
                     r"|\bor\s+(?:\(\w{1,3}\)\s*)?(?:deny|denial|disapprov\w*|reject\w*)\b", re.I)
# How many words after a polarity word are what it governs, leaving out connecting words, numbers, and the
# auxiliaries and determiners in ``FILLER`` ("may not be allocated", "reject any and all bids").
GOVERNED_WORDS = 3
FILLER = {"be", "is", "are", "was", "were", "been", "being", "will", "may", "shall", "must", "can", "could", "would",
          "should", "have", "has", "do", "does", "that", "which", "this", "these", "those", "any", "all", "such"}


# --- Source text and its hash ----------------------------------------------------

def source_text(title: str, full_title: str | None = None) -> str:
    """The text a headline is written from and checked against: the whole agenda text, one line."""
    return " ".join((full_title or title or "").split())[:MAX_SOURCE_CHARS]


def qualifies(title: str, full_title: str | None = None) -> bool:
    return len(source_text(title, full_title)) > MIN_SOURCE_CHARS


def source_sha256(text: str) -> str:
    """The cache key: the same agenda text gets the same headline, whatever item, meeting, or document carries it."""
    return hashlib.sha256(text.encode()).hexdigest()


def response_schema() -> dict:
    return {"type": "object", "properties": {
        "decision": {"type": "string", "enum": ["shorten", "skip"]},
        "skipReason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "title": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        "required": ["decision", "skipReason", "title"], "additionalProperties": False}


def prompt_sha256(system: str) -> str:
    return hashlib.sha256((system + json.dumps(response_schema(), sort_keys=True)).encode()).hexdigest()


# --- Validation ------------------------------------------------------------------

class Rejected(ValueError):
    def __init__(self, kind: str, problem: str):
        self.kind, self.problem = kind, problem
        super().__init__(problem)


def _normalize(text: str) -> str:
    """Case, accents, curly quotes, possessives, and thousands separators removed."""
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = text.casefold().replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"(?<=[^\W\d_])'s\b", "", text)
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)


def _tokens(text: str) -> list[str]:
    """Words and numbers; a decimal stays one number ("2200007.50"), so "$2.2 million" cannot borrow a stray "2"."""
    return re.findall(r"\d+(?:\.\d+)+|[^\W_]+", _normalize(text))


def _bases(word: str) -> set[str]:
    """``word`` and the stems its common English endings leave: plural, past, -ing, -al, -ion, -ment."""
    forms = {word}
    if word.isdigit() or len(word) < 4:
        return forms
    for suffix, replacements in (("ies", ("y",)), ("es", ("", "e")), ("s", ("",)), ("ed", ("", "e")), ("ing", ("", "e")),
                                 ("al", ("", "e")), ("ion", ("", "e")), ("ment", ("",)), ("d", ("",))):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[:-len(suffix)]
            forms |= {stem + r for r in replacements}
            if len(stem) >= 3 and stem[-1] == stem[-2]:
                forms.add(stem[:-1])  # "planned" -> "plan"
    # "approval"/"approved"/"approving" all reach "approv"; "revocation" reaches "revoc", as does "revoke" below.
    forms |= {f[:-1] for f in list(forms) if f.endswith("e") and len(f) > 4}
    if word.endswith("ation") and len(word) > 7:
        forms.add(word[:-5])  # "termination" -> "termin", "terminate" -> "termin"
    if word.endswith("ate") and len(word) > 5:
        forms.add(word[:-3])
    return forms


def _group(word: str) -> int | None:
    return next((n for n, group in enumerate(POLARITY) if word in group), None)


def _required(source: str, named: set[str]) -> dict[int, int]:
    """How many words of each ``POLARITY`` group the headline must keep.

    A polarity word counts when one of the next ``GOVERNED_WORDS`` words it governs is something the headline
    names (``named``: the stems of the headline's own words): "deny the appeal" in a headline about the appeal,
    "denial of Tentative Parcel Map" in one about the map. One that governs only what the headline leaves out
    ("amendments that do not increase the total compensation", "terminate on March 31, 2027") does not, nor does
    one inside parentheses (procedural notes such as "(No discussion will take place)") or a benign phrase.
    Counted, not just noted: "deny the appeal of the Planning Commission's denial" needs both.
    """
    words = _tokens(_BENIGN.sub(" ", re.sub(r"\([^()]*\)", " ", source)))
    counts: dict[int, int] = {}
    for index, word in enumerate(words):
        group = _group(word)
        if group is None:
            continue
        governed = [w for w in words[index + 1:index + 12] if w not in GLUE | FILLER and not any(c.isdigit() for c in w)
                    and _group(w) is None][:GOVERNED_WORDS]
        if any(_bases(w) & named for w in governed):
            counts[group] = counts.get(group, 0) + 1
    return counts


def validate(title: str, source: str) -> str:
    """The headline as published, or Rejected naming the first problem."""
    headline = " ".join(str(title or "").split()).rstrip(".;:,")
    words = _tokens(headline)
    count = len(headline.split())  # "2026-27" and "three-lot" are one word each here
    if not MIN_WORDS <= count <= MAX_WORDS:
        raise Rejected("length", f"the headline has {count} words (allowed {MIN_WORDS}-{MAX_WORDS})")
    if len(headline) > MAX_CHARS:
        raise Rejected("length", f"the headline has {len(headline)} characters (at most {MAX_CHARS})")
    if len(headline) >= 0.8 * len(source):
        raise Rejected("length", "the headline is not much shorter than the agenda text")
    said = _tokens(source)
    exact = set(said)
    stems = set().union(*(_bases(w) for w in said)) if said else set()
    numbers = [w for w in words if any(c.isdigit() for c in w)]
    missing_numbers = [w for w in numbers if w not in exact]
    if missing_numbers:
        raise Rejected("number", "numbers not printed in the agenda text: " + ", ".join(dict.fromkeys(missing_numbers)))
    added = [w for w in words if not any(c.isdigit() for c in w) and w not in GLUE and w not in exact and not (_bases(w) & stems)]
    if added:
        raise Rejected("adds", "words not in the agenda text: " + ", ".join(dict.fromkeys(added)))
    named = set().union(*(_bases(w) for w in words if w not in GLUE | FILLER and _group(w) is None))
    kept: dict[int, int] = {}
    for w in words:
        if (group := _group(w)) is not None:
            kept[group] = kept.get(group, 0) + 1
    lost = [POLARITY[n][0] for n, count in sorted(_required(source, named).items()) if kept.get(n, 0) < count]
    if lost:
        raise Rejected("negation", "drops a word that reverses or limits what it names: " + ", ".join(lost))
    return headline[0].upper() + headline[1:]


# --- Input selection ------------------------------------------------------------------

def render_user_message(row: dict) -> str:
    header = [f"Agency: {row['agency_name']}", f"Meeting: {row['meeting_title']}"]
    if row.get("section"):
        header.append(f"Agenda section: {row['section']}")
    return "\n".join(header) + f"\n\n<agenda_text>\n{row['source']}\n</agenda_text>\n\nWrite the headline for this agenda item."


def _pending(conn, *, agency: str | None, meeting: str | None, force: bool, prompt_hash: str, today: date) -> list[dict]:
    """One row per distinct long agenda text without a current headline, nearest meeting first."""
    stored = {r["source_sha256"]: r for r in conn.execute(
        "SELECT source_sha256,status,prompt_sha256,validator_version FROM ai_short_titles")}
    overridden = {r["h"] for r in conn.execute("SELECT short_title->>'sourceSha256' AS h FROM editorial WHERE short_title IS NOT NULL")}
    rows: dict[str, dict] = {}
    for r in conn.execute(f"""SELECT i.id AS item_id,i.title,i.full_title,i.section,i.citations,i.meeting_id,
            m.title AS meeting_title,m.local_date,m.agency_id,a.name AS agency_name
            FROM agenda_items i JOIN meetings m ON m.id=i.meeting_id JOIN agencies a ON a.id=m.agency_id
            WHERE i.active AND {NOT_PLACEHOLDER_SQL} AND m.quarantine_reason IS NULL
              AND (%s::text IS NULL OR m.agency_id=%s) AND (%s::text IS NULL OR m.id=%s)
            ORDER BY i.meeting_id,i.item_order,i.id""", (agency, agency, meeting, meeting)):
        if not qualifies(r["title"], r["full_title"]):
            continue
        citations = r["citations"] if isinstance(r["citations"], list) else json.loads(r["citations"])
        if not in_scope(r["agency_id"], r["title"], citations):
            continue  # the same scope as staff report summaries (item_scope)
        r["source"] = source_text(r["title"], r["full_title"])
        r["source_sha256"] = source_sha256(r["source"])
        if r["source_sha256"] in overridden:
            continue  # the operator suppressed or replaced this text's headline
        done = stored.get(r["source_sha256"])
        reason = ("new" if done is None else "prompt" if done["prompt_sha256"] != prompt_hash
                  else "validator" if done["status"] == "validation_failed" and done["validator_version"] != VALIDATOR_VERSION
                  else None)
        if (reason or force) and r["source_sha256"] not in rows:
            r["reason"] = reason or "forced"
            r["user"] = render_user_message(r)
            rows[r["source_sha256"]] = r
    pending = list(rows.values())
    pending.sort(key=lambda r: (abs((r["local_date"] - today).days) if r["local_date"] else 10_000, r["meeting_id"]))
    return pending


# --- Run ------------------------------------------------------------------------------

def shorten_titles(conn, *, agency: str | None = None, meeting: str | None = None, max_items: int | None = None,
                   force: bool = False, dry_run: bool = False, max_cost: float = DEFAULT_MAX_RUN_COST,
                   monthly_budget: float = DEFAULT_MONTHLY_BUDGET, clients: dict | None = None,
                   today: date | None = None, deadline: float | None = None) -> dict:
    """Write headlines for pending long titles within the cost limit; ``deadline`` stops before the next one."""
    system = PROMPT.read_text().strip()
    schema = response_schema()
    prompt_hash = prompt_sha256(system)
    pending = _pending(conn, agency=agency, meeting=meeting, force=force, prompt_hash=prompt_hash, today=today or date.today())
    shared = estimate_tokens(system) + estimate_tokens(json.dumps(schema))
    selected, running = [], 0.0
    for row in pending:
        estimate = cost_usd(CHAIN[0][0], uncached_input=shared + estimate_tokens(row["user"]), output=EST_OUTPUT_TOKENS)
        if (max_items is not None and len(selected) >= max_items) or running + estimate > max_cost:
            break
        selected.append(row)
        running += estimate
    spent_this_month = month_to_date_spend(conn)
    reasons: dict[str, int] = {}
    for row in pending:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    report = {"titlesPending": len(pending), "titlesPendingByReason": reasons, "titlesSelected": len(selected),
              "estimatedCostUsd": round(running, 4), "monthToDateUsd": round(spent_this_month, 4),
              "monthlyBudgetUsd": monthly_budget, "dryRun": dry_run, "published": {}, "skipped": 0,
              "validationFailed": 0, "failures": {}, "apiErrors": 0, "costUsd": 0.0}
    if len(selected) < len(pending):
        report["deferred"] = len(pending) - len(selected)
    conn.commit()
    if dry_run or not selected:
        return report
    if clients is None and not has_credentials():
        report["skippedRun"] = "no Claude credentials (API key or workload identity federation)"
        return report
    if spent_this_month >= monthly_budget:
        raise SystemExit(f"Month-to-date AI spend ${spent_this_month:.2f} has reached the ${monthly_budget:.2f} budget")
    clients = clients or {}
    by_meeting: dict[str, list] = {}
    for row in selected:
        by_meeting.setdefault(row["meeting_id"], []).append(row)
    attempted = 0
    for meeting_id, rows in by_meeting.items():
        results, usage = [], []
        for row in rows:
            if report["costUsd"] >= max_cost or spent_this_month + report["costUsd"] >= monthly_budget:
                report["stoppedEarly"] = "cost limit reached; remaining titles are shortened on the next run"
                break
            if out_of_time(deadline):
                time_budget_stop(report, "titles are shortened", titlesDeferred=len(selected) - attempted)
                break
            attempted += 1
            result = shorten(row, system, schema, clients, usage, report)
            if result:
                results.append(result)
        _store(conn, results, usage, prompt_hash)
        conn.commit()
        if report.get("stoppedEarly"):
            break
    report["costUsd"] = round(report["costUsd"], 6)
    return report


FIXES = {
    "adds": ("Use only words that appear in the agenda text, in any form (\"approve\" may become \"approval\"), plus "
             "small connecting words such as \"the\", \"of\", \"for\", \"to\". Replace or drop the words named above."),
    "number": "Copy numbers, amounts, and dates exactly as the agenda prints them, or leave them out.",
    "negation": "Keep the word named above (or another form of it) in the headline, with the action it reverses or limits.",
    "length": "Write 6 to 14 words.",
}


def shorten(row: dict, system: str, schema: dict, clients: dict, usage: list, report: dict) -> dict | None:
    """One headline: Haiku twice, then Sonnet once; None when only API errors occurred (tried again next run)."""
    base = {"source_sha256": row["source_sha256"], "text": "", "error": None}
    last_error, feedback, answered = None, "", False
    for model, effort in CHAIN:
        client = clients.get(model) or clients.setdefault(model, AnthropicTopicModel(model, effort))
        for _ in range(ATTEMPTS[model]):
            started = time.monotonic()
            try:
                reply = client.complete(system=system, user=row["user"] + feedback, schema=schema, max_tokens=MAX_TOKENS)
            except ModelError as exc:
                if exc.fatal:
                    raise SystemExit(f"Short titles stopped: {exc}") from exc
                usage.append(_usage(model, row["meeting_id"], {}, ok=False, error=str(exc)))
                report["apiErrors"] += 1
                continue
            report.setdefault("latencySeconds", []).append(round(time.monotonic() - started, 2))
            answered = True
            parsed, problem, rejected = _parse(reply), None, None
            if parsed is None:
                problem = "response was not the expected JSON object"
            elif parsed["decision"] == "skip":
                report["costUsd"] += _add_usage(usage, model, row["meeting_id"], reply.usage, ok=True, error=None)
                report["skipped"] += 1
                return {**base, "model": reply.model or model, "status": "skipped", "error": parsed.get("skipReason") or "skipped"}
            else:
                try:
                    text = validate(parsed.get("title") or "", row["source"])
                except Rejected as exc:
                    rejected, problem = exc, f"validation: {exc}"
            report["costUsd"] += _add_usage(usage, model, row["meeting_id"], reply.usage, ok=problem is None,
                                            error=problem and f"{problem}{REPLY_MARK}{reply.text[:500]}")
            if problem is None:
                report["published"][model] = report["published"].get(model, 0) + 1
                return {**base, "model": reply.model or model, "status": "published", "text": text}
            last_error = problem
            kind = f"validation: {rejected.kind}" if rejected else problem
            report["failures"][kind] = report["failures"].get(kind, 0) + 1
            feedback = retry_message(parsed, rejected, problem)
    if not answered:
        return None
    report["validationFailed"] += 1
    return {**base, "model": CHAIN[-1][0], "status": "validation_failed", "error": (last_error or "")[:500]}


REPLY_MARK = "\n\nreply: "


def retry_message(parsed: dict | None, rejected: Rejected | None, problem: str) -> str:
    if rejected is None:
        return f"\n\nYour previous answer was rejected: {problem}. Answer again in the required format."
    return (f"\n\n<rejected_answer>\nYour previous headline was rejected and will not be published: {rejected.problem}.\n"
            f"Rejected headline: “{(parsed or {}).get('title') or ''}”\nHow to fix it: {FIXES.get(rejected.kind, FIXES['adds'])}\n"
            "</rejected_answer>\nAnswer again, following every rule.")


def _parse(reply) -> dict | None:
    if reply.stop_reason in ("refusal", "max_tokens"):
        return None
    try:
        parsed = json.loads(reply.text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or parsed.get("decision") not in ("shorten", "skip"):
        return None
    if parsed["decision"] == "shorten" and not isinstance(parsed.get("title"), str):
        return None
    return parsed


def _usage(model: str, meeting_id: str, counts: dict, *, ok: bool, error: str | None) -> dict:
    counts = {name: int(counts.get(name, 0) or 0) for name in USAGE_FIELDS}
    cost = cost_usd(model, uncached_input=counts["input_tokens"], output=counts["output_tokens"],
                    cache_write=counts["cache_creation_input_tokens"], cache_read=counts["cache_read_input_tokens"])
    return {"model": model, "meeting_id": meeting_id, "ok": ok, **counts, "cost_usd": cost, "error": error}


def _add_usage(usage: list, model: str, meeting_id: str, counts: dict, *, ok: bool, error: str | None) -> float:
    usage.append(_usage(model, meeting_id, counts, ok=ok, error=error))
    return usage[-1]["cost_usd"]


def _store(conn, results: list[dict], usage: list[dict], prompt_hash: str) -> None:
    """One statement per meeting: headlines and API usage."""
    if not results and not usage:
        return
    conn.execute("""WITH stored AS (
            INSERT INTO ai_short_titles(source_sha256,prompt_sha256,model,status,text,error,validator_version)
            SELECT source_sha256,%(prompt)s,model,status,text,error,%(validator)s
            FROM jsonb_to_recordset(%(rows)s::jsonb) AS r(source_sha256 text,model text,status text,text text,error text)
            ON CONFLICT(source_sha256) DO UPDATE SET prompt_sha256=excluded.prompt_sha256,model=excluded.model,
              status=excluded.status,text=excluded.text,error=excluded.error,validator_version=excluded.validator_version,
              generated_at=now())
        INSERT INTO ai_usage(purpose,model,meeting_id,ok,input_tokens,output_tokens,cache_creation_input_tokens,
            cache_read_input_tokens,cost_usd,error)
        SELECT %(purpose)s,model,meeting_id,ok,input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens,
            cost_usd,error FROM jsonb_to_recordset(%(usage)s::jsonb) AS u(model text,meeting_id text,ok boolean,input_tokens int,
            output_tokens int,cache_creation_input_tokens int,cache_read_input_tokens int,cost_usd numeric,error text)""",
                 {"rows": json.dumps(results), "prompt": prompt_hash, "validator": VALIDATOR_VERSION, "purpose": PURPOSE,
                  "usage": json.dumps(usage)})


# --- Operator overrides -------------------------------------------------------------

def set_override(conn, item_id: str, *, text: str | None = None, suppress: bool = False, clear: bool = False,
                 reason: str = "", author: str = "") -> dict:
    """Suppress or replace the headline of an item's agenda text, or clear the override; one per text.

    The override is stored on the item's ``editorial`` row with the hash of its current agenda text, and the export
    applies it to every item with that text. Setting one replaces any other item's override for the same text.
    """
    if sum((text is not None, suppress, clear)) != 1:
        raise ValueError("give exactly one of a replacement text, suppress, or clear")
    item = conn.execute("SELECT id,title,full_title FROM agenda_items WHERE id=%s", (item_id,)).fetchone()
    if not item:
        raise ValueError(f"unknown item {item_id}")
    if not qualifies(item["title"], item["full_title"]):
        raise ValueError(f"item {item_id} has {MIN_SOURCE_CHARS} characters of agenda text or fewer; it gets no short title")
    digest = source_sha256(source_text(item["title"], item["full_title"]))
    override = None
    if not clear:
        if text is not None:
            text = " ".join(text.split())
            if not text or len(text) > MAX_CHARS:
                raise ValueError(f"a replacement short title needs 1 to {MAX_CHARS} characters")
        if not reason.strip() or not author.strip():
            raise ValueError("a short title override requires a reason and an author")
        override = {"text": text, "sourceSha256": digest, "reason": reason.strip(), "author": author.strip(),
                    "updatedAt": datetime.now(timezone.utc).isoformat()}
    with conn.transaction():
        conn.execute("UPDATE editorial SET short_title=NULL,updated_at=now() WHERE short_title->>'sourceSha256'=%s", (digest,))
        if override:
            conn.execute("""INSERT INTO editorial(item_id,short_title) VALUES (%s,%s)
                ON CONFLICT(item_id) DO UPDATE SET short_title=excluded.short_title,updated_at=now()""",
                         (item_id, json.dumps(override)))
        conn.execute("UPDATE agenda_items SET updated_at=clock_timestamp() WHERE id=%s", (item_id,))
    return {"item": item_id, "sourceSha256": digest,
            "override": "cleared" if clear else "suppressed" if suppress else "replaced", "text": text}


# --- Export ---------------------------------------------------------------------------

class Headlines:
    """Published headlines by source hash for the export, operator overrides applied, in one query.

    An override wins over Claude's headline: a suppressed one maps to None (the item keeps its own title), a
    replacement to the operator's text.
    """

    def __init__(self, conn):
        self.rows: dict[str, str | None] = {}
        for r in conn.execute("""SELECT source_sha256,text,false AS override FROM ai_short_titles WHERE status='published'
                UNION ALL SELECT short_title->>'sourceSha256',short_title->>'text',true FROM editorial WHERE short_title IS NOT NULL
                ORDER BY override"""):
            self.rows[r["source_sha256"]] = r["text"]

    def for_item(self, title: str, full_title: str | None) -> str | None:
        if not self.rows or not qualifies(title, full_title):
            return None
        return self.rows.get(source_sha256(source_text(title, full_title)))
