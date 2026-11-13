"""Rule ``ordered-list-item-reports``: an ordered-list agenda page links each item's own board report.

The captured agenda is the meeting's HTML page (``html_ordered_list``; the Olympic Valley
PSD's agenda posts). Each lettered item ("F-2 Bike Trail Snow Removal Contract"), and each
part of the meeting without lettered items, links the files posted for it: for an action
item, one board report PDF named for the item
("F-2_2026-10-27_Bike-Trail-Snow-Removal-Contract_Compiled-1.pdf").

Two signals must agree. On the meeting page, read again: the item's own list entry, whose
title is the captured item's title, links exactly one PDF, and its file name starts with the
item number. In the report: its first page prints the ``header`` line ("BOARD REPORT") and
the item number after the ``heading`` label ("EXHIBIT: F-2, 13 Pages"; ``{n}`` stands for
the number), shares the item title's words, and its "MEETING DATE: October 27, 2026", when
printed, is the meeting's. Check registers, financial statements, outside agencies'
summaries, and draft minutes linked under consent and informational items print an exhibit
label but no board report header, and stay unmapped.
"""
from __future__ import annotations

import re
import urllib.parse

from ..html_ordered_list import agenda_rows
from ..staff_reports import (MIN_TITLE_OVERLAP, Context, Mapping, Regex, Uncertain, linked_reports, norm_number,
                             printed_meeting_dates, title_overlap)

DEFAULT_HEADER = r"^\s*BOARD REPORT\s*$"  # a line of the report's first page
DEFAULT_HEADING = r"(?i)\bEXHIBIT:\s*{n}\b"  # the item's label on that page; {n} is the item number
MAX_REPORTS = 60  # report downloads per meeting (a regular agenda links about 40 files)
SAME_TITLE = 0.9


def _agenda(ctx: Context):
    pages = [d for d in ctx.meeting.documents if d.media_type == "text/html"]
    if len({d.url for d in pages}) != 1:
        raise Uncertain("no single HTML agenda page")
    return pages[0]


def _file_name(url: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])


def map_ordered_list(ctx: Context, *, header: Regex = DEFAULT_HEADER, heading: Regex = DEFAULT_HEADING) -> list[Mapping]:
    agenda = _agenda(ctx)
    rows = agenda_rows(ctx.html(agenda.url), agenda.url)
    if not rows:
        raise Uncertain("the agenda page has no ordered-list agenda now")
    by_number = {norm_number(r["number"]): r for r in rows}
    day = ctx.meeting.local_date
    candidates, links, items = {}, {}, []
    for item in ctx.numbered():
        number = norm_number(item.number)
        row = by_number.get(number)
        if row is None:
            ctx.skip(item, "item number is not on the agenda page")
            continue
        if title_overlap(item.title, row["title"]) < SAME_TITLE or title_overlap(row["title"], item.title) < SAME_TITLE:
            ctx.skip(item, "agenda page title differs from the captured item")
            continue
        pdfs = [u for u in row["links"] if urllib.parse.urlparse(u).path.lower().endswith(".pdf")]
        if len(pdfs) != 1:
            ctx.skip(item, f"{len(pdfs)} PDF links under item {item.number}")
            continue
        name = _file_name(pdfs[0])
        if not re.match(rf"{re.escape(number)}(?!\d)", name.upper()):
            ctx.skip(item, "the linked file is not named for the item")
            continue
        if len(candidates) >= MAX_REPORTS:
            ctx.skip(item, f"more than {MAX_REPORTS} reports in this meeting")
            continue
        candidates[number] = {pdfs[0]}
        links[item.id] = name
        items.append(item)

    def verify(item, report):
        first = report["pages"][0]["text"] if report["pages"] else ""
        top = re.search(header, first, re.M)
        if not top:
            return "not a board report"
        found = re.search(heading.replace("{n}", re.escape(norm_number(item.number))), first, re.M)
        if not found:
            return f"report does not name item {item.number}"
        overlap = title_overlap(item.title, first)
        if overlap < MIN_TITLE_OVERLAP:
            return "report title differs from the agenda item"
        evidence = {"agendaLink": links[item.id], "header": " ".join(top.group().split()),
                    "reportHeading": " ".join(found.group().split()), "titleOverlap": round(overlap, 2)}
        printed = printed_meeting_dates(first)
        if printed and day:
            if not any(start <= day <= end for start, end in printed):
                return "report is for another meeting date"
            evidence["meetingDate"] = day.isoformat()
        return evidence
    return linked_reports(ctx, items, candidates, verify=verify, kind="staff-report", title="Item {number} board report")


RULES = {"ordered-list-item-reports": map_ordered_list}
