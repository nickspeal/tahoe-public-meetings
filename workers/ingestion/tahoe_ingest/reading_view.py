"""A reading view of ``pdftotext -layout`` page text, for the summary model and its validator (decision 0024).

Stored page text keeps the PDF's physical layout. Two features of that layout break
sentences that a reader sees as whole:

- **Side labels.** Forms such as the Nevada water board's loan commitments print a
  label column beside the body ("System Infrastructure  Elk Point ... is supported
  by two wells,"), and pdftotext puts each label on the same line as the body text
  beside it, so "for the project" lands in the middle of a sentence.
- **Words hyphenated across a line break** ("recom-" / "mendation").

``view`` rewrites a page into a reading order: in a block whose lines share a body
column with labels to its left, the labels come first, each on its own line, then
the body lines; and a word split by a hyphen at the end of a line is joined when the
joined word is printed elsewhere in the report. Nothing else changes, every
character of the view comes from the page, and ``View.span`` maps a passage of the
view back to the page's own text, so citations still quote the stored page (which
is what the export and the website show). The stored text itself is never
rewritten: mapping rules, fingerprints, and exports keep reading it as before.

The label rule is conservative on purpose. A block becomes two columns only when its
body lines start at one column, at least eight characters in, and every line that
starts further left is a short label (at most four words) followed by body text that
starts at that column. A single label line needs its body within a character of the
column, or a gap of two spaces before it; ordinary prose rarely meets either.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MIN_BODY_COLUMN = 8
MAX_LABEL_WORDS = 4
MAX_LABEL_CHARS = 32


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
    splits = [_columns(block) if block[0][1].strip() else None for block in blocks]
    # A form page has several label blocks, or one with three or more labels. A single block with one or two
    # "labels" is more often a centred heading or a hanging indent, so it is left as printed.
    found = [split for split in splits if split]
    if len(found) < 2 and not any(_labelled(split, block) >= 3 for split, block in zip(splits, blocks) if split):
        splits = [None] * len(blocks)
    segments: list[tuple[int, str]] = []  # (offset in text, exact substring), one per output line
    for block, split in zip(blocks, splits):
        segments += _block(block, split)
    return _join(segments, text, words)


def _labelled(split: list[int | None], block: list[tuple[int, str]]) -> int:
    return sum(at is not None and at < len(line) for at, (_, line) in zip(split, block))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _block(block: list[tuple[int, str]], split: list[int | None] | None) -> list[tuple[int, str]]:
    if split is None:
        return [_strip(offset, line) for offset, line in block]
    # A label goes before the body line beside it, unless that line continues a body sentence (it or its
    # label starts in lower case: "for the project" beside "uranium levels"); then it waits until the
    # sentence ends, so no label lands inside a sentence.
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
        continues = open_sentence and (body[1][:1].islower() or (label is not None and label[1][:1].islower()))
        if not continues:
            out += waiting
            waiting = []
        out.append(body)
        open_sentence = len(body[1].split()) >= 4 and not body[1].endswith((".", "?", "!", ":", ";"))
    return out + waiting


def _columns(block: list[tuple[int, str]]) -> list[int | None] | None:
    """Where each line's body starts (None: the whole line is body; len(line): all label), or None for one column.

    Each indentation of a line at least ``MIN_BODY_COLUMN`` in is a candidate body column; the one that
    explains the most label lines wins (then the one where more bodies start exactly at it).
    """
    if len(block) < 2:
        return None
    best, best_score = None, None
    for column in sorted({_indent(line) for _, line in block if _indent(line) >= MIN_BODY_COLUMN}):
        found = _split_at(block, column)
        if found and (best_score is None or found[1] > best_score):
            best, best_score = found[0], found[1]
    return best


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
               for i in continuation):
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


_LIST_MARKER = re.compile(r"^(?:\(?[0-9]{1,3}[.)]|\(?[a-zA-Z][.)]|\(?[ivxIVX]{1,4}[.)])$")


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
