"""Rule ``ntpud-packet``: North Tahoe Public Utility District staff reports in the stored agenda packet.

Each meeting page links an "Agenda and Packet" PDF, which discovery stores as
``ntpud-agenda-packet``. Its first pages are the agenda, and unlike the standalone agenda
they end each item with the packet pages of its report::

    E.   CONSENT CALENDAR: ...
         3. Award a Construction Contract and Authorize the General Manager to Execute the
            Agreement with KG Walters Construction Co., Inc ... (Pages 15-18)

The packet's pages are stamped with their own PDF page number, and each report opens
with ``DATE:  October 13, 2026   ITEM: E-3``. Items are read from the stored packet, so
the rule needs no download (and works while the district's site refuses requests).

Signals: the packet's own agenda row for the item (its number under its section letter,
and at least 0.8 of the item title's distinctive words) prints "(Pages X-Y)" (agenda
side), and PDF page X prints the meeting's date after "DATE:" and "ITEM:" with the same
letter and number ("D.1" <-> "D-1"; report side). The report is pages X..Y as printed;
a range in which another item's report header appears stores nothing. An item whose
report is split in parts ("ITEM: G-3a", "ITEM: G-3b") maps to the whole printed range. Agenda rows may
wrap over several lines and across a page break, and pdftotext can glue the previous
page's stamp to the first line of the next ("11. Approve" for stamp 1 + item 1), which
the row reader undoes. Minutes, closed-session items, and items without a page range
are skipped. Reports with an action carry "RECOMMENDATION:" (text on the following
lines) and end with "MOTION: Approve Staff Recommendation".
"""
from __future__ import annotations

import re
from datetime import date

from ..staff_reports import Context, Mapping, Uncertain, leading, norm_number, title_overlap

ROW_TITLE_OVERLAP = 0.8
MAX_AGENDA_PAGES = 6
_HEADER = re.compile(r"DATE:\s*([A-Z][a-z]+)\.?\s+(\d{1,2}),\s*(\d{4})\s{2,}ITEM:\s*([A-Z])\s*-\s*(\d{1,2})([a-z]?)\b")
_ANY_HEADER = re.compile(r"^\s*DATE:.*\bITEM:\s*([A-Z])\s*-\s*(\d{1,2})", re.M)
_PAGES = re.compile(r"\(Pages?\s+(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?\)")
_LETTER = re.compile(r"^\s{0,3}([A-Z])\.\s+(\S.*)$")
_SUB = re.compile(r"^\s{2,}(\d{1,2})\.\s+(\S.*)$")
_FOOTER = re.compile(r"^\s*Agenda Posted on\b")
_MINUTES = re.compile(r"\bminutes\b", re.I)
_CLOSED = re.compile(r"closed session", re.I)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


def report_header(text: str) -> tuple[date | None, str] | None:
    """``(printed date, "E.3")`` from a report's "DATE: ... ITEM: E-3" line, or None (sub-reports keep their letter: "G.3A")."""
    found = _HEADER.search(text)
    if not found:
        return None
    month, day, year, letter, number, suffix = found.groups()
    try:
        printed = date(int(year), _MONTHS.index(month.casefold()) + 1, int(day)) if month.casefold() in _MONTHS else None
    except ValueError:
        printed = None
    return printed, f"{letter}.{number}{suffix.upper()}"


def agenda_lines(pages: dict[int, str]) -> list[str]:
    """The agenda pages' lines in order, without blank lines, page footers, or page stamps.

    A page's stamp is its last line (a bare number). When the stamp is missing there and
    the next page's first line starts with it glued to an item number ("11. Approve" on
    page 2 after page 1), the stamp is removed from that line.
    """
    lines: list[str] = []
    stamp_missing = False
    for number in sorted(pages):
        page = [line for line in pages[number].splitlines() if line.strip()]
        if page and stamp_missing and number > 1:
            glued = re.match(rf"^\s*{number - 1}(\d{{1,2}}\.\s.*)$", page[0])
            if glued:
                page[0] = "         " + glued.group(1)
        if page and number > 1 and re.match(r"\d{1,2}\.\s", page[0]):
            page[0] = "         " + page[0]  # stored page text starts at the first character, dropping the indent
        stamp_missing = not (page and re.fullmatch(rf"\s*{number}\s*", page[-1]))
        if not stamp_missing:
            page = page[:-1]
        lines.extend(line for line in page if not _FOOTER.match(line))
    return lines


def agenda_rows(lines: list[str]) -> dict[str, list]:
    """``{"E.3": [row text, ...]}``: each section letter and numbered item with its wrapped lines joined."""
    rows: dict[str, list] = {}
    letter, key, text = None, None, []

    def close():
        if key:
            rows.setdefault(key, []).append(" ".join(" ".join(text).split()))
    for line in lines:
        heading, sub = _LETTER.match(line), _SUB.match(line)
        if heading:
            close()
            letter, key, text = heading.group(1), heading.group(1), [heading.group(2)]
        elif sub and letter:
            close()
            key, text = f"{letter}.{sub.group(1)}", [sub.group(2)]
        elif key:
            text.append(line)
    close()
    return rows


def map_ntpud(ctx: Context) -> list[Mapping]:
    items = []
    for item in ctx.numbered():
        if _CLOSED.search(item.section or ""):
            ctx.skip(item, "closed session")
        elif _MINUTES.search(item.title):
            ctx.skip(item, "meeting minutes")
        else:
            items.append(item)
    packets = {d.sha256: d for d in ctx.meeting.documents if d.media_type == "application/pdf" and (
        d.source_document_id == "ntpud-agenda-packet" or re.search(r"-Agenda-and-Packet\.pdf$", d.url, re.I))}
    if len(packets) > 1:
        raise Uncertain("more than one agenda packet")
    if not packets:
        for item in items:
            ctx.skip(item, "agenda packet not captured")
        return []
    packet = next(iter(packets.values()))
    stored = ctx.stored_pages(packet.version_id)
    agenda = {}
    for number in range(1, MAX_AGENDA_PAGES + 1):
        if number not in stored or _ANY_HEADER.search(stored[number]):
            break
        agenda[number] = stored[number]
    if not agenda:
        raise Uncertain("the packet does not open with the agenda")
    rows = agenda_rows(agenda_lines(agenda))
    wanted = {}
    for item in items:
        found = rows.get(norm_number(item.number), [])
        if len(found) != 1:
            ctx.skip(item, f"item {item.number} appears {len(found)} times in the packet's agenda")
            continue
        row = found[0]
        if title_overlap(item.title, row) < ROW_TITLE_OVERLAP:
            ctx.skip(item, "packet agenda row differs from the item title")
            continue
        printed = _PAGES.findall(row)
        if len(printed) != 1:
            ctx.skip(item, "packet agenda gives no page range" if not printed else "packet agenda gives several page ranges")
            continue
        first, last = int(printed[0][0]), int(printed[0][1] or printed[0][0])
        if not 1 <= first <= last <= packet.page_count:
            ctx.skip(item, f"page range {first}-{last} is outside the packet")
            continue
        wanted[item.id] = (first, last, row)
    if not wanted:
        return []
    pages = ctx.pages(packet, needed=lambda p: all(n in p for first, last, _ in wanted.values()
                                                   for n in range(first, last + 1)))
    mappings = []
    for item in items:
        if item.id not in wanted:
            continue
        first, last, row = wanted[item.id]
        label = norm_number(item.number)
        header = report_header(pages.get(first, ""))
        if header is None:
            ctx.skip(item, f"page {first} has no report header")
            continue
        printed, number = header
        if number not in (label, f"{label}A"):  # an item split into sub-reports opens with the first ("ITEM: G-3a")
            ctx.skip(item, f"page {first} is headed ITEM: {number.replace('.', '-')}")
            continue
        if printed != ctx.meeting.local_date:
            ctx.skip(item, "report is for another meeting date")
            continue
        others = {f"{m[0]}.{m[1]}" for n in range(first + 1, last + 1) for m in _ANY_HEADER.findall(pages.get(n, ""))}
        if others - {label}:
            ctx.skip(item, f"pages {first}-{last} include the report for item {sorted(others - {label})[0]}")
            continue
        printed_item = f"ITEM: {number.replace('.', '-')[:len(label)]}{number[len(label):].lower()}"
        evidence = {"agendaPages": re.search(r"\(Pages?[^)]*\)", row).group(), "header": printed_item,
                    "pages": f"{first}-{last}", "meetingDate": ctx.meeting.local_date.isoformat()}
        tail = [line for line in pages[first].splitlines() if line.strip()]
        if tail and tail[-1].strip() == str(first):
            evidence["packetStamp"] = str(first)
        mappings.append(Mapping(item.id, item.fingerprint, "staff-report", "Staff report in the agenda packet", packet.url,
                                first, last, packet.page_count, packet.sha256, evidence, leading(pages, first, last)))
    return mappings


RULES = {"ntpud-packet": map_ntpud}
