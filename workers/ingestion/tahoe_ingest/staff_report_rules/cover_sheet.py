"""Rule ``cover-sheet-packet``: an agenda packet in which each item's report opens with a cover sheet.

The meeting posts one PDF: the agenda pages, then, for each item with a report, a one-page
cover sheet followed by the item's attachments. A cover sheet reads (Truckee-Donner
Recreation and Park District)::

                            BOARD AGENDA ITEM COVER SHEET

    Item #:          6.1 – Adoption Policy #247 - Boundaries for
                     Employees and Volunteers Interacting with Minors

    Presenter:       <name>, Human Resources Manager/ District Clerk

    Recommendation:

The packet is the one stored document (by hash) whose pages print the ``heading`` parameter
(default "BOARD AGENDA ITEM COVER SHEET"). Signals: exactly one cover sheet names the item's
number after "Item #:" ("8.0" names item 8), and at least ``MIN_TITLE_OVERLAP`` of the cover
sheet's title words appear in the item title (that direction, because agenda titles add
"Discussion / Possible Action"). Cover sheets print no meeting date; the packet is the
meeting's own captured document, whose opening pages are the agenda the items were read from.
The report runs from its cover sheet to the page before the next cover sheet, whether or not
that one's number could be read, or to the packet's last page. Closed-session items have no
cover sheet and are skipped. The cover sheet's action label is "Recommendation:".
"""
from __future__ import annotations

import re

from ..staff_reports import (MIN_TITLE_OVERLAP, Context, Mapping, Regex, Uncertain, leading, norm_number,
                             stored_pdf_packets, title_overlap)

DEFAULT_HEADING = r"BOARD AGENDA ITEM COVER SHEET"
# The title runs on until a blank line or the next "Presenter:" style label.
_ITEM = re.compile(r"^\s*Item\s*#:[ \t]*(\d{1,2}(?:\.\d{1,2})?)[ \t]*[–-]?[ \t]*(.*(?:\n(?![ \t]*[A-Z][a-z]+:)[ \t]*\S.*){0,2})", re.M)
_CLOSED = re.compile(r"closed session", re.I)


def cover_label(text: str) -> tuple[str, str] | None:
    """The item number and title a cover sheet prints after "Item #:" ("8.0" -> "8"), or None."""
    found = _ITEM.search(text)
    if not found:
        return None
    number = norm_number(found.group(1))
    return re.sub(r"\.0$", "", number), " ".join(found.group(2).split())


def map_cover_sheets(ctx: Context, *, heading: Regex = DEFAULT_HEADING) -> list[Mapping]:
    cover = re.compile(heading)
    items = [i for i in ctx.numbered() if not _CLOSED.search(i.section or "")]
    for item in ctx.meeting.items:
        if item.number.strip() and _CLOSED.search(item.section or "") and item.id not in ctx.skipped:
            ctx.skip(item, "closed session")
    pdfs = stored_pdf_packets(ctx, cover)
    if len(pdfs) > 1:
        raise Uncertain("more than one agenda packet")
    if not pdfs:
        for item in items:
            ctx.skip(item, "no agenda packet with cover sheets")
        return []
    packet = pdfs[0]
    pages = ctx.stored_pages(packet.version_id)
    covers = sorted(n for n, text in pages.items() if cover.search(text))
    labelled: dict[str, list[tuple[int, str]]] = {}
    for number in covers:
        if label := cover_label(pages[number]):
            labelled.setdefault(label[0], []).append((number, label[1]))
    last = max(packet.page_count or 0, max(pages))
    mappings = []
    for item in items:
        label = norm_number(item.number)
        found = labelled.get(label, [])
        if len(found) != 1:
            ctx.skip(item, f"{len(found)} cover sheets name item {label}" if found else f"no cover sheet names item {label}")
            continue
        start, printed = found[0]
        overlap = title_overlap(printed, item.title)
        if overlap < MIN_TITLE_OVERLAP:
            ctx.skip(item, "cover sheet title differs from the agenda item")
            continue
        end = next((n - 1 for n in covers if n > start), last)
        mappings.append(Mapping(item.id, item.fingerprint, "staff-report", "Item cover sheet and report in the agenda packet",
                                packet.url, start, end, packet.page_count, packet.sha256,
                                {"header": cover.search(pages[start]).group(), "packetStamp": f"Item #: {label}",
                                 "pages": f"{start}-{end}",
                                 "reportHeading": printed, "titleOverlap": round(overlap, 2)},
                                leading(pages, start, end)))
    return mappings


RULES = {"cover-sheet-packet": map_cover_sheets}
