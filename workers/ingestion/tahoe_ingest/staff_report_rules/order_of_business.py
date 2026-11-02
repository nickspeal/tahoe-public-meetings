"""Rule ``order-of-business-item-files``: an "Order of Business" meeting page links each item's own staff report.

The captured agenda is the meeting's HTML page (``html_order_of_business``; the California
State Lands Commission). Each numbered item is a table row whose party name links the
item's staff report, a PDF named for the meeting date and the item number:
``12-17-24_11.pdf`` (2024) or ``11-10-21-26_11.pdf`` (2026). Public comment letters
listed under the description are other files and are never taken.

Two signals must agree. On the meeting page, read again: the item's own row, whose text
is the captured item's title, links exactly one report whose file name gives the meeting
date (``MM-DD-YY``) and ends with the item number. In the report: its first page prints
"Staff Report 11" (leading zeros optional), shares the item title's words (the party and
the land described), and its "Meeting Date: 12/17/24", when printed, is the meeting's.
Roman-numbered parts of the meeting and lettered emergency items are not mapped.
"""
from __future__ import annotations

import re
import urllib.parse

from ..html_order_of_business import agenda_rows
from ..staff_reports import (MIN_TITLE_OVERLAP, Context, Mapping, Uncertain, linked_reports, norm_number,
                             printed_meeting_dates, title_overlap)

MAX_REPORTS = 120  # report downloads per meeting (the December 2025 agenda had 110 items)
SAME_TITLE = 0.9


def _agenda(ctx: Context):
    pages = [d for d in ctx.meeting.documents if d.media_type == "text/html"]
    if len({d.url for d in pages}) != 1:
        raise Uncertain("no single HTML agenda page")
    return pages[0]


def _file_name(url: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])


def map_order_of_business(ctx: Context) -> list[Mapping]:
    agenda = _agenda(ctx)
    rows = {norm_number(r["number"]): r for r in agenda_rows(ctx.html(agenda.url), agenda.url) if r["kind"] == "item"}
    if not rows:
        raise Uncertain("the agenda page has no Order of Business items now")
    day = ctx.meeting.local_date
    stamp = f"{day:%m-%d-%y}" if day else None
    candidates, links = {}, {}
    items = []
    for item in ctx.numbered():
        if not item.number.isdigit():
            ctx.skip(item, "a part of the meeting or a lettered item, not a numbered agenda item")
            continue
        items.append(item)
        row = rows.get(norm_number(item.number))
        if row is None:
            ctx.skip(item, "item number is not on the agenda page")
            continue
        if title_overlap(item.title, row["title"]) < SAME_TITLE or title_overlap(row["title"], item.title) < SAME_TITLE:
            ctx.skip(item, "agenda page title differs from the captured item")
            continue
        named = [u for u in row["pdfs"] if stamp and stamp in _file_name(u)
                 and re.search(rf"_0*{int(item.number)}\.pdf$", _file_name(u), re.I)]
        if len(named) != 1:
            ctx.skip(item, f"{len(named)} report links named for item {item.number} of this meeting")
            continue
        if len(candidates) >= MAX_REPORTS:
            ctx.skip(item, f"more than {MAX_REPORTS} reports in this meeting")
            continue
        candidates[norm_number(item.number)] = {named[0]}
        links[item.id] = _file_name(named[0])

    def verify(item, report):
        first = report["pages"][0]["text"] if report["pages"] else ""
        heading = re.search(rf"^\s*Staff\s+Report\s+0*{int(item.number)}\s*$", first, re.M | re.I)
        if not heading:
            return f"report does not name item {item.number}"
        overlap = title_overlap(item.title, first)
        if overlap < MIN_TITLE_OVERLAP:
            return "report title differs from the agenda item"
        evidence = {"agendaLink": links[item.id], "reportHeading": " ".join(heading.group().split()),
                    "titleOverlap": round(overlap, 2)}
        printed = printed_meeting_dates(first)
        if printed and day:
            if not any(start <= day <= end for start, end in printed):
                return "report is for another meeting date"
            evidence["meetingDate"] = day.isoformat()
        return evidence
    return linked_reports(ctx, [i for i in items if norm_number(i.number) in candidates], candidates, verify=verify,
                          kind="staff-report", title="Item {number} staff report")


RULES = {"order-of-business-item-files": map_order_of_business}
