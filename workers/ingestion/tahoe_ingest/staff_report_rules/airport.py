"""Rule ``airport-tab-files``: Truckee Tahoe Airport District, one staff report file per agenda TAB.

The meeting's official page (``/board_meetings/<id>/attachments``, HTML) lists every
file by its printed name: "TAB 04 - Staff Report Airfield NOE 2026.pdf", with
letter-suffixed files ("TAB 04a - NOE_Memo_Airfield.pdf") as that report's attachments.
The agenda prints a dashed leader and the TAB at the end of an item's (possibly wrapped)
line, "4. Notice of Exemption ... ------- TAB 04". The TAB is not the item number: untabbed
items (presentations, the counsel report) shift it. A staff report's first page reads::

    AGENDA ITEM: ___10____
    BOARD OF DIRECTORS STAFF REPORT
    AGENDA TITLE:         Truckee Tahoe Airport District Surplus Land Sale
    MEETING DATE:         October 21, 2026
    ACTION RECOMMENDED: Consider approval of ...

Signals: the agenda row for the item names one TAB, and exactly one base file on the
attachments page carries that TAB (agenda side); the report's "AGENDA ITEM:" prints the
item number and its "MEETING DATE:" is the meeting's date ("Oct 21, 2026" and "JUNE 24,
2026" both occur; report side). When the header number is left blank, its "AGENDA
TITLE:" must instead hold ``TITLE_FALLBACK`` of the item title's distinctive words. A
header naming another number (General Manager reports numbered by TAB), another
meeting's date, or no header at all (minutes, bills, budget workbooks, letters) stores
nothing, and so do "TAB 00" late files the agenda never names. The report's action
labels are "RECOMMENDED ACTION:" and "ACTION RECOMMENDED:"; "MOTION:" and "SAMPLE
MOTION(S):" are motion wording, not the recommendation.
"""
from __future__ import annotations

import re
from datetime import date

from ..staff_reports import (PRINTED_DATE, Context, Item, Mapping, Uncertain, linked_reports, norm_number, parse_printed_date,
                             title_overlap)

TITLE_FALLBACK = 0.8
MAX_REPORTS = 40  # downloads per meeting; items beyond this are skipped
ROW_TITLE_OVERLAP = 0.8
_ROW = re.compile(r"^\s*(\d{1,2})\.\s+(\S.*)$")
_HEADING = re.compile(r"^[A-Z][A-Z &/,'’-]{3,}:?\s*$")  # "ACTION ITEMS", "MANAGEMENT TEAM REPORTS"
_AGENDA_TAB = re.compile(r"-{3,}\s*TAB\s*(\d{1,2})\s*$", re.I)
_FILE_TAB = re.compile(r"^\s*TAB\s*(\d{1,2})\s*-", re.I)  # "TAB 04 - ...", not "TAB 04a - ..."
_ITEM = re.compile(r"^[ \t]*AGENDA ITEM:?[ \t_]*(\d{1,2})?[ \t_]*$", re.M | re.I)  # also "Agenda Item___09____"
_TITLE = re.compile(r"AGENDA TITLE:\s*(.+?)(?=\n\s*\n|\n\s*MEETING DATE:|\Z)", re.S)
_DATE = re.compile(rf"MEETING DATE:\s*({PRINTED_DATE})")


def meeting_date(text: str) -> date | None:
    """The report's "MEETING DATE:" ("October 21, 2026", "Oct 21, 2026", "JUNE 24, 2026"), or None."""
    found = _DATE.search(text)
    return parse_printed_date(found.group(1)) if found else None


def agenda_tabs(pages: dict[int, str]) -> dict[str, list]:
    """``{"10": [(row text, {TAB numbers})]}``: each numbered agenda row with its wrapped lines and the TABs they print."""
    rows: dict[str, list] = {}
    current = None
    for number in sorted(pages):
        for line in pages[number].splitlines():
            row = _ROW.match(line)
            if row:
                current = [row.group(2)]
                rows.setdefault(str(int(row.group(1))), []).append(current)
            elif current is not None and (not line.strip() or _HEADING.match(line.strip())):
                current = None
            elif current is not None:
                current.append(line.strip())
    found: dict[str, list] = {}
    for key, entries in rows.items():
        for lines in entries:
            tabs = {str(int(m.group(1))) for line in lines if (m := _AGENDA_TAB.search(line))}
            text = " ".join(re.sub(r"-{3,}\s*TAB\s*\d{1,2}\s*$", "", line, flags=re.I) for line in lines)
            found.setdefault(key, []).append((" ".join(text.split()), tabs))
    return found


def map_airport(ctx: Context) -> list[Mapping]:
    agenda = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
              and d.version_id in {c["documentId"] for i in ctx.meeting.items for c in i.citations}]
    if len({d.sha256 for d in agenda}) != 1:
        raise Uncertain("no single agenda PDF")
    rows = agenda_tabs(ctx.stored_pages(agenda[0].version_id))
    wanted: dict[str, str] = {}
    candidates = []
    for item in ctx.numbered():
        found = rows.get(str(int(norm_number(item.number))) if norm_number(item.number).isdigit() else "", [])
        if len(found) != 1:
            ctx.skip(item, f"item {item.number} appears {len(found)} times in the agenda")
            continue
        text, tabs = found[0]
        if title_overlap(item.title, text) < ROW_TITLE_OVERLAP:
            ctx.skip(item, "agenda row differs from the item title")
        elif len(tabs) != 1:
            ctx.skip(item, "agenda gives no TAB" if not tabs else "agenda gives several TABs")
        elif len(candidates) >= MAX_REPORTS:
            ctx.skip(item, "too many reports for one meeting")
        else:
            wanted[item.id] = next(iter(tabs))
            candidates.append(item)
    if not candidates:
        return []
    if not ctx.meeting.official_url:
        raise Uncertain("meeting has no attachments page")
    files: dict[str, dict] = {}
    for link in ctx.html_links(ctx.meeting.official_url):
        tab = _FILE_TAB.match(link["text"])
        if tab and int(tab.group(1)) > 0:
            files.setdefault(str(int(tab.group(1))), {})[link["url"]] = " ".join(link["text"].split())
    links = {norm_number(item.number): set(files.get(wanted[item.id], {})) for item in candidates}

    def verify(item: Item, report: dict) -> dict | str:
        first = report["pages"][0]["text"] if report["pages"] else ""
        header = _ITEM.search(first)
        if not header:
            return "file has no AGENDA ITEM header"
        printed = meeting_date(first)
        if printed != ctx.meeting.local_date:
            return "report is for another meeting date" if printed else "report prints no meeting date"
        title = _TITLE.search(first)
        title = " ".join(title.group(1).split()) if title else ""
        evidence = {"agendaLink": files[wanted[item.id]][report["url"]], "agendaTab": f"TAB {int(wanted[item.id]):02d}",
                    "meetingDate": ctx.meeting.local_date.isoformat()}
        if header.group(1):
            if int(header.group(1)) != int(norm_number(item.number)):
                return f"report is headed AGENDA ITEM {header.group(1)}"
            evidence["reportHeading"] = " ".join(header.group().split())
        else:
            overlap = title_overlap(item.title, title)
            if overlap < TITLE_FALLBACK:
                return "report has no item number and its title differs from the agenda item"
            evidence["reportHeading"] = f"AGENDA TITLE: {title}"
            evidence["titleOverlap"] = round(overlap, 2)
        return evidence
    return linked_reports(ctx, candidates, links, verify=verify, kind="staff-report", title="Item {number} staff report",
                          missing="{count} TAB files for item {number}")


RULES = {"airport-tab-files": map_airport}
