"""Agenda item summaries selected from each item's own staff report, with Claude (IDEA-016).

For every item with a verified staff report (``staff_reports``), the first pages of
the report go to Haiku 5.5 (Sonnet 5.5 as fallback, as in ``ai_topics``), which
returns two or three sentences, each trimmed from one passage of the report and
citing its page. The maintainer's rule (decision 0024) is that a summary repeats
the staff report verbatim as much as possible, changing it only to shorten it.

``validate`` enforces that rule before anything is published (decision 0010; no
human approval): each cited passage must be found on its page in the stored text,
and each sentence must be the passage with words deleted, allowing only a
grammatical "the", "a", "an", or "and" to be inserted. Numbers must come from the
passage, and a deleted negation fails the sentence. A summary with any failing
sentence is not published; the item keeps its agenda excerpt.

Results live in ``ai_item_summaries``, one row per item, keyed to everything the
model saw. The export shows a published row only while it matches the item's
current source fingerprint and staff report, so a revised agenda or report falls
back to the excerpt until the next run. Operator corrections and suppression in
``editorial`` apply on top. Usage goes to ``ai_usage`` under the shared monthly
budget, and writes are batched per meeting (decision 0020).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from .ai_topics import DEFAULT_MONTHLY_BUDGET, month_to_date_spend
from .evaluate_topics import AnthropicTopicModel, ModelError, USAGE_FIELDS, cost_usd, estimate_tokens, has_credentials
from .requested_actions import for_meeting as requested_actions
from .staff_reports import clean_text, procedural, Item, report_document_id

PROMPT = Path(__file__).with_name("prompts") / "staff_report_summary.md"
METHOD = "claude-staff-report-v1"
# Raise when ``validate`` accepts more than it did: items that failed validation are tried again once, and only they
# (a prompt change would send every item again). Stored on each result; results from before it existed count as 1.
VALIDATOR_VERSION = 1
CHAIN = (("claude-haiku-5-5", "low"), ("claude-sonnet-5-5", "medium"))
# Haiku gets a second try with the validator's objection; Sonnet costs about 20 times more, so one try.
ATTEMPTS = {"claude-haiku-5-5": 2, "claude-sonnet-5-5": 1}
PURPOSE = "staff-report-summary"
MAX_TOKENS = 4096
EST_OUTPUT_TOKENS = 800  # sentences, their source passages, and low-effort thinking
DEFAULT_MAX_RUN_COST = 0.50  # USD per run for summaries, separate from classification's limit

MAX_SENTENCES = 3
MIN_SENTENCE_WORDS = 5
MAX_SENTENCE_WORDS = 45
MAX_SUMMARY_WORDS = 110
MAX_SOURCE_WORDS = 160
# The only words a sentence may contain that its passage does not, where a deletion needs one.
GLUE = {"the", "a", "an", "and"}
# Cutting a passage's start or end is safe; cutting out its middle can join words into a new claim
# ("a resolution authorizing an application for X" -> "a resolution for X"), so middle cuts stay short.
MAX_INNER_CUTS = 3
MAX_INNER_CUT_WORDS = 12
NEGATIONS = {"not", "no", "never", "neither", "nor", "without", "unless", "except", "none", "cannot", "nothing", "non"}


def response_schema() -> dict:
    sentence = {"type": "object", "properties": {"page": {"type": "integer"}, "source": {"type": "string"},
                                                 "sentence": {"type": "string"}},
                "required": ["page", "source", "sentence"], "additionalProperties": False}
    return {"type": "object", "properties": {
        "decision": {"type": "string", "enum": ["summarize", "skip"]},
        "skipReason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "sentences": {"type": "array", "items": sentence}},
        "required": ["decision", "skipReason", "sentences"], "additionalProperties": False}


def prompt_sha256(system: str) -> str:
    return hashlib.sha256((system + json.dumps(response_schema(), sort_keys=True)).encode()).hexdigest()


# --- Validation ------------------------------------------------------------------

def _tokens(text: str) -> list[tuple[str, int, int]]:
    """Words with their character offsets in ``text`` itself (callers clean page text first)."""
    return [(m.group().casefold(), m.start(), m.end()) for m in re.finditer(r"[^\W_]+", text)]


def locate(source: str, page_text: str) -> tuple[int, int] | None:
    """Character span of ``source`` on the page, comparing words only (case, punctuation, and line breaks ignored).

    A copy with a stray word or two still finds its passage, but the span returned
    is always the page's own text, which is what the sentence is checked against.
    """
    wanted = [t for t, _, _ in _tokens(source)]
    page = _tokens(page_text)
    words = [t for t, _, _ in page]
    if len(wanted) < MIN_SENTENCE_WORDS or len(wanted) > MAX_SOURCE_WORDS:
        return None
    n = len(wanted)
    for start in range(len(words) - n + 1):
        if words[start:start + n] == wanted:
            return page[start][1], page[start + n - 1][2]
    blocks = [b for b in SequenceMatcher(None, words, wanted, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        return None
    # The longest run anchors the passage; keep the runs that line up with it.
    anchor = max(blocks, key=lambda b: b.size)
    aligned = [b for b in blocks if b.a - b.b == anchor.a - anchor.b or abs((b.a - b.b) - (anchor.a - anchor.b)) <= 2]
    matched = sum(b.size for b in aligned)
    first, last = min(b.a for b in aligned), max(b.a + b.size for b in aligned) - 1
    if matched < 0.9 * n or last - first + 1 > n + 3:
        return None
    return page[first][1], page[last][2]


def _alignment(sentence: list[str], passage: list[str]) -> list[tuple[int, int]]:
    """Longest common subsequence of two word lists, as (sentence index, passage index) pairs."""
    rows, cols = len(sentence), len(passage)
    table = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        for j in range(cols - 1, -1, -1):
            table[i][j] = table[i + 1][j + 1] + 1 if sentence[i] == passage[j] else max(table[i + 1][j], table[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < rows and j < cols:
        if sentence[i] == passage[j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def check_sentence(sentence: str, passage: str) -> str | None:
    """Why ``sentence`` is not ``passage`` with words deleted, or None when it is."""
    said = [t for t, _, _ in _tokens(sentence)]
    source = [t for t, _, _ in _tokens(passage)]
    if not MIN_SENTENCE_WORDS <= len(said) <= MAX_SENTENCE_WORDS:
        return f"sentence has {len(said)} words (allowed {MIN_SENTENCE_WORDS}-{MAX_SENTENCE_WORDS})"
    pairs = _alignment(said, source)
    kept = {i for i, _ in pairs}
    added = [said[i] for i in range(len(said)) if i not in kept]
    if any(word not in GLUE for word in added):
        return "adds words not in the cited passage: " + ", ".join(sorted({w for w in added if w not in GLUE}))
    if len(added) > max(1, len(said) // 10):
        return "inserts too many connecting words"
    used = [j for _, j in pairs]
    if not used:
        return "shares no words with the cited passage"
    if min(used) > 0:  # Dropping a sentence's opening is fine only up to a comma or colon ("If X is approved, ...").
        opening = passage[:_tokens(passage)[min(used)][1]].rstrip()
        if not opening.endswith((",", ":", ";")) and not re.search(r"(?:^|\s)\(?(?:\d{1,2}|[a-zA-Z])[.)]$", opening):
            return "starts in the middle of the passage's sentence"
    cuts = [b - a - 1 for a, b in zip(used, used[1:]) if b - a > 1]
    if len(cuts) > MAX_INNER_CUTS or any(cut > MAX_INNER_CUT_WORDS for cut in cuts):
        return f"cuts too much from the middle of the passage ({len(cuts)} cuts, longest {max(cuts)} words)"
    dropped = set(range(min(used) - 3, max(used) + 4)) - set(used)
    if any(0 <= j < len(source) and source[j] in NEGATIONS for j in dropped):
        return "drops a negation from the cited passage"
    return None


def validate(sentences: list[dict], pages: dict[int, str], document_id: str, *, limit: int = MAX_SENTENCES) -> tuple[str, list[dict]]:
    """The summary text and its citations, or ValueError naming the first problem."""
    if not 1 <= len(sentences) <= limit:
        raise ValueError(f"{len(sentences)} sentences (allowed 1-{limit})")
    pages = {n: clean_text(text) for n, text in pages.items()}
    texts, citations, total = [], [], 0
    for number, entry in enumerate(sentences, 1):
        sentence = " ".join(str(entry.get("sentence", "")).split())
        page = entry.get("page")
        if page not in pages:
            raise ValueError(f"sentence {number} cites page {page}, which was not provided")
        span = locate(str(entry.get("source", "")), pages[page])
        if span is None:
            raise ValueError(f"sentence {number}: cited passage is not on page {page}")
        passage = pages[page][span[0]:span[1]]
        before = pages[page][:span[0]]
        if before.strip() and not (before.rstrip()[-1] in ".?!:;•" or ("\n" in before[len(before.rstrip()):] or before.endswith("\n"))
                                   and (passage[0].isupper() or passage[0].isdigit())):
            raise ValueError(f"sentence {number}: the cited passage starts in the middle of a sentence on page {page}")
        problem = check_sentence(sentence, passage)
        if problem:
            raise ValueError(f"sentence {number}: {problem}: “{sentence[:200]}”")
        total += len(_tokens(sentence))
        if not re.search(r"[.!?][\"”’)]?$", sentence):
            sentence += "."
        texts.append(sentence[0].upper() + sentence[1:])
        citations.append({"documentId": document_id, "page": page, "quote": passage})
    if total > MAX_SUMMARY_WORDS:
        raise ValueError(f"summary has {total} words (at most {MAX_SUMMARY_WORDS})")
    if len({t.casefold() for t in texts}) != len(texts):
        raise ValueError("repeats a sentence")
    return " ".join(texts), citations


# --- Input selection ------------------------------------------------------------------

def _pending(conn, *, agency: str | None, meeting: str | None, force: bool, prompt_hash: str, today: date) -> list[dict]:
    rows, candidates = [], []
    for r in conn.execute("""SELECT s.item_id,s.sha256 AS report_sha256,s.title AS report_title,s.pages,s.source_fingerprint,
            i.meeting_id,i.number,i.title,i.section,i.citations,m.title AS meeting_title,m.local_date,m.agency_id,
            a.name AS agency_name,x.input_sha256 AS done,x.status AS done_status,x.prompt_sha256 AS done_prompt,
            x.report_sha256 AS done_report,x.source_fingerprint AS done_fingerprint,x.validator_version AS done_validator
            FROM item_staff_reports s
            JOIN agenda_items i ON i.id=s.item_id AND i.source_fingerprint=s.source_fingerprint AND i.active
            JOIN meetings m ON m.id=i.meeting_id JOIN agencies a ON a.id=m.agency_id
            LEFT JOIN ai_item_summaries x ON x.item_id=s.item_id
            WHERE m.quarantine_reason IS NULL AND (%s::text IS NULL OR m.agency_id=%s) AND (%s::text IS NULL OR m.id=%s)
            ORDER BY i.meeting_id,i.item_order,i.id""", (agency, agency, meeting, meeting)):
        item = Item(r["item_id"], r["number"] or "", r["title"], r["section"], r["source_fingerprint"], [])
        if not procedural(item):
            candidates.append(r)
    actions = _requested_actions(conn, {r["meeting_id"] for r in candidates})
    for r in candidates:
        r["pages"] = r["pages"] if isinstance(r["pages"], list) else json.loads(r["pages"])
        r["action"] = actions.get(r["item_id"])
        r["user"] = render_user_message(r)
        r["input_sha256"] = hashlib.sha256(json.dumps({"prompt": prompt_hash, "report": r["report_sha256"],
                                                       "user": r["user"]}).encode()).hexdigest()
        r["reason"] = _reason(r, prompt_hash)
        if r["reason"] or force:
            r["reason"] = r["reason"] or "forced"
            rows.append(r)
    return sorted(rows, key=lambda r: (abs((r["local_date"] - today).days) if r["local_date"] else 10_000, r["meeting_id"]))


def _reason(row: dict, prompt_hash: str) -> str | None:
    """Why an item needs a summary, or None when its stored result still stands.

    ``new``: never summarized. ``prompt``, ``report``, ``agenda``: the prompt, the staff report, or the agenda
    item changed. ``other``: something else in the message changed (the item or report title, the requested
    action, the agency or meeting name). ``validator``: it failed validation, and the validator has changed since.
    """
    if row["done"] is None:
        return "new"
    if row["done"] != row["input_sha256"]:
        if row["done_prompt"] != prompt_hash:
            return "prompt"
        if row["done_report"] != row["report_sha256"]:
            return "report"
        if row["done_fingerprint"] != row["source_fingerprint"]:
            return "agenda"
        return "other"
    if row["done_status"] == "validation_failed" and row["done_validator"] != VALIDATOR_VERSION:
        return "validator"
    return None


def _requested_actions(conn, meeting_ids: set) -> dict:
    """The agenda's requested action for every item of these meetings: two queries, whatever their number."""
    if not meeting_ids:
        return {}
    items: dict[str, list] = {}
    for r in conn.execute("SELECT id,meeting_id,citations FROM agenda_items WHERE active AND meeting_id=ANY(%s) ORDER BY item_order,id",
                          (list(meeting_ids),)):
        citations = r["citations"] if isinstance(r["citations"], list) else json.loads(r["citations"])
        items.setdefault(r["meeting_id"], []).append({"id": r["id"], "citations": citations})
    keys = sorted({(c["documentId"], int(c["page"])) for rows in items.values() for i in rows for c in i["citations"]})
    pages = {(r["document_version_id"], r["page_number"]): r["text"] for r in conn.execute(
        """SELECT p.document_version_id,p.page_number,p.text FROM document_pages p
        JOIN unnest(%s::text[],%s::int[]) AS k(version_id,page) ON p.document_version_id=k.version_id AND p.page_number=k.page""",
        ([k[0] for k in keys], [k[1] for k in keys]))} if keys else {}
    found = {}
    for rows in items.values():
        found.update(requested_actions(rows, pages))
    return found


def render_user_message(row: dict) -> str:
    header = [f"Agency: {row['agency_name']}", f"Meeting: {row['meeting_title']}"
              + (f" ({row['local_date'].isoformat()})" if row["local_date"] else "")]
    header.append(f"Agenda item {row['number']}: {row['title']}" if row["number"] else f"Agenda item: {row['title']}")
    header.append(f"Report: {row['report_title']}")
    pages = "\n\n".join(f'<page number="{p["number"]}">\n{p["text"]}\n</page>' for p in row["pages"])
    if row.get("action"):
        return ("\n".join(header) + f"\n\n<requested_action>{row['action']['text']}</requested_action>\n\n"
                "The summary already begins with this requested action from the agenda. Add one or two sentences from the "
                "staff report pages that a resident most needs beyond it.\n\n" + pages)
    return "\n".join(header) + "\n\nSummarize this agenda item from its staff report pages.\n\n" + pages


# --- Run ------------------------------------------------------------------------------

def summarize_items(conn, *, agency: str | None = None, meeting: str | None = None, max_items: int | None = None,
                    force: bool = False, dry_run: bool = False, max_cost: float = DEFAULT_MAX_RUN_COST,
                    monthly_budget: float = DEFAULT_MONTHLY_BUDGET, clients: dict | None = None,
                    today: date | None = None) -> dict:
    system = PROMPT.read_text().strip()
    schema = response_schema()
    prompt_hash = prompt_sha256(system)
    pending = _pending(conn, agency=agency, meeting=meeting, force=force, prompt_hash=prompt_hash, today=today or date.today())
    shared = estimate_tokens(system) + estimate_tokens(json.dumps(schema))
    estimates = [cost_usd(CHAIN[0][0], uncached_input=shared + estimate_tokens(r["user"]), output=EST_OUTPUT_TOKENS) for r in pending]
    # A run takes as many items as its cost limit allows; the rest wait for the next run.
    selected, running = [], 0.0
    for row, estimate in zip(pending, estimates):
        if (max_items is not None and len(selected) >= max_items) or running + estimate > max_cost:
            break
        selected.append(row)
        running += estimate
    spent_this_month = month_to_date_spend(conn)
    reasons: dict[str, int] = {}
    for row in pending:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    report = {"itemsPending": len(pending), "itemsPendingByReason": reasons, "itemsSelected": len(selected), "estimatedCostUsd": round(running, 4),
              "monthToDateUsd": round(spent_this_month, 4), "monthlyBudgetUsd": monthly_budget, "dryRun": dry_run,
              "published": {}, "itemsSkipped": 0, "validationFailed": 0, "failures": {}, "apiErrors": 0, "costUsd": 0.0}
    if len(selected) < len(pending):
        report["deferred"] = len(pending) - len(selected)
    conn.commit()
    if dry_run or not selected:
        return report
    if clients is None and not has_credentials():
        report["skipped"] = "no Claude credentials (API key or workload identity federation)"
        return report
    if spent_this_month >= monthly_budget:
        raise SystemExit(f"Month-to-date AI spend ${spent_this_month:.2f} has reached the ${monthly_budget:.2f} budget")
    clients = clients or {}
    by_meeting: dict[str, list] = {}
    for row in selected:
        by_meeting.setdefault(row["meeting_id"], []).append(row)
    for meeting_id, rows in by_meeting.items():
        results, usage = [], []
        for row in rows:
            if report["costUsd"] >= max_cost or spent_this_month + report["costUsd"] >= monthly_budget:
                report["stoppedEarly"] = "cost limit reached; remaining items are summarized on the next run"
                break
            result = _summarize(row, system, schema, clients, usage, report)
            if result:
                results.append(result)
        _store(conn, meeting_id, results, usage, prompt_hash)
        conn.commit()
        if report.get("stoppedEarly"):
            break
    report["costUsd"] = round(report["costUsd"], 6)
    return report


def _summarize(row: dict, system: str, schema: dict, clients: dict, usage: list, report: dict) -> dict | None:
    pages = {p["number"]: p["text"] for p in row["pages"]}
    document_id = report_document_id(row["item_id"])
    action = row.get("action")
    base = {"item_id": row["item_id"], "input_sha256": row["input_sha256"], "source_fingerprint": row["source_fingerprint"],
            "report_sha256": row["report_sha256"], "text": "", "citations": [], "error": None}
    last_error, answered = None, False
    for model, effort in CHAIN:
        client = clients.get(model) or clients.setdefault(model, AnthropicTopicModel(model, effort))
        for _ in range(ATTEMPTS[model]):
            user = row["user"] if not last_error else row["user"] + (
                f"\n\nAn earlier answer was rejected ({last_error}). Copy the report's own words; only delete words.")
            try:
                reply = client.complete(system=system, user=user, schema=schema, max_tokens=MAX_TOKENS)
            except ModelError as exc:
                if exc.fatal:
                    raise SystemExit(f"Item summaries stopped: {exc}") from exc
                usage.append(_usage(model, row["meeting_id"], {}, ok=False, error=str(exc)))
                report["apiErrors"] += 1
                continue
            answered = True
            parsed, problem = _parse(reply)
            if problem is None and parsed["decision"] == "skip":
                report["costUsd"] += _add_usage(usage, model, row["meeting_id"], reply.usage, ok=True, error=None)
                report["itemsSkipped"] += 1
                return {**base, "model": reply.model or model, "status": "skipped", "error": parsed.get("skipReason") or "skipped"}
            if problem is None:
                try:
                    sentences = parsed["sentences"]
                    if action:  # The agenda already leads with the requested action; drop restatements of it.
                        sentences = [e for e in sentences if not _repeats(str(e.get("sentence", "")), action["text"])]
                        if not sentences:
                            report["costUsd"] += _add_usage(usage, model, row["meeting_id"], reply.usage, ok=True, error=None)
                            report["itemsSkipped"] += 1
                            return {**base, "model": reply.model or model, "status": "skipped", "error": "only repeats the requested action"}
                    text, citations = validate(sentences, pages, document_id, limit=MAX_SENTENCES - 1 if action else MAX_SENTENCES)
                    if action:
                        text, citations = action["text"] + " " + text, [action["citation"], *citations]
                except ValueError as exc:
                    problem = f"validation: {exc}"
            report["costUsd"] += _add_usage(usage, model, row["meeting_id"], reply.usage, ok=problem is None, error=problem)
            if problem is None:
                report["published"][model] = report["published"].get(model, 0) + 1
                return {**base, "model": reply.model or model, "status": "published", "text": text, "citations": citations}
            last_error = problem
            report["failures"][problem.split(":")[0]] = report["failures"].get(problem.split(":")[0], 0) + 1
    if not answered:
        return None  # Only API errors: nothing is recorded, so the next run tries again.
    report["validationFailed"] += 1
    return {**base, "model": CHAIN[-1][0], "status": "validation_failed", "error": (last_error or "")[:500]}


def _repeats(sentence: str, action: str) -> bool:
    said = {t for t, _, _ in _tokens(sentence)}
    return bool(said) and len(said & {t for t, _, _ in _tokens(clean_text(action))}) >= 0.8 * len(said)


def _parse(reply) -> tuple[dict, str | None]:
    if reply.stop_reason == "refusal":
        return {}, "model declined the request (refusal)"
    if reply.stop_reason == "max_tokens":
        return {}, "response truncated at max_tokens"
    try:
        parsed = json.loads(reply.text)
    except json.JSONDecodeError:
        return {}, "response was not the expected JSON object"
    if not isinstance(parsed, dict) or parsed.get("decision") not in ("summarize", "skip") or not isinstance(parsed.get("sentences"), list):
        return {}, "response was not the expected JSON object"
    if parsed["decision"] == "summarize" and not all(isinstance(s, dict) for s in parsed["sentences"]):
        return {}, "response was not the expected JSON object"
    return parsed, None


def _usage(model: str, meeting_id: str, counts: dict, *, ok: bool, error: str | None) -> dict:
    counts = {name: int(counts.get(name, 0) or 0) for name in USAGE_FIELDS}
    cost = cost_usd(model, uncached_input=counts["input_tokens"], output=counts["output_tokens"],
                    cache_write=counts["cache_creation_input_tokens"], cache_read=counts["cache_read_input_tokens"])
    return {"model": model, "meeting_id": meeting_id, "ok": ok, **counts, "cost_usd": cost, "error": error}


def _add_usage(usage: list, model: str, meeting_id: str, counts: dict, *, ok: bool, error: str | None) -> float:
    usage.append(_usage(model, meeting_id, counts, ok=ok, error=error))
    return usage[-1]["cost_usd"]


def _store(conn, meeting_id: str, results: list[dict], usage: list[dict], prompt_hash: str) -> None:
    """One statement per meeting: results, the audit trail for published summaries, and API usage."""
    if not results and not usage:
        return
    conn.execute("""WITH incoming AS (
            SELECT * FROM jsonb_to_recordset(%(rows)s::jsonb) AS r(item_id text,input_sha256 text,source_fingerprint text,
              report_sha256 text,model text,status text,text text,citations jsonb,error text)),
        stored AS (
            INSERT INTO ai_item_summaries(item_id,input_sha256,source_fingerprint,report_sha256,prompt_sha256,model,status,text,citations,error,
              validator_version)
            SELECT item_id,input_sha256,source_fingerprint,report_sha256,%(prompt)s,model,status,text,citations,error,%(validator)s FROM incoming
            ON CONFLICT(item_id) DO UPDATE SET input_sha256=excluded.input_sha256,source_fingerprint=excluded.source_fingerprint,
              report_sha256=excluded.report_sha256,prompt_sha256=excluded.prompt_sha256,model=excluded.model,status=excluded.status,
              text=excluded.text,citations=excluded.citations,error=excluded.error,validator_version=excluded.validator_version,
              summarized_at=now()),
        audit AS (
            INSERT INTO summary_versions(item_id,text,status,method,source_fingerprint,citations,error)
            SELECT item_id,text,status,%(method)s,source_fingerprint,citations,error FROM incoming WHERE status='published')
        INSERT INTO ai_usage(purpose,model,meeting_id,ok,input_tokens,output_tokens,cache_creation_input_tokens,
            cache_read_input_tokens,cost_usd,error)
        SELECT %(purpose)s,model,meeting_id,ok,input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens,
            cost_usd,error FROM jsonb_to_recordset(%(usage)s::jsonb) AS u(model text,meeting_id text,ok boolean,input_tokens int,
            output_tokens int,cache_creation_input_tokens int,cache_read_input_tokens int,cost_usd numeric,error text)""",
                 {"rows": json.dumps(results), "prompt": prompt_hash, "validator": VALIDATOR_VERSION, "method": METHOD, "purpose": PURPOSE,
                  "usage": json.dumps(usage)})


# --- Export ---------------------------------------------------------------------------

class Supporting:
    """Each item's staff report, its current summary, and its attachment links for the export, in one query."""

    def __init__(self, conn):
        self.rows = {r["item_id"]: r for r in conn.execute(
            """SELECT i.id AS item_id,s.item_id IS NOT NULL AS has_report,s.kind,s.title,s.url,s.page_start,s.page_end,
              s.page_count,s.sha256,s.pages,s.media_type,s.mapped_at,a.text AS summary_text,a.citations AS summary_citations,a.summarized_at,
              t.files AS attachments
            FROM agenda_items i
            LEFT JOIN item_staff_reports s ON s.item_id=i.id AND s.source_fingerprint=i.source_fingerprint
            LEFT JOIN ai_item_summaries a ON a.item_id=s.item_id AND a.status='published'
              AND a.source_fingerprint=i.source_fingerprint AND a.report_sha256=s.sha256
            LEFT JOIN item_attachments t ON t.item_id=i.id AND t.source_fingerprint=i.source_fingerprint
            WHERE i.active AND (s.item_id IS NOT NULL OR t.item_id IS NOT NULL)""")}

    def summary(self, item_id: str, excerpt: dict | None, iso, action: dict | None = None) -> dict | None:
        """The staff report summary while it matches the item and its current report; else the agenda's requested action; else the excerpt."""
        row = self.rows.get(item_id)
        if not row or row["summary_text"] is None:
            if action:
                from .requested_actions import METHOD as ACTION_METHOD
                return {"text": action["text"], "status": "published", "method": ACTION_METHOD,
                        "generatedAt": (excerpt or {}).get("generatedAt"), "citations": [action["citation"]]}
            return excerpt
        citations = row["summary_citations"]
        return {"text": row["summary_text"], "status": "published", "method": METHOD, "generatedAt": iso(row["summarized_at"]),
                "citations": citations if isinstance(citations, list) else json.loads(citations)}

    def report(self, item_id: str, summary: dict | None, iso) -> dict | None:
        from .staff_reports import public_report
        row = self.rows.get(item_id)
        if not row or not row["has_report"]:
            return None
        document_id = report_document_id(item_id)
        cited = {c["page"] for c in (summary or {}).get("citations", []) if c.get("documentId") == document_id}
        return public_report(item_id, row, cited, iso)

    def attachments(self, item_id: str) -> list | None:
        """The files the agenda links under the item, in agenda order: url, printed name, label."""
        row = self.rows.get(item_id)
        files = row["attachments"] if row else None
        if files is None:
            return None
        return [{k: f[k] for k in ("url", "name", "label")} for f in (files if isinstance(files, list) else json.loads(files))]
