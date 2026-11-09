"""Text of a Word (.docx) document, with its list numbering, for capture (standard library only).

Agencies that post agendas as Word files rely on Word's automatic list numbering:
"A. Call to Order" is stored as the paragraph text "Call to Order" plus a reference
to a list level in ``word/numbering.xml``. Agenda layouts read the printed labels,
so this module renders each numbered paragraph's label the way Word shows it,
from the list definitions (start values, number formats such as "%1." or "%1.%2",
overrides, and restarts when a higher level is used).

The document body is read in order: paragraphs, table rows (cells joined on one
line), and content controls. Headers, footers, comments, footnotes, deleted text,
and field instructions are left out. Each paragraph is one line; a numbered
paragraph is indented four spaces per list level, so outline layouts see the
nesting. Hard page breaks start a new source section; Word's own pagination is not
stored in the file, so sections are not printed pages.

Handled by file type, never by agency (decision 0027). Legacy binary .doc files
are not read here.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
WARNING = "Word document text retained as source sections; it has no PDF page numbers."
MAX_PART_BYTES = 40 * 1024 * 1024  # an uncompressed XML part larger than this is refused (zip bomb guard)
INDENT = "    "


def is_docx(head: bytes, content_type: str = "") -> bool:
    """Whether a download looks like a .docx: the declared Word type, or a zip archive (checked again on open)."""
    return "wordprocessingml" in content_type.lower() or head.startswith(b"PK\x03\x04")


def _part(archive: zipfile.ZipFile, name: str) -> ET.Element | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PART_BYTES:
        raise ValueError(f"Word document part {name} is too large to read")
    return ET.fromstring(archive.read(info))


def _val(node: ET.Element | None, tag: str) -> str | None:
    found = node.find(W + tag) if node is not None else None
    return found.get(W + "val") if found is not None else None


# ---------------------------------------------------------------- list numbering

def _roman(n: int) -> str:
    out = ""
    for value, letters in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                           (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= value:
            out, n = out + letters, n - value
    return out


def _letters(n: int) -> str:
    # Word repeats the letter past Z: 27 -> "AA", 28 -> "BB".
    return chr(ord("A") + (n - 1) % 26) * ((n - 1) // 26 + 1) if n > 0 else ""


def format_number(n: int, fmt: str) -> str:
    """One level's counter in a Word number format (``w:numFmt``); unknown formats print as decimals."""
    if fmt in ("bullet",):
        return "•"
    if fmt == "none":
        return ""
    if fmt == "upperLetter":
        return _letters(n)
    if fmt == "lowerLetter":
        return _letters(n).lower()
    if fmt == "upperRoman":
        return _roman(n)
    if fmt == "lowerRoman":
        return _roman(n).lower()
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)


class _Level:
    def __init__(self, node: ET.Element):
        self.start = int(_val(node, "start") or 1)
        self.fmt = _val(node, "numFmt") or "decimal"
        self.text = _val(node, "lvlText")
        restart = _val(node, "lvlRestart")
        self.restart = int(restart) if restart is not None else None  # None: after any higher level
        self.legal = node.find(W + "isLgl") is not None
        if self.text is None:
            self.text = ""


class Numbering:
    """Word list definitions and the running counters of one document."""

    def __init__(self, root: ET.Element | None, styles: ET.Element | None):
        self.abstract: dict[str, dict[int, _Level]] = {}
        self.nums: dict[str, tuple[str, dict[int, int], dict[int, _Level]]] = {}
        if root is not None:
            for node in root.findall(W + "abstractNum"):
                self.abstract[node.get(W + "abstractNumId")] = {int(lvl.get(W + "ilvl")): _Level(lvl) for lvl in node.findall(W + "lvl")}
            for node in root.findall(W + "num"):
                starts, levels = {}, {}
                for override in node.findall(W + "lvlOverride"):
                    ilvl = int(override.get(W + "ilvl"))
                    if (start := _val(override, "startOverride")) is not None:
                        starts[ilvl] = int(start)
                    if (lvl := override.find(W + "lvl")) is not None:
                        levels[ilvl] = _Level(lvl)
                self.nums[node.get(W + "numId")] = (_val(node, "abstractNumId"), starts, levels)
        self.style_numbering = self._style_numbering(styles)
        self.counters: dict[str, dict[int, int]] = {}  # per abstract list: level -> last number shown
        self.started: set[str] = set()  # numIds whose start overrides have been applied

    @staticmethod
    def _style_numbering(styles: ET.Element | None) -> dict[str, tuple[str | None, int | None]]:
        """Paragraph styles that carry list numbering (``w:numPr`` in the style), following ``w:basedOn``."""
        if styles is None:
            return {}
        own, parent = {}, {}
        for style in styles.findall(W + "style"):
            sid = style.get(W + "styleId")
            ppr = style.find(W + "pPr")
            numpr = ppr.find(W + "numPr") if ppr is not None else None
            if numpr is not None:
                ilvl = _val(numpr, "ilvl")
                own[sid] = (_val(numpr, "numId"), int(ilvl) if ilvl is not None else None)
            parent[sid] = _val(style, "basedOn")
        resolved = {}
        for sid in parent:
            seen, at = set(), sid
            while at and at not in own and at not in seen:
                seen.add(at)
                at = parent.get(at)
            if at in own:
                resolved[sid] = own[at]
        return resolved

    def level(self, num_id: str, ilvl: int) -> _Level | None:
        abstract_id, _, levels = self.nums.get(num_id, (None, {}, {}))
        return levels.get(ilvl) or self.abstract.get(abstract_id, {}).get(ilvl)

    def label(self, num_id: str | None, ilvl: int) -> str:
        """The label Word prints for the next paragraph of list ``num_id`` at level ``ilvl``; advances the counters."""
        if not num_id or num_id == "0" or num_id not in self.nums:
            return ""
        abstract_id, starts, _ = self.nums[num_id]
        counters = self.counters.setdefault(abstract_id or num_id, {})
        if num_id not in self.started:
            # A list instance with start overrides begins again from them (Word's "restart numbering").
            self.started.add(num_id)
            for lvl, start in starts.items():
                counters[lvl] = start - 1
        current = self.level(num_id, ilvl)
        if current is None:
            return ""
        counters[ilvl] = counters.get(ilvl, current.start - 1) + 1
        for deeper in [d for d in counters if d > ilvl]:
            spec = self.level(num_id, deeper)
            restart = spec.restart if spec else None
            # lvlRestart n restarts the level after a level numbered 1..n (one-based) is used; 0 never restarts.
            if restart is None or (restart and ilvl + 1 <= restart):
                del counters[deeper]
        def piece(match):
            lvl = int(match.group(1)) - 1
            spec = self.level(num_id, lvl)
            value = counters.get(lvl, spec.start if spec else 1)
            return format_number(value, "decimal" if current.legal else (spec.fmt if spec else "decimal"))
        if current.fmt == "bullet":
            return "•"
        return re.sub(r"%([1-9])", piece, current.text).strip()

    def paragraph(self, ppr: ET.Element | None) -> tuple[str, int]:
        """(label, list level) of a paragraph from its own ``w:numPr`` or its style's; ("", 0) when unnumbered."""
        numpr = ppr.find(W + "numPr") if ppr is not None else None
        style = _val(ppr, "pStyle") if ppr is not None else None
        num_id, ilvl = self.style_numbering.get(style, (None, None)) if style else (None, None)
        if numpr is not None:
            num_id = _val(numpr, "numId") or num_id
            if (own := _val(numpr, "ilvl")) is not None:
                ilvl = int(own)
        if num_id is None:
            return "", 0
        ilvl = ilvl or 0
        return self.label(num_id, ilvl), ilvl


# ---------------------------------------------------------------- body text

class _Reader:
    def __init__(self, numbering: Numbering):
        self.numbering = numbering
        self.sections: list[list[str]] = [[]]

    def page_break(self):
        if self.sections[-1]:
            self.sections.append([])

    def runs(self, paragraph: ET.Element) -> tuple[str, bool]:
        """A paragraph's visible text and whether a hard page break follows it."""
        parts, breaks = [], False
        def walk(node):
            nonlocal breaks
            for child in node:
                tag = child.tag
                if tag == W + "t":
                    parts.append(child.text or "")
                elif tag == W + "tab":
                    parts.append("\t")
                elif tag in (W + "br", W + "cr"):
                    if child.get(W + "type") == "page":
                        breaks = True
                    else:
                        parts.append("\n")
                elif tag in (W + "noBreakHyphen", W + "softHyphen"):
                    parts.append("-" if tag == W + "noBreakHyphen" else "")
                elif tag in (W + "del", W + "delText", W + "instrText", W + "pPr", W + "rPr", W + "footnoteReference",
                             W + "commentReference"):
                    continue
                else:
                    walk(child)
        walk(paragraph)
        return "".join(parts), breaks

    def paragraph_lines(self, paragraph: ET.Element) -> list[str]:
        ppr = paragraph.find(W + "pPr")
        if ppr is not None and ppr.find(W + "pageBreakBefore") is not None and _val(ppr, "pageBreakBefore") != "0":
            self.page_break()
        text, page_after = self.runs(paragraph)
        label, ilvl = self.numbering.paragraph(ppr)
        lines = [" ".join(line.replace("\xa0", " ").split()) for line in text.split("\n")]
        if label and text.strip():
            lines[0] = f"{label} {lines[0]}".strip()
        elif label and not text.strip():
            lines = [""]  # an empty numbered paragraph still advances Word's counter, but prints nothing useful
        if label:
            lines = [INDENT * ilvl + line if line else line for line in lines]
        return lines, page_after

    def block(self, node: ET.Element):
        for child in node:
            if child.tag == W + "p":
                lines, page_after = self.paragraph_lines(child)
                self.sections[-1].extend(lines)
                if page_after:
                    self.page_break()
            elif child.tag == W + "tbl":
                for row in child.iter(W + "tr"):
                    cells = []
                    for cell in row.findall(W + "tc"):
                        inner = _Reader(self.numbering)
                        inner.block(cell)
                        text = " ".join(line.strip() for section in inner.sections for line in section if line.strip())
                        cells.append(text)
                    line = "   ".join(c for c in cells if c)
                    self.sections[-1].append(line)
            elif child.tag in (W + "sdt", W + "sdtContent", W + "customXml", W + "smartTag", W + "ins"):
                self.block(child)


def docx_sections(raw: bytes) -> list[str]:
    """The document's text as source sections (split at hard page breaks), with list labels rendered."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("source is not a readable Word document") from exc
    with archive:
        body = _part(archive, "word/document.xml")
        if body is None:
            raise ValueError("source is a zip archive but not a Word document")
        numbering = Numbering(_part(archive, "word/numbering.xml"), _part(archive, "word/styles.xml"))
    reader = _Reader(numbering)
    content = body.find(W + "body")
    reader.block(content if content is not None else body)
    sections = []
    for lines in reader.sections:
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(line.rstrip() for line in lines)).strip()
        if text:
            sections.append(text)
    return sections


def extract_docx(raw: bytes) -> dict:
    """``download_extract``'s result for a .docx: one source section per hard page break."""
    sections = docx_sections(raw)
    if not sections:
        raise ValueError("Word document extraction produced no text")
    return {"media_type": MEDIA_TYPE, "page_count": len(sections),
            "pages": [{"page_number": n, "text": text} for n, text in enumerate(sections, 1)],
            "warnings": [WARNING]}
