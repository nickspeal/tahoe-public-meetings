# 0011: Claude for automated analysis
Status: accepted
Date: 2026-10-20
Related ideas: IDEA-001, IDEA-002, IDEA-013
Supersedes: the "Gemini is a candidate" provider language in decision 0005 and the implementation handoff

## Context

Automated topic classification and, later, item summaries need an external model API. Earlier planning named Gemini Flash-Lite as a low-cost candidate to benchmark, and left the provider open. In Round 2 feedback, the maintainer said he prefers Claude for automatic analysis "unless there is a strong cost or convenience reason", because he believes it generally performs better. The [topic classification evaluation](../research/topic-classification-evaluation-2026-10-20.md) estimates Claude Haiku 5.5 agenda-level classification at about $0.02/month for 22 meetings and under $0.10/month for 100 meetings, inside the $2–5 AI reservation and the total budget of under $20/month.

## Decision

The maintainer confirmed this direction in Round 2 feedback.

- **Claude, through the Anthropic API, is the default provider** for automated analysis: topic classification and generated summaries.
- **Claude Haiku 5.5 (`claude-haiku-5-5`) is the first candidate for classification.** It goes into production only after the maintainer reviews the live comparison with the rule-based classifier (`tahoe-ingest evaluate-topics`). If Haiku's quality falls short, try a larger Claude model, such as Sonnet 5.5, before another vendor. Summaries get their own evaluation.
- **A cheaper or non-Claude option is adopted only for a strong cost or convenience reason.** Examples: projected spend that would exceed the AI envelope, or a capability Claude lacks. A few cents per month of difference is not strong.
- **Keep provider abstraction.** Provider calls live in one adapter (currently `AnthropicTopicModel.complete`). Stored results stay provider-neutral: topic IDs, a reason, model ID, prompt hash, and usage. Application schemas, exports, and operator overrides do not depend on a vendor. The rule classifier remains available as a deterministic, no-cost fallback and as explanation evidence.

No API key, Anthropic account, or paid usage was used or provisioned in this work. The evaluation ran as a dry run.

## Classification rules (the maintainer, October 20, 2026)

Accepted after the first live comparison:

- **Tahoe relevance gates every topic.** An item about a specific place outside the Lake Tahoe Basin and the Truckee and North Lake Tahoe communities gets no topics, even when its subject matches one (for example, Lahontan Water Board permits in Death Valley or Hinkley). The classifier returns `tahoeRelevant` and the parser drops topics when it is false. The rule classifier cannot make this judgment. Amended October 28, 2026 at the maintainer's request: an item that applies across an agency's whole jurisdiction (a countywide ordinance, plan, program, grant, or public process) is relevant when that jurisdiction includes part of the Tahoe area, even when the item names no place. The earlier wording required such items to "plainly apply" to Tahoe, which excluded countywide items from Placer, Douglas, and Washoe counties whose Tahoe residents are affected. Statewide items from state agencies still need a plain Tahoe application.
- **Hot Topics has two parts:** the named watchlist, plus items Claude predicts will draw substantial public interest or controversy among Tahoe residents. Reasons start with `watchlist:` or `predicted:`. The watchlist still changes over time.
- **The reason comes before the topics** in the structured output, so the tags follow from the explanation.
- **Elections is a new topic** for elections, ballot measures, and agency positions on them. A measure's subject is tagged too (Measure S is also Environment). Ballot measures are not Hot Topics by default.
- **Guardrails for a cheaper model:** item refs (`i1`, `i2`, …) cannot be confused with agenda numbers; a reply with any unknown, missing, or repeated ref is rejected as a whole, and a rejected, refused, truncated, or malformed reply is retried once. The maintainer's labels live in `tests/fixtures/topic-gold.json`.

- **Haiku 5.5 at low effort is the production default, with Sonnet 5.5 at medium effort as the fallback** (the maintainer, October 20, 2026). `worker classify-topics` sends each new or changed meeting to Haiku; a failed reply is retried once, then the meeting goes to Sonnet; if both fail, the items keep their rule tags. Results are stored in `ai_topic_classifications`, and the export prefers them while they match the item's current source fingerprint. Every call is recorded in `ai_usage`; a run stops at `--max-cost` (default $1) and refuses to start once month-to-date spend reaches `--monthly-budget` (default $5). Escalating disputed items to Sonnet is parked as IDEA-015.
- **Hot Topics predictions are calibrated by the maintainer's judgment examples** (`judgmentExamples` on the topic in `packages/domain/topics.json`), which grow over time.

On 33 gold-labeled items (hard cases the prompt was tuned on, so optimistic), Haiku reached 85% exact match and Sonnet 91%, against 55% for the rules. Haiku costs about $0.02 to classify all 325 current items.

## Alternatives and consequences

Gemini Flash-Lite remains a fallback, not a default. The price recorded in earlier research ($0.25/$1.50 per million input/output tokens) is above Haiku 5.5's list price ($0.10/$0.50), so cost does not favor it at this volume. Both figures are dated; recheck them before budgeting.

Consequences: the maintainer needs an Anthropic API account with billing and spend limits before any live or scheduled use. The key must be stored as a secret, never committed. Locally, use the environment or the gitignored `.env`; Cloud Run uses a secret. The `anthropic` SDK stays an optional `ai` extra, and the core worker runs without it. Haiku 5.5 has no server-side refusal fallback. In production, items in a refused or malformed reply must keep their rule tags, with the failure recorded. The evaluation harness already records such items as errors. Scheduled runs should use the Batch API (50% discount) and record actual usage against the budget. Prompt caching helps only for multi-meeting runs.

## Revisit when

- The live comparison or the maintainer's gold labels show Claude classification is not better than the rules, or not good enough.
- Measured AI spend approaches the AI envelope.
- Provider pricing, terms, or data-retention requirements change materially.
- A non-Claude option offers a capability or operational simplification the project needs.
