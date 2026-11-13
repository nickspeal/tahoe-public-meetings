"""Rule ``item-memo-packet``: an agenda packet in which each item's staff memo opens with an "AGENDA ITEM #N" page.

The meeting posts the agenda and a packet that reprints it, followed by a memo for each item
with a report, then the memo's attachments. A memo's first page reads (Truckee Donner Public
Utility District, on CivicClerk)::

    AGENDA ITEM #8

    MEETING DATE: October 7, 2026
    TO:                Board of Directors
    FROM:              <name>, Director of Human Resources & Risk Management
    SUBJECT:           District's Workforce Vacancies, Recruitment, and Retention Efforts

    RECOMMENDATION:

The packet is the one stored document (by hash) with such a page. Signals: exactly one page
dated this meeting opens with the item's number, its "MEETING DATE:" is the meeting's date,
and at least ``MIN_TITLE_OVERLAP`` of its "SUBJECT:" words appear in the item title (that
direction, because agenda titles add "Consideration of"). A memo header dated another day
(an earlier memo kept as an attachment) is neither a start nor an end. The memo runs from its
header page to the page before the next header dated this meeting, or to the packet's last
page, so a department update's several memos and every attachment stay with their item.
Closed-session items are skipped. The memo's action label is "RECOMMENDATION:".
"""
from __future__ import annotations

import re

from ..staff_reports import (MIN_TITLE_OVERLAP, PRINTED_DATE, Context, Mapping, Uncertain, leading, norm_number,
                             parse_printed_date, stored_pdf_packets, title_overlap)

_HEADER = re.compile(r"^\s*AGENDA ITEM\s*#\s*(\d{1,2})\s*$")
_DATE = re.compile(rf"MEETING DATE:\s*({PRINTED_DATE})")
# The subject runs on until a blank line or the next "APPROVED BY:" style label.
_SUBJECT = re.compile(r"^\s*SUBJECT:[ \t]*(.*(?:\n(?![ \t]*[A-Z][A-Z ]+:)[ \t]*\S.*){0,2})", re.M)
_CLOSED = re.compile(r"closed session", re.I)


def memo_header(text: str) -> tuple[str, object, str] | None:
    """``(item number, printed meeting date or None, subject)`` for a page whose first line is "AGENDA ITEM #N"."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    header = _HEADER.match(first)
    if not header:
        return None
    day = _DATE.search(text[:1500])
    subject = _SUBJECT.search(text[:3000])
    return (header.group(1), parse_printed_date(day.group(1)) if day else None,
            " ".join(subject.group(1).split()) if subject else "")


def map_item_memos(ctx: Context) -> list[Mapping]:
    items = [i for i in ctx.numbered() if not _CLOSED.search(i.section or "")]
    for item in ctx.meeting.items:
        if item.number.strip() and _CLOSED.search(item.section or "") and item.id not in ctx.skipped:
            ctx.skip(item, "closed session")
    pdfs = stored_pdf_packets(ctx, re.compile(r"^\s*AGENDA ITEM\s*#\s*\d", re.M))
    if len(pdfs) > 1:
        raise Uncertain("more than one agenda packet")
    if not pdfs:
        for item in items:
            ctx.skip(item, "no agenda packet with item memos")
        return []
    packet = pdfs[0]
    pages = ctx.stored_pages(packet.version_id)
    headers = {n: found for n, text in sorted(pages.items()) if (found := memo_header(text))}
    # A header dated another meeting is an attachment; an undated one still ends the memo before it.
    own = sorted(n for n, (_, day, _) in headers.items() if day in (None, ctx.meeting.local_date))
    last = max(packet.page_count or 0, max(pages))
    mappings = []
    for item in items:
        label = norm_number(item.number)
        found = [n for n in own if headers[n][0] == label]
        if len(found) != 1:
            ctx.skip(item, f"{len(found)} memos headed AGENDA ITEM #{label}" if found else f"no memo headed AGENDA ITEM #{label}")
            continue
        start = found[0]
        _, day, subject = headers[start]
        if day != ctx.meeting.local_date:
            ctx.skip(item, "memo prints no meeting date")
            continue
        overlap = title_overlap(subject, item.title) if subject else 0.0
        if overlap < MIN_TITLE_OVERLAP:
            ctx.skip(item, "memo subject differs from the agenda item")
            continue
        end = next((n - 1 for n in own if n > start), last)
        mappings.append(Mapping(item.id, item.fingerprint, "staff-memo", "Staff memo in the agenda packet",
                                packet.url, start, end, packet.page_count, packet.sha256,
                                {"header": f"AGENDA ITEM #{label}", "pages": f"{start}-{end}",
                                 "reportHeading": f"SUBJECT: {subject}", "titleOverlap": round(overlap, 2),
                                 "meetingDate": ctx.meeting.local_date.isoformat()},
                                leading(pages, start, end)))
    return mappings


RULES = {"item-memo-packet": map_item_memos}
