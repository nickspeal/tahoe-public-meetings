"""California Fish and Game Commission (``california-fish-game``): one "Staff Summary" PDF per item, listed in a documents index.

The Commission's year page (the meeting's official URL, shared by every meeting that
year) lists each meeting's "Agenda (PDF)" followed by its "Meeting Documents (PDF)": an
index PDF that repeats the agenda outline with each item title linked to that item's
document, the item number printed unlinked at the left margin of the same line. Each
document opens with a header that names the item and the meeting:

    Item No. 5
    Staff Summary for October 13-16, 2026        (committees: "Committee Staff Summary for ...")
    5.  Department Lands

Two signals must agree: the index links the file on the line numbered N (its link text
overlapping the item title), and the file's first page prints "Item No. N", the
numbered title, and a "Staff Summary for" date whose first day is the meeting date. An
item whose index entry links several documents (sub-items (A), (B) ...) is skipped
unless the numbered line itself carries a single link. The index and the agenda are
matched by DocumentID (the "&inline" suffix varies between sources). Downloads per
meeting: the year page, the index, and at most ``MAX_REPORTS`` summaries.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from ..staff_reports import (MIN_TITLE_OVERLAP, Context, FetchFailed, Mapping, Uncertain, linked_reports, title_overlap)

MAX_REPORTS = 40  # summaries downloaded per meeting; later items are skipped with a reason
MARGIN = 100  # item numbers and section headings sit at left ~81; titles at ~135, sub-item titles at ~189
POSTING_WINDOW = timedelta(days=7)  # a missing index is retried until a week after the meeting
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_INDEX_DATE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}})(?:\s*[-–]\s*\d{{1,2}})?,\s*(\d{{4}})")
_SUMMARY_DATE = re.compile(rf"(?:Committee\s+)?Staff Summary for\s+({_MONTHS})\s+(\d{{1,2}})(?:\s*[-–]\s*\d{{1,2}})?,\s*(\d{{4}})")
_ITEM_NO = re.compile(r"^\s*Item No\.\s*(\d+)([A-Z]?)\s*$", re.M)
_BINDER = re.compile(r"Meeting Binder|\(\d+\s*MB\)|^Items? \d+ through \d+", re.I)
# Standing items the core's procedural list misses: their "staff summaries" only list comments received or the agenda order.
_ROUTINE = re.compile(r"^(?:general public comment\b|(?:consider )?approv(?:e|ing) (?:the )?agenda\b)", re.I)


def document_id(url: str) -> str | None:
    """The FileHandler ``DocumentID`` of an nrm.dfg.ca.gov link ("...DocumentID=247178&inline=" -> "247178")."""
    found = parse_qs(urlparse(url).query).get("DocumentID") or [""]
    return found[0] if found[0].isdigit() else None


def _printed_date(match) -> date | None:
    try:
        return date(int(match.group(3)), _MONTHS.split("|").index(match.group(1)) + 1, int(match.group(2)))
    except ValueError:
        return None


def index_link(ctx: Context) -> str:
    """The meeting's "Meeting Documents (PDF)" link: the first one after its own "Agenda (PDF)" on the year page."""
    ids = {document_id(d.url) for d in ctx.meeting.documents if d.media_type == "application/pdf"}
    if len(ids) != 1 or None in ids:
        raise Uncertain("no single agenda DocumentID")
    agenda_id = ids.pop()
    links = ctx.html_links(ctx.meeting.official_url)
    agendas = [n for n, link in enumerate(links) if re.fullmatch(r"Agenda\s*\(PDF\)", link["text"].strip())]
    own = [n for n in agendas if document_id(links[n]["url"]) == agenda_id]
    if len(own) != 1:
        raise Uncertain(f"the year page lists the captured agenda {len(own)} times")
    end = next((n for n in agendas if n > own[0]), len(links))
    found = [link["url"] for link in links[own[0] + 1:end]
             if re.fullmatch(r"Meeting Documents\s*\(PDF\)", link["text"].strip()) and document_id(link["url"])]
    if len(found) == 1:
        return found[0]
    if not found and ctx.meeting.local_date and ctx.meeting.local_date + POSTING_WINDOW >= date.today():
        raise FetchFailed("the meeting documents index is not posted yet")
    raise Uncertain(f"{len(found)} meeting documents indexes follow the agenda")


def index_entries(fetched: dict) -> dict[str, dict]:
    """``{item number: {"line": {url: text}, "all": {url: text}, "page"}}`` from the index's link and text boxes.

    An entry starts at an unlinked "N." box at the left margin and runs to the next margin
    box (a heading, a binder link, "Executive Session") or "Public Receipt of Documents".
    ``line`` holds links on the numbered line itself; ``all`` adds sub-item links. A
    number printed twice is dropped.
    """
    boxes = sorted(fetched.get("links", []) + fetched.get("boxes", []), key=lambda b: (b["page"], b["top"], b["left"]))
    entries: dict[str, dict] = {}
    repeated, current = set(), None
    for box in boxes:
        text = (box.get("text") or "").strip()
        if re.fullmatch(r"Public Receipt of Documents", text, re.I):
            current = None
            break
        if box["left"] < MARGIN and text:
            number = re.fullmatch(r"(\d+)\.", text)
            if number and not box.get("url"):
                key = number.group(1)
                if key in entries:
                    repeated.add(key)
                current = entries[key] = {"line": {}, "all": {}, "page": box["page"], "top": box["top"]}
            else:
                current = None
            continue
        url = box.get("url") or ""
        if current is None or not re.match(r"https?://", url) or _BINDER.search(text):
            continue
        on_line = box["page"] == current["page"] and abs(box["top"] - current["top"]) <= 3
        for target in ("all", "line") if on_line else ("all",):
            current[target][url] = f"{current[target].get(url, '')} {text}".strip()
    return {k: v for k, v in entries.items() if k not in repeated}


def map_fish_game(ctx: Context) -> list[Mapping]:
    meeting_day = ctx.meeting.local_date
    index_url = index_link(ctx)
    fetched = ctx.read(index_url, links=True)
    first = fetched["pages"][0]["text"] if fetched["pages"] else ""
    if not re.search(r"Meeting Documents Index", first, re.I):
        raise Uncertain("the meeting documents link is not a documents index")
    printed = _INDEX_DATE.search(first[first.lower().index("meeting documents index"):])
    if not printed or _printed_date(printed) != meeting_day:
        raise Uncertain("the documents index is for another meeting date")
    entries = index_entries(fetched)
    candidates: dict[str, set] = {}
    link_text: dict[str, str] = {}
    budget: list[str] = []
    items = []
    for item in ctx.numbered():
        if _ROUTINE.match(item.title.strip()):
            ctx.skip(item, "procedural")
        else:
            items.append(item)
    for item in items:
        entry = entries.get(item.number.strip())
        if not entry or not entry["all"]:
            continue
        if len(entry["line"]) != 1:  # only sub-item documents (even a lone one): none is the item's own summary
            ctx.skip(item, f"{len(entry['all'])} documents for item {item.number} in the index")
            continue
        url = next(iter(entry["line"]))
        candidates[item.number.strip()] = {url}
        link_text[item.id] = entry["all"][url]  # the whole title, wrapped lines included
        if url not in budget:
            budget.append(url)
    allowed = set(budget[:MAX_REPORTS])
    pending = [i for i in items if i.id not in ctx.skipped]

    def precheck(item, url):
        if url not in allowed:
            return f"download cap reached ({MAX_REPORTS} reports per meeting)"
        if title_overlap(item.title, link_text[item.id]) < MIN_TITLE_OVERLAP:
            return "index link text differs from the agenda item"
        return None

    def verify(item, report):
        page = report["pages"][0]["text"] if report["pages"] else ""
        header = _ITEM_NO.search(page)
        if not header or header.group(1) != item.number.strip() or header.group(2):
            return f"staff summary does not name item {item.number}"
        stated = _SUMMARY_DATE.search(page)
        if not stated or _printed_date(stated) != meeting_day:
            return "staff summary is for another meeting date"
        heading = re.search(rf"^\s*{re.escape(item.number.strip())}\.\s+(\S.*(?:\n[ \t]*\S.*)?)", page, re.M)
        overlap = title_overlap(item.title, heading.group(1)) if heading else 0.0
        if overlap < MIN_TITLE_OVERLAP:
            return "staff summary title differs from the agenda item"
        return {"agendaLink": link_text[item.id], "agendaPage": entries[item.number.strip()]["page"],
                "meetingDocuments": index_url, "reportHeading": f"Item No. {item.number.strip()}",
                "titleOverlap": round(overlap, 2), "meetingDate": meeting_day.isoformat()}
    return linked_reports(ctx, pending, candidates, verify=verify, precheck=precheck, kind="staff-report",
                          title="Item {number} staff summary", missing="{count} documents for item {number} in the index")


RULES = {"california-fish-game": map_fish_game}
