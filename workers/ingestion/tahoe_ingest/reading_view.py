"""A reading view of ``pdftotext -layout`` page text, for the summary model and its validator (decision 0024).

Stored page text keeps the PDF's physical layout. Three features of that layout break
sentences that a reader sees as whole:

- **Side labels.** Forms such as the Nevada water board's loan commitments print a
  label column beside the body ("System Infrastructure  Elk Point ... is supported
  by two wells,"), and pdftotext puts each label on the same line as the body text
  beside it, so "for the project" lands in the middle of a sentence.
- **Tables of wrapped cells.** A table whose cells hold sentences (the Fish and Game
  Commission's requests and staff recommendations) is printed row by row, so each
  line joins pieces of two or more sentences.
- **Words hyphenated across a line break** ("recom-" / "mendation").

``view`` rewrites a page into a reading order: in a block whose lines share a body
column with labels to its left, the labels come first, each on its own line, then
the body lines; a table of wrapped cells is read column by column; and a word split
by a hyphen at the end of a line is joined when the joined word is printed elsewhere
in the report. Nothing else changes, every character of the view comes from the page,
and ``View.span`` maps a passage of the view back to the page's own text, so
citations still quote the stored page (which is what the export and the website
show). The stored text itself is never rewritten: mapping rules, fingerprints, and
exports keep reading it as before.

The label rule is conservative on purpose. A block becomes two columns only when its
body lines start at one column, at least eight characters in, and every line that
starts further left is a short label (at most four words) followed by body text that
starts at that column. A single label line needs its body within a character of the
column, or a gap of two spaces before it; ordinary prose rarely meets either. Stronger
evidence, a label column as ``pdf_columns`` sets it apart when the page is read
(every body after a gutter of two spaces at one column, beside sentences), also
counts when the block has only two labels, no body line of its own at the column, or
a heading above it or a footer below it. A label beside a sentence that is still
running waits until the sentence ends, and a value in the label column
("$1,369,900.00") never interrupts one. A table is read by columns only when it has
gutters blank on every line and two of its columns hold wrapped sentences, so
bulleted lists, hanging labels, and tables of names and amounts stay in rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MIN_BODY_COLUMN = 8
MAX_LABEL_WORDS = 4
MAX_LABEL_CHARS = 32
MAX_PEEL = 2  # heading or footer lines taken off either end of a block to find its label column
MIN_TABLE_LINES = 3
MIN_PROSE_WORDS = 6


@dataclass(frozen=True)
class View:
    text: str
    origin: tuple[int, ...]  # for each character of ``text``, its index in ``source``
    source: str

    def span(self, start: int, end: int) -> tuple[int, int]:
        """The page's own span covering view characters ``start:end`` (labels moved out of it included)."""
        covered = self.origin[start:end]
        return min(covered), max(covered) + 1


_WORD = re.compile(r"[^\W_]+")


def vocabulary(texts) -> set[str]:
    """Words printed whole anywhere in these texts, for joining hyphenated words."""
    return {w.casefold() for text in texts for w in _WORD.findall(text)}


def view(text: str, words: set[str] | None = None) -> View:
    """The page in reading order; ``words`` (``vocabulary`` of the report) attests joined hyphenations."""
    words = vocabulary([text]) if words is None else words
    lines, offset = [], 0
    for line in text.split("\n"):
        lines.append((offset, line))
        offset += len(line) + 1
    blocks: list[list[tuple[int, str]]] = []  # runs of non-blank lines, and each blank line on its own
    for entry in lines:
        if entry[1].strip() and blocks and blocks[-1][-1][1].strip():
            blocks[-1].append(entry)
        else:
            blocks.append([entry])
    blocks = [piece for block in blocks for piece in _peeled(block)]
    splits = [_columns(block) if block[0][1].strip() else None for block in blocks]
    # A form page has several label blocks, or one with three or more labels, or two whose bodies start exactly
    # at one column after a gutter of two spaces (as ``pdf_columns`` prints a form's label column). A single
    # block with one or two looser "labels" is more often a centred heading or a hanging indent, so it is left
    # as printed.
    found = [split for split in splits if split]
    if len(found) < 2 and not any(_labelled(split, block) >= 3 or _guttered(split, block)
                                  for split, block in zip(splits, blocks) if split):
        splits = [None] * len(blocks)
    segments: list[tuple[int, str]] = []  # (offset in text, exact substring), one per output line
    for block, split in zip(blocks, splits):
        strips = _strips(block) if split is None and block[0][1].strip() else None
        segments += _table(block, strips) if strips else _block(block, split)
    return _join(segments, text, words)


def _labelled(split: list[int | None], block: list[tuple[int, str]]) -> int:
    return sum(at is not None and at < len(line) for at, (_, line) in zip(split, block))


def _one_gutter(split: list[int | None], block: list[tuple[int, str]]) -> bool:
    """Every label line's body starts at one column, after at least two spaces."""
    labelled = [(at, line) for at, (_, line) in zip(split, block) if at is not None and at < len(line)]
    return len({at for at, _ in labelled}) == 1 and all(line[at - 2:at] == "  " for at, line in labelled)


def _guttered(split: list[int | None], block: list[tuple[int, str]]) -> bool:
    """A label column as ``pdf_columns`` prints it: two or more label lines without a colon (which the looser
    rules already read), each with its body at one column after at least two spaces, beside two or more sentences
    (body lines of ``MIN_PROSE_WORDS`` words with no gaps inside, so not a row of a wider table)."""
    labelled = [(at, line) for at, (_, line) in zip(split, block) if at is not None and at < len(line)]
    if len(labelled) < 2 or not _one_gutter(split, block) or any(line[:at].rstrip().endswith(":") for at, line in labelled):
        return False
    column = labelled[0][0]
    bodies = [line[at:] for at, line in labelled] + [line for at, (_, line) in zip(split, block)
                                                     if at is None and _indent(line) == column]
    return sum("  " not in body.strip() and len(body.split()) >= MIN_PROSE_WORDS for body in bodies) >= 2


def _peeled(block: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """The block, or the block split so that a heading above a label column or a footer below it (a line from
    the margin that is too long to be a label, or one run of text) doesn't hide the column from ``_columns``.
    Only a column with three or more labels whose bodies all start at it after two spaces, or the evidence of
    ``_guttered``, is looked for this way."""
    if not block[0][1].strip() or len(block) < 3 or _columns(block) is not None:
        return [block]
    crossing = [_indent(line) < MIN_BODY_COLUMN and (not _is_label(line) or "  " not in line.strip()) for _, line in block]
    for trimmed in range(1, 2 * MAX_PEEL + 1):
        for head in range(min(trimmed, MAX_PEEL) + 1):
            tail = trimmed - head
            if tail > MAX_PEEL or not all(crossing[:head]) or not all(crossing[len(block) - tail:]):
                continue
            inner = block[head:len(block) - tail]
            split = _columns(inner) if len(inner) >= 2 else None
            if split is not None and (_guttered(split, inner) or _labelled(split, inner) >= 3 and _one_gutter(split, inner)):
                return [piece for piece in (block[:head], inner, block[len(block) - tail:]) if piece]
    return [block]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _block(block: list[tuple[int, str]], split: list[int | None] | None) -> list[tuple[int, str]]:
    if split is None:
        return [_strip(offset, line) for offset, line in block]
    # A label goes before the body line beside it, unless that line continues a body sentence (it has no label,
    # or it or its label starts in lower case: "for the project" beside "uranium levels", or its label is a
    # value: "$1,369,900.00" beside "Board for Financing Water Projects (Board) approve"); then it waits until
    # the sentence ends, so no label lands inside a sentence.
    out, waiting, open_sentence = [], [], False
    for (offset, line), at in zip(block, split):
        label = body = None
        if at is None:
            body = _strip(offset, line)
        elif at >= len(line):
            label = _strip(offset, line)
        else:
            label, body = _strip(offset, line[:at]), _strip(offset + at, line[at:])
        if label:
            waiting.append(label)
        if body is None:
            continue
        continues = open_sentence and (label is None or body[1][:1].islower() or not label[1][:1].isupper())
        if not continues:
            out += waiting
            waiting = []
        out.append(body)
        open_sentence = len(body[1].split()) >= 4 and not body[1].endswith((".", "?", "!", ":", ";"))
    return out + waiting


def _strips(block: list[tuple[int, str]]) -> list[tuple[int, int]] | None:
    """The column ranges of a table block whose cells hold wrapped sentences, or None.

    Columns are separated by gutters, runs of two or more character positions that are blank on every line of the
    block. The block counts as such a table only when two of its columns hold wrapped paragraphs: at least
    ``MIN_TABLE_LINES`` lines, two or more of them continuing a sentence (starting in lower case) with at least
    three words. A table of short cells (names, amounts, dates), a bulleted list, and a hanging label
    ("WHEREAS,") are left in rows, as printed.
    """
    if len(block) < MIN_TABLE_LINES:
        return None
    width = max(len(line) for _, line in block)
    blank = [all(c >= len(line) or line[c] == " " for _, line in block) for c in range(width)]
    ranges, start, c = [], None, 0
    while c < width:
        if not blank[c]:
            start = c if start is None else start
            c += 1
            continue
        run = c
        while run < width and blank[run]:
            run += 1
        if start is not None and (run - c >= 2 or run == width):
            ranges.append((start, c))
            start = None
        c = run
    if start is not None:
        ranges.append((start, width))
    if len(ranges) < 2:
        return None

    def wrapped(lo: int, hi: int) -> bool:
        cells = [line[lo:hi].strip() for _, line in block]
        cells = [cell for cell in cells if cell]
        return len(cells) >= MIN_TABLE_LINES and sum(cell[:1].islower() and len(cell.split()) >= 3 for cell in cells) >= 2
    return ranges if sum(wrapped(lo, hi) for lo, hi in ranges) >= 2 else None


def _table(block: list[tuple[int, str]], strips: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """A table block read column by column, each column top to bottom."""
    return [_strip(offset + lo, line[lo:hi]) for lo, hi in strips for offset, line in block if line[lo:hi].strip()]


def _columns(block: list[tuple[int, str]]) -> list[int | None] | None:
    """Where each line's body starts (None: the whole line is body; len(line): all label), or None for one column.

    Each indentation of a line at least ``MIN_BODY_COLUMN`` in is a candidate body column; the one that
    explains the most label lines wins (then the one where more bodies start exactly at it).
    """
    if len(block) < 2:
        return None
    best, best_score = None, None
    indents = {_indent(line) for _, line in block if _indent(line) >= MIN_BODY_COLUMN}
    # Where every line has a label (no body line starts at the column), the column is where bodies start after
    # a gutter of two spaces; it counts only with the evidence of ``_guttered``.
    gutters = {m.end() for _, line in block for m in [_GUTTER.search(line)] if m and m.end() >= MIN_BODY_COLUMN} - indents
    for column in sorted(indents | gutters):
        found = _split_at(block, column)
        if found and (column in indents or _guttered(found[0], block)) and (best_score is None or found[1] > best_score):
            best, best_score = found[0], found[1]
    return best


_GUTTER = re.compile(r"(?<=\S)  +(?=\S)")


def _split_at(block: list[tuple[int, str]], column: int):
    split, labelled, exact = [], 0, 0
    for index, (_, line) in enumerate(block):
        indent = _indent(line)
        if indent >= column - 1:
            split.append(None)
            continue
        at = _label_end(line, column)
        if at is None:
            return None
        if at < len(line):
            gap = line[at - 2:at] == "  "
            following = block[index + 1][1] if index + 1 < len(block) else ""
            # Its body must run on to the next line, starting at the column in lower case: a centred
            # heading over a centred subtitle does not.
            flows = (abs(_indent(following) - column) <= 1 and "  " not in following.strip()
                     and following.strip()[:1].islower())
            if index == 0 and not gap and (len(line[at:].split()) == 1 or not flows):
                split.append(None)  # a heading that runs into the body column ("Elk Point Country Club HOA")
                continue
            labelled += 1
            exact += abs(at - column) <= 1 or gap
        split.append(at)
    if not labelled or 2 * exact < labelled:
        return None
    # The body must flow: a label line's body continues on the next line, which starts at the column and is
    # prose (no table gaps). Tables and prose that happens to have an indented line below it fail here.
    lines = [line for _, line in block]
    continuation = [i for i, line in enumerate(lines) if split[i] is None and abs(_indent(line) - column) <= 1]
    if any("  " in lines[i].strip() for i in continuation):
        return None
    if not any(i - 1 >= 0 and split[i - 1] is not None and split[i - 1] < len(lines[i - 1]) and len(lines[i].split()) >= 3
               for i in continuation) and not _guttered(split, block):  # a guttered column needn't show the flow
        return None
    return split, (labelled, exact, column)


def _label_end(line: str, column: int) -> int | None:
    """Where the body starts on a line that begins with a label, len(line) for a label alone, or None."""
    if len(line.rstrip()) < column and _is_label(line):
        return len(line)
    for at in range(max(1, column - 3), column + 2):
        if at < len(line) and line[at - 1] == " " and line[at] != " " and _is_label(line[:at]):
            return at
    return None


def _is_label(text: str) -> bool:
    text = text.strip()
    return (bool(text) and len(text) <= MAX_LABEL_CHARS and len(text.split()) <= MAX_LABEL_WORDS
            and sum(c.isalnum() for c in text) >= 2 and not _LIST_MARKER.match(text.split()[0]) and not text.endswith((",", ";")))


_LIST_MARKER = re.compile(r"^(?:\(?[a-zA-Z]{0,2}[0-9]{1,3}[.)]|\(?[a-zA-Z][.)]|\(?[ivxIVX]{1,4}[.)])$")


def _strip(offset: int, line: str) -> tuple[int, str]:
    lead = len(line) - len(line.lstrip())
    return offset + lead, line.strip()


_HYPHEN_END = re.compile(r"([^\W\d_]+)-$")
_WORD_START = re.compile(r"^([^\W\d_]+)")


def _join(segments: list[tuple[int, str]], text: str, words: set[str]) -> View:
    out: list[str] = []
    origin: list[int] = []
    for index, (offset, line) in enumerate(segments):
        if index:
            previous = out[-1] if out else ""
            end = _HYPHEN_END.search("".join(out[-1:]))
            start = _WORD_START.match(line)
            if (end and start and line[:1].islower()
                    and (end.group(1) + start.group(1)).casefold() in words):
                out[-1] = previous[:-1]
                origin.pop()  # the hyphen
                out.append(line)
                origin.extend(range(offset, offset + len(line)))
                continue
            out.append("\n")
            origin.append(origin[-1] + 1 if origin else 0)
        out.append(line)
        origin.extend(range(offset, offset + len(line)))
    joined = "".join(out)
    return View(joined, tuple(min(o, max(len(text) - 1, 0)) for o in origin), text)
