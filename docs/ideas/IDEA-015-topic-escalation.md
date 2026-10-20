# IDEA-015: Escalate disputed topic tags to a larger model

Status: parked
Stage: Operations

## Problem and audience
Haiku 5.5 tags most agenda items well, but it sometimes drops a second topic or over-predicts Hot Topics. Residents who filter by topic miss items or see noise.

## Smallest useful version
After Haiku classifies a meeting, send only the items where Haiku and the rule classifier disagree (about 5% of items in the October 2026 evaluation) to Sonnet 5.5, with the meeting header for context, and keep Sonnet's answer for those items.

## Evidence and dependencies
In the October 20, 2026 evaluation (28 hard-case labels), this escalation gained one exact match over Haiku alone and lost some recall, so it was not adopted. The production chain already falls back to Sonnet when Haiku's reply fails validation (decision 0011). Needs a larger gold set, labeled on items the prompt was not tuned on, to measure the benefit.

## Acceptance criteria
On held-out gold labels, escalation improves exact-match accuracy by a clear margin over Haiku alone, at a projected cost that stays inside the AI budget.

## Open questions
Is rule disagreement the right trigger, or should Haiku flag its own low-confidence items?

## Links and updates
- 2026-10-20: the maintainer chose Haiku as the default with a Sonnet fallback, and asked to keep escalation as an idea. See [decision 0011](../decisions/0011-claude-for-automated-analysis.md).
