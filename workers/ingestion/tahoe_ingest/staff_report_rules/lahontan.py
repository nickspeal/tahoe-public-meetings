"""Rule ``lahontan-item-files``: the Lahontan Water Board's agenda PDF links ``itemN.pdf``, headed "ITEM N" with the item's title."""
from __future__ import annotations

import re

from ..staff_reports import (MIN_TITLE_OVERLAP, Context, Mapping, Uncertain, linked_reports, norm_number, pdf_agenda,
                             title_overlap)


def map_lahontan(ctx: Context) -> list[Mapping]:
    agenda = pdf_agenda(ctx, prefer=r"agenda")
    fetched = ctx.read(agenda.url, last_page=1, links=True)
    if fetched["sha256"] != agenda.sha256:
        raise Uncertain("the agenda PDF changed since it was captured")
    base = agenda.url.rsplit("/", 1)[0] + "/"
    links: dict[str, set] = {}
    for link in fetched["links"]:
        named = re.fullmatch(re.escape(base) + r"item(\d+)\.pdf", link["url"], re.I)
        if named:
            links.setdefault(named.group(1), set()).add(link["url"])

    def verify(item, report):
        first = report["pages"][0]["text"] if report["pages"] else ""
        heading = re.search(rf"^\s*ITEM\s+{re.escape(norm_number(item.number))}\s*$", first, re.M)
        if not heading:
            return "report does not name this item number"
        if title_overlap(item.title, first[heading.end():heading.end() + 400]) < MIN_TITLE_OVERLAP:
            return "report title differs from the agenda item"
        meeting_of = re.search(r"MEETING OF\s+([A-Z]+)\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?,\s*(\d{4})", first)
        day = ctx.meeting.local_date
        if meeting_of and day and (meeting_of.group(1).title(), int(meeting_of.group(2))) != (f"{day:%B}", day.year):
            return "report is for another meeting"
        return {"agendaLink": report["url"].rsplit("/", 1)[1], "reportHeading": f"ITEM {item.number}"}
    return linked_reports(ctx, ctx.numbered(), links, verify=verify, kind="staff-report", title="Item {number} staff report",
                          missing="{count} item links for item {number}")


RULES = {"lahontan-item-files": map_lahontan}
