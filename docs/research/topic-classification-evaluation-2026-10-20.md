# Topic classification evaluation (October 20, 2026)

Status: the harness is built and tested, and its dry run has been run on the local data. The live Claude comparison has **not** been run: this environment has no Anthropic API key, so no API calls or paid usage happened. [Decision 0011](../decisions/0011-claude-for-automated-analysis.md) records the provider choice.

This answers two items in the maintainer's Round 2 feedback:

- **A.** How far can rule-based topic tags go, compared with Claude Haiku tagging the same agenda items? What would Haiku cost?
- **B.** Is the high-level agenda enough to classify each item? If it is, classify from it, and link readers straight to the fuller material.

## Method

`tahoe-ingest evaluate-topics` ([source](../../workers/ingestion/tahoe_ingest/evaluate_topics.py)) only reads the database. It loads every active agenda item along with its agency, title, section, and **agenda excerpt**. The excerpt is the text on the cited agenda page that starts at the item's title and stops at the next item's title. It can run onto the following page, and it is capped at `--context-chars` (default 1,200 characters, about 300 tokens). Neighboring items and packet attachments stay out. The last item in a meeting gets only its own title, because what follows it is posting and accessibility boilerplate. `--limit` takes a stable sample stratified by agency. Each agency is shuffled with its own seed derived from `--seed`, and the sample comes back in agenda order.

Four methods label each item:

| Method | Input | Role |
| --- | --- | --- |
| `rules` | Title, plus TTD-specific context | The production classifier (`title-rules-v4.1`) |
| `rules-on-agenda-context` | Same patterns run over the agenda excerpt | Diagnostic only: shows what naive keyword matching on richer text does |
| `claude:title-only` | Number, section, title | Claude Haiku 5.5 |
| `claude:agenda-context` | Number, section, title, agenda excerpt | Claude Haiku 5.5 |

Claude gets **one request per meeting per mode**, so it sees the meeting's whole item list together. The system prompt is the rubric: [`prompts/topic_classification.md`](../../workers/ingestion/tahoe_ingest/prompts/topic_classification.md), filled in with each topic's ID, name, description, and examples from [`topics.json`](../../packages/domain/topics.json). The maintainer can edit that file directly. The rubric comes first in the request and carries a `cache_control` marker. Output is constrained by a JSON schema: each item gets a `ref`, a list of `topics` restricted to the catalog IDs, and a one-line `reason`. Requests use model `claude-haiku-5-5` at `effort: "low"`, with adaptive thinking left at its default. They send no `temperature`, because Haiku 5.5 rejects non-default sampling values. When a reply is missing an item, or the model refuses, that item is recorded as an error and the run continues. Before calling the API, a live run refuses to start if the estimated spend exceeds `--max-cost` (default $1). Short Term Rentals implies Housing for every method, as in production.

There are no gold labels yet, so the results are framed as **disagreement review**. For each pair of methods the harness reports an agreement matrix per topic (both, A only, B only, neither, percent agreement, Cohen's kappa), the exact-match rate per item, and the items that differ. Pairs are rules vs each Claude mode, the two Claude modes against each other, and rules vs the diagnostic. Disagreements go to `data/topic-eval/disagreements.json` and `.md` (gitignored), each with a `"gold": null` field. The maintainer can fill those in, and `--gold PATH` then scores every method on precision, recall, and F1. Rewriting the review file keeps any existing labels. Labels from disagreements alone over-represent hard cases, so `--review-all` adds agreeing items for an unbiased gold sample.

Without a key, token counts come from a chars/4 estimate. Output is assumed at 45 tokens per item plus 300 thinking tokens per request. A live run reports actual `usage` (input, cache write, cache read, output) and projects costs from those numbers instead. Prices are Claude API list prices from the claude-api skill cache dated 2026-10-06. For Haiku 5.5 that is $0.10/M input and $0.50/M output for prompts up to 100K tokens, and $0.50/$2.50 beyond that. Cache reads cost 0.1× input, 5-minute cache writes 1.25×, and the Batch API halves everything. [Check current pricing](https://platform.claude.com/docs/en/about-claude/pricing) before budgeting.

## Running it

Dry run (no key needed, no network):

```sh
python3 scripts/project.py worker evaluate-topics --out data/topic-eval/results.json
python3 scripts/project.py worker evaluate-topics --agency slt --limit 40 --seed 7   # a smaller stable sample
```

Live comparison (what the maintainer needs to do):

```sh
uv sync --project workers/ingestion --extra test --extra ai   # installs the optional anthropic SDK
export ANTHROPIC_API_KEY=sk-ant-...                           # or put it in the gitignored .env
python3 scripts/project.py worker evaluate-topics --out data/topic-eval/results.json
```

The full run is 30 requests: 15 meetings × 2 modes. The estimate is about $0.025. Re-running `project.py setup` reinstalls only the locked test extra, which removes the SDK, so repeat the `uv sync` line afterwards. Then read `data/topic-eval/disagreements.md`, fill in `gold` in `disagreements.json`, and score:

```sh
python3 scripts/project.py worker evaluate-topics --gold data/topic-eval/disagreements.json
python3 scripts/project.py worker evaluate-topics --review-all --limit 80   # optional unbiased gold sample
```

Try `--effort medium` if the reasons look shallow. Once the labels are settled, copy them to a tracked fixture so they can become a regression test.

## Dry-run results on the current data

Shared local database, 2026-10-20. All 278 active items in 15 meetings: Lahontan 35, SLT 149, Truckee 7, TTD 87. Every item had an agenda excerpt. Seed `20261020`, prompt SHA-256 `472429da3dc1…`, rules `title-rules-v4.1-1958de9e5b92`.

```text
Automatic topic counts (multi-topic items count more than once):
  topic                                    rules   rules-on-agenda-context
  biking                                       5                         7
  transit                                     16                        16
  housing                                      5                         6
  short-term-rentals                           1                         1
  environment                                 10                        23
  hot-topics                                   3                         3

rules vs rules-on-agenda-context: 263/278 items identical (95%)
  topic                 both  A only  B only  neither   agree   kappa
  biking                   5       0       2      271     99%    0.83
  transit                 16       0       0      262    100%    1.00
  housing                  5       0       1      272    100%    0.91
  short-term-rentals       1       0       0      277    100%    1.00
  environment             10       0      13      255     95%    0.58
  hot-topics               3       0       0      275    100%    1.00

Token usage by mode (input includes the shared rubric/schema prefix once per request):
  title-only      estimate: 15 requests, 278 items, ~23,521 input, ~17,010 output
  agenda-context  estimate: 15 requests, 278 items, ~47,737 input, ~17,010 output
```

What this shows about the limits of the rules:

- **Low coverage.** The rules tag 35 of 278 items (13%). They tag **0 of 35** Lahontan items, though most of that board's agenda is water-quality permitting, such as "Revised Waste Discharge Requirements for Tahoe-Truckee Sanitation Agency" and "PFAS Investigations Summary for Drinking Water". Titles name the instrument, not the topic keyword.
- **Misses that need judgment.** Items to check: SLT "Time-Share Amendments Discussion" (time-shares and condo-hotels are close to Short Term Rentals), drainage maintenance and bridge scour contracts, the Tahoe Keys Marina redevelopment appeal, the Mid-Town Specific Plan, and TTD's Federal Transportation Improvement Program items. None of these matches a keyword.
- **Context-blind false positives.** "Airport Runway 18/36 Pavement Maintenance", on two SLT agendas, is tagged Biking by the road-repair pattern.
- **Richer text doesn't help keyword rules.** Running the same patterns over the agenda excerpt changes 15 items, and most of the changes are wrong. Nine of the 13 new Environment tags come from environmental-review boilerplate ("exempt from the California Environmental Quality Act", an EIR reference) or from the name of a funding line. The new Biking and Housing tags come from a "Johnson Blvd Complete Streets Project account" used as a funding source, "sidewalks" in a snow-removal contract, and "housing area" in a Death Valley permit. About four of the new tags are plausible: stormwater practices in a sanitation permit, a hazardous-fuels agreement, an environmental restoration program, and an environmental justice plan. Using agenda context needs judgment, which is where an LLM should help. The live run will measure whether it does.

These are review candidates, not established errors. The review file lists them with titles, excerpts, and rule evidence.

## Cost projection

From the dry run's per-item estimates, scaled to 18.5 items per meeting (the corpus average). USD per month:

| Mode | Meetings/month | Standard | Batch API | Cached rubric | Batch + cache | Cold cache writes | Sonnet 5.5, standard |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| title-only | 22 | $0.016 | $0.008 | $0.014 | $0.007 | $0.016 | $0.32 |
| title-only | 100 | $0.072 | $0.036 | $0.065 | $0.032 | $0.075 | $1.45 |
| agenda-context | 22 | $0.020 | $0.010 | $0.018 | $0.009 | $0.020 | $0.39 |
| agenda-context | 100 | $0.089 | $0.044 | $0.081 | $0.040 | $0.091 | $1.77 |

Per meeting that is about 1,600 input and 1,100 output tokens (title-only), or about 3,200 input and 1,100 output (agenda-context). The rubric plus schema is about 870 tokens, above Haiku 5.5's 512-token cache minimum.

- **Output dominates the cost**: one reason per item plus thinking. Caching the rubric saves only about 10%, and only when requests arrive within the 5-minute cache window, as in a backfill or a Batch API run. A meeting classified alone pays the 1.25× write ("cold"), so caching should only be enabled for multi-meeting runs. The Batch API halves the cost and suits scheduled processing, where latency doesn't matter.
- **Cost is not the deciding factor.** Even at 100 meetings a month, Sonnet 5.5 would be under $2, against the $2–5 AI reservation in the [budget](../planning/budget.md). Quality should decide.
- **The estimate is approximate.** chars/4 can be off by tens of percent; Haiku 5.5's tokenizer produces about 30% more tokens than Haiku 4.5's for the same text. Thinking at low effort is an assumption. The live run replaces both with measured usage.

**The full-packet alternative, for hypothesis B.** Sending whole packets instead of agenda excerpts would cost far more. The five TTD packets average 291 pages, and the archive averages about 3,400 text characters per page, so one packet is about 246K tokens. The measured October 6 SLT packet is 505 pages and about 305K tokens. Over 100K tokens, Haiku 5.5's long-context rate applies, so each meeting costs about $0.13–0.16, or about $0.03 if the packet is split into chunks under 100K. Agenda context costs about $0.001 per meeting. At 22 meetings a month, full packets come to roughly $0.6–3.4; at 100 meetings, $2.5–16. That is still possible under the budget, but 30–170× the agenda-only cost. Most of those tokens are attachments (financial tables, minutes, maps) that rarely bear on a topic decision. Whether full packets would improve accuracy has not been measured.

## Hypothesis B: is the high-level agenda enough?

Inspected 2026-10-20. Stored agenda pages were read from the database. One official document per format was fetched into temporary scratch space and deleted afterwards. The June 3 TTD packet was read read-only from the earlier TTD project's local text archive (outside this repository), which was not modified.

| Agency | Where the agenda sits | Per-item signal on the agenda | "Executive summary" equivalent | Path to full content |
| --- | --- | --- | --- | --- |
| **SLT** City Council | A separate compact agenda PDF (3–9 pages), distinct from the packet (the October 6 packet is 505 pages) | Strong. Each numbered item has its title, **Requested Action / Suggested Motions**, often a CEQA Determination, Responsible Staff, and attachment file names. Excerpts average 429 characters, against 78 for titles. | A one-page **"Item N- Executive Summary.pdf"** per item, holding an *Executive Summary* paragraph, the requested action, staff, and attachments (checked for item 11) | The agenda PDF embeds per-attachment links on `slt.granicus.com/services/legistar/download/pdf/…`: 62 on October 6, including the 15 executive summaries and each staff report. `pdftotext` drops them; `pdftohtml -xml` keeps them. |
| **TTD** Board | The first 3 pages ("Agenda Page 1–3") of a single 166–540-page packet PDF | Moderate. Long "For Possible Action:" titles name the subject. Nothing is summarized beyond the title, so excerpts (133 characters on average) add little to titles (76). | The staff **MEMORANDUM** at the item's page: Date/To/From/**Subject**/**Action Requested**/**Fiscal Analysis**/Background/Discussion, with a footer like "AGENDA ITEM: IV.A." | The agenda's right-hand **Page** column refers to the packet's stamped "~ Page N ~" footer, not to the PDF page index. On June 3, agenda "Page 8" is PDF page 15. Mapping one to the other needs the full packet text at ingestion. |
| **Truckee** Town Council | Packet pages 1–3 ("Page 1 of 3"), then supporting material from page 4 (joint meeting, October 13) | Strong for the one stored meeting. Items carry a "Recommendation:" with lettered actions. Regular meetings link Municode HTML agendas, which are not yet captured. | The staff report at PDF page 4, headed "Item 5.1", with Title / **Recommendation** / **EXECUTIVE SUMMARY** / Background | Each staff-report page carries an "Item N.N" header, and here the packet page numbers match PDF pages |
| **Lahontan** Water Board (captured, not an original target) | The agenda PDF (6–7 pages) | Strong. Each item has a description sentence ("The Water Board will consider adopting revised Waste Discharge Requirements… best management practices for stormwater…") and a staff name. | Per-item PDFs (`…/agenda/2026/oct/item6.pdf`) beginning ITEM N, title, CHRONOLOGY, BACKGROUND | The agenda links `item1.pdf` through `item11.pdf` directly |

**Recommendation.** Classify from the agenda level by default: the title, the section, and the item's own agenda excerpt. On these four formats the agenda names the subject of nearly every substantive item. For SLT and Lahontan, the excerpt adds the requested action or a description. For TTD, the title alone carries most of the signal. Sending packets would multiply cost 30–170× and mostly add attachments. This remains a hypothesis until the live run and the maintainer's labels confirm it. Caveats:

1. **The agenda can't help with generic titles.** "Executive Director Report", "Agenda Planning Calendar", "Strategic Planning Workshop", closed sessions, and consent bundles can hide covered subjects. Those items, and any Claude flags as uncertain, are where an executive summary is worth fetching. That would be a targeted second pass, not a default.
2. **Some gaps are rubric gaps, not missing information.** Should a wastewater discharge permit count as Environment? The agenda states the facts; the maintainer's topic definitions decide. Gold labels will show whether the rubric or the input is the limit.
3. **The "executive summary" lives somewhere different in each format.** It is an SLT link, a TTD packet page through the stamped-page map, a Truckee page header, or a Lahontan item PDF. Each needs per-agency ingestion work: keeping link annotations or a stamped-page map while the temporary PDF is available. It would not need permanent PDFs.
4. **The evidence is thin for Truckee** (one special joint meeting, 7 items) and covers only one meeting format for SLT and TTD. Some stored TTD citation quotes are fuzzy matches that start on the previous item's last line. IV.E on October 7, for example, starts with "Program" from IV.D, which shifts excerpt boundaries by one line.
5. **Agenda revisions** (TTD marks modified agendas) mean the classification must follow the current agenda version. The existing source-hash invalidation already does this.

**Direct links to full content.** Each agenda item on a meeting page now opens the official document at its cited page, labeled for example "Read in agenda (page 2)" or "Read in agenda packet (page 1)". PDF links use the `#page=N` fragment; HTML documents get a plain link. The next step is linking straight to the item's executive summary or staff report, using the per-agency sources in the table, tracked as [IDEA-013](../ideas/IDEA-013-item-supporting-documents.md).

## Next steps

1. The maintainer runs the live comparison above and reviews `disagreements.md`.
2. If Haiku agenda-context agrees with the maintainer's labels better than the rules, wire it in as a classifier behind the same evidence model, with the Batch API and the rules kept as fallback. Store model ID, prompt hash, reason, and usage. Keep operator overrides unchanged.
3. Implement the per-agency supporting-document links, then evaluate an escalation pass for generic or uncertain items.
