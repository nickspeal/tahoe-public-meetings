"""Incline Village GID (``ivgid``, CivicClerk): staff memos inside the stored agenda packet.

Every memo in the packet opens with the item's stamp and a dated memo header:
"ITEM G.1." / "MEMORANDUM" / "SUBJECT: <item title>" / "DATE: October 14, 2026". The
memo runs from that page to the page before the next item's stamp ("Item J.1." on the
long-range calendar counts too). Two signals agree: the stamp names the agenda item's
number with its title beneath it, and the memo is dated the meeting day. A stamp on a
memo dated another day (an earlier memo kept in an item's backup, such as June's
"ITEM H.3." inside October's I.3) is ignored, both as a start and as an end. A
sub-item's memo ("ITEM G.2.A.") belongs to its item, which the agenda lists as G.2.

The packet is read from stored pages, so a scan downloads nothing. The CivicClerk API's
per-item attachments (named "Item G.1. ... - pages 5 - 9") are not used: their page
numbers drift from the packet's by up to three pages, and their links are expiring
signed URLs that must not be stored.
"""
from __future__ import annotations

import re

from ..staff_reports import MIN_TITLE_OVERLAP, Context, Mapping, Uncertain, leading, long_date, norm_number, title_overlap

# The stamp is a page's first line: alone ("ITEM G.1."), or right-aligned after a running title
# ("LONG RANGE CALENDAR      Item J.1."). Minutes quoting "Item G.3." further down do not count.
_STAMP = re.compile(r"^\s*(?:\S.*?\s{2,})?ITEM\s+([A-Z])\.\s*(\d{1,2})\.(?:\s*([A-Z])\.)?\s*$", re.I)
_MEMO_DATE = re.compile(r"^\s*DATE:\s*([A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4})", re.M)


def _first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")


def page_stamp(text: str) -> tuple[str, str | None] | None:
    """``(item number, memo date or None)`` for a page whose first line carries an "ITEM G.1." stamp."""
    stamp = _STAMP.match(_first_line(text))
    if not stamp:
        return None
    date = _MEMO_DATE.search(text[:3000]) if re.search(r"\bMEMORANDUM\b", text[:800]) else None
    return f"{stamp.group(1).upper()}.{stamp.group(2)}", " ".join(date.group(1).split()) if date else None


def map_ivgid(ctx: Context) -> list[Mapping]:
    items = ctx.numbered()
    if not items or not ctx.meeting.local_date:
        return []
    packets = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
               and re.search(r"packet", d.source_document_id + " " + d.title, re.I)]
    if len(packets) != 1:
        raise Uncertain("no single agenda packet")
    packet = packets[0]
    pages = ctx.pages(packet, needed=bool)  # stored text (blank pages are not stored), else one download
    day = long_date(ctx.meeting.local_date).casefold()
    stamps = {}  # page -> (item number, dated this meeting day)
    for number in sorted(pages):
        found = page_stamp(pages[number])
        if not found:
            continue
        item_number, dated = found
        if dated and dated.replace(" ,", ",").casefold() != day:
            continue  # An earlier memo in an item's backup: neither a start nor an end.
        stamps[number] = (item_number, bool(dated))
    mappings = []
    for item in items:
        number = norm_number(item.number)
        mine = [p for p, (n, _) in stamps.items() if n == number]
        if not mine:
            ctx.skip(item, f"no packet memo stamped ITEM {item.number}.")
            continue
        start = mine[0]
        later = [p for p, (n, _) in stamps.items() if p > start and n != number]
        end = (later[0] - 1) if later else max(pages)
        if any(p > end for p in mine):
            ctx.skip(item, f"packet pages stamped ITEM {item.number}. are interrupted by another item")
            continue
        if not stamps[start][1]:
            ctx.skip(item, f"ITEM {item.number}. memo is not dated {long_date(ctx.meeting.local_date)}")
            continue
        overlap = title_overlap(item.title, pages[start][:2000])
        if overlap < MIN_TITLE_OVERLAP:
            ctx.skip(item, "memo subject differs from the agenda item")
            continue
        header = " ".join(_first_line(pages[start]).split())
        mappings.append(Mapping(item.id, item.fingerprint, "staff-memo", "Staff memo in the agenda packet", packet.url, start, end,
                                packet.page_count, packet.sha256,
                                {"header": header, "pages": f"{start}-{end}", "titleOverlap": round(overlap, 2),
                                 "meetingDate": ctx.meeting.local_date.isoformat()}, leading(pages, start, end)))
    return mappings


RULES = {"ivgid": map_ivgid}
