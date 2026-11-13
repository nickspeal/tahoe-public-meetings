"""Rule ``agenda-item-memo-links``: an agenda PDF whose items link their own memo, named for the item number.

The North Tahoe Fire Protection District posts a "LINKED" agenda PDF: each item's line
carries a link annotation to the item's file on the district's website, whose name starts
with the item number and a dash ("11-CL-TTAD-Funding-Agreement-for-River-Road-....pdf",
"7-CL-LAFCO-Nomination-Alternate-Seat.pdf"). An item's memo opens::

    To:   NTFPD Board of Directors
    From: ..., Fire Chief
    Date: 10/27/2026
    SUBJECT:       AGENDA ITEM #11, CONSIDER RESOLUTION 19-2026 AUTHORIZING
                   THE FIRE CHIEF, OR HIS DESIGNEE, TO ENTER INTO ...

Signals: exactly one agenda link whose file name starts with the item's number (agenda
side), and the memo's SUBJECT line names "AGENDA ITEM #<number>" (some memos leave out
the "#") followed by at least ``MIN_TITLE_OVERLAP`` of the item title's distinctive words
(report side). The memo's "Date:" is when it was written, so it is not checked against
the meeting. Finance and department reports under a numbered heading (13.1, 14.3) link
files numbered without the dot ("131-...", "143-...") that print no such SUBJECT line, and
lettered parts of an item (9.a, 9.b) share their item's memo, so these stay unmapped.
"""
from __future__ import annotations

import re
import urllib.parse

from ..staff_reports import (MIN_TITLE_OVERLAP, Context, Mapping, Uncertain, linked_reports, norm_number, pdf_agenda,
                             title_overlap)

_FILE = re.compile(r"^(\d{1,2})-[^/]*\.pdf$", re.I)
_SUBJECT = re.compile(r"SUBJECT:\s*(AGENDA\s+ITEM\s*#?\s*(\d{1,2}))\b[\s,.:;–-]*(.*?)(?=\n[ \t]*\n|\Z)", re.S | re.I)


def file_name(url: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1])


def map_item_memo_links(ctx: Context) -> list[Mapping]:
    agenda = pdf_agenda(ctx, prefer=r"agenda")
    fetched = ctx.read(agenda.url, links=True)
    if fetched["sha256"] != agenda.sha256:
        raise Uncertain("the agenda PDF changed since it was captured")
    links: dict[str, set] = {}
    for link in fetched["links"]:
        named = _FILE.match(file_name(link["url"]))
        if named and urllib.parse.urlparse(link["url"]).scheme == "https":
            links.setdefault(named.group(1), set()).add(link["url"])

    def verify(item, report):
        first = report["pages"][0]["text"] if report["pages"] else ""
        subject = _SUBJECT.search(first)
        if not subject or subject.group(2) != norm_number(item.number):
            return "report does not name this item number"
        overlap = title_overlap(item.title, subject.group(3))
        if overlap < MIN_TITLE_OVERLAP:
            return "report title differs from the agenda item"
        return {"agendaLink": file_name(report["url"]), "reportHeading": " ".join(subject.group(1).split()),
                "titleOverlap": round(overlap, 2)}
    return linked_reports(ctx, ctx.numbered(), links, verify=verify, kind="staff-report", title="Item {number} staff report",
                          missing="{count} item links for item {number}")


RULES = {"agenda-item-memo-links": map_item_memo_links}
