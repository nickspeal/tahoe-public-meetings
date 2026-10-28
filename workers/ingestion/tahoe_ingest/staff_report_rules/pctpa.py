"""Placer County Transportation Planning Agency (``placer-transportation``): memos in the agenda packet.

PCTPA posts one PDF per meeting ("PCTPA Agenda with Memos"), stored as the agenda: three
or four agenda pages, then the packet, whose pages carry a bare stamp at the bottom
right ("40", inserted pages "12A"). The agenda's right column prints "Pg. 40" on a
lettered item's first or second line. That stamp is looked up (never computed: inserted
pages shift the offset) and must sit on exactly one packet page, which opens a memo::

    MEMORANDUM
    TO:            PCTPA Board of Directors                     DATE: October 28, 2026
    SUBJECT:       SR 65 PHASE 1 PROJECT CONSTRUCTION FUNDING AND
                   AGREEMENT APPROVALS
    ACTIONS REQUESTED

Signals: the agenda's "Pg." stamp lands on one page (agenda side), and that page starts
with "MEMORANDUM", prints the meeting's date after "DATE:", and its "SUBJECT:" carries
``MIN_TITLE_OVERLAP`` of the item heading's distinctive words (report side). The memo
runs over the following pages headed "Page N" and stops at an "Agenda Item K,
Attachment 1" page, a new memo, or a page without that header.

When an action item lists numbered motions ("L. Placer-Sacramento Gateway Plan Update /
1. Adopt Resolution ... 2. Authorize ..."), the agenda parser stores the motions as
items "L.1" and "L.2" whose section is the parent heading and keeps no "L" item. Those
sub-items take the parent letter's "Pg." and are checked against the parent heading
(which must also be the heading printed on the agenda line), so both map to the one
memo. Consent sub-items are not mapped: their "Pg." points at the item's attachment
(resolution, claim, agreement), and the consent memo explains several items per page,
which would need paragraph-level citations. The memo's action label is "ACTION
REQUESTED" / "ACTIONS REQUESTED" on its own line.
"""
from __future__ import annotations

import re
from datetime import date

from ..staff_reports import MIN_TITLE_OVERLAP, Context, Item, Mapping, Uncertain, leading, norm_number, title_overlap

MAX_MEMO_PAGES = 12
_PG = re.compile(r"\S\s{2,}(?:(?:Action|Info)\s+)?Pg\.\s*(\d{1,3}[A-Z]?)\s*$")
_LETTER = re.compile(r"^\s{0,3}([A-Z])\.\s+(\S.*)$")
_SUB = re.compile(r"^\s{3,12}(\d{1,2})\.\s+(\S.*)$")
_LIST = re.compile(r"^\s*[a-z]\.\s")  # "a. PCTPA   Pg. 81": a status report's own entry, not the line above it
_DATE = re.compile(r"\bDATE:\s*([A-Z][a-z]+)\.?\s+(\d{1,2}),\s*(\d{4})")
_SUBJECT = re.compile(r"SUBJECT:[ \t]*(.*(?:\n[ \t]{10,}\S.*){0,2})")
_CONTINUATION = re.compile(r"^\s*Page \d+\s*$", re.M)
_ATTACHMENT = re.compile(r"^\s*Agenda Item [A-Z]\b[\s,]*(?:\n\s*)?Attachment", re.M)
_CONSENT = re.compile(r"^\s*Consent Calendar", re.I)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


def packet_stamp(text: str) -> str | None:
    """The bottom-right packet stamp ("40", "12A") of one page, or None for unstamped pages.

    The stamp is the last field of the last non-empty line, set off by three or more
    spaces, after the letterhead's "www.pctpa.net" and a "Rev. 10/21/2026" note are removed.
    """
    lines = [re.sub(r"www\.pctpa\.net|Rev\.\s*\d{1,2}/\d{1,2}/\d{2,4}", "", line) for line in text.rstrip().splitlines()]
    lines = [line for line in lines if line.strip()][-3:]
    for line in reversed(lines):
        found = re.search(r"(?:^|\s{3,})(\d{1,3}[A-Z]?)\s*$", line)
        if found:
            return found.group(1)
    return None


def _columns(line: str) -> str:
    """An agenda line without its right-hand "Action" / "Info" / "Pg. N" column."""
    return re.split(r"\s{3,}", line.strip())[0]


def agenda_rows(pages: dict[int, str]) -> dict[str, dict]:
    """``{"K": {"text", "pg", "page"}, "G.5": {...}}`` from the agenda pages: each lettered item and numbered sub-item,
    the text of its first line, and the "Pg." its first or second line prints (None when there is none)."""
    lines = [(n, line) for n in sorted(pages) for line in pages[n].splitlines()]
    rows: dict[str, dict] = {}
    letter = None
    for index, (page, line) in enumerate(lines):
        heading, sub = _LETTER.match(line), _SUB.match(line)
        if heading:
            letter, key, text = heading.group(1), heading.group(1), heading.group(2)
        elif sub and letter:
            key, text = f"{letter}.{sub.group(1)}", sub.group(2)
        else:
            continue
        pg = _PG.search(line)
        if not pg and index + 1 < len(lines):
            following = lines[index + 1][1]
            if not _LETTER.match(following) and not _SUB.match(following) and not _LIST.match(following):
                pg = _PG.search(following)
        if key in rows:
            rows[key]["repeated"] = True
        else:
            rows[key] = {"text": _columns(text), "pg": pg.group(1) if pg else None, "page": page}
    return rows


def memo_date(text: str) -> date | None:
    found = _DATE.search(text)
    if not found or found.group(1).casefold() not in _MONTHS:
        return None
    try:
        return date(int(found.group(3)), _MONTHS.index(found.group(1).casefold()) + 1, int(found.group(2)))
    except ValueError:
        return None


def _target(item: Item, rows: dict, numbers: set) -> tuple[str, str, str] | str:
    """The agenda "Pg." stamp, the heading to compare with the memo's SUBJECT, and the row key used, or a skip reason."""
    key = norm_number(item.number)
    row = rows.get(key)
    if row is None or row.get("repeated"):
        return "item not found once in the agenda"
    if "." not in key:
        if title_overlap(row["text"], item.title) < 0.8:
            return "agenda line differs from the item title"
        return (row["pg"], item.title, key) if row["pg"] else "agenda gives no packet page"
    if _CONSENT.match(item.section or ""):
        return "consent sub-item: its page reference is the attachment, not a memo"
    if row["pg"]:
        return row["pg"], item.title, key
    parent = key.split(".")[0]
    above = rows.get(parent)
    if parent in numbers or above is None or above.get("repeated") or not above["pg"]:
        return "agenda gives no packet page"
    # A numbered motion under an action heading: the heading (stored as the item's section) owns the memo.
    if not item.section or title_overlap(above["text"], item.section) < 0.8:
        return "parent heading differs from the item's section"
    return above["pg"], item.section, parent


def map_pctpa(ctx: Context) -> list[Mapping]:
    candidates = ctx.numbered()
    cited = {c["documentId"] for item in ctx.meeting.items for c in item.citations}
    packets = {d.sha256: d for d in ctx.meeting.documents if d.media_type == "application/pdf" and d.version_id in cited}
    if len(packets) != 1:
        raise Uncertain("no single agenda packet")
    packet = next(iter(packets.values()))
    agenda_pages = {int(c["page"]) for item in ctx.meeting.items for c in item.citations if c["documentId"] == packet.version_id}
    stored = ctx.stored_pages(packet.version_id)
    rows = agenda_rows({n: stored.get(n, "") for n in range(1, max(agenda_pages, default=0) + 1)})
    numbers = {norm_number(i.number) for i in ctx.meeting.items if i.number.strip()}
    wanted = {}
    for item in candidates:
        target = _target(item, rows, numbers)
        if isinstance(target, str):
            ctx.skip(item, target)
        else:
            wanted[item.id] = target
    if not wanted:
        return []

    def has_stamps(pages):
        return {pg for pg, _, _ in wanted.values()} <= {packet_stamp(t) for t in pages.values()}
    pages = ctx.pages(packet, needed=has_stamps)
    stamped: dict[str, list] = {}
    for number, text in pages.items():
        if number not in agenda_pages and (stamp := packet_stamp(text)):
            stamped.setdefault(stamp, []).append(number)
    mappings = []
    for item in candidates:
        if item.id not in wanted:
            continue
        pg, heading, key = wanted[item.id]
        found = stamped.get(pg, [])
        if len(found) != 1:
            ctx.skip(item, f"packet page {pg} is stamped on {len(found)} pages")
            continue
        start = found[0]
        first = pages[start]
        if not first.lstrip().startswith("MEMORANDUM"):
            ctx.skip(item, f"packet page {pg} is not a memo")
            continue
        if memo_date(first) != ctx.meeting.local_date:
            ctx.skip(item, "memo is for another meeting date")
            continue
        subject = _SUBJECT.search(first)
        subject = " ".join(subject.group(1).split()) if subject else ""
        overlap = title_overlap(heading, subject)
        if overlap < MIN_TITLE_OVERLAP:
            ctx.skip(item, "memo subject differs from the agenda item")
            continue
        end = start
        while end + 1 in pages and end + 1 - start < MAX_MEMO_PAGES:
            top = "\n".join(pages[end + 1].strip().splitlines()[:6])
            if _ATTACHMENT.search(top) or top.lstrip().startswith("MEMORANDUM") or not _CONTINUATION.search(top):
                break
            end += 1
        evidence = {"agendaPageColumn": f"Pg. {pg}", "packetStamp": pg, "reportHeading": f"SUBJECT: {subject}",
                    "titleOverlap": round(overlap, 2), "meetingDate": ctx.meeting.local_date.isoformat(),
                    "pages": f"{start}-{end}"}
        if key != norm_number(item.number):
            evidence["agendaHeading"] = f"{key}. {rows[key]['text']}"
        mappings.append(Mapping(item.id, item.fingerprint, "staff-memo", "Staff memo in the agenda packet", packet.url,
                                start, end, packet.page_count, packet.sha256, evidence, leading(pages, start, end)))
    return mappings


RULES = {"placer-transportation": map_pctpa}
