"""Tahoe Transportation District (``ttd``, ``ttd-rtcpc``): staff memos in the agenda packet, found from the agenda's Page column.

The agenda's Page column refers to the packet's stamped "~ Page N ~" footer, and every
page of a staff memo and its attachments carries an "AGENDA ITEM: IV.A." footer. The
memo runs from the stamped page for as long as the footer names the same item. A
single posted PDF may be both the agenda and the packet. The Regional Transit and
Capital Programs Committee's packets use the same layout; their footer reads either
"AGENDA ITEM:" or "RTCPC AGENDA ITEM:", and older packets have none (items are skipped).
"""
from __future__ import annotations

import re

from ..staff_reports import Context, Mapping, Uncertain, agenda_page_column, leading, norm_number

_TTD_STAMP = re.compile(r"~\s*Page\s+(\d+)\s*~")
# The board's own footer, not a committee's ("FPC AGENDA ITEM: III.B." follows it on finance memos).
_TTD_FOOTER = re.compile(r"(?:^|\s{2,})AGENDA ITEM:?\s*([IVXLC]+(?:\.[A-Z0-9]+)*)\.?", re.M)
_RTCPC_FOOTER = re.compile(r"(?:^|\s{2,})(?:RTCPC\s+)?AGENDA ITEM:?\s*([IVXLC]+(?:\.[A-Z0-9]+)*)\.?", re.M)


def map_ttd(ctx: Context, footer: re.Pattern = _TTD_FOOTER) -> list[Mapping]:
    candidates = ctx.numbered()
    pdfs = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"]
    packets = [d for d in pdfs if re.search(r"packet", d.source_document_id + " " + d.title, re.I)]
    if len(packets) != 1:  # A single posted PDF is both the agenda and the packet.
        cited = {c["documentId"] for item in candidates for c in item.citations}
        packets = [d for d in pdfs if d.version_id in cited]
    if len(packets) != 1:
        raise Uncertain("no single agenda packet")
    packet = packets[0]
    wanted = {}
    for item in candidates:
        page = agenda_page_column(item, ctx)
        if page is None:
            ctx.skip(item, "agenda gives no packet page")
        else:
            wanted[item.id] = page
    if not wanted:
        return []

    def has_stamps(pages):
        stamps = {int(m) for text in pages.values() for m in _TTD_STAMP.findall(text)}
        return set(wanted.values()) <= stamps
    pages = ctx.pages(packet, needed=has_stamps)
    stamp_to_page: dict[int, list] = {}
    footers = {}
    for number, text in pages.items():
        for stamp in set(_TTD_STAMP.findall(text)):
            stamp_to_page.setdefault(int(stamp), []).append(number)
        labels = footer.findall(text)
        if labels:
            footers[number] = norm_number(labels[-1])
    mappings = []
    for item in candidates:
        if item.id not in wanted:
            continue
        start = stamp_to_page.get(wanted[item.id], [])
        if len(start) != 1:
            ctx.skip(item, f"packet page {wanted[item.id]} is stamped on {len(start)} pages")
            continue
        start = start[0]
        label = norm_number(item.number)
        if footers.get(start) != label:
            ctx.skip(item, f"packet page {wanted[item.id]} is not marked AGENDA ITEM: {item.number}")
            continue
        end = start
        while footers.get(end + 1) == label:
            end += 1
        mappings.append(Mapping(item.id, item.fingerprint, "staff-memo", "Staff memo in the agenda packet",
                                packet.url, start, end, packet.page_count, packet.sha256,
                                {"agendaPageColumn": wanted[item.id], "packetStamp": f"~ Page {wanted[item.id]} ~",
                                 "footer": f"AGENDA ITEM: {item.number}."},
                                leading(pages, start, end)))
    return mappings


RULES = {"ttd": map_ttd, "ttd-rtcpc": lambda ctx: map_ttd(ctx, _RTCPC_FOOTER)}
