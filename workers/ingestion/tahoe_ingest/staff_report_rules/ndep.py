"""Nevada Board for Financing Water Projects (``ndep``): project staff reports in the meeting's "Material" packet.

The board's meeting page has one table row per meeting with "Agenda", "Minutes",
"Material", and "Presentation" links. The Material PDF opens with the agenda, then the
minutes, program updates, and one section per funding project:

    Loan Commitment from the                (or "Letter of Intent from the")
    Drinking Water State Revolving Fund
    Board for Financing Water Projects
    August 2026
    Elk Point Country Club HOA              (the recipient, which the agenda item names)
     Total Commitment   Recommendation ...
     Amount             Resolution   ...
     $78,000.00         D08-0826             (program letter, item number, meeting MMYY)
    ...
    Page 1 of 6                Elk Point                August 2026

followed by a "RESOLUTION D08-0826" (Page 1 of 2 ...). File names such as "Agenda.pdf"
and "Material.pdf" are reused by later meetings, so the packet is used only when its
first page matches the captured agenda (its words and printed meeting date), and the
mapping stores the packet's hash. Two signals must agree for an item: the resolution
code's item number and month (the agenda numbers the item, the packet labels the
section), and the recipient named at the top of the section, which must overlap the
item title. Program updates, minutes, priority lists, and other non-project items have
no project section and stay unmapped.
"""
from __future__ import annotations

import re
from urllib.parse import unquote

from ..staff_reports import MIN_TITLE_OVERLAP, Context, Mapping, Uncertain, leading, long_date, title_overlap, words

MIN_AGENDA_WORDS = 0.9  # share of the captured agenda's first-page words the packet's first page must print
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_SECTION = re.compile(r"^\s*(Loan Commitment|Letter of Intent) from the\s*$")
_CODE = re.compile(r"(?<![\w-])([DG])(\d{2})-(\d{2})(\d{2})(?![\w-])")
_FOOTER = re.compile(r"^\s*Page\s+(\d+)\s+of\s+(\d+)\b")
_TABLE = re.compile(r"^\s*(?:Total Commitment|Estimated Project|Recommendation)\b")


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _footer(text: str) -> tuple[int, int] | None:
    lines = _lines(text)
    found = _FOOTER.match(lines[-1]) if lines else None
    return (int(found.group(1)), int(found.group(2))) if found else None


def _significant(text: str) -> set[str]:
    return {w for w in words(text) if len(w) > 3}


def material_link(ctx: Context, agenda_url: str) -> str:
    """The "Material" link in the captured agenda's row: after the agenda link and before the next "Agenda" link."""
    links = ctx.html_links(ctx.meeting.official_url)
    own = [n for n, link in enumerate(links) if unquote(link["url"]) == unquote(agenda_url)]
    if len(own) != 1:
        raise Uncertain(f"the meeting page links the captured agenda {len(own)} times")
    end = next((n for n in range(own[0] + 1, len(links)) if re.fullmatch(r"Agenda", links[n]["text"].strip(), re.I)), len(links))
    found = {link["url"] for link in links[own[0] + 1:end] if re.fullmatch(r"(?:Meeting\s+)?Materials?", link["text"].strip(), re.I)}
    if len(found) != 1:
        raise Uncertain(f"{len(found)} meeting material links follow the captured agenda")
    return found.pop()


def project_sections(pages: dict[int, str], meeting_month: str) -> list[dict]:
    """Each project section: ``{"code", "number", "mmyy", "start", "end", "recipient", "top"}``.

    A section starts on a page whose first line is "Loan Commitment from the" or "Letter
    of Intent from the" and whose fourth line is the meeting's month ("August 2026"). Its
    "Page 1 of N" footer gives the length (every page must carry "Page k of N"), and a
    following "RESOLUTION <code>" run of the same code is included.
    """
    found = []
    for start, text in sorted(pages.items()):
        lines = _lines(text)
        if len(lines) < 6 or not _SECTION.match(lines[0]) or lines[3].strip() != meeting_month:
            continue
        footer = _footer(text)
        if not footer or footer[0] != 1:
            continue
        length = footer[1]
        if any(_footer(pages.get(start + k, "")) != (k + 1, length) for k in range(length)):
            continue
        table = next((n for n in range(4, len(lines)) if _TABLE.match(lines[n])), None)
        recipient = " ".join(line.strip() for line in lines[4:table]) if table else ""
        top = text.split("Management and Structure")[0]
        codes = {m.group(): m for m in _CODE.finditer(top)}
        if not recipient or len(codes) != 1:
            continue
        code = next(iter(codes.values()))
        end = start + length - 1
        resolution = pages.get(end + 1, "")
        tail = _footer(resolution)
        first = _lines(resolution)[0].strip() if _lines(resolution) else ""
        if first == f"RESOLUTION {code.group()}" and tail and tail[0] == 1 and all(
                _footer(pages.get(end + 1 + k, "")) == (k + 1, tail[1]) for k in range(tail[1])):
            end += tail[1]
        found.append({"code": code.group(), "number": int(code.group(2)), "mmyy": code.group(3) + code.group(4),
                      "start": start, "end": end, "recipient": recipient, "top": top})
    return found


def map_ndep(ctx: Context) -> list[Mapping]:
    agendas = sorted((d for d in ctx.meeting.documents if d.media_type == "application/pdf"), key=lambda d: d.version_id)
    if len({(unquote(d.url), d.sha256) for d in agendas}) != 1:  # the same agenda is often captured under two source IDs
        raise Uncertain("no single agenda PDF")
    agenda = agendas[0]
    day = ctx.meeting.local_date
    url = material_link(ctx, agenda.url)
    fetched = ctx.read(url)
    pages = {p["page_number"]: p["text"] for p in fetched["pages"]}
    stored = ctx.stored_pages(agenda.version_id).get(1, "")
    expected = _significant(stored)
    shared = len(expected & _significant(pages.get(1, ""))) / len(expected) if expected else 0.0
    printed = re.search(rf"FINANCING WATER PROJECTS\s+((?:{_MONTHS}) \d{{1,2}}, \d{{4}})", pages.get(1, ""), re.I)
    if shared < MIN_AGENDA_WORDS or not printed or printed.group(1) != long_date(day):
        raise Uncertain("the meeting materials do not open with the captured agenda")
    sections = project_sections(pages, f"{day:%B} {day.year}")
    mappings = []
    for item in ctx.numbered():
        if not item.number.strip().isdigit():
            ctx.skip(item, "item number is not a resolution number")
            continue
        found = [s for s in sections if s["number"] == int(item.number)]
        if not found:
            ctx.skip(item, "no project section in the meeting materials")
            continue
        if len(found) > 1:
            ctx.skip(item, f"{len(found)} project sections carry item {item.number}'s resolution number")
            continue
        section = found[0]
        if section["mmyy"] != f"{day:%m%y}":
            ctx.skip(item, f"resolution {section['code']} is for another month")
            continue
        title = re.sub(r"\((?:For Possible Action|Discussion)\)", "", item.title, flags=re.I)
        overlap = title_overlap(title, section["recipient"])
        if overlap < MIN_TITLE_OVERLAP:
            ctx.skip(item, "project section names another recipient")
            continue
        start, end = section["start"], section["end"]
        mappings.append(Mapping(item.id, item.fingerprint, "staff-report", "Project staff report in the meeting materials",
                                url, start, end, fetched["page_count"], fetched["sha256"],
                                {"agendaLink": "Material", "resolution": section["code"], "reportHeading": section["recipient"],
                                 "pages": f"{start}-{end}", "titleOverlap": round(overlap, 2), "meetingDate": day.isoformat()},
                                leading(pages, start, end)))
    return mappings


RULES = {"ndep": map_ndep}
