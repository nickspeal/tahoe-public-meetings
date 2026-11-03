"""Keep a PDF page's columns apart in ``pdftotext -layout`` text, using word positions.

``pdftotext -layout`` prints each row of a page as one line and turns horizontal
distance into spaces at an approximate character pitch. Where a column is printed in
a different font from the one beside it, the approximation drifts: a form's label
column ("System need", right-aligned against the body in a bold face) can end one
space before the body text beside it, while the body's own continuation lines start
three characters further right. The text then reads "System need The system currently
operates" with nothing to tell the label from the sentence (``reading_view``), and a
table's last two columns can run together the same way ("10/13-16/2026 specific
funding appropriated").

``align`` repairs that with the word boxes poppler reports for the same page
(``pdftotext -bbox``). A word starts a column when the gap before it is wider than
``COLUMN_GAP`` times the text height and ``COLUMN_RATIO`` times the line's ordinary
gaps between words (so the evenly stretched spaces of justified text never count, nor
a wide space after a sentence), or wider than ``ALIGNED_GAP`` and ``ALIGNED_RATIO``
when it starts at the same horizontal position as such a column's words on other
lines. Only a column with a word printed a single space after the word before it (and
no colon, list marker, or bullet to show where the label ends) is changed: its words
are moved right, with spaces, to the character column where the furthest-right of them
starts, and at least ``MIN_GUTTER`` spaces after the word before them. A collapsed gap
that no other line shares only gets the gutter, and only on a line with enough other
gaps to compare it with.

Only spaces are added: every word, its order, and its line stay as they were, so a
citation quoted from the old text (compared with runs of whitespace collapsed, as the
export does) is still found in the new text. A line whose words don't match the boxes
exactly is left as printed, and so is a page whose columns pdftotext already kept
apart, which is most pages.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from statistics import median

COLUMN_GAP = 0.55  # gap / text height that can start a column on its own (a word space is ~0.2, after a sentence ~0.4)
COLUMN_RATIO = 2.5  # ... when it is also this many times the line's ordinary gaps
SENTENCE_GAP = 1.5  # after a sentence's full stop, only a gap this wide starts a column
ALIGNED_GAP = 0.3  # gap / text height that starts a column where other lines' column words start
ALIGNED_RATIO = 1.5
ALIGN_POINTS = 1.5  # words starting within this many points of each other share a column
MIN_GUTTER = 2  # spaces kept between a column's first word and the word before it


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def height(self) -> float:
        return max(self.y1 - self.y0, 0.1)


_PAGE = re.compile(r"<page\b[^>]*>(.*?)</page>", re.S)
_WORD = re.compile(r'<word xMin="([-\d.]+)" yMin="([-\d.]+)" xMax="([-\d.]+)" yMax="([-\d.]+)">(.*?)</word>', re.S)


def bbox_pages(markup: str) -> list[list[Box]]:
    """The word boxes of each page, in page order, from ``pdftotext -bbox`` output."""
    return [[Box(float(a), float(b), float(c), float(d), html.unescape(t)) for a, b, c, d, t in _WORD.findall(page)]
            for page in _PAGE.findall(markup)]


def _rows(boxes: list[Box]) -> list[list[Box]]:
    """Words grouped into printed rows (vertical centres within 0.4 of the text height), top to bottom, left to right."""
    rows: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: ((b.y0 + b.y1) / 2, b.x0)):
        centre = (box.y0 + box.y1) / 2
        if rows:
            first = rows[-1][0]
            if abs((first.y0 + first.y1) / 2 - centre) <= 0.4 * min(first.height, box.height):
                rows[-1].append(box)
                continue
        rows.append([box])
    return [sorted(row, key=lambda b: b.x0) for row in rows]


@dataclass(eq=False)
class _Word:
    line: int
    start: int  # character offsets in the (growing) line
    end: int
    box: Box
    previous: "_Word | None"
    gap: float = 0.0  # the gap before it as a share of the text height (0 for a line's first word)
    ordinary: float = 0.0  # the line's other gaps (their median), the same way
    strong: bool = False  # starts a column on its own
    weak: bool = False  # starts a column if other lines' column words start at its position
    short_line: bool = False  # too few other gaps on its line to compare with (a heading in spaced-out capitals)


def _match(line: str, row: list[Box]) -> list[tuple[int, int, Box]] | None:
    """The row's words that make up ``line`` (left to right, skipping words printed on another text line), with
    their character offsets in it, or None unless every character of the line is matched."""
    at, spans = 0, []
    for box in row:
        while at < len(line) and line[at].isspace():
            at += 1
        if line.startswith(box.text, at):
            spans.append((at, at + len(box.text), box))
            at += len(box.text)
    return spans if not line[at:].strip() else None


def _place(lines: list[str], boxes: list[Box]) -> list[list[_Word]]:
    """Each text line's words with their boxes, or [] for a line that doesn't match the boxes of a printed row.

    pdftotext can print one row as two lines (a label set between two body lines, say), so a line may take
    only some of a row's words; the rest stay for the lines after it.
    """
    rows = _rows(boxes)
    placed: list[list[_Word]] = [[] for _ in lines]
    cursor, pending = 0, None  # the next row to look at, and a row whose other words may be on the next line
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        spans = None
        for r in ([pending] if pending is not None else []) + list(range(cursor, min(cursor + 4, len(rows)))):
            spans = _match(line, rows[r])
            if spans:
                used = {id(box) for _, _, box in spans}
                rows[r] = [box for box in rows[r] if id(box) not in used]
                cursor = max(cursor, r + 1)
                pending = r if rows[r] else None
                break
        else:
            pending = None
        if not spans:
            continue
        previous = None
        for start, end, box in spans:
            word = _Word(index, start, end, box, previous)
            if previous is not None:
                word.gap = (box.x0 - previous.box.x1) / min(box.height, previous.box.height)
            placed[index].append(word)
            previous = word
    gaps = [w.gap for words in placed for w in words if w.previous is not None]
    typical = median(gaps) if gaps else 0.0
    for words in placed:
        for word in words[1:]:
            others = [w.gap for w in words[1:] if w is not word]
            word.ordinary = max(median(others) if len(others) >= 2 else typical, 0.1)
            word.short_line = len(others) < 2
            sentence = word.previous.box.text.endswith((".", "?", "!")) and word.gap < SENTENCE_GAP
            word.strong = word.gap >= COLUMN_GAP and word.gap >= COLUMN_RATIO * word.ordinary and not sentence
            word.weak = word.strong or (word.gap >= ALIGNED_GAP and word.gap >= ALIGNED_RATIO * word.ordinary
                                        and not sentence)
    return placed


# What already marks the end of a label or list marker, so a single space after it hides no column:
# "SUBJECT:", "1.", "a)", "A2.", a bullet, an "o" sub-bullet.
_MARKED = re.compile(r"(?::|^[^\w\s]|^o|^\(?[A-Za-z0-9]{1,3}[.)])$")


def _collapsed(word: _Word) -> bool:
    """A column's word printed a single space after the word before it, with nothing else to show the column."""
    return word.weak and word.start - word.previous.end == 1 and not _MARKED.search(word.previous.box.text)


def _pad(lines: list[str], placed: list[list[_Word]], word: _Word, column: int) -> None:
    """Spaces before ``word`` so it starts at ``column``; the rest of its line moves with it."""
    pad = column - word.start
    if pad <= 0:
        return
    line = lines[word.line]
    lines[word.line] = line[:word.start] + " " * pad + line[word.start:]
    for later in placed[word.line]:
        if later.start >= word.start:
            later.start += pad
            later.end += pad


def align(text: str, boxes: list[Box]) -> str:
    """The page text with each collapsed column's words starting at one character column (spaces added only)."""
    lines = text.split("\n")
    placed = _place(lines, boxes)
    starts = sorted((w for words in placed for w in words if w.previous is None or w.weak), key=lambda w: w.box.x0)
    clusters: list[list[_Word]] = []
    for word in starts:
        if clusters and word.box.x0 - clusters[-1][0].box.x0 <= ALIGN_POINTS:
            clusters[-1].append(word)
        else:
            clusters.append([word])
    for cluster in clusters:  # left to right, so a column's padding is settled before the columns right of it
        if not any(w.previous is not None and _collapsed(w) for w in cluster):
            continue
        if len({w.line for w in cluster}) >= 2 and any(w.strong for w in cluster):
            target = max(max(w.start for w in cluster),
                         max(w.previous.end + MIN_GUTTER for w in cluster if w.previous is not None))
            for word in cluster:
                _pad(lines, placed, word, target)
        else:
            for word in cluster:
                if word.previous is not None and word.strong and not word.short_line and _collapsed(word):
                    _pad(lines, placed, word, word.previous.end + MIN_GUTTER)
    return "\n".join(lines)
