"""Production topic classification with Claude: Haiku first, Sonnet as fallback, rule tags last.

Each meeting's active agenda items go to Claude in one request, with the same
agenda-level input and prompt that ``evaluate-topics`` measures (decision 0011).
A reply that is refused, truncated, malformed, or misaligned is retried once,
then the meeting falls back to the next model in ``CHAIN``. When every model
fails, nothing is stored and the export keeps the rule tags for those items.

Results live in ``ai_topic_classifications``, one row per item. The export uses
a row only while it matches the item's current source fingerprint, so a revised
agenda shows rule tags until the next run. Operator overrides apply on top
either way. Every API call is recorded in ``ai_usage`` with its cost, and a run
refuses to start once month-to-date spend reaches the monthly budget.
"""
from __future__ import annotations

import hashlib
import json
import time

from .catalog import topic_catalog
from .evaluate_topics import (
    AnthropicTopicModel, ModelError, USAGE_FIELDS, _by_meeting, build_requests, cost_usd, estimated_usage,
    has_credentials, load_items, parse_reply, render_system_prompt, response_schema,
)

MODE = "agenda-context"
CHAIN = (("claude-haiku-5-5", "low"), ("claude-sonnet-5-5", "medium"))
ATTEMPTS_PER_MODEL = 2
DEFAULT_MAX_RUN_COST = 1.00  # USD for one run, including fallbacks
DEFAULT_MONTHLY_BUDGET = 5.00  # USD; the AI share of the under-$20/month total (decision 0011)
PURPOSE = "topic-classification"


def out_of_time(deadline: float | None) -> bool:
    """True once a scheduled run's time budget (a ``time.monotonic()`` deadline) has passed; None means no budget.

    Claude-backed steps check it before each new unit of work (a meeting, item, or batch), never mid-request,
    and stop the way a cost limit stops them: what is done stays committed and the rest waits for the next run.
    """
    return deadline is not None and time.monotonic() >= deadline


def time_budget_stop(report: dict, remaining: str, **deferred) -> None:
    """Record a time-budget stop in a step report: ``stoppedEarly`` plus what was left for the next run."""
    report["stoppedEarly"] = f"time budget reached; remaining {remaining} on the next run"
    report["timeBudgetReached"] = True
    report.update(deferred)


def prompt_sha256(system: str, schema: dict) -> str:
    return hashlib.sha256((system + json.dumps(schema, sort_keys=True)).encode()).hexdigest()


def item_input_sha256(item, prompt_hash: str) -> str:
    """Everything the model sees about an item, plus the prompt; a change means it needs reclassifying."""
    payload = {"prompt": prompt_hash, "agency": item.agency_name, "meeting": item.meeting_title, "date": item.meeting_date,
               "number": item.number, "section": item.section, "title": item.title, "excerpt": item.excerpt}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def month_to_date_spend(conn) -> float:
    row = conn.execute("SELECT COALESCE(SUM(cost_usd),0) AS total FROM ai_usage "
                       "WHERE recorded_at >= date_trunc('month', now())").fetchone()
    return float(row["total"])


def _stale_meetings(conn, items, prompt_hash: str, force: bool) -> dict:
    stored = {r["item_id"]: r["input_sha256"] for r in conn.execute("SELECT item_id,input_sha256 FROM ai_topic_classifications")}
    meetings = _by_meeting(items)
    if force:
        return meetings
    return {mid: rows for mid, rows in meetings.items()
            if any(stored.get(i.id) != item_input_sha256(i, prompt_hash) for i in rows)}


def classify_topics(conn, *, agency: str | None = None, max_meetings: int | None = None, force: bool = False,
                    dry_run: bool = False, max_cost: float = DEFAULT_MAX_RUN_COST,
                    monthly_budget: float = DEFAULT_MONTHLY_BUDGET, clients: dict | None = None,
                    deadline: float | None = None) -> dict:
    """Classify meetings whose items are new or changed since their last classification.

    ``deadline`` (a ``time.monotonic()`` value) stops the run before the next meeting once it passes.
    """
    catalog = list(topic_catalog())
    topic_ids = [t["id"] for t in catalog]
    system, schema = render_system_prompt(catalog=catalog), response_schema(catalog)
    prompt_hash = prompt_sha256(system, schema)
    items = load_items(conn, agency=agency)
    pending = _stale_meetings(conn, items, prompt_hash, force)
    if max_meetings is not None:
        pending = dict(list(pending.items())[:max_meetings])
    requests = build_requests([i for rows in pending.values() for i in rows], [MODE], system)
    primary = CHAIN[0][0]
    estimate = sum(cost_usd(primary, uncached_input=u["sharedPrefixTokens"] * u["requests"] + u["itemInputTokens"],
                            output=u["outputTokens"], requests=u["requests"]) for u in estimated_usage(requests, schema).values())
    spent_this_month = month_to_date_spend(conn)
    report = {"promptSha256": prompt_hash, "chain": [m for m, _ in CHAIN], "meetingsPending": len(pending),
              "itemsPending": sum(len(rows) for rows in pending.values()), "estimatedCostUsd": round(estimate, 4),
              "monthToDateUsd": round(spent_this_month, 4), "monthlyBudgetUsd": monthly_budget,
              "dryRun": dry_run, "classified": {}, "failedMeetings": [], "costUsd": 0.0}
    if dry_run or not requests:
        return report
    if clients is None and not has_credentials():
        report["skipped"] = "no Claude credentials (API key or workload identity federation); rule tags stay in place"
        return report
    if spent_this_month >= monthly_budget:
        raise SystemExit(f"Month-to-date AI spend ${spent_this_month:.2f} has reached the ${monthly_budget:.2f} budget")
    if estimate > max_cost:
        raise SystemExit(f"Estimated ${estimate:.4f} exceeds --max-cost ${max_cost:.2f}; use --max-meetings or raise it")
    clients = clients or {}
    for done, request in enumerate(requests):
        if report["costUsd"] >= max_cost or spent_this_month + report["costUsd"] >= monthly_budget:
            report["stoppedEarly"] = "cost limit reached; remaining meetings keep rule tags until the next run"
            break
        if out_of_time(deadline):
            time_budget_stop(report, "meetings keep rule tags and are classified", meetingsDeferred=len(requests) - done)
            break
        rows = {i.id: i for i in pending[request.meeting_id]}
        stored = False
        for model, effort in CHAIN:
            client = clients.get(model) or clients.setdefault(model, AnthropicTopicModel(model, effort))
            for _ in range(ATTEMPTS_PER_MODEL):
                try:
                    reply = client.complete(system=request.system, user=request.user, schema=schema, max_tokens=request.max_tokens)
                except ModelError as exc:
                    if exc.fatal:
                        raise SystemExit(f"Topic classification stopped: {exc}") from exc
                    _record_usage(conn, model, request.meeting_id, {}, ok=False, error=str(exc))
                    continue
                results, problems = parse_reply(reply, request, topic_ids)
                ok = not any(r["error"] for r in results.values())
                report["costUsd"] += _record_usage(conn, model, request.meeting_id, reply.usage, ok=ok,
                                                   error=None if ok else "; ".join(problems))
                if ok:
                    _store(conn, rows, results, model=reply.model or model, prompt_hash=prompt_hash)
                    report["classified"][model] = report["classified"].get(model, 0) + len(rows)
                    stored = True
                    break
            if stored:
                break
        if not stored:
            report["failedMeetings"].append(request.meeting_id)
        conn.commit()
    report["costUsd"] = round(report["costUsd"], 6)
    return report


def _record_usage(conn, model: str, meeting_id: str, usage: dict, *, ok: bool, error: str | None) -> float:
    counts = {name: int(usage.get(name, 0) or 0) for name in USAGE_FIELDS}
    cost = cost_usd(model, uncached_input=counts["input_tokens"], output=counts["output_tokens"],
                    cache_write=counts["cache_creation_input_tokens"], cache_read=counts["cache_read_input_tokens"])
    conn.execute("""INSERT INTO ai_usage(purpose,model,meeting_id,ok,input_tokens,output_tokens,
        cache_creation_input_tokens,cache_read_input_tokens,cost_usd,error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (PURPOSE, model, meeting_id, ok, *(counts[name] for name in USAGE_FIELDS), cost, error))
    return cost


def _store(conn, rows: dict, results: dict, *, model: str, prompt_hash: str) -> None:
    """One statement per meeting, however many items it has; a prompt change reclassifies them all (decision 0020)."""
    incoming = [{"item_id": item_id, "input_sha256": item_input_sha256(rows[item_id], prompt_hash),
                 "tahoe_relevant": result.get("tahoeRelevant", True), "topics": result["topics"], "reason": result["reason"]}
                for item_id, result in results.items()]
    conn.execute("""INSERT INTO ai_topic_classifications(item_id,input_sha256,source_fingerprint,prompt_sha256,model,
        tahoe_relevant,topics,reason)
        SELECT r.item_id,r.input_sha256,i.source_fingerprint,%(prompt)s,%(model)s,r.tahoe_relevant,r.topics,r.reason
        FROM jsonb_to_recordset(%(rows)s::jsonb) AS r(item_id text,input_sha256 text,tahoe_relevant boolean,topics jsonb,reason text)
        JOIN agenda_items i ON i.id=r.item_id
        ON CONFLICT(item_id) DO UPDATE SET input_sha256=excluded.input_sha256,source_fingerprint=excluded.source_fingerprint,
        prompt_sha256=excluded.prompt_sha256,model=excluded.model,tahoe_relevant=excluded.tahoe_relevant,
        topics=excluded.topics,reason=excluded.reason,classified_at=now()""",
        {"rows": json.dumps(incoming), "prompt": prompt_hash, "model": model})
