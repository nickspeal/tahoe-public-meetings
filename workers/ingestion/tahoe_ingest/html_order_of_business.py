"""Agenda items from an "Order of Business" meeting page (the California State Lands Commission's format).

The meeting page is the agenda. After an ``<h1>Order of Business</h1>`` heading, each part
of the meeting is an ``<h2>`` headed with a Roman numeral ("V.  Consent Calendar 01-57"),
and the numbered items sit in tables beneath it, one row each: a ``<th>`` printing the
number ("01") and a ``<td>`` with the party in bold, linked to the item's staff report
PDF, then the description. An emergency item added to a part is a paragraph labelled
"(a)" followed by the paragraph that names it. Text before the heading (the outline and
the voting record that the page gains after the meeting) is not read.

The parse is all or nothing: Roman parts must run I, II, III…; numbered items must run
1, 2, 3… with no gap or repeat; a calendar that states its numbers ("Consent Calendar
01-57", "Informational Calendar 60") must hold exactly those, and the stated calendars
together every item; and every title must be quoted exactly from the page text. Pages
since 2026 end with an "Adjourn" part, which ends the reading; earlier ones end with
Closed Session or Public Comment.
"""
from __future__ import annotations

import re
import urllib.parse

from .html_dom import Node, document

ROMAN = re.compile(r"^(?P<label>[IVX]+)\.\s+(?P<title>\S.*)$")
LETTER = re.compile(r"^\((?P<label>[a-z])\)\s+(?P<title>\S.*)$")
RANGE = re.compile(r"\bCalendar\s+(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?$")
MAX_TITLE = 280
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10}


def roman(label: str) -> int:
    total = 0
    for symbol, following in zip(label, label[1:] + " "):
        value = _ROMAN_VALUES[symbol]
        total += -value if _ROMAN_VALUES.get(following, 0) > value else value
    return total


def recognizes(tree: Node) -> bool:
    return any(flat(n) == "Order of Business" for n in tree.find("h1"))


# Tags after which html_source.html_text breaks a line; text elsewhere runs together, as on the page.
_BREAKS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "section", "table", "td", "th", "ul", "ol"}


def flat(node: Node) -> str:
    """A node's text as ``html_text`` renders it, on one line: "<b>(</b>APPLICANT)" stays "(APPLICANT)"."""
    parts: list[str] = []

    def walk(n):
        for child in n.children:
            if isinstance(child, Node):
                if child.tag in {"script", "style", "noscript"}:
                    continue
                if child.tag in _BREAKS:
                    parts.append(" ")
                walk(child)
                if child.tag in _BREAKS:
                    parts.append(" ")
            else:
                parts.append(child)
    walk(node)
    return " ".join("".join(parts).split())


def _elements(node: Node) -> list[Node]:
    return [c for c in node.children if isinstance(c, Node)]


def _pdf_links(node: Node, base: str) -> list[str]:
    found = []
    for a in node.find("a"):
        url = urllib.parse.urljoin(base, a.attrs.get("href", ""))
        if urllib.parse.urlparse(url).path.lower().endswith(".pdf") and url not in found:
            found.append(url)
    return found


def _bounded(text: str) -> str:
    if len(text) <= MAX_TITLE:
        return text
    cut = text[:MAX_TITLE].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:")


def _item_title(cell: Node) -> tuple[str, str]:
    """(title, passage) of an item cell: the party and the description up to its CEQA note, and the whole cell."""
    passage = flat(cell)
    # Public comment letters are listed under the description; they are not part of it.
    lists = " ".join(flat(ul) for ul in cell.find("ul"))
    if lists and passage.endswith(lists):
        passage = passage[: -len(lists)].rstrip()
    first = re.split(r"\.\s+CEQA Consideration\b", passage, maxsplit=1)[0].rstrip(".")
    title = _bounded(first)
    removed = re.search(r"\b(Removed from the agenda(?: on)? \d{1,2}/\d{1,2}/\d{4})", passage)
    if removed:
        title = _bounded(title) + f" ({removed[1]})"
    return title, passage


def agenda_rows(markup: str, base: str = "") -> list[dict]:
    """Every part and item after the "Order of Business" heading, in page order.

    Each row is ``{"number", "title", "passage", "section", "pdfs", "kind"}``: ``kind`` is
    "part" for a Roman-numbered part, "item" for a numbered table row, "sub" for a lettered
    item; ``pdfs`` are the PDF links in the row (the first is the staff report; the list
    after the description holds public comments). Empty when the page is not this format.
    """
    tree = document(markup)
    if not recognizes(tree):
        return []
    following = {}  # each element's next element sibling
    for parent in [tree, *tree.find()]:
        elements = _elements(parent)
        following.update((id(a), b) for a, b in zip(elements, elements[1:]))
    rows: list[dict] = []
    started = False
    part = None
    for node in tree.find():
        if node.tag == "h1" and flat(node) == "Order of Business":
            started = True
            continue
        if not started:
            continue
        if node.tag == "h2":
            found = ROMAN.match(flat(node))
            if found:
                part = {"number": found["label"], "title": found["title"], "passage": flat(node),
                        "section": found["title"], "pdfs": _pdf_links(node, base), "kind": "part"}
                rows.append(part)
                if found["title"].lower().startswith("adjourn"):
                    break
        elif node.tag == "tr" and part:
            cells = _elements(node)
            if len(cells) != 2 or cells[0].tag != "th" or cells[1].tag != "td":
                continue
            label = flat(cells[0])
            if not re.fullmatch(r"\d{1,3}", label):
                continue  # the "Item / Summary" header row
            title, passage = _item_title(cells[1])
            rows.append({"number": label, "title": title, "passage": passage, "section": part["title"],
                         "pdfs": _pdf_links(cells[1], base), "kind": "item"})
        elif node.tag == "p" and part and (found := LETTER.match(flat(node))):
            # A lettered item: "(a) Conditional Emergency Action …", then the paragraph naming it.
            named = following.get(id(node))
            if named is not None and named.tag == "p" and flat(named) and not LETTER.match(flat(named)):
                title, passage = _item_title(named)
            else:
                named, title, passage = node, _bounded(found["title"]), flat(node)
            rows.append({"number": f"{part['number']}.{found['label']}", "title": title, "passage": passage,
                         "section": part["title"], "pdfs": _pdf_links(named, base), "kind": "sub"})
    return rows


def _quote(text: str, passage: str) -> str | None:
    parts = passage.split()
    if not parts:
        return None
    found = re.search(r"\s+".join(re.escape(part) for part in parts), text)
    return found.group() if found else None


def _valid(rows: list[dict]) -> bool:
    parts = [r for r in rows if r["kind"] == "part"]
    numbers = [int(r["number"]) for r in rows if r["kind"] == "item"]
    if not parts or [roman(p["number"]) for p in parts] != list(range(1, len(parts) + 1)):
        return False
    if numbers != list(range(1, len(numbers) + 1)):
        return False
    # Each calendar states its numbers ("Consent Calendar 01-57", "Informational Calendar 60"):
    # it must hold exactly those, and together they must hold every item, so a cut-off table fails.
    held: dict[int, list[int]] = {}
    current = None
    for row in rows:
        if row["kind"] == "part":
            current = id(row)
        elif row["kind"] == "item":
            held.setdefault(current, []).append(int(row["number"]))
    stated_all = []
    for part in parts:
        stated = RANGE.search(part["title"])
        if stated:
            span = list(range(int(stated[1]), int(stated[2] or stated[1]) + 1))
            if held.get(id(part), []) != span:
                return False
            stated_all += span
    return not stated_all or stated_all == numbers


def parse(markup: str, text: str) -> tuple[list[dict], bool]:
    """``(items, complete)`` in the shape ``html_agenda.parse_html_agenda`` returns."""
    rows = agenda_rows(markup)
    if not _valid(rows):
        return [], False
    items = []
    for row in rows:
        quote = _quote(text, row["passage"])
        if not quote:
            return [], False
        items.append({"source_number": row["number"], "order": len(items) + 1, "section": row["section"],
                      "title": row["title"], "source_pages": [{"document_id": "__html__", "page_number": 1, "quote": quote}]})
    return items, True
