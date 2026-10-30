"""Deterministic outline parser for agenda layouts.

Each layout (configured in ``config/agenda-layouts.json``, see
``agenda_layouts``) describes how an agenda format numbers items: the marker
style at each outline level, where the agenda ends, and which page furniture
to ignore. The parser walks the extracted page text once and publishes items
only when the whole outline validates:

* the layout's cover text is present;
* every level counts sequentially from its first value within its parent
  (a skipped number is accepted only when the layout allows it and the
  missing label appears nowhere in the agenda);
* the agenda reaches its ending marker (normally "Adjournment"); and
* every title has an exact quote from one extracted page (a title that
  carries over a page break is quoted from the page where it starts).

Unnumbered ALL-CAPS headings name the section of the items below them. A
heading may carry a parenthetical note ("DEPARTMENT MATTERS (Items in this
category may be called at any time)"). A standalone time line ("10:00 A.M. -
TIME ALLOCATION") ends the section, because its items are scheduled on their
own. Items in an addendum printed after the ending marker join the section of
the same name, after that section's last item.

Anything else returns an incomplete result with a reason, so the meeting
keeps its text and official links but no guessed items.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .time_certain import time_heading

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10,
         "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18,
         "XIX": 19, "XX": 20}
ZERO_WIDTH = re.compile("[​‌‍﻿]")
CONNECTOR = re.compile(r"(?:[,;(&/\-–—]|\b(?:and|or|of|the|for|to|in|on|at|with|by|from|a|an|as|its|their|regarding|between))\s*$", re.I)


def continues_on_next_page(title: str, next_line: str) -> bool:
    """Whether a title that reaches the bottom of a page carries on at the top of the next.

    Page breaks usually fall between items, so the next page's first line joins
    the title only when the title plainly reads as unfinished: it ends in a
    connecting word or punctuation or inside parentheses, or the next line
    begins in lowercase (and is not an "a." or "ii)" list label).
    """
    return bool(CONNECTOR.search(title) or title.count("(") > title.count(")")
                or re.match(r"^[a-z](?![a-z]{0,3}[.)]\s)", next_line.strip()))


@dataclass(frozen=True)
class Level:
    """One outline level. ``pattern`` must capture the label as group ``v``."""
    kind: str  # num | upper | lower | roman | lroman | free (compound labels; prefix must match the parent)
    pattern: str
    item: bool = True  # False: tracked for numbering only (e.g. recommended actions)


NUM = Level("num", r"(?P<v>\d{1,2})\.(?=\s|$)")
NUM_LOOSE = Level("num", r"(?P<v>\d{1,2})(?:\.|(?=\s{2,}))(?=\s|$)")
UPPER = Level("upper", r"(?P<v>[A-Z])\.(?=\s|$)")
LOWER = Level("lower", r"(?P<v>[a-z])\.(?=\s|$)")
ROMAN_LEVEL = Level("roman", r"(?P<v>[IVX]{1,5})\.(?=\s|$)")


@dataclass(frozen=True)
class Layout:
    name: str  # the format's key in config/agenda-layouts.json
    label: str  # names the format in warnings ("NTPUD Board agenda numbering is not sequential ...")
    cover: tuple[str, ...]  # regexes that must all match the first ``cover_pages`` pages
    levels: tuple[Level, ...] = ()
    cover_pages: int = 2
    # Regexes for the publisher's agenda when ``cover`` fails (a redesigned cover): such a
    # document blocks publication with a cover warning instead of being ignored as an attachment.
    recognize: tuple[str, ...] = ()
    parser: str | None = None  # a hand-written parser (``agenda_custom.PARSERS``) instead of the outline engine
    first_pages: int = 8
    start: str | None = None  # regex; ignore everything before the first matching line
    end: str = r"(?i)^adjourn(?!\w*\s+(?:to\s+closed|as\b))"
    skip: str | None = None
    prefix: str | None = None
    suffix: str | None = None
    column_header: str | None = None
    headings: str | None = None
    heading_items: str | None = None
    stop: str | None = None
    wrap: str = "indent"  # indent | lowercase | any | none
    max_title_lines: int = 3
    title_cleanup: tuple[tuple[str, str], ...] = ()
    caps_colon: bool = False
    column_gap: bool = False  # a run of 3+ spaces ends the title column (tables without a header)
    title_end: str | None = None  # regex on the title so far; a match ends the title
    caps_lead: bool = False  # "PUBLIC COMMENT Any person may..." -> "PUBLIC COMMENT"
    end_item: bool = True
    restart: bool = False
    section_restart: bool = False
    max_gap: int = 0
    min_items: int = 3
    indent_tolerance: int = 3
    after_end: tuple["Appendix", ...] = ()
    addendum: str | None = None  # regex for a heading after the ending marker that adds numbered items


@dataclass(frozen=True)
class Appendix:
    """Items listed after the main agenda that belong to one numbered entry,
    such as a separately printed consent calendar."""
    heading: str  # regex; group ``parent`` may name the parent label
    level: Level
    parent: str | None = None  # parent label when the heading does not carry one
    end: str | None = None


@dataclass
class Row:
    page: int
    raw: str
    start: int
    source: str
    clean: str
    trimmed: bool  # clean differs from raw beyond whitespace (columns, margins)
    page_start: bool = False  # stored page text loses the first line's indentation

    @property
    def blank(self) -> bool:
        return not self.clean.strip()

    @property
    def indent(self) -> int:
        return len(self.clean) - len(self.clean.lstrip())


@dataclass
class Entry:
    level: int
    value: int
    label: str
    title: str
    page: int
    quote: str
    parent: "Entry | None"
    children: list["Entry"] = field(default_factory=list)
    heading: str | None = None
    hidden: bool = False


class Invalid(Exception):
    pass


def value_of(kind: str, label: str) -> int:
    if kind == "free":
        return 0
    if kind == "num":
        return int(label)
    if kind in {"roman", "lroman"}:
        return ROMAN[label.upper()]
    return ord(label.lower()) - 96


def rows(layout: Layout, pages: list[dict]) -> list[Row]:
    out = []
    skip = re.compile(layout.skip) if layout.skip else None
    for page in sorted(pages, key=lambda p: int(p["page_number"]))[:layout.first_pages]:
        text = page["text"]
        first = True
        for match in re.finditer(r"[^\n]*", text):
            raw = match.group()
            if match.start() and text[match.start() - 1] != "\n":
                continue  # finditer's empty match after each line
            clean = ZERO_WIDTH.sub("", raw).replace("\t", "    ").rstrip()
            if skip and skip.search(clean.strip()):
                clean = ""
            if layout.prefix:
                clean = re.sub(layout.prefix, lambda m: " " * len(m.group()), clean, count=1)
            if layout.suffix:
                clean = re.sub(layout.suffix, "", clean).rstrip()
            trimmed = clean.strip() != raw.strip()
            out.append(Row(int(page["page_number"]), raw, match.start(), text, clean, trimmed, first and bool(clean.strip())))
            first = first and not clean.strip()
    return out


def _cleanup(layout: Layout, title: str, top: bool = True) -> str:
    title = " ".join(title.split())
    for pattern, repl in layout.title_cleanup:
        title = re.sub(pattern, repl, title)
    if layout.caps_colon and top:
        head = re.match(r"^([^a-z:]{3,}?)\s*:\s+\S", title)
        if head:
            title = head.group(1)
    if layout.caps_lead and top:
        head = re.match(r"^((?:[^a-z\s]+\s+){0,10}?[^a-z\s]*[A-Z][^a-z\s]*)\s+[-–—]?\s*(?=[A-Z][a-z]|\((?=[A-Z][a-z]))", title)
        if head and len(head.group(1)) >= 4:
            title = head.group(1)
    return title.strip(" -–—:*")


def _without_note(text: str) -> str:
    """"DEPARTMENT MATTERS (Items in this category may be called at any time)" -> "DEPARTMENT MATTERS"."""
    return re.sub(r"\s*\([^()]*\)$", "", text)


def _caps(text: str) -> bool:
    letters = [c for c in re.sub(r"\([^)]*\)", "", text) if c.isalpha()]
    return len(letters) >= 4 and all(c.isupper() for c in letters)


COMMON_SHORT = {"A", "AN", "AND", "THE", "FOR", "FROM", "WITH", "OF", "TO", "IN", "ON", "AT", "BY", "OR", "NEW", "OLD",
                "ITEM", "ACT", "PLAN", "LAND", "PARK", "LAKE", "CITY", "BIKE", "CALL", "ROLL", "OPEN", "TEAM", "AREA",
                "LONG", "HEAR", "STAFF", "BOARD", "FUND", "UNIT", "WORK", "YEAR", "LEGAL", "CODE", "ROAD", "ALL",
                "ANY", "NOT", "ITS", "OUR", "BUT", "AD", "HOC", "ONE", "TWO", "USE", "LAW", "END", "LIFE", "FIRE",
                "WATER", "MAIN", "SIDE", "TOWN", "BUS", "AIR", "FISH", "GAME", "TAX", "PAY", "RISK", "LINK", "AS"}


def section_name(title: str) -> str:
    text = re.sub(r"(?i)\s*\((?:for possible action|no action|non-action item)\)", "", title)
    text = re.sub(r"\s*[-–—:.]\s*$", "", text).strip()
    lead = re.match(r"^([^a-z]{3,}?)\s*(?::|\s[–—-])\s+(?=.*[a-z])", text)
    if lead and _caps(lead.group(1)):
        text = lead.group(1)  # "REVENUE SHARING – In approving..." -> "REVENUE SHARING"
    if not _caps(text):
        return text
    return title_case(text)


def title_case(text: str) -> str:
    """Title-case ALL-CAPS words while keeping short acronyms (SVRA, TRPA, EIR, "(TTD)")."""
    return " ".join(word if (len(re.sub(r"\W", "", word)) <= 4 and re.sub(r"\W", "", word) not in COMMON_SHORT
                             and re.sub(r"\W", "", word).isalpha()) else "-".join(p.capitalize() for p in word.split("-"))
                    for word in text.split())


class Parser:
    def __init__(self, layout: Layout, document_id: str, pages: list[dict]):
        self.layout = layout
        self.document_id = document_id
        self.rows = rows(layout, pages)
        self.patterns = [re.compile(r"^(?P<indent>\s*)" + level.pattern + r"\s*(?P<t>.*)$") for level in layout.levels]
        self.end = re.compile(layout.end)
        self.stop = re.compile(layout.stop) if layout.stop else None
        self.headings = re.compile(layout.headings) if layout.headings else None
        self.heading_items = re.compile(layout.heading_items) if layout.heading_items else None
        self.header = re.compile(layout.column_header) if layout.column_header else None
        self.end_row = -1

    # -- marker detection -------------------------------------------------
    def markers(self, row: Row) -> list[tuple[int, re.Match]]:
        found = []
        for index, pattern in enumerate(self.patterns):
            match = pattern.match(row.clean)
            if match:
                found.append((index, match))
        return found

    def is_marker(self, row: Row) -> bool:
        return any(pattern.match(row.clean) for pattern in self.patterns)

    def is_heading(self, text: str) -> bool:
        if not self.headings:
            return False
        base = _without_note(text)
        # The note form needs a worded heading, so "APN 1318-16-810-060 (Planner: ...)" stays item text.
        return bool(self.headings.search(text) or (base != text and _caps(base) and self.headings.search(base)))

    def heading_name(self, text: str) -> str | None:
        """The section a heading line opens; None for one that closes a section ("END CONSENT CALENDAR")."""
        if re.match(r"(?i)^end\b", text):
            return None
        return section_name(_cleanup(self.layout, text if self.headings.search(text) else _without_note(text)))

    # -- titles -------------------------------------------------------------
    def title(self, index: int, column: int, clip: int | None, top: bool = True) -> tuple[str, str, int]:
        """Title text, exact quote, and the last row index used."""
        layout = self.layout
        first = self.rows[index]
        gapped = False

        def segment(text: str, start: int) -> str:
            nonlocal gapped
            text = text[start:clip] if clip else text[start:]
            text = text.strip()
            if layout.column_gap:
                cut = re.search(r"\s{3,}", text)
                if cut:
                    text, gapped = text[:cut.start()], True
            return text

        parts = [segment(first.clean, column)]
        used = [index]
        title_end = re.compile(layout.title_end) if layout.title_end else None
        previous = parts[0]
        j = index
        while j + 1 < len(self.rows):
            j += 1
            if len(used) >= layout.max_title_lines or layout.wrap == "none":
                break
            if title_end and title_end.search(previous):
                break
            row = self.rows[j]
            turned = False
            if (row.page != first.page or row.blank) and self.rows[used[-1]].page == first.page \
                    and clip is None and not layout.column_gap:
                # A title that ends its page may carry on at the top of the next one.
                following = self.next_page_row(j, first.page)
                if following is not None and continues_on_next_page(" ".join(parts), self.rows[following].clean):
                    j, row, turned = following, self.rows[following], True
            text = (row.clean[:clip] if clip else row.clean).rstrip()
            stripped = text.strip()
            if (row.page != self.rows[used[-1]].page and not turned) or not stripped or self.is_marker(row):
                break
            if self.end.search(stripped) or (self.stop and self.stop.search(stripped)):
                break
            row_indent = len(text) - len(text.lstrip())
            outdented = row_indent < column - 2 or row.page_start
            if (self.is_heading(stripped) or
                    (self.heading_items and self.heading_items.search(stripped))) and (outdented or layout.wrap != "indent"):
                break
            if self.header and self.header.search(row.clean):
                break
            if layout.wrap == "indent" and row_indent < column - 2 and not turned:
                break  # (stored page text loses the indentation of a page's first line)
            if _caps(previous) and not _caps(stripped):
                break  # an ALL-CAPS heading followed by its description
            joined = " ".join(parts)
            open_paren = joined.count("(") > joined.count(")")
            if layout.wrap == "lowercase" and not (re.match(r"^[a-z0-9\[“\"'$]", stripped) or CONNECTOR.search(previous)
                                                    or open_paren or (_caps(previous) and _caps(stripped))):
                break
            stripped = segment(row.clean, 0) if layout.column_gap else stripped
            if not stripped:
                break
            parts.append(stripped)
            used.append(j)
            previous = stripped
        title = _cleanup(layout, " ".join(parts), top)
        quoted = [k for k in used if self.rows[k].page == first.page]
        modified = clip is not None or gapped or any(self.rows[k].trimmed for k in quoted)
        if modified or len(quoted) == 1:
            source_line = first.raw[:clip] if clip and not first.trimmed else first.raw
            quote = parts[0] if parts[0] in source_line else ""
        else:
            last = self.rows[quoted[-1]]
            offset = first.raw.index(parts[0]) if parts[0] in first.raw else column
            quote = first.source[first.start + offset:last.start + len(last.raw)].strip()
        return title, quote[:300], used[-1]

    def next_page_row(self, index: int, page: int) -> int | None:
        """The first text row after ``page`` when only blank rows (or skipped furniture) remain on it from ``index``."""
        k = index
        while k < len(self.rows) and self.rows[k].page == page:
            if not self.rows[k].blank:
                return None
            k += 1
        while k < len(self.rows) and self.rows[k].blank:
            k += 1
        return k if k < len(self.rows) else None

    # -- main walk ------------------------------------------------------------
    def parse(self) -> list[Entry]:
        layout = self.layout
        levels = layout.levels
        roots: list[Entry] = []
        stack: list[Entry] = []
        level_indent: dict[int, tuple[int, int, int]] = {}
        heading: str | None = None
        clip = None
        clip_page = None
        started = False
        ended = False
        consumed_until = -1
        unnumbered = 0
        start = re.compile(layout.start) if layout.start else None
        for idx, row in enumerate(self.rows):
            if start is not None:
                if start.search(row.clean.strip()):
                    start = None
                continue
            if row.page != clip_page:
                clip, clip_page = None, row.page
            if self.header:
                header = self.header.search(row.clean)
                if header:
                    clip = header.start("col") - 1
                    continue
            if idx <= consumed_until or row.blank:
                continue
            stripped = row.clean.strip()
            found = self.markers(row)
            choice = self.choose(row, found, stack, level_indent, started)
            if choice is None and found and started and not self.end.search(stripped):
                deepest = stack[-1] if stack else None
                reference = level_indent.get(deepest.level) if deepest is not None else None
                if reference and reference[2] == row.page and not row.page_start and \
                        len(found[0][1].group("indent")) > reference[0] + layout.indent_tolerance:
                    continue  # a numbered list inside an item's description
                raise Invalid(f"{layout.label} numbering is not sequential near “{stripped[:60]}”; review required.")
            if choice is not None and choice[0] == -1:
                # Dotted children ("7.1") without a printed "7." entry: the section heading names the parent.
                match = choice[1]
                parent = Entry(0, int(match.group("p")), match.group("p"), heading or f"Item {match.group('p')}",
                               row.page, "", None, heading=heading)
                roots.append(parent)
                stack = [parent]
                choice = (1, match)
            if choice is not None:
                level_index, match = choice
                if layout.restart and level_index == 0 and roots and \
                        value_of(levels[0].kind, match.group("v")) == 1 and not ended:
                    roots, stack, level_indent = [], [], {}
                if not row.page_start and match.group("t").strip():
                    level_indent[level_index] = (len(match.group("indent")), match.start("t"), row.page)
                title_row, column = idx, match.start("t")
                if not match.group("t").strip():
                    # The label sits alone on its line; the title is the next text line.
                    title_row = next((j for j in range(idx + 1, len(self.rows)) if not self.rows[j].blank), None)
                    if title_row is not None and self.rows[title_row].page == row.page and self.is_marker(self.rows[title_row]) \
                            and layout.levels[level_index].kind != "free":
                        # A numbered placeholder with no title (e.g. a withdrawn Legistar file): keep the numbering only.
                        stack = stack[:level_index]
                        placeholder = Entry(level_index, value_of(levels[level_index].kind, match.group("v")), match.group("v"),
                                            "", row.page, "", stack[-1] if stack else None, hidden=True)
                        (placeholder.parent.children if placeholder.parent else roots).append(placeholder)
                        stack.append(placeholder)
                        continue
                    if title_row is None or self.rows[title_row].page != row.page or self.is_marker(self.rows[title_row]):
                        raise Invalid(f"{layout.label} item {match.group('v')} has no title; review required.")
                    column = self.rows[title_row].indent
                title, quote, last = self.title(title_row, column, clip, top=level_index == 0)
                consumed_until = last
                if re.match(r"^(?:\d{1,2}|[A-Za-z]|[IVX]{1,5})\.\s", match.group("t")):
                    raise Invalid(f"{layout.label} item {match.group('v')} contains a second item number; review required.")
                if not title:
                    raise Invalid(f"{layout.label} item {match.group('v')} has no title; review required.")
                stack = stack[:level_index]
                parent = stack[-1] if stack else None
                entry = Entry(level_index, value_of(levels[level_index].kind, match.group("v")), match.group("v"),
                              title, row.page, quote, parent, heading=heading)
                (parent.children if parent else roots).append(entry)
                stack.append(entry)
                started = True
                if level_index == 0:
                    clip = None if not self.header else clip
                if self.end.search(title):
                    ended = True
                    self.end_row = last
                    break
                continue
            if not started:
                if self.heading_items and self.heading_items.search(stripped):
                    started = True
                else:
                    if self.is_heading(stripped):
                        heading = section_name(stripped if self.headings.search(stripped) else _without_note(stripped))
                    continue
            if self.end.search(stripped):
                if layout.end_item:
                    unnumbered += 1
                    segment = row.clean[:clip].strip() if clip else stripped
                    quote = segment if segment in row.raw else ""
                    roots.append(Entry(-1, unnumbered, "", _cleanup(layout, segment), row.page, quote, None))
                ended = True
                self.end_row = idx
                break
            if self.headings and time_heading(stripped):
                heading = None  # items heard at a stated time are not part of the section above
                continue
            if self.is_heading(stripped):
                heading = self.heading_name(stripped)
                if layout.section_restart:
                    stack = []
                continue
            if self.heading_items and self.heading_items.search(stripped):
                # Procedural headings are one line; any text after them is a description.
                segment = row.clean[:clip].strip() if clip else stripped
                unnumbered += 1
                heading = None
                roots.append(Entry(-1, unnumbered, "", _cleanup(layout, segment), row.page,
                                   segment if segment in row.raw else "", None))
                stack = stack[:1]
                continue
        if start is not None:
            raise Invalid(f"{layout.label} agenda heading was not found; review required.")
        if not started:
            raise Invalid(f"{layout.label} agenda items were not found; review required.")
        if not ended:
            raise Invalid(f"{layout.label} agenda ending was not found; review required.")
        self.appendices(roots)
        self.addendum(roots)
        return roots

    def choose(self, row, found, stack, level_indent, started):
        layout = self.layout
        options = []
        depth = len(stack)
        for level_index, match in found:
            if level_index > depth:
                continue
            level = layout.levels[level_index]
            if level.kind == "free" and level_index == 1 and stack and match.groupdict().get("p") \
                    and layout.levels[0].kind == "num" and match.group("p") == str(stack[0].value + 1):
                options.append((0, 0, -1, match))  # parent entry is implied by its children
                continue
            if level.kind == "free":
                # Compound labels such as 8.E.2 carry their parent's number; bare labels follow the open parent.
                prefix = match.groupdict().get("p")
                if level_index == 0 or level_index > depth or (prefix is not None and prefix != stack[level_index - 1].label):
                    continue
                options.append((0, 0, level_index, match))
                continue
            value = value_of(level.kind, match.group("v"))
            if level_index == depth:
                expected = 1
            else:
                expected = stack[level_index].value + 1
            gap = value - expected
            if gap > 0 and self.label_present(level_index, expected, value):
                continue  # the skipped label exists, so this is not a numbering gap in the source
            if gap < 0 or gap > layout.max_gap:
                restart = level_index == 0 and value == 1 and (layout.restart or layout.section_restart)
                if not restart:
                    continue
                gap = 0
            if level_index == 0 and not started and value != 1:
                continue
            indent, column = len(match.group("indent")), match.start("t")
            known = level_indent.get(level_index)
            if known is not None and known[2] != row.page:
                known = None  # pdftotext margins can shift between pages
            distance = 0
            if known is not None and not row.page_start:
                # Right-aligned labels (VIII.) shift the label but keep the title column.
                distance = min(abs(indent - known[0]), abs(column - known[1]) if match.group("t").strip() else 99)
                if distance > layout.indent_tolerance:
                    continue
            parent = level_indent.get(level_index - 1, (-1, -1, row.page))
            if known is None and level_index > 0 and stack and not row.page_start and parent[2] == row.page \
                    and indent <= parent[0] - 1:
                continue
            options.append((gap, distance, level_index, match))
        if not options:
            return None
        options.sort(key=lambda o: (o[0], o[1], -o[2]))
        return options[0][2], options[0][3]

    def label_present(self, level_index: int, first: int, stop: int) -> bool:
        pattern = self.patterns[level_index]
        kind = self.layout.levels[level_index].kind
        loose = None
        if kind == "num":
            # Any line that starts with a skipped number, even malformed ("12x"), means the gap is not in the source.
            loose = re.compile(r"^\s*(?:" + "|".join(str(n) for n in range(first, stop)) + r")(?!\d)")
        for row in self.rows:
            match = pattern.match(row.clean)
            if match and first <= value_of(kind, match.group("v")) < stop:
                return True
            if loose and loose.match(row.clean):
                return True
        return False

    def appendices(self, roots: list[Entry]) -> None:
        if not self.layout.after_end:
            return
        for appendix in self.layout.after_end:
            heading = re.compile(appendix.heading)
            pattern = re.compile(r"^(?P<indent>\s*)" + appendix.level.pattern + r"\s*(?P<t>.*)$")
            end = re.compile(appendix.end) if appendix.end else None
            parent = None
            children: list[Entry] = []
            clip = None
            for idx, row in enumerate(self.rows):
                if idx <= self.end_row:
                    continue
                stripped = row.clean.strip()
                if parent is not None and self.header and self.header.search(row.clean):
                    clip = self.header.search(row.clean).start("col") - 1
                    continue
                if parent is None:
                    found = heading.search(stripped)
                    if found:
                        label = found.groupdict().get("parent") or appendix.parent
                        parent = next((e for e in roots if e.label == label), None)
                        if parent is None:
                            raise Invalid(f"{self.layout.label} appendix heading does not match an agenda item; review required.")
                    continue
                if end and end.search(stripped):
                    break
                match = pattern.match(row.clean)
                if not match or not match.group("t").strip():
                    continue
                value = value_of(appendix.level.kind, match.group("v"))
                if value != len(children) + 1:
                    if children:
                        break
                    continue
                title, quote, _ = self.title(idx, match.start("t"), clip, top=False)
                children.append(Entry(parent.level + 1, value, match.group("v"), title, row.page, quote, parent))
            if parent is not None:
                if not children:
                    raise Invalid(f"{self.layout.label} appendix items were not found; review required.")
                parent.children.extend(children)


    def addendum(self, roots: list[Entry]) -> None:
        """Numbered items added after the ending marker ("ADDENDUM", then "CLOSED SESSION", "28. ...").

        Numbering continues from the main agenda. Each item is placed after the last
        main-agenda item in the section of the same name, or before the ending
        marker when the agenda has no such section. An addendum can also reprint an
        existing item with a note ("Staff recommending this item be Continued");
        that item keeps its place in the main agenda.
        """
        if not self.layout.addendum:
            return
        marker = re.compile(self.layout.addendum)
        start = next((i for i in range(self.end_row + 1, len(self.rows)) if marker.search(self.rows[i].clean.strip())), None)
        if start is None:
            return
        level = self.layout.levels[0]
        numbered = [entry for entry in roots if entry.level == 0]
        listed = {entry.label for entry in numbered}
        expected = numbered[-1].value + 1 if numbered else 1
        heading = None
        added: list[Entry] = []
        consumed = start
        for idx in range(start + 1, len(self.rows)):
            row = self.rows[idx]
            if idx <= consumed or row.blank:
                continue
            stripped = row.clean.strip()
            match = self.patterns[0].match(row.clean)
            if match and match.group("v") in listed:
                _, _, consumed = self.title(idx, match.start("t"), None)
            elif match:
                if value_of(level.kind, match.group("v")) != expected or not match.group("t").strip():
                    raise Invalid(f"{self.layout.label} addendum numbering is not sequential near “{stripped[:60]}”; review required.")
                title, quote, consumed = self.title(idx, match.start("t"), None)
                added.append(Entry(0, expected, match.group("v"), title, row.page, quote, None, heading=heading))
                expected += 1
            elif self.is_heading(stripped):
                heading = self.heading_name(stripped)
            elif time_heading(stripped):
                heading = None
        if not added and consumed == start:
            raise Invalid(f"{self.layout.label} addendum items were not found; review required.")
        for entry in added:
            same = [i for i, root in enumerate(roots) if entry.heading and (root.heading or "").casefold() == entry.heading.casefold()]
            ending = len(roots) - 1 if roots and roots[-1].level < 0 else len(roots)
            roots.insert(same[-1] + 1 if same else ending, entry)


def flatten(layout: Layout, document_id: str, roots: list[Entry]) -> list[dict]:
    items: list[dict] = []
    keys: set[str] = set()

    def has_items(entry: Entry) -> bool:
        return any(layout_level_item(layout, child) for child in entry.children)

    def number(entry: Entry) -> str | None:
        labels = []
        node = entry
        while node is not None:
            if node.label:
                labels.append(node.label)
            node = node.parent
        return ".".join(reversed(labels)) or None

    def visit(entry: Entry, section: str | None) -> None:
        if entry.hidden or (entry.level >= 0 and not layout_level_item(layout, entry)):
            return
        if entry.level >= 0 and has_items(entry):
            for child in entry.children:
                visit(child, section_name(entry.title))
            return
        source_number = number(entry)
        key = source_number or f"unnumbered-{len(items) + 1}"
        if key in keys:
            key = f"{key}@{len(items) + 1}"
        keys.add(key)
        items.append({"source_number": source_number, "source_key": key, "order": len(items) + 1,
                      "section": section, "title": entry.title,
                      "source_pages": [{"document_id": document_id, "page_number": entry.page, "quote": entry.quote}]})

    for root in roots:
        visit(root, root.heading)
    return items


def layout_level_item(layout: Layout, entry: Entry) -> bool:
    return entry.level < 0 or layout.levels[entry.level].item


def cover_matches(layout: Layout, pages: list[dict], patterns: tuple[str, ...] | None = None) -> bool:
    """Whether every cover pattern (or every one of ``patterns``) occurs in the layout's cover pages."""
    head = "\n".join(p["text"] for p in sorted(pages, key=lambda p: int(p["page_number"]))[:layout.cover_pages])
    return all(re.search(pattern, head) for pattern in (layout.cover if patterns is None else patterns))


def parse_layout(layout: Layout, document_id: str, pages: list[dict]) -> tuple[list[dict], str | None]:
    """Items for one document, or ([], reason) when the outline does not validate."""
    if not cover_matches(layout, pages):
        return [], f"{layout.label} cover was not recognized; agenda items require review."
    try:
        roots = Parser(layout, document_id, pages).parse()
    except Invalid as exc:
        return [], str(exc)
    items = flatten(layout, document_id, roots)
    page_text = {int(p["page_number"]): p["text"] for p in pages}
    if len(items) < layout.min_items:
        return [], f"{layout.label} has too few recognizable items; review required."
    for item in items:
        ref = item["source_pages"][0]
        if not item["title"] or not ref["quote"] or ref["quote"] not in page_text.get(ref["page_number"], ""):
            return [], f"{layout.label} item {item['source_number'] or item['title'][:40]} has no exact source quote; review required."
    return items, None
