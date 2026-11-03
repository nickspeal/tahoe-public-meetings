"""Agenda item summaries selected from each item's own staff report, with Claude (IDEA-016).

For every item with a verified staff report (``staff_reports``), the first pages of
the report go to Haiku 5.5 (Sonnet 5.5 as fallback, as in ``ai_topics``), which
returns one to three sentences, each trimmed from one passage of the report and
citing its page. The maintainer's rule (decision 0024) is that a summary repeats
the staff report verbatim as much as possible, changing it only to shorten it.

``validate`` enforces that rule before anything is published (decision 0010; no
human approval): each cited passage must be found on its page (read in reading
order, ``reading_view``), and each sentence must be the passage with words deleted,
allowing only a grammatical "the", "a", "an", or "and" to be inserted. Numbers must
be kept whole, a deleted negation fails the sentence unless it was set off from the
kept words, and no cut inside a sentence may remove who recommends it or a
condition. A sentence that is a valid trim of a passage other than the one cited
is cited to that passage instead. When the first
sentence fails, nothing is published and the item keeps its agenda text; a later
sentence that fails is left out of the summary. A rejected answer is retried with
the rejected sentence, its source, the page text near it, and how to fix it.

Results live in ``ai_item_summaries``, one row per item, keyed to everything the
model saw. The export shows a published row only while it matches the item's
current source fingerprint and staff report, so a revised agenda or report falls
back to the excerpt until the next run. An agenda re-posted unchanged as a new
file changes the fingerprint but not the input, so its results are rebound
(``_rebind``) rather than summarized again. Operator corrections and suppression in
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

from .ai_topics import DEFAULT_MONTHLY_BUDGET, month_to_date_spend, out_of_time, time_budget_stop
from .evaluate_topics import AnthropicTopicModel, ModelError, USAGE_FIELDS, cost_usd, estimate_tokens, has_credentials
from .item_scope import in_scope
from .requested_actions import for_meeting as requested_actions
from .reading_view import View, view, vocabulary
from .staff_reports import clean_text, procedural, Item, report_document_id

PROMPT = Path(__file__).with_name("prompts") / "staff_report_summary.md"
METHOD = "claude-staff-report-v1"
# Raise when ``validate`` accepts more than it did: items that failed validation are tried again once, and only they
# (a prompt change would send every item again). Stored on each result; results from before it existed count as 1.
# 2 (2026-11-02): reading view, every occurrence of a passage, passages opening with "$" or "(", list markers and
# bullets, dropped earlier sentences, negations limited to the kept sentence, later failing sentences left out.
# 3 (2026-11-03): fewest-cuts alignment, set-off negations, whole numbers (stricter), printed-word counts, headings
# with a dash, a source's own list marker, sources extended to their sentence's start, a parenthetical cut, sentences
# found on another page or passage than cited, and no inner cut of "recommends", "if", "unless", and the like.
VALIDATOR_VERSION = 3
CHAIN = (("claude-haiku-5-5", "low"), ("claude-sonnet-5-5", "medium"))
# Haiku gets a second try with the validator's objection; Sonnet costs about 20 times more, so one try. The second
# Haiku try rescues nearly every faithful first answer that failed; a third rescued none of those measured (decision 0024).
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
#
# Every check runs on the reading view of each page (``reading_view``): side labels moved out of the sentences
# beside them, and words hyphenated across a line break joined when the report prints the joined word. A
# citation still quotes the page's own text (``View.span``), which is what the export checks and the site shows.

class Rejected(ValueError):
    """Why a summary or one of its sentences failed, with what a retry message needs to explain it."""

    def __init__(self, number: int | None, kind: str, problem: str, *, entry: dict | None = None, near: str = "",
                 show_sentence: bool = False):
        entry = entry or {}
        self.number, self.kind, self.problem, self.near = number, kind, problem, near
        self.sentence = " ".join(str(entry.get("sentence", "")).split())
        self.source = " ".join(str(entry.get("source", "")).split())
        self.page = entry.get("page")
        message = problem if number is None else f"sentence {number}: {problem}"
        super().__init__(message + (f": “{self.sentence[:200]}”" if show_sentence else ""))


def _tokens(text: str) -> list[tuple[str, int, int]]:
    """Words with their character offsets in ``text`` itself (callers clean page text first).

    Digits and letters are separate tokens, so "2027secured" (a lost space) is two words and each digit group
    of "$1,267,300" is its own token; ``_numbers`` keeps such groups together.
    """
    return [(m.group().casefold(), m.start(), m.end()) for m in re.finditer(r"\d+|[^\W\d_]+", text)]


def _word_count(text: str) -> int:
    """Printed words: "$1,267,300" and "2026-2031" are one word each."""
    return sum(1 for word in text.split() if re.search(r"[^\W_]", word))


def _printed_words(text: str, tokens: list[tuple[str, int, int]], first: int, stop: int) -> int:
    """Printed words covered by ``tokens[first:stop]``."""
    return len(text[tokens[first][1]:tokens[stop - 1][2]].split()) if first < stop else 0


def _numbers(text: str, tokens: list[tuple[str, int, int]]) -> list[list[int]]:
    """Token indexes of each printed number with more than one digit group: "$1,267,300", "3.25", "2026-2031"."""
    groups, current = [], []
    for index, (token, start, _) in enumerate(tokens):
        if token.isdigit() and current and re.fullmatch(r"[.,/:–-]", text[tokens[current[-1]][2]:start]):
            current.append(index)
            continue
        if len(current) > 1:
            groups.append(current)
        current = [index] if token.isdigit() else []
    if len(current) > 1:
        groups.append(current)
    return groups


# Characters that open a passage before its first word: "$900 collected", "(1) Approve", "“Make the Best Better”".
OPENERS = "$(\"“‘'[#"
# A list marker or bullet before a passage starts it: "3)", "(a)", "•", the Symbol-font bullet (U+F0B7) that
# pdftotext reads from Word documents; "1.", "b.", "iv.", an "o" sub-bullet, or a dash only at the start of a line
# (a period after "F." mid-line is more often an initial).
_MARKER_END = re.compile(r"(?:(?:^|\s)(?:\(?(?:\d{1,2}|[a-zA-Z]|[ivxIVX]{1,4})\)|[•●▪■◦‣∙])"
                         r"|(?:^|\n)[ \t]*(?:[o*–—-]|(?:\d{1,2}|[a-zA-Z]|[ivxIVX]{1,4})\.))$")
# A list marker at the very start of a cited source: "d. Approve the Project".
_LEADING_MARKER = re.compile(r"\(?(?:\d{1,2}|[a-zA-Z]|[ivxIVX]{1,4})[.)]\s+(?=[A-Z])")
# Abbreviations whose period does not end a sentence: "Amendment No. 3", "Mr. Smith", "approx. 40".
# A single letter is an initial ("Edward F. Ricketts"), except as a list marker after a sentence ("work. A. The")
# or as the name of an attachment, exhibit, or the like ("included as Attachment C. Both").
_ABBREVIATION = re.compile(r"(?:^|[\s(])(?:no|nos|mr|mrs|ms|dr|st|vs|approx|ca|e\.g|i\.e)\.$", re.I)
_INITIAL = re.compile(r"(?:^|[\s(])([A-Za-z]+\s+)?[A-Za-z]\.$")
_NAMED_LETTER = {"attachment", "exhibit", "appendix", "section", "item", "table", "figure", "phase", "option",
                 "alternative", "schedule", "part", "unit", "zone", "area", "tier", "plan", "lot", "parcel"}
_SENTENCE_END = re.compile(r"[.?!][\"”’)]?(?=\s|$)")


def _passage_start(text: str, start: int) -> int:
    """``start`` moved back over the punctuation that opens the passage's first word."""
    while start and text[start - 1] in OPENERS:
        start -= 1
    return start


def _starts_sentence(text: str, start: int) -> bool:
    """Whether a sentence of ``text`` may start at ``start``: after a sentence's end, a heading's colon or dash, a
    list marker or bullet, or at a line that starts with a capital letter, a digit, or an opening mark."""
    start = _passage_start(text, start)
    before = text[:start]
    if not before.strip():
        return True
    if re.search(r"(?:[^\W_]|\d[.,])\Z", before):
        return False  # inside a word or number: "00" of "$2,304,924.00" does not follow a sentence's period
    stripped = before.rstrip()
    first = text[start:start + 1]
    if stripped[-1] in ":;" or _MARKER_END.search(stripped):
        return True
    if re.search(r"[.?!:;]\s+(?:[A-Za-z]|\d{1,2}|[ivx]{1,4})\.$", stripped):
        return True  # a list marker after a sentence: "proceed with the work. A. The Fire Station ..."
    if re.search(r"[.?!][\"”’)]?$", stripped):
        initial = _INITIAL.search(stripped)
        if initial and (initial.group(1) or "").strip().casefold() not in _NAMED_LETTER:
            return False
        return not first.islower() and not _ABBREVIATION.search(stripped)
    if "\n" in before[len(stripped):]:
        return first.isupper() or first.isdigit() or first in OPENERS
    return _after_heading_dash(text, start)


def _after_heading_dash(text: str, start: int) -> bool:
    """A sentence after a short or capitalized heading and a dash at the start of its line:
    "Net Income – For the two months ended ...", "PUBLIC HEARING - The Recorder-Clerk's Office ..."."""
    line_start = text.rfind("\n", 0, start) + 1
    heading = text[line_start:start]
    if not text[start:start + 1].isupper() or not re.search(r"\S\s+[–—-]\s+$", heading):
        return False
    if len(heading.split()) > 3 and heading.upper() != heading:
        return False
    previous = text[:line_start].rstrip(" \t")
    return not previous.strip() or previous.endswith("\n") or previous.rstrip()[-1] in ".:;!?"


def _source_starts(text: str, start: int) -> bool:
    """Whether a cited source may begin at ``start``: where a sentence starts, or at the list marker of one
    ("d. Approve the Project"), when that marker follows a sentence's end or starts a line."""
    if _starts_sentence(text, start):
        return True
    if not _LEADING_MARKER.match(text, start):
        return False
    before = text[:start].rstrip()
    return not before or before[-1] in ".:;!?" or "\n" in text[len(before):start]


def _clear_start(text: str, start: int) -> bool:
    """A sentence start that does not rest on a line break alone (the layout can put any word first on a line),
    for passages the validator finds itself."""
    if not _starts_sentence(text, start):
        return False
    before = text[:_passage_start(text, start)]
    stripped = before.rstrip()
    if not stripped or "\n\n" in before[len(stripped):]:
        return True
    return bool(re.search(r"[.?!:;][\"”’)]?$", stripped) or _MARKER_END.search(stripped))


def _sentence_start_before(text: str, start: int, *, within: int, clear: bool = False) -> int | None:
    """Where the sentence containing ``start`` starts, at most ``within`` words back, or None."""
    tokens = _tokens(text[:start])
    starts = _clear_start if clear else _starts_sentence
    for index in range(len(tokens) - 1, max(-1, len(tokens) - 1 - within), -1):
        if starts(text, tokens[index][1]):
            return tokens[index][1]
    return 0 if len(tokens) <= within else None


def _sentence_end_after(text: str, end: int) -> int:
    """Where the sentence containing ``end`` ends: the next period, question, or exclamation mark followed by a
    sentence's start, or the end of the page."""
    found = _SENTENCE_END.search(text, max(0, end - 1))
    while found:
        rest = text[found.end():]
        if _starts_sentence(text, found.end() + len(rest) - len(rest.lstrip())):
            return found.end()
        found = _SENTENCE_END.search(text, found.end())
    return len(text)


def _spans(source: str, text: str) -> list[tuple[int, int]]:
    """Character spans of ``source`` in ``text``, comparing words only (case, punctuation, and line breaks ignored).

    Every exact occurrence is returned, so a recommended motion that repeats the item's title is found where it
    starts a sentence. A copy with a stray word or two still finds its passages (each run of matching words is
    tried as the anchor), but a span is always the page's own text, which is what the sentence is checked against.
    """
    wanted = [t for t, _, _ in _tokens(source)]
    page = _tokens(text)
    words = [t for t, _, _ in page]
    n = len(wanted)
    if not n:
        return []
    exact = [(page[s][1], page[s + n - 1][2]) for s in range(len(words) - n + 1) if words[s:s + n] == wanted]
    if exact:
        return exact
    blocks = [b for b in SequenceMatcher(None, words, wanted, autojunk=False).get_matching_blocks() if b.size]
    found = []
    for anchor in sorted(blocks, key=lambda b: -b.size):
        aligned = [b for b in blocks if abs((b.a - b.b) - (anchor.a - anchor.b)) <= 6]
        first, last = min(b.a for b in aligned), max(b.a + b.size for b in aligned)
        extra = _printed_words(text, page, first, last) - _word_count(source)
        if sum(b.size for b in aligned) >= 0.9 * n and extra <= 3 and (page[first][1], page[last - 1][2]) not in found:
            found.append((page[first][1], page[last - 1][2]))
    return found


def _nearest(source: str, text: str) -> str:
    """The page text around the closest match for ``source``, for a retry message."""
    wanted = [t for t, _, _ in _tokens(source)]
    page = _tokens(text)
    blocks = [b for b in SequenceMatcher(None, [t for t, _, _ in page], wanted, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        return ""
    anchor = max(blocks, key=lambda b: b.size)
    start = page[max(0, anchor.a - anchor.b)][1]
    end = page[min(len(page) - 1, anchor.a - anchor.b + len(wanted) + 3)][2]
    return text[max(0, start - 120):end + 40]


def _sentence_ids(passage: str, tokens: list[tuple[str, int, int]]) -> list[int]:
    """Which sentence of ``passage`` each token is in."""
    ids, current = [], 0
    for index, (_, start, _) in enumerate(tokens):
        if index and re.search(r"[.?!]", passage[tokens[index - 1][2]:start]) and _starts_sentence(passage, start):
            current += 1
        ids.append(current)
    return ids


def _alignment(sentence: list[str], passage: list[str]) -> list[tuple[int, int]]:
    """Which passage word each kept sentence word is, as (sentence index, passage index) pairs.

    As many words as possible are matched, then with as few cuts inside the passage as possible (skipping the
    passage's start and end is free). A plain longest common subsequence could match an early "the" in the
    wrong clause and report cuts the sentence never made.
    """
    rows, cols = len(sentence), len(passage)
    match, before, after, gap = 1000, 0, 1, 2  # a match outweighs any number of cuts; the states of the walk
    # best[i][j][state]: the score of aligning sentence[i:] with passage[j:]; how[...] the move that achieves it.
    best = [[[0] * 3 for _ in range(cols + 1)] for _ in range(rows + 1)]
    how = [[[""] * 3 for _ in range(cols + 1)] for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        for j in range(cols, -1, -1):
            for state in (before, after, gap):
                # In order of preference when scores tie: match, skip a passage word, keep a word not in the passage.
                options = []
                if j < cols and sentence[i] == passage[j]:
                    options.append((best[i + 1][j + 1][after] + match, "match"))
                if j < cols:
                    options.append((best[i][j + 1][before if state == before else gap] - (state == after), "skip"))
                options.append((best[i + 1][j][state], "keep-glue"))
                best[i][j][state], how[i][j][state] = max(options, key=lambda option: option[0])
    pairs, i, j, state = [], 0, 0, before
    while i < rows:
        move = how[i][j][state]
        if move == "match":
            pairs.append((i, j))
            i, j, state = i + 1, j + 1, after
        elif move == "skip":
            j, state = j + 1, before if state == before else gap
        else:
            i += 1
    return pairs


# Words an inner cut may not remove: they say who recommends or proposes it, or make it conditional or uncertain.
CUT_GUARD = {"recommends", "recommend", "recommended", "recommending", "requests", "requested", "proposes", "proposed",
             "if", "unless", "should", "may", "might", "would", "could"}
# Negations that make the whole statement conditional or exclude part of it: never cut, wherever they are.
STRICT_NEGATIONS = {"unless", "except", "neither", "nor"}


def _set_off(passage: str, tokens: list[tuple[str, int, int]], negation: int, used: list[int]) -> bool:
    """Whether a dropped negation belongs to a phrase the kept words do not depend on.

    After the kept words: a trailing clause set off by punctuation or starting with "with", "including", "and",
    "or", or "but" ("Approve the amendment, with no change to the term"). Inside them: a bracketed aside
    ("$63,875.00 [no County match] for personnel"), or a contrast set off by commas or dashes that starts with
    the negation ("approval, not denial, of the permit"). A negation before the kept words, or one that makes
    the statement conditional ("unless", "except"), never is.
    """
    first, last = min(used), max(used)
    if negation < first or tokens[negation][0] in STRICT_NEGATIONS:
        return False
    if negation > last:
        between = passage[tokens[last][2]:tokens[negation][1]]
        follows = tokens[last + 1][0] if last + 1 < len(tokens) else ""
        return bool(re.search(r"[,;:(\[—–]", between)) or follows in {"with", "including", "and", "or", "but"}
    left = passage[tokens[max(u for u in used if u < negation)][2]:tokens[negation][1]]
    right = passage[tokens[negation][2]:tokens[min(u for u in used if u > negation)][1]]
    if re.search(r"[(\[]", left) and re.search(r"[)\]]", right):
        return True
    return bool(re.search(r"[,—–]\s*$", left)) and bool(re.search(r"[,—–]", right))


def _problem(sentence: str, passage: str) -> tuple[str, str] | None:
    """Why ``sentence`` is not ``passage`` with words deleted, as (kind, explanation), or None when it is."""
    said = [t for t, _, _ in _tokens(sentence)]
    tokens = _tokens(passage)
    source = [t for t, _, _ in tokens]
    count = _word_count(sentence)
    if not MIN_SENTENCE_WORDS <= count <= MAX_SENTENCE_WORDS:
        return "length", f"sentence has {count} words (allowed {MIN_SENTENCE_WORDS}-{MAX_SENTENCE_WORDS})"
    pairs = _alignment(said, source)
    kept = {i for i, _ in pairs}
    added = [said[i] for i in range(len(said)) if i not in kept]
    if any(word not in GLUE for word in added):
        return "adds", "adds words not in the cited passage: " + ", ".join(sorted({w for w in added if w not in GLUE}))
    if len(added) > max(1, len(said) // 10):
        return "glue", "inserts too many connecting words"
    used = [j for _, j in pairs]
    if not used:
        return "unrelated", "shares no words with the cited passage"
    # The sentence may drop whole sentences before it, an opening clause up to its comma, colon, or semicolon
    # ("If X is approved, ..."), or a list marker ("(1)"); never part of its own sentence.
    if min(used) > 0:
        start = tokens[min(used)][1]
        opening = passage[:_passage_start(passage, start)].rstrip()
        if not opening.endswith(",") and not _starts_sentence(passage, start):
            return "opening", "starts in the middle of the passage's sentence"
    # A number is kept whole or not at all: "$1,267,300" never becomes "$1,300", nor "3.25%" "3%".
    kept_words = set(used)
    for number in _numbers(passage, tokens):
        if kept_words & set(number) and not set(number) <= kept_words:
            return "number", "keeps only part of a number"
    # Cuts inside the sentence: never of who recommends or proposes it, or of a condition; one bracketed aside
    # ("Agreement (Amendment No. 3)") is not counted against the limit on cuts.
    cuts = []
    for a, b in zip(used, used[1:]):
        if b - a == 1:
            continue
        if {t for t, _, _ in tokens[a + 1:b]} & CUT_GUARD:
            return "cut-guard", "cuts out who recommends or proposes it, or a condition"
        if re.fullmatch(r"[,;]?\s*[(\[][^()\[\]]*[)\]]\s*[,;]?", passage[tokens[a][2]:tokens[b][1]].strip()):
            continue
        cuts.append(_printed_words(passage, tokens, a + 1, b))
    if len(cuts) > MAX_INNER_CUTS or any(cut > MAX_INNER_CUT_WORDS for cut in cuts):
        return "cuts", f"cuts too much from the middle of the passage ({len(cuts)} cuts, longest {max(cuts)} words)"
    # A negation within or next to the kept words must be kept, within the kept words' own sentences ("No action
    # is required." after the kept sentence, or "Project No. 0184", is not a negation of it), unless it belongs
    # to a phrase set off from them (``_set_off``).
    ids = _sentence_ids(passage, tokens)
    own = range(ids[min(used)], ids[max(used)] + 1)
    for j in sorted(set(range(min(used) - 3, max(used) + 4)) - kept_words):
        if 0 <= j < len(source) and source[j] in NEGATIONS and ids[j] in own:
            if source[j] == "no" and re.match(r"\.\s*\d", passage[tokens[j][2]:]):
                continue
            if not _set_off(passage, tokens, j, used):
                return "negation", "drops a negation from the cited passage"
    return None


def check_sentence(sentence: str, passage: str) -> str | None:
    """Why ``sentence`` is not ``passage`` with words deleted, or None when it is."""
    found = _problem(sentence, passage)
    return found[1] if found else None


# A rejection the cited source alone may have caused: the sentence is then looked for on every page (``_locate``).
# A sentence that is too long, adds too many words, keeps part of a number, or drops a negation is not.
_LOCATABLE = {"page", "short-source", "long-source", "not-found", "mid-page", "opening", "adds", "unrelated", "cuts", "cut-guard"}
MAX_LOCATED_WORDS = 250


def _locate(sentence: str, views: dict, page: int | None) -> tuple[int, int, int] | None:
    """A passage on any page (the cited page first) that ``sentence`` is a valid trim of, as (page, start, end).

    The model often trims a real sentence of the report but cites the wrong page, copies its source imperfectly,
    or gives a neighbouring sentence. The passage is the report's whole sentence around the sentence's first words,
    starting at a clear sentence start, so the same rules apply as to a cited passage.
    """
    said = [t for t, _, _ in _tokens(sentence)]
    if len(said) < 3:
        return None
    order = sorted(views, key=lambda n: n != page)
    for number in order:
        text = views[number].text
        tokens = _tokens(text)
        for index, (word, start, _) in enumerate(tokens):
            if word not in said[:3]:
                continue
            begin = start if _clear_start(text, start) else _sentence_start_before(text, start, within=40, clear=True)
            if begin is None:
                continue
            begin = _passage_start(text, begin)
            end = _sentence_end_after(text, tokens[min(len(tokens) - 1, index + len(said) - 1)][2])
            passage = text[begin:end]
            if _word_count(passage) > MAX_LOCATED_WORDS or _problem(sentence, passage):
                continue
            # Cite from the latest clear sentence start before the first kept word, so the quote is that sentence.
            passage_tokens = _tokens(passage)
            first = min(j for _, j in _alignment(said, [t for t, _, _ in passage_tokens]))
            for t in range(first, 0, -1):
                position = begin + passage_tokens[t][1]
                if _clear_start(text, position):
                    trimmed = _passage_start(text, position)
                    if _problem(sentence, text[trimmed:end]) is None:
                        begin = trimmed
                    break
            return number, begin, end
    return None


def _check_cited(number: int, entry: dict, sentence: str, views: dict) -> tuple[int, int, int]:
    """The cited passage that ``sentence`` is a valid trim of, as (page, start, end); or Rejected."""
    page = entry.get("page")
    if page not in views:
        raise Rejected(number, "page", f"cites page {page}, which was not provided", entry=entry)
    source = str(entry.get("source", ""))
    count = _word_count(source)
    if count < MIN_SENTENCE_WORDS:
        raise Rejected(number, "short-source", f"its source has {count} words (at least {MIN_SENTENCE_WORDS})", entry=entry)
    if count > MAX_SOURCE_WORDS:
        raise Rejected(number, "long-source", f"its source has {count} words (at most {MAX_SOURCE_WORDS})", entry=entry)
    text = views[page].text
    spans = _spans(source, text)
    if not spans:
        raise Rejected(number, "not-found", f"cited passage is not on page {page}", entry=entry, near=_nearest(source, text))
    starting = [span for span in spans if _source_starts(text, span[0])]
    if not starting:  # a source copied from mid-sentence: check the sentence against the report's whole sentence
        for start, end in spans:
            begin = _sentence_start_before(text, start, within=60)
            if begin is not None:
                starting = [(begin, end)]
                break
    if not starting:
        start = spans[0][0]
        raise Rejected(number, "mid-page", f"the cited passage starts in the middle of a sentence on page {page}",
                       entry=entry, near=text[max(0, start - 160):start + 60])
    start, end = starting[0]
    start = _passage_start(text, start)
    found = _problem(sentence, text[start:end])
    if found:
        raise Rejected(number, found[0], found[1], entry=entry, near=text[start:end], show_sentence=True)
    return page, start, end


def _check_entry(number: int, entry: dict, views: dict) -> tuple[str, dict, int]:
    """One sentence: its published text, its citation (the page's own text), and its word count; or Rejected.

    When the cited source is what failed, a passage on any page that the sentence is a valid trim of is cited
    instead (``_locate``); the citation then quotes that passage, never the model's source.
    """
    sentence = " ".join(str(entry.get("sentence", "")).split())
    try:
        page, start, end = _check_cited(number, entry, sentence, views)
    except Rejected as rejected:
        located = _locate(sentence, views, entry.get("page")) if rejected.kind in _LOCATABLE else None
        if located is None:
            raise
        page, start, end = located
    if not re.search(r"[.!?][\"”’)]?$", sentence):
        sentence += "."
    view = views[page]
    first, last = view.span(start, end)
    citation = {"page": page, "quote": view.source[first:last]}
    return sentence[0].upper() + sentence[1:], citation, _word_count(sentence)


def page_views(pages: dict[int, str]) -> dict[int, View]:
    """The reading view of each page, with the report's own words attesting joined hyphenations."""
    cleaned = {n: clean_text(text) for n, text in pages.items()}
    words = vocabulary(cleaned.values())
    return {n: view(text, words) for n, text in cleaned.items()}


def validate(sentences: list[dict], pages: dict[int, str], document_id: str, *, limit: int = MAX_SENTENCES,
             dropped: list[str] | None = None) -> tuple[str, list[dict]]:
    """The summary text and its citations, or Rejected (a ValueError) naming the first problem.

    The first sentence must pass. A later sentence that fails, repeats an earlier one, or goes past the
    sentence or word limits is left out (and described in ``dropped``); every sentence published has
    passed every check.
    """
    if not sentences:
        raise Rejected(None, "count", f"0 sentences (allowed 1-{limit})")
    dropped = [] if dropped is None else dropped
    views = page_views(pages)
    texts, citations, total = [], [], 0
    for number, entry in enumerate(sentences, 1):
        if number > limit:
            dropped.append(f"sentence {number}: more than {limit} sentences")
            continue
        try:
            text, citation, words = _check_entry(number, entry, views)
        except Rejected as rejected:
            if number == 1:
                raise
            dropped.append(str(rejected))
            continue
        if text.casefold() in {t.casefold() for t in texts}:
            dropped.append(f"sentence {number}: repeats a sentence")
            continue
        if total + words > MAX_SUMMARY_WORDS:
            dropped.append(f"sentence {number}: the summary would pass {MAX_SUMMARY_WORDS} words")
            continue
        texts.append(text)
        citations.append({"documentId": document_id, **citation})
        total += words
    return " ".join(texts), citations


# --- Input selection ------------------------------------------------------------------

def _pending(conn, *, agency: str | None, meeting: str | None, force: bool, prompt_hash: str,
             today: date) -> tuple[list[dict], list[dict]]:
    """The items to summarize, nearest meeting first, and the stored results to rebind (``_rebind``)."""
    rows, candidates, rebind = [], [], []
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
        citations = r["citations"] if isinstance(r["citations"], list) else json.loads(r["citations"])
        # Mapping leaves out items beyond the agency's ``itemScope``; this also keeps an older mapping from a summary.
        if not procedural(item) and in_scope(r["agency_id"], r["title"], citations):
            candidates.append(r)
    actions = _requested_actions(conn, {r["meeting_id"] for r in candidates})
    for r in candidates:
        r["pages"] = r["pages"] if isinstance(r["pages"], list) else json.loads(r["pages"])
        r["action"] = actions.get(r["item_id"])
        r["user"] = render_user_message(r)
        r["input_sha256"] = hashlib.sha256(json.dumps({"prompt": prompt_hash, "report": r["report_sha256"],
                                                       "user": r["user"]}).encode()).hexdigest()
        r["reason"] = _reason(r, prompt_hash)
        if r["done"] == r["input_sha256"] and r["done_fingerprint"] != r["source_fingerprint"]:
            rebind.append({"item_id": r["item_id"], "input_sha256": r["input_sha256"], "old_fingerprint": r["done_fingerprint"],
                           "source_fingerprint": r["source_fingerprint"], "action_citation": (r["action"] or {}).get("citation")})
        if r["reason"] or force:
            r["reason"] = r["reason"] or "forced"
            rows.append(r)
    rows.sort(key=lambda r: (abs((r["local_date"] - today).days) if r["local_date"] else 10_000, r["meeting_id"]))
    return rows, rebind


def _rebind(conn, rebind: list[dict]) -> int:
    """Point stored results at their items' current fingerprints, in one statement; returns the rows updated.

    An agenda posted again as a new file, with the same text, changes every item's fingerprint (its citations name
    the new document version) but not what the model would be sent, so the input hash still matches and nothing is
    summarized again. The stored result keeps its text and is rebound to the current fingerprint, without which
    the export would hide it. A published summary that leads with the agenda's requested action also has that
    citation replaced by the current one, so it names a document version the export still carries. Skipped and
    validation-failed results are rebound too: they were judged on the same input, and staying bound to the
    agenda version they describe keeps ``_reason`` and the stored row consistent.
    """
    if not rebind:
        return 0
    return conn.execute("""UPDATE ai_item_summaries x SET source_fingerprint=r.source_fingerprint,
            citations=CASE WHEN r.action_citation IS NOT NULL AND x.status='published'
              AND x.citations->0->>'quote'=r.action_citation->>'quote'
              THEN jsonb_set(x.citations,'{0}',r.action_citation) ELSE x.citations END
        FROM jsonb_to_recordset(%s::jsonb) AS r(item_id text,input_sha256 text,old_fingerprint text,source_fingerprint text,
          action_citation jsonb)
        WHERE x.item_id=r.item_id AND x.input_sha256=r.input_sha256 AND x.source_fingerprint IS NOT DISTINCT FROM r.old_fingerprint""",
                        (json.dumps(rebind),)).rowcount


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
    # The model reads the pages as the validator does: in reading order (``reading_view``).
    views = page_views({p["number"]: p["text"] for p in row["pages"]})
    pages = "\n\n".join(f'<page number="{n}">\n{v.text}\n</page>' for n, v in views.items())
    if row.get("action"):
        return ("\n".join(header) + f"\n\n<requested_action>{row['action']['text']}</requested_action>\n\n"
                "The summary already begins with this requested action from the agenda. Add one or two sentences from the "
                "staff report pages that a resident most needs beyond it.\n\n" + pages)
    return "\n".join(header) + "\n\nSummarize this agenda item from its staff report pages.\n\n" + pages


# --- Run ------------------------------------------------------------------------------

def summarize_items(conn, *, agency: str | None = None, meeting: str | None = None, max_items: int | None = None,
                    force: bool = False, dry_run: bool = False, max_cost: float = DEFAULT_MAX_RUN_COST,
                    monthly_budget: float = DEFAULT_MONTHLY_BUDGET, clients: dict | None = None,
                    today: date | None = None, deadline: float | None = None) -> dict:
    """Summarize pending items within the cost limit; ``deadline`` (``time.monotonic()``) stops before the next item.

    Each item is one request; a meeting's finished items are stored together, including when a limit stops the run.
    """
    system = PROMPT.read_text().strip()
    schema = response_schema()
    prompt_hash = prompt_sha256(system)
    pending, rebind = _pending(conn, agency=agency, meeting=meeting, force=force, prompt_hash=prompt_hash, today=today or date.today())
    # Results whose input is unchanged but whose agenda was posted again are rebound, not summarized (a dry run only counts them).
    rebound = len(rebind) if dry_run else _rebind(conn, rebind)
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
    report = {"itemsPending": len(pending), "itemsPendingByReason": reasons, "itemsRebound": rebound,
              "itemsSelected": len(selected), "estimatedCostUsd": round(running, 4),
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
    attempted = 0
    for meeting_id, rows in by_meeting.items():
        results, usage = [], []
        for row in rows:
            if report["costUsd"] >= max_cost or spent_this_month + report["costUsd"] >= monthly_budget:
                report["stoppedEarly"] = "cost limit reached; remaining items are summarized on the next run"
                break
            if out_of_time(deadline):
                time_budget_stop(report, "items are summarized", itemsDeferred=len(selected) - attempted)
                break
            attempted += 1
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
    last_error, feedback, answered = None, "", False
    for model, effort in CHAIN:
        client = clients.get(model) or clients.setdefault(model, AnthropicTopicModel(model, effort))
        for _ in range(ATTEMPTS[model]):
            try:
                reply = client.complete(system=system, user=row["user"] + feedback, schema=schema, max_tokens=MAX_TOKENS)
            except ModelError as exc:
                if exc.fatal:
                    raise SystemExit(f"Item summaries stopped: {exc}") from exc
                usage.append(_usage(model, row["meeting_id"], {}, ok=False, error=str(exc)))
                report["apiErrors"] += 1
                continue
            answered = True
            parsed, problem = _parse(reply)
            rejected, dropped = None, []
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
                    text, citations = validate(sentences, pages, document_id, limit=MAX_SENTENCES - 1 if action else MAX_SENTENCES,
                                               dropped=dropped)
                    if action:
                        text, citations = action["text"] + " " + text, [action["citation"], *citations]
                except Rejected as exc:
                    rejected, problem = exc, f"validation: {exc}"
            # A rejected reply is kept with its usage row, so a validator change can be checked against it offline.
            note = problem or ("published without " + "; ".join(dropped) if dropped else None)
            report["costUsd"] += _add_usage(usage, model, row["meeting_id"], reply.usage, ok=problem is None,
                                            error=note and f"{note}{REPLY_MARK}{reply.text[:MAX_STORED_REPLY]}")
            if problem is None:
                report["published"][model] = report["published"].get(model, 0) + 1
                report["sentencesDropped"] = report.get("sentencesDropped", 0) + len(dropped)
                return {**base, "model": reply.model or model, "status": "published", "text": text, "citations": citations}
            last_error = problem
            kind = f"validation: {rejected.kind}" if rejected else problem
            report["failures"][kind] = report["failures"].get(kind, 0) + 1
            feedback = retry_message(rejected, problem)
    if not answered:
        return None  # Only API errors: nothing is recorded, so the next run tries again.
    report["validationFailed"] += 1
    return {**base, "model": CHAIN[-1][0], "status": "validation_failed", "error": (last_error or "")[:500]}


# What a retry is told for each kind of rejection, after the rejected sentence, its source, and the page text near it.
FIXES = {
    "short-source": ("That source is too short to stand as a sentence. Leave out one-line labels and boilerplate such as "
                     "\"Receive and file\", \"For presentation only\", \"None\", or \"No financial impact\"; choose a passage "
                     "of at least 5 words that says what the item is or does, or leave that sentence out."),
    "long-source": "Give as `source` only the one sentence you trimmed, not its whole paragraph.",
    "not-found": ("Copy `source` character for character from the page it is on (the page text nearest to it is shown "
                  "above). A passage never spans two pages."),
    "mid-page": ("Start `source` where the report's sentence starts: after the previous sentence's period, a heading's "
                 "colon, or a list marker (the page text just before it is shown above)."),
    "opening": ("Keep the start of the passage's sentence. You may drop only whole sentences before it, an opening clause "
                "that ends in a comma or colon, or a list marker."),
    "cuts": ("Delete from the end of the sentence rather than its middle: at most 3 cuts inside it, each of at most 12 "
             "words. A sentence that stops early is fine."),
    "negation": ("Keep \"not\", \"no\", \"without\", \"unless\", and similar words with the words around them, or end "
                 "the sentence before the clause that contains them."),
    "number": ("Copy every number whole, as printed: never shorten \"$1,267,300\" to \"$1,300\", \"3.25%\" to \"3%\", or "
               "\"2026-2031\" to \"2026\"."),
    "cut-guard": ("Keep who recommends, requests, or proposes the action, and any \"if\", \"unless\", \"should\", \"may\", "
                  "or \"would\", with the words around them; end the sentence earlier instead of cutting them out."),
    "adds": "Use only words that are in `source`; the only words you may add are \"the\", \"a\", \"an\", and \"and\".",
    "glue": "Add at most one \"the\", \"a\", \"an\", or \"and\" for every ten words.",
    "length": ("Each sentence has 5 to 45 words: end a long one earlier, or instead of a short label choose a passage "
               "that says what the item is or does."),
    "page": "Give as `page` the number of the <page> tag the passage is on.",
    "unrelated": "Each sentence is its `source` with words deleted.",
    "count": "Return one to three sentences, or skip.",
}
REPLY_MARK = "\n\nreply: "  # separates a usage row's error from the reply it rejected (scripts/usage groups on the part before)
MAX_STORED_REPLY = 6000


def retry_message(rejected: Rejected | None, problem: str) -> str:
    """What the next attempt is told about the last one."""
    if rejected is None:
        return f"\n\nYour previous answer was rejected: {problem}. Answer again in the required format."
    lines = ["", "", "<rejected_answer>",
             "Your previous answer was rejected and will not be published. "
             + (f"Sentence {rejected.number} (page {rejected.page}): " if rejected.number else "") + rejected.problem + "."]
    if rejected.sentence:
        lines.append(f"Rejected sentence: “{rejected.sentence}”")
    if rejected.source:
        lines.append(f"Its source: “{rejected.source}”")
    if rejected.near:
        near = " ".join(rejected.near.split())
        lines.append(("The page's own passage" if rejected.kind in _SENTENCE_KINDS else "Page text near it") + f": “{near}”")
    lines += [f"How to fix it: {FIXES.get(rejected.kind, FIXES['unrelated'])}", "</rejected_answer>",
              "Answer again with the whole summary, following every rule."]
    return "\n".join(lines)


_SENTENCE_KINDS = {"length", "adds", "glue", "unrelated", "opening", "cuts", "negation", "number", "cut-guard"}


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
