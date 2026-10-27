"""The requested or recommended action an agenda prints under an item, copied verbatim (no model).

Many agendas state what the body is asked to do directly under each item, for example
South Lake Tahoe's "Requested Action / Suggested Motions: Pass a Resolution ..." or
Truckee's "Recommended Action: That Council ...". That text is already in the stored
agenda pages, so it is read deterministically:

- The label must start a line between this item's cited title and the next item's
  (items are ordered by where their quotes sit in the same agenda document), so the
  action is never attributed to a neighbouring item.
- The action runs to the next blank line, field label ("CEQA Determination:",
  "Responsible Staff Member(s):"), attachment file name, or the next item, and must be
  one complete statement ending in a period, of 5 to 120 words.

The result is the exact page text, cited to its page, so it passes the same evidence
check as every other citation. A TTD-style "For Possible Action:" title is the title
itself and is not repeated.
"""
from __future__ import annotations

import re

from .staff_reports import clean_text

METHOD = "agenda-requested-action-v1"
MIN_WORDS, MAX_WORDS = 5, 120
_LABEL = re.compile(r"^[ \t]*(?:Requested Action\s*/\s*Suggested Motions?|Requested Actions?|Recommended Actions?|"
                    r"Staff Recommendations?|Recommendations?|Suggested Motions?|Action Requested)[ \t]*:[ \t]*", re.I | re.M)
_STOP = re.compile(r"\n[ \t]*\n|\n[ \t]*[A-Z][\w /()'’,-]{2,48}:[ \t]|\n[^\n]*\.(?:pdf|docx?|xlsx?|pptx?)[ \t]*(?=\n|$)", re.I)


def _position(citation: dict, pages: dict) -> tuple | None:
    text = pages.get((citation["documentId"], int(citation["page"])))
    if text is None or citation["quote"] not in text:
        return None
    start = text.index(citation["quote"])
    return citation["documentId"], int(citation["page"]), start, start + len(citation["quote"])


def for_meeting(items: list[dict], pages: dict) -> dict[str, dict]:
    """``{item id: {"text", "citation"}}`` for a meeting's items; ``pages`` maps (version id, page number) to text.

    Items are dicts with ``id`` and ``citations`` (``[{documentId, page, quote}]``).
    """
    located = []
    for item in items:
        citations = item["citations"] if isinstance(item["citations"], list) else []
        position = _position(citations[0], pages) if citations else None
        if position:
            located.append((position, item["id"]))
    located.sort()
    found = {}
    for index, (position, item_id) in enumerate(located):
        document, page, _, end = position
        following = located[index + 1][0] if index + 1 < len(located) else None
        if following and following[0] != document:
            following = None
        # The item's own text: the rest of its page, up to the next item's title (on this page or the next).
        text = pages[(document, page)]
        block_end = following[2] if following and following[1] == page else len(text)
        action = _action(text[end:block_end], end, text)
        if action is None and not (following and following[1] == page):
            continue  # Actions that start on a later page are not attributed.
        if action:
            found[item_id] = {"text": action[0], "citation": {"documentId": document, "page": page, "quote": action[1]}}
    return found


def _action(block: str, offset: int, page_text: str) -> tuple[str, str] | None:
    label = _LABEL.search(block)
    if not label:
        return None
    rest = block[label.end():]
    stop = _STOP.search(rest)
    quote = rest[:stop.start()] if stop else rest
    quote = quote.rstrip()
    shown = " ".join(clean_text(quote).split())
    words = shown.split()
    if not (MIN_WORDS <= len(words) <= MAX_WORDS) or not re.search(r"[.)]$", shown) or shown.endswith(":"):
        return None
    if stop is None and offset + label.end() + len(rest) >= len(page_text.rstrip()):
        return None  # Runs off the page: it may continue, so it is not a complete statement.
    return shown, quote
