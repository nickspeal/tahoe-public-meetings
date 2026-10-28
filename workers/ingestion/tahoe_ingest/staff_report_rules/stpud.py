"""South Tahoe Public Utility District (``stpud``): board memos in the single agenda packet.

Each regular meeting posts one PDF, "<date> District Board Agenda Packet.pdf": the agenda
pages, then a memo for each consent and action item. Discovery stores that packet as the
agenda, sometimes twice under two URLs with the same hash, so the packet is the one
stored document (by hash) whose pages print "BOARD AGENDA ITEM". A memo's first page
reads::

    BOARD AGENDA ITEM 6c
    MEETING DATE:               October 15, 2026
    ITEM – PROJECT NAME:        2026 Water Meter Installation Project
    REQUESTED BOARD ACTION: 1) Approve Change Order No. 2 ...

Signals: exactly one packet page is headed with the item's number (dots dropped, "6.c"
-> "6c"), and that page's "MEETING DATE:" is the meeting's date (zero-padded days
allowed) and at least ``MIN_TITLE_OVERLAP`` of its "ITEM – PROJECT NAME:" words appear
in the item title (that direction, because some agenda titles also carry the requested
action). The memo runs from the header page to the page printing "CONCURRENCE WITH
REQUESTED ACTION" (its sign-off block), never into the attachments that follow; a memo
whose sign-off is missing within ``MAX_MEMO_PAGES`` or before the next header stores
nothing. Closed-session memos are boilerplate and are skipped, as are items without a
memo (claims, minutes, staff and General Manager reports). The memo's action label is
"REQUESTED BOARD ACTION:".
"""
from __future__ import annotations

import re
from datetime import date

from ..staff_reports import MIN_TITLE_OVERLAP, Context, Mapping, Uncertain, leading, norm_number, title_overlap

MAX_MEMO_PAGES = 6
_HEADER = re.compile(r"^\s*BOARD AGENDA ITEM\s+(\d+\s*[A-Za-z]?)\s*$", re.M)
_DATE = re.compile(r"MEETING DATE:\s*([A-Z][a-z]+)\s+(\d{1,2}),\s*(\d{4})")
_NAME = re.compile(r"ITEM\s*[–-]\s*PROJECT NAME:[ \t]*(.*(?:\n[ \t]{20,}\S.*){0,2})")
_MEMO_END = re.compile(r"CONCURRENCE WITH REQUESTED ACTION")
_CLOSED = re.compile(r"closed session", re.I)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


def memo_date(text: str) -> date | None:
    """The memo's "MEETING DATE:" ("September 03, 2026" is allowed), or None."""
    found = _DATE.search(text)
    if not found or found.group(1).casefold() not in _MONTHS:
        return None
    try:
        return date(int(found.group(3)), _MONTHS.index(found.group(1).casefold()) + 1, int(found.group(2)))
    except ValueError:
        return None


def _header_number(text: str) -> str | None:
    found = _HEADER.search(text)
    return re.sub(r"\s+", "", found.group(1)).upper() if found else None


def map_stpud(ctx: Context) -> list[Mapping]:
    items = [i for i in ctx.numbered() if not _CLOSED.search(i.section or "")]
    for item in ctx.meeting.items:
        if item.number.strip() and _CLOSED.search(item.section or "") and item.id not in ctx.skipped:
            ctx.skip(item, "closed session")
    pdfs = {}
    for document in sorted(ctx.meeting.documents, key=lambda d: ("%2B" in d.url, d.source_document_id)):
        if document.media_type == "application/pdf" and document.sha256 not in pdfs:
            if any(_header_number(text) for text in ctx.stored_pages(document.version_id).values()):
                pdfs[document.sha256] = document
    if len(pdfs) > 1:
        raise Uncertain("more than one agenda packet")
    if not pdfs:
        for item in items:
            ctx.skip(item, "no agenda packet with board memos")
        return []
    packet = next(iter(pdfs.values()))
    pages = ctx.stored_pages(packet.version_id)
    headed: dict[str, list] = {}
    for number, text in pages.items():
        if label := _header_number(text):
            headed.setdefault(label, []).append(number)
    mappings = []
    for item in items:
        label = norm_number(item.number).replace(".", "")
        found = headed.get(label, [])
        if len(found) != 1:
            ctx.skip(item, f"{len(found)} packet pages headed BOARD AGENDA ITEM {label.lower()}" if found
                     else f"no memo headed BOARD AGENDA ITEM {label.lower()}")
            continue
        start = found[0]
        first = pages[start]
        if memo_date(first) != ctx.meeting.local_date:
            ctx.skip(item, "memo is for another meeting date")
            continue
        name = _NAME.search(first)
        project = " ".join(name.group(1).split()) if name else ""
        overlap = title_overlap(project, item.title) if project else 0.0
        if overlap < MIN_TITLE_OVERLAP:
            ctx.skip(item, "memo project name differs from the agenda item")
            continue
        end = start
        while not _MEMO_END.search(pages.get(end, "")):
            end += 1
            if end - start >= MAX_MEMO_PAGES or end not in pages or _header_number(pages[end]):
                end = None
                break
        if end is None:
            ctx.skip(item, "memo sign-off (CONCURRENCE WITH REQUESTED ACTION) not found")
            continue
        mappings.append(Mapping(item.id, item.fingerprint, "staff-memo", "Board agenda item memo in the agenda packet",
                                packet.url, start, end, packet.page_count, packet.sha256,
                                {"header": f"BOARD AGENDA ITEM {label.lower()}", "pages": f"{start}-{end}",
                                 "reportHeading": f"ITEM – PROJECT NAME: {project}", "titleOverlap": round(overlap, 2),
                                 "meetingDate": ctx.meeting.local_date.isoformat()},
                                leading(pages, start, end)))
    return mappings


RULES = {"stpud": map_stpud}
