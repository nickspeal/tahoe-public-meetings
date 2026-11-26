"""Format ``ttd-packet-grid``: the Tahoe Transportation District's tentative agenda planning calendar as a grid.

TTD introduced this layout in its December 2, 2026 board packet, replacing ``ttd-packet-pages``. The calendar is
the run of packet pages titled "TAHOE TRANSPORTATION DISTRICT - TENTATIVE AGENDA PLANNING CALENDAR - 2027", already
stored as the packet's page text, so nothing is downloaded. It is an Excel table in fiscal quarters ("FY QUARTER
3"). Each block starts with one header line of side-by-side columns, one per body, each printing its date with the
year and its label ("January 4, 2027 - RTCP Committee") and then "Agenda", the heading of its section column. Under
each header, a column's rows are numbered from 1. A row's number, subject, and section ("Business", "Consent",
"Report", "Proclamation") may print on different lines of the row, and a subject may wrap onto the next line with
the other columns interleaved, so a line without a number continues the column's latest row. A number with no
subject is an empty row. "CANCELED", "CANCELLED", "No Meeting", and "N/A", in a header after its label or as a
row's only text, mark the column's state, and a canceled column has no entries. Only labels in ``bodies`` are
read, so the Finance & Personnel Committee column is skipped. The packet's page footer ("~ Page 192 ~", "Meeting
Packet - December 2, 2026") and the staff-report line above it ("AGENDA ITEM: VII") are not part of the calendar.
Column positions come from each block's own header line, because they shift between pages. There is no
"(Updated ...)" date: the calendar is as of the meeting whose packet carries it.
"""
from __future__ import annotations

import re
from datetime import date

from . import (CalendarFormat, CalendarInvalid, YearCursor, calendar, collapse, entry, first_line, month_number,
               title_facts)

STATE = re.compile(r"(?:CANCELL?ED|No Meeting|N/A)\.?", re.I)
COLUMN_HEADING = re.compile(r"(?P<month>[A-Z][a-z]+)\.?\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4})\s+[-–]\s+(?P<label>.+?)"
                            r"(?:\s+[-–]\s+(?P<state>(?i:CANCELL?ED|No Meeting|N/A)))?")
SECTION_HEADING = "Agenda"
QUARTER = re.compile(r"FY\s+QUARTER\s+\d", re.I)
FOOTER = re.compile(r"~\s*Page\s+\d+\s*~|AGENDA ITEM:", re.I)
NUMBER = re.compile(r"\d{1,2}")
SECTION = re.compile(r"[A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*){0,2}")  # "Business", "Consent", "Public Hearing"
WORD = re.compile(r"\S+")
TOLERANCE = 2  # characters a section may start before its "Agenda" heading


def header_cells(line: str) -> list[tuple[int, str]] | None:
    """``[(column, text)]`` of a block header line: date-and-label cells, each followed by "Agenda"; else None."""
    cells = [(m.start(), m.group()) for m in re.finditer(r"\S+(?: \S+)*", line)]
    if not cells or len(cells) % 2 or any(t != SECTION_HEADING for _, t in cells[1::2]):
        return None
    return cells if all(COLUMN_HEADING.fullmatch(t) for _, t in cells[::2]) else None


def locate(pages: list[dict], *, heading: str) -> list[dict]:
    """The longest run of consecutive pages that starts with a page whose first line contains ``heading``; a
    following page continues it when it starts with the title, a fiscal quarter, or a block header."""
    best: list[dict] = []
    run: list[dict] = []
    for page in sorted(pages, key=lambda p: p["page_number"]):
        top = first_line(page)
        follows = run and page["page_number"] == run[-1]["page_number"] + 1
        if heading.casefold() in top.casefold():
            run = run + [page] if follows else [page]
        elif follows and (QUARTER.fullmatch(top) or header_cells(top)):
            run = run + [page]
        else:
            run = []
            continue
        if len(run) > len(best):
            best = run
    return best


def state_of(text: str) -> str:
    return "canceled" if text.strip().lower().startswith("cancel") else collapse(text).lower().rstrip(".")


def new_block(cells: list[tuple[int, str]], page: int, cursor: YearCursor) -> dict:
    """A block from its header: per column, where its rows start, where its section column starts, and its label."""
    columns = []
    for index in range(0, len(cells), 2):
        (start, printed), (section, _) = cells[index], cells[index + 1]
        m = COLUMN_HEADING.fullmatch(printed)
        local_date = cursor.place(month_number(m["month"], printed), int(m["day"]), printed, int(m["year"]))
        columns.append({"heading": printed, "label": collapse(m["label"]), "local_date": local_date, "start": start,
                        "section": section, "state": state_of(m["state"]) if m["state"] else None, "rows": [],
                        "open": None})
    # A column's rows begin halfway between the previous column's section heading and its own date heading, so
    # its row numbers, printed left of the heading, stay in it.
    for before, column in zip(columns, columns[1:]):
        column["left"] = (before["section"] + len(SECTION_HEADING) + column["start"]) // 2
    columns[0]["left"] = 0
    return {"page": page, "columns": columns}


def split(line: str, columns: list[dict]) -> list[list[tuple[int, str]]]:
    """The line's words, ``[(column, word)]``, for each block column, by where each word starts."""
    found: list[list[tuple[int, str]]] = [[] for _ in columns]
    for m in WORD.finditer(line):
        index = max(i for i, c in enumerate(columns) if m.start() >= c["left"])  # the first column's left is 0
        found[index].append((m.start(), m.group()))
    return found


def text_of(line: str, words: list[tuple[int, str]]) -> str:
    return collapse(line[words[0][0]:words[-1][0] + len(words[-1][1])]) if words else ""


def add(column: dict, line: str, words: list[tuple[int, str]], page: int) -> None:
    """One line of a column: an optional row number, then subject words, then section words. A line with nothing
    in this column leaves its latest row open, since the row's subject may print below its number."""
    if not words:
        return
    if NUMBER.fullmatch(words[0][1]) and words[0][0] < column["start"]:
        column["open"] = {"page": page, "number": int(words[0][1]), "fragments": [], "sections": set()}
        column["rows"].append(column["open"])
        words = words[1:]
    if words and STATE.fullmatch(text_of(line, words)):
        column["state"], column["open"] = state_of(text_of(line, words)), None
        return
    subject = [(c, w) for c, w in words if c < column["section"] - TOLERANCE]
    section = text_of(line, [(c, w) for c, w in words if c >= column["section"] - TOLERANCE])
    if column["open"] is None:
        raise CalendarInvalid(f"page {page}: {text_of(line, words)!r} in {column['heading']!r} is in no numbered row")
    if section:
        if not SECTION.fullmatch(section):
            raise CalendarInvalid(f"page {page}: {section!r} in {column['heading']!r} is not an agenda section")
        column["open"]["sections"].add(section)
    if subject:
        column["open"]["fragments"].append(text_of(line, subject))


def calendar_lines(page: dict, title: str) -> list[str]:
    """The page's lines without the calendar title and fiscal quarter lines, and nothing from the packet footer on."""
    lines = []
    for line in page["text"].splitlines():
        if FOOTER.search(line):
            break
        lines.append("" if title.casefold() in line.casefold() or QUARTER.fullmatch(line.strip()) else line)
    return lines


def parse(pages: list[dict], *, config: dict, carried_by: date) -> dict:
    if not pages:
        raise CalendarInvalid("the calendar has no pages")
    title = first_line(pages[0])
    year, published_on = title_facts(title, config["title"], carried_by)
    cursor, blocks, block = YearCursor(year), [], None
    for page in pages:
        number = page["page_number"]
        for line in calendar_lines(page, config["title"]):
            if (cells := header_cells(line)):
                block = new_block(cells, number, cursor)
                blocks.append(block)
                continue
            if not line.strip():
                if block:
                    for column in block["columns"]:
                        column["open"] = None  # a blank line ends the rows above it
                continue
            if block is None:
                raise CalendarInvalid(f"page {number}: {collapse(line)!r} is outside any date block")
            for column, words in zip(block["columns"], split(line, block["columns"])):
                add(column, line, words, number)
    if not blocks:
        raise CalendarInvalid("the calendar has no date blocks")
    return calendar(title, published_on, year, *results(blocks, config["bodies"]), pages)


def results(blocks: list[dict], bodies: dict) -> tuple[list[dict], list[dict]]:
    """Block summaries and entries for the followed columns; a canceled column has none."""
    summaries, entries = [], []
    for block in blocks:
        for column in block["columns"]:
            if column["label"] not in bodies:
                continue
            body = bodies[column["label"]]
            found = []
            for row in column["rows"]:
                if len(row["sections"]) > 1:
                    raise CalendarInvalid(f"row {row['number']} of {column['heading']!r} has more than one section")
                if row["fragments"] and column["state"] != "canceled":
                    found.append(entry(agency_id=body["id"], fragments=row["fragments"], page=row["page"],
                                       meeting_title=body["meetingTitle"], local_date=column["local_date"],
                                       section=next(iter(row["sections"]), None)))
            entries += found
            summaries.append({"label": column["label"], "agency_id": body["id"], "heading": column["heading"],
                              "page": block["page"], "local_date": column["local_date"], "month": None,
                              "pending": False, "start_time": None, "special": False, "state": column["state"],
                              "entries": len(found)})
    return summaries, entries


RULES = {"ttd-packet-grid": CalendarFormat(locate, parse)}
