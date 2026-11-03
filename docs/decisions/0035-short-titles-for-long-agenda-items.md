# 0035: Short titles for long agenda items

Status: accepted by the maintainer on 2026-11-03, with the 200-character threshold and a $0.05 per-run cost limit. The prompt, the validator's word lists, the model chain, and the operator override are proposed defaults.
Date: 2026-11-03
Related ideas: IDEA-020, IDEA-016
Related decisions: 0010, 0011, 0020, 0024

## Context

Some agencies print an item's whole recommended action as its title. With the whole text of cut-off titles now kept (`agenda_items.full_title`, 2026-11-03), the live export of 2026-11-03 (2,459 items in 224 meetings; each cut title's whole text recomputed locally from the export's stored pages) has these lengths of agenda text per item:

| | Items | Median | 90th percentile | Longest | Over 150 | Over 200 | Over 300 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| All agencies | 2,459 | 52 | 419 | 2,702 | 679 (28%) | 568 (23%) | 357 (15%) |
| El Dorado County Board of Supervisors | 187 | 410 | 1,078 | 2,702 | 166 | 155 | 115 |
| Washoe County Commission | 171 | 439 | 830 | 2,059 | 138 | 130 | 112 |
| Douglas County Commission | 164 | 245 | 464 | 843 | 108 | 93 | 65 |
| State Lands Commission | 74 | 274 | 278 | 315 | 65 | 62 | 1 |
| Douglas County RTC | 38 | 231 | 491 | 661 | 26 | 20 | 16 |
| South Lake Tahoe City Council | 202 | 36 | 186 | 717 | 31 | 19 | 10 |
| North Tahoe PUD | 103 | 46 | 231 | 495 | 17 | 15 | 3 |
| Washoe County Planning Commission | 106 | 36 | 515 | 2,013 | 14 | 13 | 12 |
| Incline Village GID | 55 | 76 | 304 | 528 | 16 | 10 | 6 |
| Every other agency | 1,359 | | | | 98 | 51 | 17 |

Lengths are characters with whitespace collapsed. Items over 200 characters, by meeting month: 206 in September and 252 in October (the first two months with every agency covered), 48 in the first days of November. Standing items repeat, so there were 553 distinct texts. 520 of them are in scope: the State Lands Commission's non-Tahoe items are left out, as they are for summaries.

A 400-character title means a paragraph naming the department, the statute, and numbered sub-actions before the reader learns what the item is. That makes a meeting page hard to scan. The cut-title work ends a cut title with an ellipsis and shows the whole text in the expanded item, which helps, but the first lines of a Legistar recommendation ("Health and Human Services Agency (HHSA) recommending the Board: 1) Make findings pursuant to Section 3.13.030 (B) of…") seldom say what the item is about.

## Decision

The maintainer accepted the prototype as evaluated below, with these settings:

- **What qualifies.** An item whose whole agenda text (`full_title` when the parser cut the title, else the title) is longer than **200 characters**. That is the 77th percentile and catches nearly every El Dorado, Washoe, and Douglas recommendation. Placeholders, quarantined meetings, and items outside an agency's `itemScope` are skipped.
- **Generation** (`tahoe-ingest shorten-titles`, `tahoe_ingest/short_titles.py`, prompt `prompts/short_title.md`). Haiku 5.5 at low effort writes a headline of 6 to 14 words (at most 16) naming the action and the subject a resident would recognize, using only the agenda's own words. A rejected headline goes back with the reason; Haiku gets two tries, then Sonnet 5.5 one. The model may skip an item it cannot shorten faithfully.
- **Validation before publication** (decision 0010; no human approval). Every word must be a word of the agenda text, a simple inflection of one ("approve" may become "approval" or "approving"), or one of 17 connecting words ("the", "of", "for", "to", "with"…). Every number, amount, and date must be printed in the text: "$2,200,007.50" may not become "$2.2 million". The headline must have 4 to 16 words, at most 120 characters, and be under 80% of the text's length. A polarity word ("deny", "not", "repeal", "rescind", "terminate", "prohibit", "without", and similar) must be kept when it governs something the headline names, meaning one of the next three content words after it is in the headline, and each such occurrence counts. So "recommending the Board deny the appeal of the Planning Commission's denial" needs both "deny" and "denial" in a headline about the appeal. Three kinds of polarity word are exempt: those inside parentheses (procedural notes such as "(No discussion will take place)"), caps ("not to exceed", "not more than", "at no cost", "with no change to"), and El Dorado's delegation clause ("amendments which do not increase the maximum dollar amount").
- **Caching.** Results are stored in `ai_short_titles`, keyed by a hash of the agenda text the headline was written from, not by item. An agenda re-posted unchanged as a new PDF changes item fingerprints but not the text, so its headlines stay shown with no rebind and no request. A standing item with the same long text at the next meeting reuses its headline. A revised title has no headline until the next run writes one. Failed and skipped texts are not sent again until the prompt or `VALIDATOR_VERSION` changes. Each rejected reply is kept in its `ai_usage.error`.
- **Export and website.** The export adds an optional `shortTitle` to such items, read in one more bulk query (the export goes from 14 to 15 statements, still fixed). The meeting page shows the headline in place of the title, followed by a small "Shortened" mark whose hover text says it was shortened from the agenda's wording. The expanded item always shows the agenda's whole text under "From the agenda". A cut title already shows it; for an uncut title the title itself is added. Topic lists on the timeline use the headline too.
- **Pipeline.** The `short-titles` step runs after `classify-subjects` and before `summarize-items`, under the shared $5 monthly AI budget and the run's time budget. Its per-run limit, `--short-title-max-cost`, defaults to **$0.05** (about 180 titles, about 6 minutes), so the scheduled job runs it with no change to its arguments; 0 turns it off. It runs before summaries, not after as in the prototype, because a summary backlog that reaches the 45-minute time budget would otherwise leave it no time; it is small and bounded, so it takes little from summaries.
- **Operator override** (proposed default). `tahoe-ingest short-title ITEM_ID --suppress` hides a headline, so the item's own title shows (with its ellipsis and whole text when it was cut); `--text "..."` replaces it with the operator's text (at most 120 characters), shown with the same "Shortened" mark; `--clear` removes the override. `--reason` and `--author` are required, as for summary corrections, and stay internal: the export carries only the shown headline (decision 0010). The override is stored in `editorial.short_title` with the hash of the agenda text it was made for, so it applies wherever that text appears, as the cached headline does, and keeps that text from being sent to Claude. A revised title leaves it behind and gets a new headline. There is one override per text; setting one replaces another item's override for the same text. It does not mark the meeting revised.

## Measurements (2026-11-03, live export, no database writes)

The generator ran as it would ship (Haiku ×2, then Sonnet) on all 520 qualifying in-scope texts. Sonnet alone ran on a 60-item sample (30 El Dorado Board of Supervisors items, 30 items from 22 other agencies) for comparison.

| | Haiku, then Sonnet (all 520) | Sonnet only (60-item sample) |
| --- | --- | --- |
| Published on the first try | 424 (81.5%) | 37 (61.7%) |
| Published in the end | 507 (97.5%): Haiku 501, Sonnet 6 | 57 (95%) |
| Skipped by the model | 4 (0.8%): public comment and agenda-correction boilerplate | 1 |
| Failed validation (full title stays) | 9 (1.7%) | 2 |
| Requests | 627 (Haiku 612, Sonnet 15) | 82 |
| Cost | $0.136, **$0.00026 per item** | $0.128, $0.0021 per item (8×) |
| Latency per request, median / 90th percentile | 1.6 s / 3.4 s | 1.4 s / 1.9 s |
| Latency per item, median / 90th percentile / max | 2.0 s / 4.2 s / 44 s | 1.6 s / 3.2 s / 3.7 s |

- **Rejections** (116 of 627 requests): 56 added words ("closed session", "homeless", "against", "reappointments", "four", "adding"), 32 too many words, 12 too many characters, 14 dropped polarity words, and 2 numbers not printed in the text. Haiku's second try rescued 77 of 92 items. Sonnet rescued 6 of 15. Sonnet's own rejections were mostly length: it writes 17 to 21 words.
- **Polarity rule.** A first version required every polarity word in the text to be kept. It rejected most long El Dorado items, which carry "amendments that do not increase…" and "not expressly identified in County classifications", and Washoe's routine "approve, approve with conditions, or deny". Re-checked offline against all 190 stored replies of that first run, the governed-word rule rejects 5 instead of 25; the remaining ones are real, such as "rescinding prior Resolution 164-2018".
- **Cost and volume.** About 250 qualifying items a month, fewer after standing items are cached: **about $0.07 a month**, under 2% of the $5 AI budget. Database operations: one write per meeting with new long items, plus two reads per run and one in the export, so a few hundred a month (decision 0020: well under 1% of the quota). The first run's backlog (about 520 texts, about $0.14) clears in three runs at $0.05 each, about 6 minutes per run.
- **Spend** for this evaluation, including a pilot, the first-rule runs, and the Sonnet comparison: $0.48.

### Quality

Every one of the 60 sample headlines (Haiku chain) was read against its agenda text, as were about 100 more from the full run whose text has a polarity word.

- **Faithfulness.** No published headline adds a fact; the validator guarantees that at the word and number level. None reverses the action. Appeals kept their direction: "Appeal of Planning Commission approval of Tahoe Keys Marina Redevelopment Project design review and permits"; "Hear or continue Verizon Wireless appeal of Planning Commission decision to deny 80-foot cell tower".
- **Usefulness.** 51 of the 57 published headlines name the action and the subject a resident would look for. 6 drop a secondary fact a resident might care about: a 90-day extension, a second contract award, "not previously authorized" buoys, "pending TRPA authorization", the doubling of a contract's price, and the discontinuation of two water right applications. In each, the full text is one click away.
- **Worst found (the full run).** "Discussion of appeal 2026-APPEAL-0003 to deny campground Major Design Review DP25-0184 with 53 campsites". The agenda describes an appeal of the Planning Commission's decision to deny an earlier appeal of the campground's approval. The headline recombines the agenda's words into a reading the text does not state; the appellant's aim, the campground's denial, is probably right but is inferred. Word-level validation cannot catch this. Also: "Award Job Order Contracting construction contracts to three lowest responsive bidders" leaves out the rejection of a fourth bidder.
- **Sonnet compared.** Sonnet kept a condition more often ("pending Tahoe Regional Planning Agency authorization", "extending term 90 days"), but it wrote longer headlines, failed length checks more, and cost 8 times as much. Haiku with a Sonnet fallback is the better default.

### Examples (Haiku chain)

| Agency | Agenda text (characters) | Headline | Judgment |
| --- | --- | --- | --- |
| El Dorado BOS | District Attorney's Office recommending the Board: 1) Authorize the District Attorney to accept a non-competitive grant from the California Governor's Office of Emergency Services (CalOES) for the Unserved/Underserved Victim Advocacy… (1,078) | Accept CalOES grant for Unserved/Underserved Victim Advocacy and Outreach Program | Good |
| El Dorado BOS | HEARING - Planning and Building Department, Planning Division, Long Range Planning Unit, recommending the Board consider adoption of the proposed El Dorado & Diamond Springs Design Standards and Guidelines… (1,033) | Hearing on adopting El Dorado and Diamond Springs design standards and guidelines | Good |
| El Dorado BOS | Health and Human Services Agency (HHSA) recommending the Board: 1) Make findings pursuant to Section 3.13.030 (B)… 3) Approve… Amendment I to Agreement for Services 9109 with Alice Rodriguez… (1,564) | Approve Amendment I to Agreement 9109 with Alice Rodriguez for Child Welfare therapeutic counseling | Good; the amount is left out |
| El Dorado BOS | Chief Administrative Office recommending the Board: 1) Approve… an amendment to the Fiscal Year (FY) 2026-27 Budget… 2) Adopt… Resolution 163-2026… adopting the County Budget… (679) | Approve amendment to FY 2026-27 budget and adopt county and special district budgets | Good |
| El Dorado BOS | Department of Transportation recommending the Board adopt… Resolution 178-2026, rescinding prior Resolution 164-2018… and reestablishing imprest cash funds in the cumulative amount of $21,400… (625) | Adopt Resolution 178-2026 rescinding and reestablishing Department of Transportation imprest cash funds of $21,400 | Good |
| El Dorado BOS | HEARING - Recorder-Clerk's Office recommending the Board conduct a public hearing to Introduce and Adopt Ordinance 5272… to update the Recorder-Clerk's Schedule of Fees… (284) | Hearing to adopt Ordinance 5272 updating Recorder-Clerk's Schedule of Fees effective January 1, 2027 | Good |
| El Dorado BOS | Surveyor's Office recommending the Board adopt… Resolution 160-2026 for Abandonment of Encumbrance 26-0013, to abandon a portion of the front setback on Lot 35… within Tahoma, at 7233 Chinkapin Road… (390) | Adopt Resolution 160-2026 abandoning a front setback encumbrance on Chinkapin Road lot in Tahoma | Good |
| El Dorado BOS | Treasurer-Tax Collector's Office recommending the Board: 1) Find that… 2) Approve… Amendment II… with Bank of America for Countywide financial services extending the term by 90 days… (723) | Approve Amendment II to Agreement 5924 with Bank of America for countywide financial services | Acceptable; drops "extending the term by 90 days" |
| Washoe BOCC | Recommendation to Acknowledge allocation from the Judicial Council of the State of Nevada to the Reno Justice Court "STAR Court" in the amount of [$2,389.30 for FY27, no match required]… (419) | Acknowledge Judicial Council allocation of $2,389.30 to Reno Justice Court STAR Court | Good |
| Douglas BOCC | Discussion to approve Contract Addendum No. 2… with Haen Constructors… The effect of the addendum is to increase the total contract price for Fiscal Year 2026-27 from $99,000 to $198,000… (463) | Approve Contract Addendum No. 2 for water emergency on-call services with Haen Constructors | Acceptable; drops the price doubling |
| South Lake Tahoe | Appeal of the Planning Commission decision approving File #DR25-007 (Tahoe Keys Marina Redevelopment Project), Granting Major Design Review, Special Use Permit, and a variance… (251) | Appeal of Planning Commission approval of Tahoe Keys Marina Redevelopment Project design review and permits | Good |
| State Lands | (Applicant trusts) Consider application for a General Lease – Recreational Use… Carnelian Bay… for two existing mooring buoys not previously authorized by the Commission… (546) | Consider General Lease for Recreational Use of two mooring buoys at Carnelian Bay | Acceptable; drops "not previously authorized" |
| TTD | Approve the South Shore Transit Management Association Funding Request of $750,000 from Tahoe Transportation District's SB 125 Award to Support the Lake Link Microtransit Program Pending the Authorization by Tahoe Regional Planning Agency (238) | Approve $750,000 SB 125 funding request for Lake Link Microtransit Program | Acceptable; drops the TRPA condition |
| Douglas BOCC | Discussion of Appeal 2026-APPEAL-0003, an appeal of the Planning Commission's decision to deny Appeal 2026-APPEAL-0002, upholding the Community Development staff's decision to approve Major Design Review DP25-0184, for a Developed Campground with 53 campsites… (about 1,000) | Discussion of appeal 2026-APPEAL-0003 to deny campground Major Design Review DP25-0184 with 53 campsites | **Worst**: recombines words into an inferred reading |
| Washoe Planning | Amendment of Conditions Case Number WAC26-0008 (NV Energy) for WSUP23-0032… to grant an additional 2-year extension of time… (1,477) | (failed validation three times: "extending" is not in the text) | Full title stays, with the ellipsis and full text |

## Alternatives and consequences

- **Ellipsis and full text only (no AI).** This is what main now does for cut titles. It costs nothing, cannot misstate anything, and needs no decision. It leaves uncut long titles (El Dorado items of 200 to 400 characters, standing Washoe items) as they are, and a cut title's first lines often name only the department and the statute. Choose it if any risk of a paraphrased headline is unacceptable.
- **Verbatim rule (decision 0024).** A headline is not verbatim: it reorders and selects the agenda's words. The validator limits it to the agenda's own vocabulary and numbers, the "Shortened" mark says it is not the agenda's title, and the whole text stays on the page. Adopting this decision accepts constrained paraphrase for headlines while summaries stay verbatim.
- **Deterministic shortening** (strip "X Department recommending the Board", keep the first numbered action). It is cheap and safe, but brittle across 35 layouts and still long for multi-clause actions. It could serve as a fallback where validation fails.
- **Risks.** Word-level checks cannot detect a misleading recombination of the agenda's own words (the campground appeal) or an omitted condition. One such case in about 160 headlines read. Mitigations: the mark, the whole text one click away, operator review of `ai_short_titles`, and the operator override (`short-title`).
- **Thresholds.** 150 characters adds about 110 items, mostly 150 to 200-character titles that already read well. 300 halves the count to the worst cases.

## Revisit when

- Readers report a misleading headline, or a spot check finds more than about 1 in 50 misleading.
- Validation failures exceed about 5%, or the monthly cost exceeds $0.50.
- Agendas change format so that titles become short again.
