"""Agenda layouts that are not numbered outlines.

* NTCD prints its agenda as a table whose item numbers are vertically
  centered beside multi-line cells, so a cell's first line (its title) sits
  above the number.
* South Shore TMA lists items as bullets under unnumbered headings.

Both return ``(items, warning)`` like ``outline.parse_layout`` and publish
nothing unless the whole agenda validates.
"""
from __future__ import annotations

import re

from .outline import CONNECTOR, ZERO_WIDTH, section_name

NTCD_REGION_END = re.compile(
    r"^(?:Posting of Agenda|How to Get Copies|Possible Changes|Public Comment\.|Members of the public|Additionally, persons|"
    r"Forum Restrictions|Responses to Public|Special Accommodations|Consent Calendar$|Administrative Agenda$|The consent calendar)")
NTCD_NUMBER = re.compile(r"^\s{0,3}(\d{1,2})(?=\s{2,}|$)")
NTCD_LETTER = re.compile(r"^\s{4,16}([A-Z])(?=\s{2,}|$)")
PAGE_COLUMN = re.compile(r"\s{3,}\d{1,3}\s*$")


def _page_lines(pages: list[dict], first_pages: int):
    for page in sorted(pages, key=lambda p: int(p["page_number"]))[:first_pages]:
        text = page["text"]
        position = 0
        for raw in text.split("\n"):
            yield int(page["page_number"]), raw, text
            position += len(raw) + 1


def parse_ntcd(document_id: str, pages: list[dict]) -> tuple[list[dict], str | None]:
    head = "\n".join(p["text"] for p in pages[:1])
    if "Board Meeting Agenda" not in head or "Agenda Item" not in head:
        return [], "NTCD agenda cover was not recognized; agenda items require review."
    # Collect table regions: the rows after each "Agenda Item" header.
    regions: list[list[tuple[int, str, str]]] = []
    current = None
    ended = False
    for page, raw, source in _page_lines(pages, 4):
        clean = PAGE_COLUMN.sub("", ZERO_WIDTH.sub("", raw)).rstrip()
        stripped = clean.strip()
        if "Agenda Item" in clean:
            current = []
            regions.append(current)
            continue
        if current is None:
            continue
        if NTCD_REGION_END.match(stripped):
            current = None
            continue
        if not stripped:
            continue
        current.append((page, raw, clean))
        if re.match(r"^\d{1,2}\s{2,}Adjournment\b", stripped) or stripped == "Adjournment":
            ended = True
            break
    if not ended:
        return [], "NTCD agenda ending was not found; review required."
    cells = []
    for region in regions:
        marks = [i for i, (_, _, clean) in enumerate(region) if NTCD_NUMBER.match(clean) or NTCD_LETTER.match(clean)]
        if not marks:
            continue
        cells.extend(_partition(region, marks))
    items = []
    numbers: list[int] = []
    letters: list[int] = []
    parent_title = None
    parent_children = 0
    pending_parent = None
    for label, lines in cells:
        title, quote, page = _cell_title(lines)
        if not title:
            return [], f"NTCD item {label} has no title; review required."
        if label.isdigit():
            if pending_parent is not None and parent_children == 0:
                items.append(pending_parent)
            value = int(label)
            if value != (numbers[-1] + 1 if numbers else 1):
                return [], f"NTCD agenda numbering is not sequential at item {label}; review required."
            numbers.append(value)
            letters = []
            parent_title, parent_children = title, 0
            pending_parent = _item(label, None, title, document_id, page, quote)
        else:
            value = ord(label) - 64
            if not numbers or value != (letters[-1] + 1 if letters else 1):
                return [], f"NTCD subitem {label} is out of sequence; review required."
            letters.append(value)
            parent_children += 1
            items.append(_item(f"{numbers[-1]}.{label}", section_name(parent_title), title, document_id, page, quote))
    if pending_parent is not None and parent_children == 0:
        items.append(pending_parent)
    if not items or not items[-1]["title"].lower().startswith("adjournment") or len(items) < 4:
        return [], "NTCD agenda ending or numbered sequence was not found; review required."
    for order, item in enumerate(items, 1):
        item["order"] = order
    return items, None


def _partition(region, marks):
    """Split region rows into one cell per marker, assuming each marker is
    vertically centered in its cell (rows above ~= rows below)."""
    n = len(region)
    gaps = [marks[i + 1] - marks[i] - 1 for i in range(len(marks) - 1)]
    first_above = marks[0]
    last_below = n - 1 - marks[-1]

    def cost(above, below, first_row):
        penalty = abs(above - below)
        if above and first_row is not None:
            text = region[first_row][2].strip()
            if not re.match(r"^[A-Z]", text):
                penalty += 0.5  # a title starts with a capital letter
        return penalty

    # best[i][a] = minimal cost of cells 0..i-1 given cell i has `a` rows above its marker
    best = [{first_above: (0.0, [])}]
    for i, gap in enumerate(gaps):
        nxt = {}
        for above, (total, splits) in best[i].items():
            for below in range(gap + 1):
                c = total + cost(above, below, marks[i] - above if above else None)
                a_next = gap - below
                if a_next not in nxt or c < nxt[a_next][0]:
                    nxt[a_next] = (c, splits + [below])
        best.append(nxt)
    final = min(((total + cost(a, last_below, marks[-1] - a if a else None), splits)
                 for a, (total, splits) in best[-1].items()), key=lambda x: x[0])
    splits = final[1]
    cells = []
    start = 0
    for i, mark in enumerate(marks):
        end = mark + (splits[i] if i < len(splits) else last_below)
        label_match = NTCD_NUMBER.match(region[mark][2]) or NTCD_LETTER.match(region[mark][2])
        cells.append((label_match.group(1), region[start:end + 1], mark - start))
        start = end + 1
    return [(label, [(page, raw, clean, idx == offset) for idx, (page, raw, clean) in enumerate(rows)])
            for label, rows, offset in cells]


def _cell_text(clean: str, is_marker: bool) -> str:
    if is_marker:
        clean = NTCD_NUMBER.sub("", clean, count=1) if NTCD_NUMBER.match(clean) else NTCD_LETTER.sub("", clean, count=1)
    return clean.strip()


def _cell_title(lines):
    texts = [(page, raw, _cell_text(clean, marker)) for page, raw, clean, marker in lines]
    texts = [t for t in texts if t[2]]
    if not texts:
        return "", "", None
    page, raw, first = texts[0]
    parts = [first]
    if len(texts) > 1:
        second = texts[1][2]
        if (re.match(r"^[a-z0-9$(]", second) or CONNECTOR.search(first) or first.count("(") > first.count(")")
                or (len(second) < 0.6 * len(first) and not second.endswith("."))):
            parts.append(second)
    title = " ".join(" ".join(parts).split())
    title = re.sub(r"^For Possible Action:\s*", "", title)
    quote = first if first in raw else ""
    return title, quote, page


def _item(number, section, title, document_id, page, quote):
    return {"source_number": number, "source_key": number or title, "order": 0, "section": section, "title": title,
            "source_pages": [{"document_id": document_id, "page_number": page, "quote": quote}]}


BULLET = re.compile(r"^(\s*)[•●]\s+(\S.*)$")
SUB_BULLET = re.compile(r"^\s*(?:o|▪|◦|-)\s+\S")


def parse_south_shore_tma(document_id: str, pages: list[dict]) -> tuple[list[dict], str | None]:
    head = pages[0]["text"] if pages else ""
    if "Board of Directors Agenda" not in head:
        return [], "South Shore TMA agenda cover was not recognized; agenda items require review."
    rows = [(page, raw, ZERO_WIDTH.sub("", raw).rstrip()) for page, raw, _ in _page_lines(pages, 3)]
    start = next((i for i, (_, _, clean) in enumerate(rows) if clean.strip() == "Call to Order"), None)
    if start is None:
        return [], "South Shore TMA agenda start was not found; review required."
    entries = []  # (kind, indent, text, page, quote)
    ended = False
    i = start
    while i < len(rows):
        page, raw, clean = rows[i]
        stripped = clean.strip()
        i += 1
        if not stripped or SUB_BULLET.match(clean):
            continue
        bullet = BULLET.match(clean)
        if bullet:
            text, column = bullet.group(2), len(clean) - len(bullet.group(2))
            parts = [text]
            while i < len(rows) and rows[i][0] == page and rows[i][2].strip() and not BULLET.match(rows[i][2]) \
                    and not SUB_BULLET.match(rows[i][2]) and len(rows[i][2]) - len(rows[i][2].lstrip()) >= column - 1:
                parts.append(rows[i][2].strip())
                i += 1
            entries.append(("bullet", len(bullet.group(1)), " ".join(parts), page, text if text in raw else ""))
            continue
        indent = len(clean) - len(clean.lstrip())
        if indent > 8:
            continue  # wrapped text of a sub-bullet
        entries.append(("heading", indent, stripped, page, stripped if stripped in raw else ""))
        if stripped.startswith("Adjournment"):
            ended = True
            break
    if not ended:
        return [], "South Shore TMA agenda ending was not found; review required."
    items = []
    for index, (kind, indent, text, page, quote) in enumerate(entries):
        title = _clean_tma(text)
        if kind == "heading":
            following = entries[index + 1] if index + 1 < len(entries) else None
            if following and (following[0] == "bullet" or (following[0] == "heading" and following[1] > indent)):
                continue  # a section heading; its bullets or subheadings are the items
            items.append(_item(None, None, title, document_id, page, quote))
            continue
        parent = next((e for e in reversed(entries[:index]) if e[0] == "heading"), None)
        grand = next((e for e in reversed(entries[:index]) if e[0] == "heading" and parent and e[1] < parent[1]), None)
        section = _clean_tma(parent[2]) if parent else None
        if grand and parent and parent[1] > 0:
            section = f"{_clean_tma(grand[2])}: {section}"
        items.append(_item(None, section, title, document_id, page, quote))
    if len(items) < 4 or not items[0]["title"].startswith("Call to Order") or not items[-1]["title"].startswith("Adjournment"):
        return [], "South Shore TMA agenda structure was not recognized; review required."
    for order, item in enumerate(items, 1):
        item["order"] = order
        item["source_key"] = f"unnumbered-{order}"
        if not item["source_pages"][0]["quote"]:
            return [], "South Shore TMA item has no exact source quote; review required."
    return items, None


def _clean_tma(text: str) -> str:
    text = re.sub(r"\s*\(Attachments? #[^)]*\)", "", text)
    text = re.sub(r"\s*[–-]\s*FOR (?:INFORMATION ONLY|POSSIBLE ACTION|ACTION)\s*$", "", text)
    return " ".join(text.split()).rstrip(":")


CUSTOM_PARSERS = {"ntcd": parse_ntcd, "south-shore-tma": parse_south_shore_tma}
