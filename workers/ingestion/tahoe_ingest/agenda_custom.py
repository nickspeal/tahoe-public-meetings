"""Hand-written parsers for agenda formats the outline engine does not describe.

A layout in ``config/agenda-layouts.json`` selects one of these with
``"parser"``. The layout still declares its label and cover like any other,
and the parser runs only on a document whose cover matched. Each parser returns
``(items, warning)`` like ``outline.parse_layout`` and publishes nothing unless
the whole agenda validates.

* ``ntcd-table``: NTCD prints its agenda as a table whose item numbers are
  vertically centered beside multi-line cells, so a cell's first line (its
  title) sits above the number.
* ``south-shore-tma-bullets``: South Shore TMA lists items as bullets under
  unnumbered headings.
* ``ttd-sections``, ``slt-council``, ``truckee-council``: the original TTD,
  South Lake Tahoe City Council, and Truckee Town Council parsers, which predate
  the outline engine. Their title wrapping, quoting, and section naming differ
  from the engine's in small ways (TTD quotes span its page-number column; SLT
  publishes its section headings as items; Truckee names the section of item 5
  and its subitems "Joint Meeting"), so they are kept as written rather than
  approximated by a layout.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .outline import CONNECTOR, ROMAN, ZERO_WIDTH, continues_on_next_page, section_name, title_case

UNVALIDATED = "Agenda structure or source quote could not be validated; items require review."

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


@dataclass
class Line:
    page: int
    text: str
    start: int
    end: int
    source: str


def _lines(pages: list[dict], *, first_pages: int = 12) -> list[Line]:
    rows = []
    for page in sorted(pages, key=lambda p: int(p["page_number"]))[:first_pages]:
        text = page["text"]
        for match in re.finditer(r"[^\n]+", text):
            if match.group().strip():
                rows.append(Line(int(page["page_number"]), match.group(), match.start(), match.end(), text))
    return rows


def _quote(lines: list[Line], start: int, end: int, prefix_start: int | None = None) -> str:
    """The exact source text of lines ``start``..``end``, cut at the first page break."""
    first = lines[start]
    while lines[end].page != first.page:
        end -= 1
    return first.source[(first.start + (prefix_start or 0)):lines[end].end].strip()[:300]


def _title(lines: list[Line], first: int, last: int, offset: int = 0) -> str:
    parts = [lines[first].text[offset:].strip()] + [line.text.strip() for line in lines[first + 1:last + 1]]
    return " ".join(" ".join(parts).split())


def _turns_page(lines: list[Line], first: int, last: int, offset: int, following: int) -> bool:
    """Whether a title on lines ``first``..``last`` carries over to line ``following`` on the next page."""
    return (lines[last].page == lines[first].page and lines[following].page != lines[first].page
            and continues_on_next_page(_title(lines, first, last, offset), lines[following].text))


def _item_at(number: str | None, order: int, section: str | None, title: str,
             document_id: str, page: int, quote: str) -> dict:
    return {"source_number": number, "order": order, "section": section, "title": title,
            "source_pages": [{"document_id": document_id, "page_number": page, "quote": quote}]}


def _checked(items: list[dict], complete: bool, warning: str | None = None) -> tuple[list[dict], str | None]:
    if not complete or not items or any(not i["source_pages"][0]["quote"] for i in items):
        return [], warning or UNVALIDATED
    return items, None


def parse_slt(document_id: str, pages: list[dict]) -> tuple[list[dict], str | None]:
    lines = _lines(pages)
    headings = {
        "Call to Order/Pledge of Allegiance", "Roll Call", "Closed Session Report", "Proclamations",
        "Public Communications", "City Commission Reports", "Consent Agenda", "Regular-Unfinished Business",
        "Regular-New Business", "Agenda Planning Calendar", "Updates and Announcements",
        "Adjournment", "Presentations", "Public Hearing", "Public Hearings", "Closed Session",
        # Special meetings use shorter headings.
        "Call to Order / Pledge of Allegiance", "Consent", "New Business", "Unfinished Business",
    }
    items = []
    section = "Opening"
    numbered = []
    seen_agenda = False
    for idx, line in enumerate(lines):
        content = line.text.strip()
        number_match = re.match(r"^(\d{1,2})\.\s+(.+)", content)
        number_only = re.fullmatch(r"(\d{1,2})\.", content)
        if number_match or number_only:
            num = int((number_match or number_only).group(1))
            if not 1 <= num <= 100 or (numbered and num != numbered[-1] + 1) or (not numbered and num != 1):
                return _checked([], False, "South Lake Tahoe agenda numbering is not sequential; review required.")
            title_idx = idx
            first_title = number_match.group(2) if number_match else ""
            if number_only:
                title_idx = idx + 1
                if title_idx >= len(lines) or lines[title_idx].page != line.page:
                    return _checked([], False, "South Lake Tahoe standalone item number has no same-page title; review required.")
                first_title = lines[title_idx].text.strip()
                if (not first_title or first_title in headings or
                        re.match(r"^(?:\d{1,2}\.\s*|Requested Action|Responsible Staff|CEQA Determination)", first_title, re.I)):
                    return _checked([], False, "South Lake Tahoe standalone item title was not recognized; review required.")
            numbered.append(num)
            start_offset = lines[title_idx].text.index(first_title)

            def title_ends(j: int) -> bool:
                text = lines[j].text.strip()
                return bool(re.match(r"^\d{1,2}\.\s*", text) or text in headings or re.match(
                    r"^(Requested Action|Responsible Staff|CEQA Determination|Item \d+\s*[-–]|\d{2}[-_])", text, re.I))

            def details_follow(j: int) -> bool:
                # The title's remaining lines lead straight into the item's details.
                k = next((k for k in range(j, min(j + 4, len(lines))) if title_ends(k)), None)
                return k is not None and k > j and lines[k].page == lines[j].page and \
                    bool(re.match(r"^(Requested Action|Responsible Staff|CEQA Determination)", lines[k].text.strip(), re.I))

            end = title_idx
            for j in range(title_idx + 1, len(lines)):
                if lines[j].page != lines[end].page and not (
                        _turns_page(lines, title_idx, end, start_offset, j)
                        or (lines[end].page == lines[title_idx].page and details_follow(j))):
                    break
                if title_ends(j):
                    break
                end = j
            title = _title(lines, title_idx, end, start_offset)
            quote = _quote(lines, title_idx, end, start_offset)
            items.append(_item_at(str(num), len(items) + 1, section, title, document_id, lines[title_idx].page, quote))
            seen_agenda = True
            continue
        if content.startswith("Councilmember Reports and Comments"):
            title = "Councilmember Reports and Comments - Assignments to Boards, Committees, and Commissions"
            items.append(_item_at(None, len(items) + 1, title, title, document_id, line.page, content))
            continue
        if content.startswith("Updates and Announcements"):
            items.append(_item_at(None, len(items) + 1, content, content, document_id, line.page, content))
            continue
        if content in headings:
            if content in {"Proclamations", "Presentations", "Public Communications", "City Commission Reports",
                           "Consent Agenda", "Consent", "Public Hearing", "Public Hearings", "Regular-Unfinished Business",
                           "Unfinished Business", "Regular-New Business", "New Business", "Agenda Planning Calendar",
                           "Closed Session", "Adjournment"}:
                section = content
            items.append(_item_at(None, len(items) + 1, section, content, document_id, line.page, content))
            if content == "Adjournment" and seen_agenda:
                break
    complete = bool(numbered and items and items[-1]["title"] == "Adjournment" and numbered == list(range(1, numbered[-1] + 1)))
    return _checked(items, complete, "South Lake Tahoe agenda ending or numbered sequence was not found; review required.")


def parse_truckee(document_id: str, pages: list[dict]) -> tuple[list[dict], str | None]:
    lines = _lines(pages, first_pages=8)
    items = []
    top_numbers = []
    section = "Agenda"
    started = False
    for idx, line in enumerate(lines):
        content = line.text.strip()
        match = re.match(r"^(\d+(?:\.\d+)?)\.?\s{2,}(.+)", content)
        if not match:
            continue
        number, first_title = match.groups()
        # Restrict to the leading agenda, before the first adjournment.
        if "." not in number:
            n = int(number)
            if n > 100 or (top_numbers and n != top_numbers[-1] + 1) or (not top_numbers and n != 1):
                if started:
                    return _checked([], False, "Truckee agenda numbering is not sequential; review required.")
                continue
            top_numbers.append(n)
        elif not top_numbers or not number.startswith(str(top_numbers[-1]) + "."):
            return _checked([], False, "Truckee subitem does not follow its section; review required.")
        started = True
        offset = line.text.index(first_title)
        end = idx
        if (not first_title.rstrip().endswith((".", ":"))
                and not first_title.lower().startswith(("adjournment", "public comment:"))):
            for j in range(idx + 1, len(lines)):
                candidate = lines[j]
                if candidate.page != lines[end].page and not _turns_page(lines, idx, end, offset, j):
                    break
                next_text = candidate.text.strip()
                if re.match(r"^\d+(?:\.\d+)?\.?\s{2,}", next_text) or re.match(r"^(Recommendation:|Town of Truckee:|Tahoe Forest Hospital District:)", next_text):
                    break
                end = j
                if next_text.endswith((".", ":")):
                    break
        title = _title(lines, idx, end, offset).rstrip(":")
        if title.startswith("Public Comment:"):
            title = "Public Comment"
        quote = _quote(lines, idx, end, offset)
        if number == "5" or title.lower() == "joint meeting":
            section = "Joint Meeting"
        items.append(_item_at(number, len(items) + 1, section if number.startswith("5") else "Agenda",
                              title, document_id, line.page, quote))
        if title.lower().startswith("adjournment"):
            break
    complete = bool(top_numbers and top_numbers == list(range(1, top_numbers[-1] + 1))
                    and items and items[-1]["title"].lower().startswith("adjournment"))
    return _checked(items, complete, "Truckee agenda ending or numbered sequence was not found; review required.")


def _ttd_title_part(raw: str) -> str:
    # TTD puts packet page numbers in a right-aligned column, sometimes on a
    # wrapped title line. Strip that column before joining lines into a title.
    return re.sub(r"\s{2,}(?:\d{1,3}|Page(?:\s+\d{1,3})?)$", "", raw).strip()


def parse_ttd(document_id: str, pages: list[dict]) -> tuple[list[dict], str | None]:
    lines = _lines(pages, first_pages=6)
    entries = []
    section = None
    section_title = None
    roman_numbers = []
    started = False
    for idx, line in enumerate(lines):
        content = line.text.strip()
        roman = re.match(r"^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV)\.\s+(.+)", content)
        if roman:
            numeral, title_start = roman.groups()
            value = ROMAN[numeral]
            if roman_numbers and value != roman_numbers[-1] + 1:
                return _checked([], False, "TTD agenda section numbering is not sequential; review required.")
            if not roman_numbers and value != 1:
                return _checked([], False, "TTD agenda does not start with section I; review required.")
            roman_numbers.append(value)
            section = numeral
            start_offset = line.text.index(title_start)
            end = idx
            for j in range(idx + 1, len(lines)):
                if "ADJOURNMENT" in title_start.upper() or title_start.upper().strip() == "PUBLIC INTEREST COMMENTS":
                    break
                other = lines[j]
                if other.page != line.page:
                    break
                value_text = other.text.strip()
                if value_text.startswith(("At this time", "This portion of the agenda", "COMPLIANCE WITH")):
                    break
                if re.match(r"^(?:[A-Z]|[IVX]{1,5})\.\s+", value_text) or value_text.startswith("Agenda Page"):
                    break
                if value_text and value_text == value_text.upper() and len(value_text) < 8:
                    break
                end = j
                if len(value_text) > 140:
                    break
            section_title = " ".join(filter(None, (
                _ttd_title_part(part.text[start_offset:] if part is line else part.text)
                for part in lines[idx:end + 1] if part.page == line.page)))
            section_title = " ".join(section_title.split())
            entries.append(("section", numeral, section_title, line.page, _quote(lines, idx, end, start_offset)))
            started = True
            if "ADJOURNMENT" in section_title.upper():
                break
            continue
        if started and section and re.match(r"^[A-Z]\.\s+", content):
            match = re.match(r"^([A-Z])\.\s+(.+)", content)
            letter, first_title = match.groups()
            offset = line.text.index(first_title)
            end = idx
            for j in range(idx + 1, len(lines)):
                other = lines[j]
                if other.page != line.page:
                    break
                value_text = other.text.strip()
                if re.match(r"^(?:[A-Z]|[IVX]{1,5})\.\s+", value_text) or value_text.startswith("Agenda Page"):
                    break
                if re.fullmatch(r"\d{1,3}", value_text):
                    continue
                end = j
            pieces = []
            for part in lines[idx:end + 1]:
                if part.page != line.page:
                    break
                text = part.text[offset:] if part is line else part.text
                text = _ttd_title_part(text)
                if text not in {"Page", ""} and not re.fullmatch(r"\d{1,3}", text):
                    pieces.append(text)
            title = " ".join(" ".join(pieces).split())
            title = re.sub(r"^For Possible Action:\s*", "", title, flags=re.I)
            title = re.sub(r"\s+\d{1,3}$", "", title)
            entries.append(("item", f"{section}.{letter}", title, line.page, _quote(lines, idx, end, offset)))
    if not roman_numbers or not entries or "ADJOURNMENT" not in entries[-1][2].upper():
        return _checked([], False, "TTD agenda ending was not found; review required.")
    sections_with_children = {number.split(".")[0] for kind, number, _, _, _ in entries if kind == "item"}
    items = []
    current_section_title = None
    for kind, number, title, page, quote in entries:
        if kind == "section":
            current_section_title = title_case(title)
            if number in sections_with_children:
                continue
        items.append(_item_at(number, len(items) + 1, current_section_title, title, document_id, page, quote))
    return _checked(items, len(items) >= 5 and roman_numbers == list(range(1, roman_numbers[-1] + 1)))


# Parser names are referenced by ``"parser"`` in config/agenda-layouts.json.
PARSERS = {"ntcd-table": parse_ntcd, "south-shore-tma-bullets": parse_south_shore_tma,
           "ttd-sections": parse_ttd, "slt-council": parse_slt, "truckee-council": parse_truckee}
