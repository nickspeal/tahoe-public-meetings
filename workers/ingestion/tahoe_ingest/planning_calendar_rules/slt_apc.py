"""Format ``slt-apc-attachment``: South Lake Tahoe City Council's agenda planning calendar.

The calendar is the PDF attached to the agenda item "Agenda Planning Calendar" and named "APC MM DD YYYY.pdf"; its
title reads "2026 Agenda Planning Calendar (Updated 09 30 2026)". It is an Excel table, read as ``pdftotext -layout``
text: a date cell ("Nov 3, 9:30 a.m.", "10/13/2026 Special Mtg", a month, or "Unscheduled/Pending"), the department,
the item, the section code (NB, PH, UB, Consent), and requester initials or a motion number. A date cell starts a
block, and rows without one belong to the block above, across pages. Column positions shift from page to page, so
each page's item, section, and initials columns are inferred from its own rows. The department and initials columns,
and notes printed right of the section column ("If Election is Certified by ..."), are never published.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import NamedTuple

from ..staff_reports import Regex
from . import (FULL_MONTH, CalendarFormat, CalendarInvalid, YearCursor, calendar, clock, collapse, entry, first_line,
               month_number, runs, title_facts)

SECTIONS = {"NB": "New business", "PH": "Public hearing", "UB": "Unfinished business", "Consent": "Consent"}
SECTION_TOKEN = re.compile(r"[A-Z][A-Za-z]{1,9}")  # a code at the section column; unknown codes ("Proc") are kept out of the subject
INITIALS = re.compile(r"[A-Z]{1,3}/(?:[A-Z]{1,3})?|Motion\s+\d{4}-\d+", re.I)
INITIALS_AT_END = re.compile(r"(?:^|\s)(?:[A-Z]{1,3}/(?:[A-Z]{1,3})?|Motion \d{4}-\d+)$")
HEADING_LIKE = re.compile(rf"(?:\d{{1,2}}/\d{{1,2}}/\d{{4}}|{FULL_MONTH}\b|(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?\s+\d"
                          r"|Unscheduled\b|Pending\b)")
COLUMN_HEADER = re.compile(r"^\s*Agenda Item\s{2,}Section\b")
TOLERANCE = 2  # characters a cell may sit off its column


def norm(text: str) -> str:
    return collapse(text).casefold()


def locate(items: list[dict], *, item_title: str, attachment_pattern: Regex) -> list[dict]:
    """``[{"url", "name"}]``: attachments named like ``attachment_pattern`` under the item titled ``item_title``."""
    found = []
    for item in items:
        if norm(item.get("title") or "") != norm(item_title):
            continue
        for attachment in item.get("attachments", []):
            name = collapse(attachment.get("name") or "")
            if re.search(attachment_pattern, name) and attachment["url"] not in {f["url"] for f in found}:
                found.append({"url": attachment["url"], "name": name})
    return found


class Columns(NamedTuple):
    item: int
    section: int | None
    next_column: int | None  # the section column, else the initials column: where the item cell ends
    has_department: bool  # a department column apart from the left margin, so text at the margin is a date cell


def mode(counter: Counter) -> int | None:
    return min(counter, key=lambda c: (-counter[c], c)) if counter else None


def infer_columns(rows: list[list[tuple[int, str]]]) -> Columns | None:
    """The page's columns: the item column is where most rows' last cell (other than a code or initials) starts."""
    items = Counter()
    for row in rows:
        cells = [c for c, t in row if c > 0 and not INITIALS.fullmatch(t) and t not in SECTIONS]
        if cells:
            items[cells[-1]] += 1
    item = mode(items)
    if item is None:
        return None
    section = mode(Counter(c for row in rows for c, t in row if c > item and t in SECTIONS))
    initials = mode(Counter(c for row in rows for c, t in row if c > item and INITIALS.fullmatch(t)))
    department = any(0 < c < item - TOLERANCE for row in rows for c, _ in row)
    return Columns(item, section, section if section is not None else initials, department)


def split_row(row: list[tuple[int, str]], cols: Columns, page: int) -> tuple[list[tuple[int, str]], str | None, str | None]:
    """``(left cells, item, section code)``. Initials, motion numbers, and notes right of the item are dropped."""
    left = [(c, t) for c, t in row if c < cols.item - TOLERANCE]
    rest = [(c, t) for c, t in row if c >= cols.item - TOLERANCE]
    for c, t in left:
        if c + len(t) >= cols.item:
            raise CalendarInvalid(f"page {page}: {t!r} runs into the item column")
    item = section = None
    if rest and rest[0][0] <= cols.item + TOLERANCE:
        c, item = rest.pop(0)
        if cols.next_column is not None and c + len(item) >= cols.next_column:
            # "... Mainstreet Corridor Plan Consent": a long item leaves one space before the section code.
            head, _, last = item.rpartition(" ")
            if cols.section is None or not head or not SECTION_TOKEN.fullmatch(last) or c + len(head) + 1 < cols.section - TOLERANCE:
                raise CalendarInvalid(f"page {page}: {item!r} runs into the section column")
            item, section = head, last
    for c, t in rest:
        if cols.next_column is None or c < cols.next_column - TOLERANCE:
            raise CalendarInvalid(f"page {page}: {t!r} is outside the item and section columns")
        if section is None and cols.section is not None and abs(c - cols.section) <= TOLERANCE and SECTION_TOKEN.fullmatch(t):
            section = t
    return left, item, section


TIME = re.compile(r"\s*,\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?(?!\w)", re.I)


def heading(text: str, cursor: YearCursor, page: int) -> dict:
    """The block a date cell starts; the department printed after it on the same line is ignored."""
    block = {"heading": None, "page": page, "local_date": None, "month": None, "pending": False, "start_time": None,
             "special": False}
    if m := re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        block["local_date"] = cursor.place(int(m[1]), int(m[2]), text, year=int(m[3]))
        end = m.end()
    elif m := re.match(r"([A-Za-z]+)\.?\s+(\d{1,2})\b", text):
        month, day, end = month_number(m[1], text), int(m[2]), m.end()
        if text[end:].lstrip().startswith(","):
            try:
                t = TIME.match(text[end:])
                block["start_time"] = clock(t[1], t[2], t[3])
            except (TypeError, ValueError):  # no time after the comma, or an impossible one
                raise CalendarInvalid(f"date heading {text!r} does not parse") from None
            end += t.end()
        block["local_date"] = cursor.place(month, day, text)
    elif m := re.match(rf"{FULL_MONTH}\b", text):
        block["month"] = cursor.place(month_number(m[0], text), 1, text)
        end = m.end()
    elif m := re.match(r"Unscheduled(?:\s*/\s*Pending)?|Pending\b", text, re.I):
        block["pending"], end = True, m.end()
    else:
        raise CalendarInvalid(f"date heading {text!r} does not parse")
    if special := re.match(r"\s*Special\s+(?:Mtg|Meeting)\b", text[end:], re.I):
        block["special"], end = True, end + special.end()
    block["heading"] = text[:end].strip()
    return block


def parse(pages: list[dict], *, config: dict, carried_by: date) -> dict:
    if not pages:
        raise CalendarInvalid("the calendar has no pages")
    body = config["bodies"].get("*")
    if body is None:
        raise ValueError("slt-apc-attachment maps the whole calendar to one body: give bodies a \"*\" entry")
    title = first_line(pages[0])
    year, published_on = title_facts(title, config["title"], carried_by)
    cursor, blocks, entries, block = YearCursor(year), [], [], None
    for page in pages:
        number = page["page_number"]
        lines = [line for line in page["text"].splitlines()
                 if line.strip() and line.strip() != title and not COLUMN_HEADER.match(line)]
        rows = [runs(line) for line in lines]
        cols = infer_columns(rows)
        if cols is None:
            continue
        for row in rows:
            left, item, section = split_row(row, cols, number)
            if left and left[0][0] == 0 and (cols.has_department or HEADING_LIKE.match(left[0][1])):
                block = heading(left[0][1], cursor, number) | {"label": "*", "agency_id": body["id"], "state": None, "entries": 0}
                blocks.append(block)
            if item is None:
                continue
            if block is None:
                raise CalendarInvalid(f"page {number}: {item!r} is outside any date block")
            found = entry(agency_id=body["id"], fragments=[item], page=number, meeting_title=body["meetingTitle"],
                          local_date=block["local_date"], month=block["month"], pending=block["pending"],
                          section=SECTIONS.get(section), start_time=block["start_time"], special=block["special"])
            if INITIALS_AT_END.search(found["subject"]):
                raise CalendarInvalid(f"page {number}: requester initials in {found['subject']!r}")
            entries.append(found)
            block["entries"] += 1
    return calendar(title, published_on, year, blocks, entries, pages)


RULES = {"slt-apc-attachment": CalendarFormat(locate, parse)}
