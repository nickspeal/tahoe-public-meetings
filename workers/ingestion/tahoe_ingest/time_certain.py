"""Time-certain agenda items: items an agency says it will not hear before a stated time.

Agencies mark these two ways, and both are read from stored page text at export
time, so no schema change or reparse is needed:

* A heading line above a group of items, such as El Dorado County's
  "11:00 A.M. - TIME ALLOCATION (Items will not be heard prior to the time
  stated)". The line holds only the time, the label, and an optional
  parenthetical, so a cover-page list of timed items is not a heading. It applies to the items that follow it until the next such line or
  the next all-caps section heading ("OPEN FORUM", "ADJOURNMENT").
* A time written into the item itself, such as "(Time-Certain 2:00 p.m.)",
  "(Timed Item 3:00 p.m.)", "Time: 4:00 PM", or a title that starts with
  "9:00 AM – PUBLIC HEARING".

Each result keeps the exact source line so a reader can check it.
"""
from __future__ import annotations

import re

TIME = r"(?P<h>1[0-2]|0?[1-9])(?::(?P<m>[0-5]\d))?\s*(?P<ap>[ap])\.?\s?m\b\.?"
_MARKERS = [re.compile(p, re.I) for p in (
    rf"^\s*{TIME}\s*[-–—:]?\s*(?:time\s+allocation|time[-\s]certain|(?:public\s+)?hearings?)\s*(?:\(.*\))?\s*$",
    rf"^\s*{TIME}.*\b(?:not\s+be\s+(?:heard|considered|called)\s+(?:prior\s+to|before)|no\s+earlier\s+than)\b",
    rf"^\s*time[-\s]certain\s*[:\-–—]?\s*{TIME}",
)]
_INLINE = [re.compile(p, re.I) for p in (
    rf"\btime[-\s]certain\s*[:\-–—]?\s*(?:of\s+|at\s+)?{TIME}",
    rf"{TIME}\s*[-–—(]?\s*time[-\s]certain\b",
    rf"\btimed\s+item\s*[:\-–—]?\s*{TIME}",
    rf"{TIME}\s*\(\s*no\s+earlier\s+than\s*\)",
    rf"\b(?:no\s+earlier\s+than|not\s+(?:be\s+heard\s+|be\s+considered\s+)?(?:before|prior\s+to))\s+{TIME}",
    rf"\btime:\s*{TIME}",
    rf"\(\s*{TIME}\s*\)",
    rf"^(?:subject:\s*)?(?:\(for\s+possible\s+action\)\s*)?{TIME}\s*[-–—:]",
)]


def _label(match: re.Match) -> str:
    return f"{int(match['h'])}:{match['m'] or '00'} {match['ap'].upper()}M"


def _heading(line: str) -> bool:
    """An all-caps section heading ("OPEN FORUM", "CAO UPDATE (May be called...)") that ends a timed group."""
    head = line.split("(")[0].strip()
    letters = [c for c in head if c.isalpha()]
    return len(letters) >= 4 and ":" not in head and not any(c.islower() for c in head)


def _locate(text: str, quote: str) -> tuple[int, int] | None:
    start = text.find(quote)
    if start >= 0:
        return start, start + len(quote)
    words = quote.split()
    if not words:
        return None
    found = re.search(r"\s+".join(map(re.escape, words)), text)
    return (found.start(), found.end()) if found else None


def _inline(item: dict) -> dict | None:
    for citation in item.get("citations") or []:
        quote = " ".join((citation.get("quote") or "").split())
        for pattern in _INLINE:
            if found := pattern.search(quote):
                return {"time": _label(found), "quote": quote[:300], "documentId": citation.get("documentId"),
                        "page": citation.get("page")}
    title = " ".join(item["title"].split())
    for pattern in _INLINE:
        if found := pattern.search(title):
            return {"time": _label(found), "quote": title[:300], "documentId": None, "page": None}
    return None


def time_certain(documents: list[dict], items: list[dict]) -> dict[str, dict]:
    """Map item IDs to ``{"time", "quote", "documentId", "page"}`` for items with a stated earliest time."""
    pages = {(d["id"], p["number"]): p["text"] for d in documents for p in d.get("pages", [])}
    spans: dict[tuple[str, int], list[tuple[int, int, str]]] = {}
    for item in items:
        for citation in (item.get("citations") or [])[:1]:
            key = (citation.get("documentId"), citation.get("page"))
            if key in pages and (span := _locate(pages[key], citation.get("quote") or "")):
                spans.setdefault(key, []).append((*span, item["id"]))
    found = {}
    for doc in documents:
        current = None
        for page in sorted(doc.get("pages", []), key=lambda p: p["number"]):
            key = (doc["id"], page["number"])
            item_spans = sorted(spans.get(key, []))
            offset = 0
            events = []  # (position, kind, payload)
            for line in page["text"].splitlines(keepends=True):
                start, end = offset, offset + len(line)
                offset = end
                if not line.strip() or any(s < end and start < e for s, e, _ in item_spans):
                    continue
                if marker := next(filter(None, (p.search(line) for p in _MARKERS)), None):
                    events.append((start, "marker", {"time": _label(marker), "quote": " ".join(line.split()),
                                                      "documentId": doc["id"], "page": page["number"]}))
                elif _heading(line):
                    events.append((start, "reset", None))
            events += [(s, "item", item_id) for s, _, item_id in item_spans]
            for _, kind, payload in sorted(events, key=lambda e: e[0]):
                if kind == "marker":
                    current = payload
                elif kind == "reset":
                    current = None
                elif current and payload not in found:
                    found[payload] = current
    for item in items:
        # A time printed in the item itself is more specific than its group heading.
        if inline := _inline(item):
            found[item["id"]] = inline
    return found
