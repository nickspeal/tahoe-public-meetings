"""Agenda items from a meeting page whose agenda is one ordered list (the Olympic Valley PSD's agenda posts).

The page is the agenda. Its parts are the ``<li>`` children of one top-level ``<ol>`` whose
first item opens "Call to Order" and whose last is "Adjourn". The list is lettered when the
page styles it so (``type="A"`` or ``list-style-type: upper-alpha``), else numbered; the
letters are not in the text, so each part's label is its position. A part's title is its
leading bold text ("<strong>Financial Consent Agenda</strong>. All items listed ..."), else
its first line. A part with a nested list is a section: each nested ``<li>`` prints its own
label, the part's label and its position joined by a hyphen ("D-1", "D-12"), then its title
on the first line, with the proposed action on the next. Those printed labels confirm the
lettering.

The parse is all or nothing: every nested item must print the label its position gives,
the list must end with "Adjourn", and every title must be quoted exactly from the page text.
A part's own links (``agenda_rows``) are the files posted for it, usually its board report.
"""
from __future__ import annotations

import re
import urllib.parse

from .html_dom import Node, document
from .outline import section_name

MAX_TITLE = 280
_BREAKS = {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr"}
_LISTS = {"ol", "ul"}
_NESTED = re.compile(r"^(?P<parent>[A-Z]|\d{1,2}) ?- ?(?P<n>\d{1,2})\s+(?P<title>\S.*)$")  # "D-12", once "F- 5"


def _items_of(node: Node) -> list[Node]:
    return [c for c in node.children if isinstance(c, Node) and c.tag == "li"]


def _lines(node: Node) -> list[str]:
    """The node's text lines as ``html_source.html_text`` breaks them, leaving out nested lists."""
    parts: list[str] = []

    def walk(n: Node) -> None:
        for child in n.children:
            if not isinstance(child, Node):
                parts.append(child)
            elif child.tag in _LISTS or child.tag in {"script", "style", "noscript"}:
                continue
            elif child.tag in _BREAKS:
                parts.append("\n")
                walk(child)
                parts.append("\n")
            else:
                walk(child)
    walk(node)
    return [line for line in (" ".join(raw.split()) for raw in "".join(parts).split("\n")) if line]


def _lead(node: Node) -> str:
    """A part's title: its leading bold text ("<strong>Old </strong><strong>and New Business.</strong>"), else its first line."""
    bold = []
    for child in node.children:
        if isinstance(child, Node) and child.tag in {"strong", "b"}:
            bold.append(child)
        elif isinstance(child, Node) or child.strip():
            break
    lines = _lines(Node("span", children=bold)) if bold else []
    if not lines:
        lines = _lines(node)
    return lines[0] if lines else ""


def _lists(tree: Node) -> list[Node]:
    """Ordered lists that are not inside a list item, in page order."""
    found: list[Node] = []

    def walk(n: Node, inside: bool) -> None:
        for child in n.children:
            if not isinstance(child, Node):
                continue
            if child.tag == "ol" and not inside:
                found.append(child)
            walk(child, inside or child.tag == "li")
    walk(tree, False)
    return found


def _agenda_list(tree: Node) -> Node | None:
    for ol in _lists(tree):
        parts = _items_of(ol)
        if len(parts) >= 3 and re.match(r"(?i)call to order\b", _lead(parts[0])) \
                and re.match(r"(?i)adjourn", _lead(parts[-1])):
            return ol
    return None


def recognizes(tree: Node) -> bool:
    return _agenda_list(tree) is not None


def _lettered(markup: str, ol: Node) -> bool:
    if ol.attrs.get("type") == "A":
        return True
    return bool(re.search(r"list-style(?:-type)?\s*:\s*upper-alpha", ol.attrs.get("style", "") + " " + markup, re.I))


def _label(position: int, lettered: bool) -> str:
    return chr(64 + position) if lettered else str(position)


def _links(node: Node, base: str) -> list[str]:
    """Absolute https links in the node, leaving out nested lists, in page order."""
    found: list[str] = []

    def walk(n: Node) -> None:
        for child in n.children:
            if not isinstance(child, Node) or child.tag in _LISTS:
                continue
            if child.tag == "a" and child.attrs.get("href"):
                url = urllib.parse.urljoin(base, child.attrs["href"].strip())
                if urllib.parse.urlparse(url).scheme == "https" and url not in found:
                    found.append(url)
            walk(child)
    walk(node)
    return found


def agenda_rows(markup: str, base: str = "") -> list[dict] | None:
    """Every part and nested item in page order, ``{"number", "title", "section", "links"}``, or None.

    None when the page has no such list or a nested item does not print the label its position gives.
    """
    tree = document(markup)
    ol = _agenda_list(tree)
    if ol is None:
        return None
    lettered = _lettered(markup, ol)
    rows: list[dict] = []
    for position, part in enumerate(_items_of(ol), 1):
        label, title = _label(position, lettered), _lead(part).rstrip(" .:")
        nested = [li for child in part.children if isinstance(child, Node) and child.tag in _LISTS for li in _items_of(child)]
        if not title:
            return None
        # A nested item's label line may follow a time note ("8:30 a.m. or as soon as the matter may be heard.").
        labelled = [next((m for line in _lines(li) if (m := _NESTED.match(line))), None) for li in nested]
        if not any(labelled):
            # No nested labels: the nested lines describe the part ("Government Code Section 54957").
            rows.append({"number": label, "title": title, "section": None, "links": _links(part, base)})
            continue
        for n, (li, match) in enumerate(zip(nested, labelled), 1):
            if not match or match["parent"] != label or int(match["n"]) != n:
                return None
            rows.append({"number": f"{label}-{n}", "title": match["title"].rstrip(" ."), "section": section_name(title),
                         "links": _links(li, base)})
    return rows


def _quote(text: str, title: str) -> str | None:
    words = title.split()
    found = re.search(r"\s+".join(re.escape(w) for w in words), text) if words else None
    return found.group() if found else None


def parse(markup: str, text: str) -> tuple[list[dict], bool]:
    """``(items, True)`` for a complete agenda list, else ``([], False)``."""
    rows = agenda_rows(markup)
    if not rows:
        return [], False
    items = []
    for row in rows:
        title = row["title"][:MAX_TITLE].rstrip()
        quote = _quote(text, title)
        if not quote:
            return [], False
        items.append({"source_number": row["number"], "order": len(items) + 1, "section": row["section"], "title": title,
                      "source_pages": [{"document_id": "__html__", "page_number": 1, "quote": quote}]})
        if title != row["title"]:
            items[-1]["full_title"] = row["title"]
    return items, True
