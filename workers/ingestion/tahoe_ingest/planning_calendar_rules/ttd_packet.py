"""Format ``ttd-packet-pages``: the Tahoe Transportation District's tentative agenda calendar in its board packet.

The calendar is the run of consecutive packet pages headed "2026 TTD/C Board/Committees Tentative Agenda Calendar",
already stored as the packet's page text, so nothing is downloaded. Each block has two columns. A month-day heading
("November 4") starts a board block, whose next line is its column header ("TTD ... TTC"); "RTCPC <month day>" starts
a committee block, and that line is also its header ("RTCPC ... Fin & Pers"). The right column starts where the
header prints its right label, and the gutter before it splits every line of the block. A bullet may wrap onto
following lines of its column, with the other column interleaved, and a block may continue across a page break.
"CANCELED", "CANCELLED", "No Meeting", and "N/A" describe a column's state, not items, and a canceled column has
no entries. Only labels in ``bodies`` are read, so the TTC and Finance & Personnel columns are skipped. The "Pending
Items" list at the top names no body label, so it is skipped too. The packet's page footer ("~ Page 188 ~", "Meeting Packet - October 7, 2026") is not
part of the calendar. There is no "(Updated ...)" date: the calendar is as of the meeting whose packet carries it.
"""
from __future__ import annotations

import re
from datetime import date

from . import (MONTHS, CalendarFormat, CalendarInvalid, YearCursor, calendar, collapse, entry, first_line,
               month_number, runs, title_facts)

BULLET = "•"
STATE = re.compile(r"(?:CANCELL?ED|No Meeting|N/A)\.?", re.I)
HEADING = re.compile(r"(?:(?P<label>[A-Z][A-Za-z&/]*)\s+)?(?P<month>[A-Z][a-z]+)\.?\s+(?P<day>\d{1,2})(?:\s*-\s*(?P<state>.+))?")
RIGHT_LABEL = re.compile(r"(?P<label>.+?)(?:\s*-\s*|\s+)(?:(?P<state>CANCELL?ED)|\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)", re.I)
LABEL = re.compile(r"[A-Z][A-Za-z&/]*(?: [A-Z&][A-Za-z&/]*)*")
FOOTER = re.compile(r"~\s*Page\s+\d+\s*~")
TOLERANCE = 2  # characters right-column text may start before the header's right label


def locate(pages: list[dict], *, heading: str) -> list[dict]:
    """The longest run of consecutive pages whose first line contains ``heading``."""
    best: list[dict] = []
    run: list[dict] = []
    for page in sorted(pages, key=lambda p: p["page_number"]):
        if heading.casefold() not in first_line(page).casefold():
            run = []
            continue
        run = run + [page] if run and page["page_number"] == run[-1]["page_number"] + 1 else [page]
        if len(run) > len(best):
            best = run
    return best


def state_of(text: str | None, printed: str) -> str | None:
    if text is None:
        return None
    if not STATE.fullmatch(text.strip()):
        raise CalendarInvalid(f"date heading {printed!r} does not parse")
    return "canceled" if text.strip().lower().startswith("cancel") else collapse(text).lower()


def right_header(text: str) -> tuple[str, str | None]:
    """'Fin & Pers - Canceled' -> ('Fin & Pers', 'canceled'); 'Fin & Pers 1:30' -> ('Fin & Pers', None)."""
    if m := RIGHT_LABEL.fullmatch(text):
        return m["label"], "canceled" if m["state"] else None
    return text, None


def heading(text: str, bodies: dict) -> re.Match | None:
    """A date heading: a month and day, or a followed body's label before any month-like word and day, which must
    then parse ("RTCPC Novmber 2" fails the calendar). An unlabelled misspelt month is caught by the column header
    that follows it."""
    match = None if text.startswith(BULLET) else HEADING.fullmatch(text)
    return match if match and (match["month"].casefold() in MONTHS or match["label"] in bodies) else None


def new_block(match: re.Match, printed: str, page: int, cursor: YearCursor) -> dict:
    day = cursor.place(month_number(match["month"], printed), int(match["day"]), printed)
    return {"heading": printed, "page": page, "local_date": day, "labels": [match["label"], None], "start": None,
            "states": [state_of(match["state"], printed), None], "items": [[], []], "open": [None, None]}


def set_columns(block: dict, cells: list[tuple[int, str]]) -> None:
    """The column header: a left label (unless the heading gave it) and a right label, whose column starts the right column."""
    if len(cells) > 1:
        block["start"] = cells[1][0]
        block["labels"][1], block["states"][1] = right_header(" ".join(t for _, t in cells[1:]))
    if block["labels"][0] is None:
        block["labels"][0] = cells[0][1]


def columns(line: str, cells: list[tuple[int, str]], start: int | None) -> list[str]:
    """``[left text, right text]``, split at the gutter before the right column."""
    split = next((c for c, _ in cells if start is not None and c >= start - TOLERANCE), len(line))
    return [line[:split].strip(), line[split:].strip()]


def add(block: dict, column: int, text: str, page: int) -> None:
    if not text:
        return
    bullet = text.startswith(BULLET)
    body = text.lstrip(BULLET).strip()
    if STATE.fullmatch(body):
        block["states"][column] = state_of(body, body)
        block["open"][column] = None
    elif bullet or block["open"][column] is None:
        block["open"][column] = {"page": page, "fragments": [body] if body else []}
        block["items"][column].append(block["open"][column])
    else:
        block["open"][column]["fragments"].append(body)


def calendar_lines(page: dict, title: str) -> list[str]:
    """The page's lines without its repeated title and the packet footer."""
    lines = []
    for line in page["text"].splitlines():
        if FOOTER.search(line):
            break
        lines.append("" if line.strip() == title else line)
    return lines


def parse(pages: list[dict], *, config: dict, carried_by: date) -> dict:
    if not pages:
        raise CalendarInvalid("the calendar has no pages")
    title = first_line(pages[0])
    year, published_on = title_facts(title, config["title"], carried_by)
    cursor, blocks, block, pending = YearCursor(year), [], None, False
    for page in pages:
        number = page["page_number"]
        for line in calendar_lines(page, title):
            cells = runs(line)
            if not cells:
                if block:
                    block["open"] = [None, None]  # a blank line ends the bullets above it
                continue
            first = cells[0][1]
            if first.casefold() == "pending items":
                pending = True
                continue
            if (match := heading(first, config["bodies"])):
                pending = False
                block = new_block(match, first, number, cursor)
                blocks.append(block)
                if match["label"]:
                    set_columns(block, cells)
                continue
            if pending:
                continue
            if block is None:
                raise CalendarInvalid(f"page {number}: {collapse(line)!r} is outside any date block")
            header = not first.startswith(BULLET) and LABEL.fullmatch(first) and not STATE.fullmatch(first) and len(cells) > 1
            if block["labels"][0] is None:
                if not header:
                    raise CalendarInvalid(f"page {number}: the {block['heading']!r} block has no column header")
                set_columns(block, cells)
                continue
            if header and first in config["bodies"]:
                raise CalendarInvalid(f"page {number}: column header {collapse(line)!r} without a date heading")
            for column, text in enumerate(columns(line, cells, block["start"])):
                add(block, column, text, number)
    return calendar(title, published_on, year, *results(blocks, config["bodies"]), pages)


def results(blocks: list[dict], bodies: dict) -> tuple[list[dict], list[dict]]:
    """Block summaries and entries for the followed columns; a canceled column has none."""
    summaries, entries = [], []
    for block in blocks:
        for column, label in enumerate(block["labels"]):
            if label not in bodies:
                continue
            body = bodies[label]
            found = [] if block["states"][column] == "canceled" else [
                entry(agency_id=body["id"], fragments=item["fragments"], page=item["page"],
                      meeting_title=body["meetingTitle"], local_date=block["local_date"])
                for item in block["items"][column] if item["fragments"]]
            entries += found
            summaries.append({"label": label, "agency_id": body["id"], "heading": block["heading"], "page": block["page"],
                              "local_date": block["local_date"], "month": None, "pending": False, "start_time": None,
                              "special": False, "state": block["states"][column], "entries": len(found)})
    return summaries, entries


RULES = {"ttd-packet-pages": CalendarFormat(locate, parse)}
