"""Compare rule-based topic tags with a Claude classifier on stored agenda items.

There is no gold label set yet, so this is a disagreement review: it reports
where methods differ and writes a file the operator can annotate into a gold set.
It only reads the database. Without ANTHROPIC_API_KEY (or with --dry-run) it
makes no network calls; it prints the sample, one meeting's prompt, the rule
results, and a cost projection from a chars/4 token estimate.

The model call sits behind a small provider interface, ``complete(...) ->
ModelReply``, so stored results and comparisons stay provider-neutral. The
Anthropic SDK is an optional extra, imported only for a live run.

Edit the prompt in ``prompts/topic_classification.md``; the topic rubric is
rendered from ``packages/domain/topics.json``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from string import Template

from .catalog import expand_topics, topic_catalog
from .topics import CLASSIFIER_VERSION, classify

MODEL_ID = "claude-haiku-5-5"
COMPARISON_MODEL_ID = "claude-sonnet-5-5"
PROMPT_TEMPLATE = Path(__file__).with_name("prompts") / "topic_classification.md"
MODES = ("title-only", "agenda-context")
DEFAULT_SEED = 20261020
DEFAULT_CONTEXT_CHARS = 1200  # about 300 tokens of agenda text per item
CHARS_PER_TOKEN = 4

# Keyless-estimate assumptions only; a live run reports actual usage instead.
EST_OUTPUT_TOKENS_PER_ITEM = 45  # one JSON entry with a one-line reason
EST_THINKING_TOKENS_PER_REQUEST = 300  # adaptive thinking at effort "low", billed as output

# USD per million tokens: Claude API list prices cached in the claude-api skill
# (2026-10-06). Verify at https://platform.claude.com/docs/en/about-claude/pricing
# before budgeting. Haiku 5.5 bills prompts over 100K tokens at the long rates.
PRICING = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "long_input": 0.50, "long_output": 2.50,
                         "long_context_over": 100_000},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
}
CACHE_WRITE_MULTIPLIER = 1.25  # 5-minute TTL
CACHE_READ_MULTIPLIER = 0.10
BATCH_MULTIPLIER = 0.50
MIN_CACHEABLE_TOKENS = 512  # shorter prefixes are silently not cached on Haiku 5.5
WORKLOADS = (("handoff estimate", 22), ("about two dozen agencies", 100))
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
RULES = "rules"
RETRIES = 1  # one more attempt when a reply is refused, truncated, malformed, or misaligned
RULES_CONTEXT = "rules-on-agenda-context"


@dataclass
class AgendaItem:
    id: str
    meeting_id: str
    agency_id: str
    agency_name: str
    meeting_title: str
    meeting_date: str | None
    number: str
    order: int
    title: str
    section: str | None
    citations: list = field(default_factory=list)
    excerpt: str = ""
    excerpt_pages: list = field(default_factory=list)


@dataclass
class ClassificationRequest:
    meeting_id: str
    mode: str
    system: str
    user: str
    refs: dict  # short ref -> item ID

    @property
    def max_tokens(self) -> int:
        # Thinking counts toward max_tokens; leave room for medium-effort thinking plus one entry per item.
        # Only tokens actually generated are billed.
        return min(32_000, 4_096 + 200 * len(self.refs))


@dataclass
class ModelReply:
    text: str
    stop_reason: str | None
    usage: dict
    model: str
    request_id: str | None = None


class ModelError(Exception):
    def __init__(self, message: str, *, fatal: bool = False):
        super().__init__(message)
        self.fatal = fatal


# --- Loading and sampling -------------------------------------------------

def load_items(conn, *, agency: str | None = None, context_chars: int = DEFAULT_CONTEXT_CHARS) -> list[AgendaItem]:
    """Read active agenda items and the agenda-page text their citations point to."""
    rows = list(conn.execute("""SELECT i.id, i.meeting_id, i.number, i.item_order, i.title, i.section, i.citations,
            m.agency_id, a.name AS agency_name, m.title AS meeting_title, m.local_date
        FROM agenda_items i JOIN meetings m ON m.id=i.meeting_id JOIN agencies a ON a.id=m.agency_id
        WHERE i.active AND (%(agency)s::text IS NULL OR m.agency_id=%(agency)s)
        ORDER BY m.agency_id, m.local_date NULLS LAST, m.id, i.item_order, i.id""", {"agency": agency}))
    items = [AgendaItem(id=r["id"], meeting_id=r["meeting_id"], agency_id=r["agency_id"], agency_name=r["agency_name"],
                        meeting_title=r["meeting_title"], meeting_date=r["local_date"].isoformat() if r["local_date"] else None,
                        number=r["number"] or "", order=r["item_order"], title=r["title"], section=r["section"],
                        citations=_json(r["citations"]) or []) for r in rows]
    versions = sorted({c["documentId"] for item in items for c in item.citations})
    pages = {(p["document_version_id"], p["page_number"]): p["text"] for p in conn.execute(
        "SELECT document_version_id, page_number, text FROM document_pages WHERE document_version_id = ANY(%s)",
        (versions,))}
    for meeting_items in _by_meeting(items).values():
        attach_excerpts(meeting_items, pages, context_chars)
    return items


def attach_excerpts(items: list[AgendaItem], pages: dict, max_chars: int) -> None:
    """Give each item the agenda text from its own title up to the next item's title.

    Items arrive in agenda order. The block may continue onto the following
    page, and is whitespace-collapsed and bounded so neighbors are excluded.
    Matched title quotes can overlap a neighbor's line; overlap goes to the
    earlier item. The last item keeps only its quote, because what follows
    it is posting and accessibility boilerplate rather than item content.
    """
    cursors: dict = {}
    anchors = [_anchor(item, pages, cursors) for item in items]
    previous = None
    for anchor in anchors:
        if anchor is None:
            continue
        if previous and previous[0] == anchor[0] and anchor[1] < previous[2] < anchor[2]:
            anchor[1] = previous[2]
        previous = anchor
    for index, item in enumerate(items):
        anchor = anchors[index]
        if anchor is None:
            continue
        key, start, end = anchor
        following = next((a for a in anchors[index + 1:] if a is not None), None)
        text = pages[key]
        used = [key[1]]
        if following is None:
            raw = text[start:end]
        elif following[0] == key:
            raw = text[start:max(end, following[1])]
        else:
            raw = text[start:]
            if following[0] == (key[0], key[1] + 1):
                raw += "\n" + pages[following[0]][:following[1]]
                used.append(key[1] + 1)
        excerpt = TRAILING_ENUMERATOR.sub("", " ".join(raw.split()))
        item.excerpt = _bounded(excerpt, max_chars)
        item.excerpt_pages = used


# The next item's number ("12.", "B.", "VII.", "5.1") sits just before its title quote.
TRAILING_ENUMERATOR = re.compile(r"\s+(?:\d{1,3}\.\d{1,2}|(?:\d{1,3}|[A-Z]|[IVXL]{1,5})\.)$")


def _anchor(item: AgendaItem, pages: dict, cursors: dict):
    if not item.citations:
        return None
    citation = item.citations[0]
    key = (citation["documentId"], int(citation["page"]))
    text, quote = pages.get(key), (citation.get("quote") or "").strip()
    if not text or not quote:
        return None
    pattern = re.compile(re.escape(quote), re.I)
    found = pattern.search(text, cursors.get(key, 0)) or pattern.search(text)
    if not found:
        return None
    cursors[key] = found.start() + 1
    return [key, found.start(), found.end()]


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " …"


def sample_items(items: list[AgendaItem], *, limit: int | None = None, seed: int = DEFAULT_SEED) -> list[AgendaItem]:
    """Stable sample stratified by agency, returned in agenda order."""
    if limit is None or limit >= len(items):
        return list(items)
    if limit < 1:
        raise ValueError("--limit must be at least 1")
    groups = defaultdict(list)
    for item in items:
        groups[item.agency_id].append(item)
    quotas = _allocate(limit, {agency: len(group) for agency, group in groups.items()})
    chosen = set()
    for agency in sorted(groups):
        pool = sorted(groups[agency], key=lambda item: item.id)
        random.Random(f"{seed}:{agency}").shuffle(pool)  # per-agency seed: stable if another agency changes
        chosen.update(item.id for item in pool[:quotas[agency]])
    return [item for item in items if item.id in chosen]


def _allocate(limit: int, sizes: dict) -> dict:
    """Proportional quotas (largest remainder), at least one per agency when possible."""
    quotas = {agency: 1 if limit >= len(sizes) else 0 for agency in sizes}
    capacity = {agency: sizes[agency] - quotas[agency] for agency in sizes}
    remaining = limit - sum(quotas.values())
    total = sum(capacity.values()) or 1
    shares = {agency: remaining * capacity[agency] / total for agency in sizes}
    for agency in sizes:
        quotas[agency] += int(shares[agency])
    leftover = limit - sum(quotas.values())
    for agency in sorted(sizes, key=lambda a: (-(shares[a] - int(shares[a])), a)):
        if leftover <= 0:
            break
        if quotas[agency] < sizes[agency]:
            quotas[agency] += 1
            leftover -= 1
    return quotas


def _by_meeting(items):
    groups = {}
    for item in items:
        groups.setdefault(item.meeting_id, []).append(item)
    return groups


# --- Rule classifier -------------------------------------------------------

def _topic_rank():
    return {topic["id"]: index for index, topic in enumerate(topic_catalog())}


def ordered(topics) -> list[str]:
    rank = _topic_rank()
    return sorted(topics, key=lambda t: (rank.get(t, len(rank)), t))


def rule_results(items: list[AgendaItem], *, use_context: bool = False) -> dict:
    """The production title rules, or (diagnostic only) the same patterns over agenda excerpts."""
    results = {}
    for item in items:
        text = item.excerpt if use_context and item.excerpt else item.title
        evidence = classify(item.agency_id, text, item.citations)
        reason = "; ".join(f'{detail["rule"]}: "{detail["matchedText"]}"' for _, detail in sorted(evidence.items()))
        results[item.id] = {"topics": ordered(evidence), "reason": reason or "no rule matched", "error": None}
    return results


# --- Prompt construction ---------------------------------------------------

def render_system_prompt(template_text: str | None = None, catalog=None) -> str:
    catalog = list(catalog or topic_catalog())
    template_text = PROMPT_TEMPLATE.read_text() if template_text is None else template_text
    lines = []
    for topic in catalog:
        description = topic["description"].rstrip(".")
        line = f"- {topic['id']} ({topic['name']}): {description}."
        if topic.get("examples"):
            line += " Examples: " + "; ".join(topic["examples"]) + "."
        # The operator's judgment calls, added over time, calibrate topics that need judgment (such as predicted Hot Topics).
        for example in topic.get("judgmentExamples", []):
            verdict = "Tag" if example["tag"] else "Do not tag"
            line += f"\n  - {verdict}: {example['item']} ({example['why']})"
        lines.append(line)
    prompt = Template(template_text).safe_substitute(
        topics="\n".join(lines), topic_ids=", ".join(topic["id"] for topic in catalog)).strip()
    missing = [topic["id"] for topic in catalog if f"- {topic['id']} (" not in prompt]
    if missing:
        raise ValueError(f"prompt template must include $topics; missing rubric for {missing}")
    return prompt


def response_schema(catalog=None) -> dict:
    ids = [topic["id"] for topic in (catalog or topic_catalog())]
    entry = {"type": "object", "properties": {
        # Reason and Tahoe relevance come before topics so the model decides after explaining.
        "ref": {"type": "string"},
        "reason": {"type": "string"},
        "tahoeRelevant": {"type": "boolean"},
        "topics": {"type": "array", "items": {"type": "string", "enum": ids}}},
        "required": ["ref", "reason", "tahoeRelevant", "topics"], "additionalProperties": False}
    return {"type": "object", "properties": {"items": {"type": "array", "items": entry}},
            "required": ["items"], "additionalProperties": False}


def render_user_message(meeting_items: list[AgendaItem], mode: str) -> tuple[str, dict]:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    first = meeting_items[0]
    header = f"Agency: {first.agency_name}\nMeeting: {first.meeting_title}"
    if first.meeting_date:
        header += f" ({first.meeting_date})"
    refs, blocks = {}, []
    for number, item in enumerate(meeting_items, 1):
        # Refs look nothing like agenda numbers ("5.1"), which a model can otherwise echo by mistake.
        ref = f"i{number}"
        refs[ref] = item.id
        lines = [f'<item ref="{ref}">']
        if item.number:
            lines.append(f"Number: {item.number}")
        if item.section and item.section != item.title:
            lines.append(f"Section: {item.section}")
        lines.append(f"Title: {item.title}")
        if mode == "agenda-context" and item.excerpt:
            pages = ", ".join(str(page) for page in item.excerpt_pages)
            lines.append(f"Agenda excerpt (page {pages}): {item.excerpt}")
        lines.append("</item>")
        blocks.append("\n".join(lines))
    return header + "\n\nClassify each agenda item below.\n\n" + "\n\n".join(blocks), refs


def build_requests(sample: list[AgendaItem], modes, system: str) -> list[ClassificationRequest]:
    requests = []
    for meeting_id, meeting_items in _by_meeting(sample).items():
        for mode in modes:
            user, refs = render_user_message(meeting_items, mode)
            requests.append(ClassificationRequest(meeting_id, mode, system, user, refs))
    return requests


# --- Model client ----------------------------------------------------------

class AnthropicTopicModel:
    """Claude Messages API client with a cached rubric and structured JSON output."""
    provider = "anthropic"

    def __init__(self, model: str = MODEL_ID, effort: str = "low", client=None):
        self.model, self.effort, self._sdk = model, effort, None
        if client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise SystemExit("The live comparison needs the optional AI dependency: "
                                 "uv sync --project workers/ingestion --extra test --extra ai") from exc
            self._sdk = anthropic
            client = anthropic.Anthropic(credentials=gcp_federated_credentials(anthropic)) if uses_gcp_federation() \
                else anthropic.Anthropic()
        self.client = client

    def complete(self, *, system: str, user: str, schema: dict, max_tokens: int) -> ModelReply:
        def call():
            return self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                # Stable rubric first so every meeting request can reuse the cached prefix.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
            )
        sdk = self._sdk
        if sdk is None:
            response = call()
        else:
            try:
                response = call()
            except (sdk.AuthenticationError, sdk.PermissionDeniedError) as exc:
                raise ModelError(f"credentials rejected ({exc.status_code}); check ANTHROPIC_API_KEY or the federation rule",
                                 fatal=True) from exc
            except sdk.NotFoundError as exc:
                raise ModelError(f"model {self.model} not found", fatal=True) from exc
            except sdk.BadRequestError as exc:
                raise ModelError(f"request rejected: {exc.message}") from exc
            except sdk.RateLimitError as exc:
                raise ModelError("rate limited after SDK retries") from exc
            except sdk.APIStatusError as exc:
                raise ModelError(f"API error {exc.status_code}") from exc
            except sdk.APIConnectionError as exc:
                raise ModelError("network error contacting the API") from exc
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        usage = {name: int(getattr(response.usage, name, 0) or 0) for name in USAGE_FIELDS}
        return ModelReply(text=text, stop_reason=response.stop_reason, usage=usage,
                          model=getattr(response, "model", self.model), request_id=getattr(response, "_request_id", None))


def has_credentials() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or uses_federation())


# Workload identity federation: the Cloud Run job trades its Google service account identity for a
# short-lived Anthropic token, so no API key is stored anywhere.
GCP_IDENTITY_URL = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
DEFAULT_FEDERATION_AUDIENCE = "https://api.anthropic.com"


def uses_federation() -> bool:
    """The SDK's federation settings are present (token file or literal token supplied by the environment)."""
    return bool(os.getenv("ANTHROPIC_FEDERATION_RULE_ID") and os.getenv("ANTHROPIC_ORGANIZATION_ID")
                and (uses_gcp_federation() or os.getenv("ANTHROPIC_IDENTITY_TOKEN_FILE")
                     or "ANTHROPIC_IDENTITY_TOKEN" in os.environ))


def uses_gcp_federation() -> bool:
    """Federation configured with Google's metadata server as the identity token source."""
    return bool(os.getenv("ANTHROPIC_FEDERATION_RULE_ID") and os.getenv("ANTHROPIC_ORGANIZATION_ID")
                and os.getenv("TAHOE_IDENTITY_TOKEN_SOURCE") == "gcp-metadata")


def gcp_identity_token(opener=urllib.request.urlopen) -> str:
    """A fresh Google ID token for the job's service account, with its email claim for the federation rule."""
    query = urllib.parse.urlencode({"audience": os.getenv("ANTHROPIC_FEDERATION_AUDIENCE", DEFAULT_FEDERATION_AUDIENCE),
                                    "format": "full"})
    request = urllib.request.Request(f"{GCP_IDENTITY_URL}?{query}", headers={"Metadata-Flavor": "Google"})
    with opener(request, timeout=10) as response:
        return response.read().decode().strip()


def gcp_federated_credentials(sdk):
    """Anthropic credentials that exchange a new metadata-server token on every refresh."""
    return sdk.WorkloadIdentityCredentials(
        identity_token_provider=gcp_identity_token,
        federation_rule_id=os.environ["ANTHROPIC_FEDERATION_RULE_ID"],
        organization_id=os.environ["ANTHROPIC_ORGANIZATION_ID"],
        service_account_id=os.getenv("ANTHROPIC_SERVICE_ACCOUNT_ID") or None,
        workspace_id=os.getenv("ANTHROPIC_WORKSPACE_ID") or None,
    )


def parse_reply(reply: ModelReply, request: ClassificationRequest, topic_ids) -> tuple[dict, list[str]]:
    """Map a structured reply back to item IDs; every unmatched item records an error."""
    problems = []
    entries = []
    if reply.stop_reason == "refusal":
        problems.append("model declined the request (refusal)")
    elif reply.stop_reason == "max_tokens":
        problems.append("response truncated at max_tokens")
    else:
        try:
            entries = json.loads(reply.text).get("items", [])
        except (json.JSONDecodeError, AttributeError):
            problems.append("response was not the expected JSON object")
    # Any unknown, missing, or repeated ref means entries may be shifted onto the wrong items,
    # so the whole reply is rejected rather than trusted item by item.
    refs = [str(entry.get("ref")) for entry in entries]
    if entries and (sorted(refs) != sorted(request.refs) or len(set(refs)) != len(refs)):
        unknown = sorted(set(refs) - set(request.refs))
        missing = sorted(set(request.refs) - set(refs))
        problems.append("misaligned refs" + (f"; unknown {unknown}" if unknown else "")
                        + (f"; missing {missing}" if missing else ""))
        entries = []
    results = {}
    for entry in entries:
        item_id = request.refs[str(entry.get("ref"))]
        # Items about places outside the Tahoe area get no topics, whatever the model listed.
        tahoe = entry.get("tahoeRelevant", True) is not False
        topics = set(entry.get("topics") or []) if tahoe else set()
        if topics - set(topic_ids):
            problems.append(f"ref {entry['ref']}: ignored unknown topics {sorted(topics - set(topic_ids))}")
        results[item_id] = {"topics": ordered(expand_topics(topics & set(topic_ids))),
                            "reason": " ".join(str(entry.get("reason", "")).split()), "tahoeRelevant": tahoe, "error": None}
    error = "; ".join(p for p in problems if "ignored unknown topics" not in p) or "missing from model response"
    for item_id in request.refs.values():
        results.setdefault(item_id, {"topics": [], "reason": "", "error": error})
    return results, problems


# --- Comparison and scoring -------------------------------------------------

def _valid(results: dict) -> dict:
    return {item_id: set(r["topics"]) for item_id, r in results.items() if r.get("error") is None}


def _kappa(both: int, only_a: int, only_b: int, neither: int):
    n = both + only_a + only_b + neither
    if not n:
        return None
    observed = (both + neither) / n
    expected = ((both + only_a) / n) * ((both + only_b) / n) + ((only_b + neither) / n) * ((only_a + neither) / n)
    return None if expected >= 1 else round((observed - expected) / (1 - expected), 3)


def compare(name_a: str, a: dict, name_b: str, b: dict, topic_ids) -> dict:
    """Agreement matrix for two methods over items both classified without error."""
    a, b = _valid(a), _valid(b)
    common = [item_id for item_id in a if item_id in b]
    topics = {}
    for topic in topic_ids:
        cells = Counter((topic in a[i], topic in b[i]) for i in common)
        both, only_a, only_b, neither = cells[(True, True)], cells[(True, False)], cells[(False, True)], cells[(False, False)]
        topics[topic] = {"both": both, "onlyA": only_a, "onlyB": only_b, "neither": neither,
                         "agreement": round((both + neither) / len(common), 3) if common else None,
                         "kappa": _kappa(both, only_a, only_b, neither)}
    disagreements = [i for i in common if a[i] != b[i]]
    return {"a": name_a, "b": name_b, "items": len(common), "exactMatch": len(common) - len(disagreements),
            "exactMatchRate": round((len(common) - len(disagreements)) / len(common), 3) if common else None,
            "topics": topics, "disagreements": disagreements}


def topic_counts(results: dict, topic_ids) -> dict:
    counts = Counter(topic for topics in _valid(results).values() for topic in topics)
    return {topic: counts[topic] for topic in topic_ids}


def load_gold(path) -> dict:
    """Accept {"items": [{"id", "gold"}]}, a list of those, or {item_id: [topics]}; null gold is unlabeled."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    pairs = ((row["id"], row.get("gold")) for row in data) if isinstance(data, list) else data.items()
    gold = {}
    for item_id, topics in pairs:
        if topics is None:
            continue
        unknown = set(topics) - {t["id"] for t in topic_catalog()}
        if unknown:
            raise ValueError(f"gold label for {item_id} uses unknown topics {sorted(unknown)}")
        gold[item_id] = expand_topics(set(topics))
    return gold


def score_against_gold(results: dict, gold: dict, topic_ids) -> dict:
    predicted = _valid(results)
    items = [item_id for item_id in gold if item_id in predicted]
    per_topic, totals = {}, Counter()
    for topic in topic_ids:
        tp = sum(topic in predicted[i] and topic in gold[i] for i in items)
        fp = sum(topic in predicted[i] and topic not in gold[i] for i in items)
        fn = sum(topic not in predicted[i] and topic in gold[i] for i in items)
        totals.update(tp=tp, fp=fp, fn=fn)
        per_topic[topic] = _prf(tp, fp, fn)
    exact = sum(predicted[i] == gold[i] for i in items)
    return {"items": len(items), "exactMatch": exact, "exactMatchRate": round(exact / len(items), 3) if items else None,
            "micro": _prf(totals["tp"], totals["fp"], totals["fn"]), "topics": per_topic}


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else (0.0 if tp + fp and tp + fn else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _round(precision), "recall": _round(recall), "f1": _round(f1)}


def _round(value):
    return None if value is None else round(value, 3)


# --- Tokens and cost -------------------------------------------------------

def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def cost_usd(model: str, *, uncached_input: float, output: float, cache_write: float = 0,
             cache_read: float = 0, batch: bool = False, requests: int = 1) -> float:
    """Token counts may total several requests; long-context rates apply per request, by its average prompt."""
    prices = PRICING[model]
    long = (uncached_input + cache_write + cache_read) / max(requests, 1) > prices.get("long_context_over", math.inf)
    p_in, p_out = (prices["long_input"], prices["long_output"]) if long else (prices["input"], prices["output"])
    total = (uncached_input * p_in + cache_write * p_in * CACHE_WRITE_MULTIPLIER
             + cache_read * p_in * CACHE_READ_MULTIPLIER + output * p_out) / 1_000_000
    return total * (BATCH_MULTIPLIER if batch else 1)


def estimated_usage(requests: list[ClassificationRequest], schema: dict) -> dict:
    """chars/4 estimate per mode; the rubric and schema are the shared (cacheable) prefix."""
    shared = estimate_tokens(requests[0].system) + estimate_tokens(json.dumps(schema)) if requests else 0
    by_mode = {}
    for request in requests:
        row = by_mode.setdefault(request.mode, {"requests": 0, "items": 0, "sharedPrefixTokens": shared,
                                               "itemInputTokens": 0, "outputTokens": 0, "source": "estimate (chars/4)"})
        row["requests"] += 1
        row["items"] += len(request.refs)
        row["itemInputTokens"] += estimate_tokens(request.user)
        row["outputTokens"] += len(request.refs) * EST_OUTPUT_TOKENS_PER_ITEM + EST_THINKING_TOKENS_PER_REQUEST
    return by_mode


def actual_cost(model: str, usage: dict) -> float:
    return cost_usd(model, uncached_input=usage["input_tokens"], output=usage["output_tokens"], requests=usage["requests"],
                    cache_write=usage["cache_creation_input_tokens"], cache_read=usage["cache_read_input_tokens"])


def project_costs(per_mode: dict, items_per_meeting: float, model: str = MODEL_ID) -> dict:
    """Monthly cost for the expected workloads, with Batch API and rubric-caching variants."""
    projection = {}
    for mode, usage in per_mode.items():
        if not usage["items"]:
            continue
        shared = usage["sharedPrefixTokens"]
        item_input = usage["itemInputTokens"] / usage["items"]
        output_per_item = usage["outputTokens"] / usage["items"]
        meeting_input = item_input * items_per_meeting
        meeting_output = output_per_item * items_per_meeting
        cacheable = shared >= MIN_CACHEABLE_TOKENS

        def per_meeting(batch=False, cached=False, chosen=model):
            if cached and cacheable:
                return cost_usd(chosen, uncached_input=meeting_input, cache_read=shared, output=meeting_output, batch=batch)
            return cost_usd(chosen, uncached_input=shared + meeting_input, output=meeting_output, batch=batch)

        rows = []
        for label, meetings in WORKLOADS:
            rows.append({"workload": label, "meetingsPerMonth": meetings,
                         "standard": round(meetings * per_meeting(), 4),
                         "batch": round(meetings * per_meeting(batch=True), 4),
                         "cachedRubric": round(meetings * per_meeting(cached=True), 4),
                         "batchCachedRubric": round(meetings * per_meeting(batch=True, cached=True), 4),
                         "coldCacheWrite": round(meetings * cost_usd(model, uncached_input=meeting_input, cache_write=shared,
                                                                      output=meeting_output), 4),
                         COMPARISON_MODEL_ID: round(meetings * per_meeting(chosen=COMPARISON_MODEL_ID), 4)})
        projection[mode] = {"source": usage["source"], "itemsPerMeeting": round(items_per_meeting, 1),
                            "inputTokensPerMeeting": round(shared + meeting_input),
                            "sharedPrefixTokens": shared, "rubricCacheable": cacheable,
                            "outputTokensPerMeeting": round(meeting_output), "rows": rows}
    return projection


# --- Review file -----------------------------------------------------------

REVIEW_INSTRUCTIONS = ("Fill each item's \"gold\" with the topic IDs it should have ([] for none) and keep null for "
                       "items you have not judged. Rerun with --gold PATH to score every method against your labels. "
                       "Disagreement-only labels over-represent hard cases; add agreeing items before treating "
                       "scores as overall accuracy. Rewriting this file keeps existing gold and notes by item ID.")


def build_review(items: list[AgendaItem], methods: dict, comparisons: list[dict], *, include_all: bool = False) -> dict:
    flagged = defaultdict(list)
    for comparison in comparisons:
        for item_id in comparison["disagreements"]:
            flagged[item_id].append(f'{comparison["a"]} vs {comparison["b"]}')
    rows = []
    for item in items:
        if item.id not in flagged and not include_all:
            continue
        rows.append({"id": item.id, "agency": item.agency_id, "meeting": item.meeting_title, "date": item.meeting_date,
                     "number": item.number, "section": item.section, "title": item.title,
                     "agendaExcerpt": item.excerpt, "excerptPages": item.excerpt_pages,
                     "methods": {name: results[item.id] for name, results in methods.items() if item.id in results},
                     "disagreements": flagged.get(item.id, []), "gold": None, "notes": ""})
    return {"instructions": REVIEW_INSTRUCTIONS, "topicIds": [t["id"] for t in topic_catalog()],
            "generatedAt": datetime.now(timezone.utc).isoformat(), "items": rows}


def write_review(review: dict, directory) -> tuple[Path, Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / "disagreements.json", directory / "disagreements.md"
    if json_path.exists():
        try:
            previous = {row["id"]: row for row in json.loads(json_path.read_text()).get("items", [])}
        except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
            previous = {}
        current = {row["id"] for row in review["items"]}
        for row in review["items"]:
            old = previous.get(row["id"], {})
            row["gold"], row["notes"] = old.get("gold"), old.get("notes", "")
        # Keep labels for items that no longer disagree; they are still valid gold.
        review["items"] += [row for item_id, row in previous.items() if item_id not in current and row.get("gold") is not None]
    json_path.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n")
    lines = ["# Topic classification disagreements", "", review["instructions"], "",
             f"Topic IDs: {', '.join(review['topicIds'])}", ""]
    for row in review["items"]:
        lines += [f"## {row['title']}", "", f"`{row['id']}` · {row['agency']} · {row['date'] or 'date unknown'}"
                  + (f" · #{row['number']}" if row["number"] else "") + (f" · {row['section']}" if row["section"] else ""), ""]
        if row["agendaExcerpt"]:
            lines += [f"> {row['agendaExcerpt']}", ""]
        lines += ["| Method | Topics | Reason |", "| --- | --- | --- |"]
        for name, result in row["methods"].items():
            topics = f"error: {result['error']}" if result["error"] else ", ".join(result["topics"]) or "none"
            lines.append(f"| {name} | {topics} | {result['reason'].replace('|', '/')} |")
        lines += ["", f"Gold: {json.dumps(row['gold'])}" + (f" · Notes: {row['notes']}" if row["notes"] else ""), ""]
    md_path.write_text("\n".join(lines))
    return json_path, md_path


# --- CLI -------------------------------------------------------------------

def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--limit", type=int, help="Agenda items to sample, stratified by agency (default: all)")
    parser.add_argument("--agency", help="Only this agency ID")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Stable sampling seed")
    parser.add_argument("--modes", default=",".join(MODES), help="Comma-separated: title-only,agenda-context")
    parser.add_argument("--model", default=MODEL_ID, choices=sorted(PRICING))
    parser.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    parser.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS,
                        help="Bound for each item's agenda excerpt (about chars/4 tokens)")
    parser.add_argument("--out", help="Write full JSON results here")
    parser.add_argument("--review-dir", default="data/topic-eval", help="Where disagreements.json/.md are written")
    parser.add_argument("--review-all", action="store_true", help="Include agreeing items in the review file")
    parser.add_argument("--gold", help="Annotated review JSON to score each method against")
    parser.add_argument("--dry-run", action="store_true", help="Never call the API, even with a key")
    parser.add_argument("--max-cost", type=float, default=1.0, help="Refuse a live run estimated above this many USD")
    parser.add_argument("--show", type=int, default=40, help="Sample rows to print")
    parser.add_argument("--preview-meeting", help="Meeting ID whose prompt is printed")


def run(conn, args, *, model_client=None) -> dict:
    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    if not modes or set(modes) - set(MODES):
        raise SystemExit(f"--modes must be drawn from {', '.join(MODES)}")
    catalog = list(topic_catalog())
    topic_ids = [topic["id"] for topic in catalog]
    corpus = load_items(conn, agency=args.agency, context_chars=args.context_chars)
    if not corpus:
        raise SystemExit("No active agenda items found" + (f" for {args.agency}" if args.agency else ""))
    sample = sample_items(corpus, limit=args.limit, seed=args.seed)
    system = render_system_prompt(catalog=catalog)
    schema = response_schema(catalog)
    requests = build_requests(sample, modes, system)
    live = not args.dry_run and (model_client is not None or has_credentials())
    items_per_meeting = len(corpus) / len(_by_meeting(corpus))

    methods = {RULES: rule_results(sample), RULES_CONTEXT: rule_results(sample, use_context=True)}
    _print_sample(corpus, sample, methods[RULES], args.show)
    preview = _preview(sample, requests, args.preview_meeting)
    _print_prompt(preview)
    estimates = estimated_usage(requests, schema)
    actual = {}
    if live:
        estimate = sum(cost_usd(args.model, uncached_input=u["sharedPrefixTokens"] * u["requests"] + u["itemInputTokens"], requests=u["requests"],
                                output=u["outputTokens"]) for u in estimates.values())
        if estimate > args.max_cost:
            raise SystemExit(f"Estimated ${estimate:.4f} exceeds --max-cost ${args.max_cost:.2f}; narrow --limit or raise it")
        client = model_client or AnthropicTopicModel(args.model, args.effort)
        print(f"\nLive comparison: {len(requests)} requests to {args.model} (effort {args.effort}), estimated ${estimate:.4f}")
        for mode in modes:
            methods[f"claude:{mode}"] = {}
        for request in requests:
            usage = actual.setdefault(request.mode, {name: 0 for name in USAGE_FIELDS} | {"requests": 0, "items": 0, "retries": 0})
            for attempt in range(1 + RETRIES):
                if attempt:
                    usage["retries"] += 1
                try:
                    reply = client.complete(system=request.system, user=request.user, schema=schema, max_tokens=request.max_tokens)
                except ModelError as exc:
                    if exc.fatal:
                        raise SystemExit(f"Live comparison stopped: {exc}") from exc
                    reply, results = None, {i: {"topics": [], "reason": "", "error": str(exc)} for i in request.refs.values()}
                    continue
                results, problems = parse_reply(reply, request, topic_ids)
                for name in USAGE_FIELDS:
                    usage[name] += reply.usage.get(name, 0)
                if problems:
                    print(f"  {request.meeting_id} [{request.mode}] attempt {attempt + 1}: {'; '.join(problems)}")
                if not any(r["error"] for r in results.values()):
                    break
            usage["requests"] += 1
            usage["items"] += len(request.refs)
            methods[f"claude:{request.mode}"].update(results)
    else:
        print("\nLive comparison skipped: " + ("--dry-run was given." if args.dry_run else "ANTHROPIC_API_KEY is not set.")
              + " Run with a key to classify the sample with Claude:\n"
              "  uv sync --project workers/ingestion --extra test --extra ai\n"
              "  export ANTHROPIC_API_KEY=...\n"
              "  python3 scripts/project.py worker evaluate-topics --out data/topic-eval/results.json")

    pairs = [(RULES, RULES_CONTEXT)] + [(RULES, f"claude:{m}") for m in modes if f"claude:{m}" in methods]
    if all(f"claude:{m}" in methods for m in MODES):
        pairs.append(("claude:title-only", "claude:agenda-context"))
    comparisons = [compare(a, methods[a], b, methods[b], topic_ids) for a, b in pairs]
    counts = {name: topic_counts(results, topic_ids) for name, results in methods.items()}
    per_mode = {}
    for mode, estimate in estimates.items():
        if mode in actual and actual[mode]["items"]:
            used = actual[mode]
            cached = used["cache_creation_input_tokens"] + used["cache_read_input_tokens"]
            total_input = used["input_tokens"] + cached
            # Every request writes or reads the same cached rubric, so its actual size is cached/requests.
            shared = cached // used["requests"] if cached else estimate["sharedPrefixTokens"]
            per_mode[mode] = {"requests": used["requests"], "items": used["items"], "sharedPrefixTokens": shared,
                              "itemInputTokens": max(0, total_input - shared * used["requests"]),
                              "outputTokens": used["output_tokens"], "source": "actual API usage"}
        else:
            per_mode[mode] = estimate
    projection = project_costs(per_mode, items_per_meeting, args.model)
    gold_scores = None
    if args.gold:
        gold = load_gold(args.gold)
        gold_scores = {name: score_against_gold(results, gold, topic_ids) for name, results in methods.items()}

    _print_counts(counts, topic_ids)
    _print_comparisons(comparisons, sample)
    _print_costs(args.model, estimates, actual, projection)
    if gold_scores:
        _print_gold(gold_scores)
    review = build_review(sample, methods, comparisons, include_all=args.review_all)
    json_path, md_path = write_review(review, args.review_dir)
    print(f"\nReview file: {json_path} ({len(review['items'])} items) and {md_path}")

    by_agency = Counter(item.agency_id for item in sample)
    report = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "config": {"model": args.model, "effort": args.effort, "modes": modes, "seed": args.seed, "limit": args.limit,
                   "agency": args.agency, "contextChars": args.context_chars, "live": live,
                   "promptTemplate": str(PROMPT_TEMPLATE.name), "promptSha256": hashlib.sha256(system.encode()).hexdigest(),
                   "ruleClassifierVersion": CLASSIFIER_VERSION},
        "corpus": {"items": len(corpus), "meetings": len(_by_meeting(corpus)),
                   "byAgency": dict(Counter(item.agency_id for item in corpus))},
        "sample": {"items": len(sample), "meetings": len(_by_meeting(sample)), "byAgency": dict(by_agency),
                   "withAgendaContext": sum(bool(item.excerpt) for item in sample)},
        "promptPreview": preview,
        "items": [{**asdict(item), "methods": {name: results.get(item.id) for name, results in methods.items()}} for item in sample],
        "topicCounts": counts, "comparisons": comparisons,
        "usage": {"estimated": estimates, "actual": actual,
                  "actualCostUsd": {mode: round(actual_cost(args.model, used), 6) for mode, used in actual.items()}},
        "costProjection": projection, "gold": gold_scores,
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        print(f"Results: {out}")
    return report


def _preview(sample, requests, meeting_id=None) -> dict:
    meetings = _by_meeting(sample)
    if meeting_id is None:
        sized = sorted((len(items), index, mid) for index, (mid, items) in enumerate(meetings.items()) if len(items) >= 5)
        meeting_id = sized[0][2] if sized else next(iter(meetings))
    chosen = [r for r in requests if r.meeting_id == meeting_id]
    if not chosen:
        raise SystemExit(f"--preview-meeting {meeting_id} is not in the sample")
    return {"meetingId": meeting_id, "system": chosen[0].system,
            "user": {r.mode: r.user for r in chosen}, "maxTokens": chosen[0].max_tokens}


def _print_sample(corpus, sample, rules, show):
    by_agency = Counter(item.agency_id for item in sample)
    print(f"Sample: {len(sample)} of {len(corpus)} active agenda items, {len(_by_meeting(sample))} meetings; "
          + ", ".join(f"{agency} {count}" for agency, count in sorted(by_agency.items())))
    print(f"Items with an agenda-page excerpt: {sum(bool(item.excerpt) for item in sample)}")
    for item in sample[:show]:
        topics = ", ".join(rules[item.id]["topics"]) or "-"
        print(f"  {item.id:<34} {item.title[:70]:<70} rules: {topics}")
    if len(sample) > show:
        print(f"  ... {len(sample) - show} more (use --show or --out for all)")


def _print_prompt(preview):
    print(f"\nPrompt for {preview['meetingId']} (system prompt is shared and cached across meetings):")
    print("--- system ---\n" + preview["system"])
    for mode, text in preview["user"].items():
        print(f"--- user [{mode}] ---\n{text}")
    print(f"--- max_tokens {preview['maxTokens']}; structured JSON output schema enforces topic IDs ---")


def _print_counts(counts, topic_ids):
    names = list(counts)
    print("\nAutomatic topic counts (multi-topic items count more than once):")
    print("  " + f"{'topic':<20}" + "".join(f"{name:>26}" for name in names))
    for topic in topic_ids:
        print("  " + f"{topic:<20}" + "".join(f"{counts[name][topic]:>26}" for name in names))


def _print_comparisons(comparisons, sample):
    titles = {item.id: item.title for item in sample}
    for comparison in comparisons:
        print(f"\n{comparison['a']} vs {comparison['b']}: {comparison['exactMatch']}/{comparison['items']} items "
              f"identical ({_pct(comparison['exactMatchRate'])})")
        print(f"  {'topic':<20}{'both':>6}{'A only':>8}{'B only':>8}{'neither':>9}{'agree':>8}{'kappa':>8}")
        for topic, row in comparison["topics"].items():
            kappa = "-" if row["kappa"] is None else f"{row['kappa']:.2f}"
            print(f"  {topic:<20}{row['both']:>6}{row['onlyA']:>8}{row['onlyB']:>8}{row['neither']:>9}"
                  f"{_pct(row['agreement']):>8}{kappa:>8}")
        for item_id in comparison["disagreements"][:12]:
            print(f"    differs: {item_id} {titles.get(item_id, '')[:80]}")
        if len(comparison["disagreements"]) > 12:
            print(f"    ... {len(comparison['disagreements']) - 12} more in the review file")


def _print_costs(model, estimates, actual, projection):
    print("\nToken usage by mode (input includes the shared rubric/schema prefix once per request):")
    for mode, row in estimates.items():
        line = (f"  {mode:<15} estimate: {row['requests']} requests, {row['items']} items, "
                f"~{row['sharedPrefixTokens'] * row['requests'] + row['itemInputTokens']:,} input, ~{row['outputTokens']:,} output")
        if mode in actual:
            used = actual[mode]
            line += (f" | actual: {used['input_tokens']:,} input + {used['cache_creation_input_tokens']:,} cache write + "
                     f"{used['cache_read_input_tokens']:,} cache read, {used['output_tokens']:,} output, "
                     f"${actual_cost(model, used):.4f}")
        print(line)
    print(f"\nMonthly cost projection for {model} (USD; list prices as of 2026-10-06, verify before budgeting):")
    print(f"  {'mode':<15}{'meetings':>9}{'standard':>10}{'batch':>9}{'cached':>9}{'batch+c':>9}{'cold':>9}"
          f"{COMPARISON_MODEL_ID:>19}")
    for mode, block in projection.items():
        for row in block["rows"]:
            print(f"  {mode:<15}{row['meetingsPerMonth']:>9}{_usd(row['standard']):>10}{_usd(row['batch']):>9}"
                  f"{_usd(row['cachedRubric']):>9}{_usd(row['batchCachedRubric']):>9}{_usd(row['coldCacheWrite']):>9}"
                  f"{_usd(row[COMPARISON_MODEL_ID]):>19}")
        print(f"  {'':<15}~{block['inputTokensPerMeeting']:,} input / ~{block['outputTokensPerMeeting']:,} output tokens per "
              f"meeting ({block['itemsPerMeeting']} items); shared prefix {block['sharedPrefixTokens']} tokens"
              + ("" if block["rubricCacheable"] else f" is below the {MIN_CACHEABLE_TOKENS}-token cache minimum")
              + f"; {block['source']}")
    print("  cached = rubric read from cache (requests within 5 minutes, e.g. a batch or backfill); "
          "cold = every request writes the cache (isolated daily runs)")


def _print_gold(scores):
    print("\nScores against gold labels:")
    for name, score in scores.items():
        micro = score["micro"]
        print(f"  {name:<26} {score['items']} items, exact {_pct(score['exactMatchRate'])}, micro precision "
              f"{_pct(micro['precision'])}, recall {_pct(micro['recall'])}, F1 {_pct(micro['f1'])}")


def _pct(value):
    return "-" if value is None else f"{value * 100:.0f}%"


def _usd(value):
    return f"${value:.4f}" if value < 1 else f"${value:.2f}"


def _json(value):
    if value is None:
        return None
    return value if isinstance(value, (dict, list)) else json.loads(value)
