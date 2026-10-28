"""Tahoe City PUD (``tcpud``, ``tcpud-parks-recreation-committee``, Granicus view 2): the agenda page links each memo.

The captured agenda is Granicus's HTML agenda page (``GeneratedAgendaViewer.php``). Each
item sits in a row whose ``numberspace`` cell prints its number (a section letter "D.",
then "3.", then sub-items "a."), and the item's staff memo is the ``MetaViewer.php``
document link under it whose text is the item title verbatim; other links under the item
(presentations, correspondence) are its attachments. The page is read again and must be
the captured version (same hash, or the same text).

Two signals must agree: the agenda link (under the item's own number, named with its
title) and the memo's first page, which opens "TAHOE CITY PUBLIC UTILITY DISTRICT /
MEMORANDUM" (or a department report heading) and names the item in its SUBJ line or
heading. The memo's ``DATE:`` is the day it was written, so it only has to fall within
``MEMO_WINDOW_DAYS`` before the meeting. Pay estimates and minutes, which do not open
with a memo heading, stay unmapped.
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import date, timedelta

from ..html_source import html_text
from ..staff_reports import (MIN_TITLE_OVERLAP, Attachments, Context, Mapping, Uncertain, _STOP, linked_reports,
                             norm_number, title_overlap, words)
from . import _granicus

MAX_REPORTS = 40  # report downloads per meeting
MEMO_WINDOW_DAYS = 30
SAME_TITLE = 0.9  # a link "named with the item title" shares this much of it, both ways

_ROW = re.compile(r'<td class\s*=\s*"numberspace">(?P<label>.*?)</td>\s*<td[^>]*>(?P<title>.*?)</td>', re.S | re.I)
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MEMO_DATE = re.compile(rf"\bDATE:\s*((?:{_MONTHS})\s+\d{{1,2}},\s*\d{{4}})")


def same_title(a: str, b: str) -> bool:
    """Two printed titles name the same thing: each shares ``SAME_TITLE`` of the other's distinctive words."""
    return title_overlap(a, b) >= SAME_TITLE and title_overlap(b, a) >= SAME_TITLE


def agenda_rows(markup: str) -> list[dict]:
    """The agenda's numbered rows in page order: ``{"number", "title", "parents", "documents": [{"url", "name"}]}``.

    Numbers are built the way the captured items carry them: "D.3", "7.a", or "G" for a
    lettered section that is itself an item. A document link belongs to the nearest row
    above it whose title it repeats (so one memo placed after sub-items 7.a and 7.b
    belongs to item 7 when it is named "Department Reports"); a link that repeats no
    title stays with the row just above it, as an attachment only.
    """
    events = sorted([(m.start(), "row", m) for m in _ROW.finditer(markup)] +
                    [(m.start(), "doc", m) for m in _granicus.DOCUMENT.finditer(markup)], key=lambda e: e[0])
    rows: list[dict] = []
    section = item = None
    for _, kind, match in events:
        if kind == "row":
            label = _granicus.text(match["label"]).rstrip(".")
            title = _granicus.text(re.sub(r"<div[^>]*>.*", "", match["title"], flags=re.S))
            if re.fullmatch(r"[A-Z]", label) and "<strong>" in match["label"].lower():
                section, item, number, parents = label, None, label, []
            elif re.fullmatch(r"\d+", label):
                item = f"{section}.{label}" if section else label
                number, parents = item, [section] if section else []
            elif re.fullmatch(r"[a-z]", label) and item:
                number, parents = f"{item}.{label}", [item] + ([section] if section else [])
            else:
                continue
            rows.append({"number": number, "title": title, "parents": parents, "documents": []})
        elif rows:
            name = _granicus.text(match["name"])
            owner = rows[-1]
            if not same_title(owner["title"], name):
                for parent in owner["parents"]:
                    row = next((r for r in reversed(rows) if r["number"] == parent), None)
                    if row and same_title(row["title"], name):
                        owner = row
                        break
            owner["documents"].append({"url": html.unescape(match["url"]), "name": name})
    return rows


def _agenda(ctx: Context):
    pages = [d for d in ctx.meeting.documents if d.media_type == "text/html"
             and re.search(r"//tahoecitypud\.granicus\.com/(?:Generated)?AgendaViewer\.php", d.url, re.I)]
    if len({d.url for d in pages}) != 1:
        raise Uncertain("no single Granicus agenda page")
    return pages[0]


def map_tcpud(ctx: Context) -> list[Mapping]:
    agenda = _agenda(ctx)
    markup = _granicus.markup(ctx, agenda.url)
    if not markup.strip():
        raise Uncertain("the agenda page is empty now (Granicus moves an event's agenda to its video clip)")
    if hashlib.sha256(markup.encode("utf-8")).hexdigest() != agenda.sha256:
        stored = "\n".join(ctx.stored_pages(agenda.version_id).values())
        if " ".join(html_text(markup).split()) != " ".join(stored.split()):
            raise Uncertain("the agenda page changed since it was captured")
    rows = {norm_number(r["number"]): r for r in agenda_rows(markup)}
    items = ctx.numbered()
    ctx.attachments = [Attachments(i.id, i.fingerprint, [{"url": d["url"], "name": d["name"], "label": d["name"]}
                                                          for d in rows[norm_number(i.number)]["documents"]],
                                   {"heading": i.number})
                       for i in ctx.meeting.items if rows.get(norm_number(i.number), {}).get("documents")]
    candidates, links = {}, {}
    for item in items:
        row = rows.get(norm_number(item.number))
        if row is None:
            ctx.skip(item, "item number is not on the agenda page")
            continue
        if not same_title(item.title, row["title"]):
            ctx.skip(item, "agenda page title differs from the captured item")
            continue
        if len({w for w in words(item.title) if len(w) > 3 and w not in _STOP}) < 2:
            ctx.skip(item, "title too short to check against the report")
            continue
        named = [d for d in row["documents"] if same_title(item.title, d["name"])]
        if len(named) != 1:
            ctx.skip(item, f"{len(named)} agenda links named with the item title")
            continue
        if len(candidates) >= MAX_REPORTS:
            ctx.skip(item, f"more than {MAX_REPORTS} reports in this meeting")
            continue
        candidates[norm_number(item.number)] = {named[0]["url"]}
        links[item.id] = named[0]["name"]

    def verify(item, report):
        first = report["pages"][0]["text"] if report["pages"] else ""
        evidence = memo_names_item(first, item.title, ctx.meeting.local_date)
        return evidence if isinstance(evidence, str) else {"agendaLink": links[item.id], **evidence}
    return linked_reports(ctx, [i for i in items if norm_number(i.number) in candidates], candidates, verify=verify,
                          kind="staff-memo", title="Staff memo")


def memo_names_item(first_page: str, title: str, meeting_date: date | None) -> dict | str:
    """Evidence that a TCPUD memo's first page is about ``title``, or the reason it is not."""
    lines = [line.strip() for line in first_page.splitlines() if line.strip()][:20]
    if not lines or not re.match(r"TAHOE CITY PUBLIC UTILITY DISTRICT$", lines[0]):
        return "report does not open with the district's memo heading"
    heading = []  # "MEMORANDUM", or a department report title that may wrap onto a second line
    for line in lines[1:4]:
        if not re.fullmatch(r"[A-Z&’',/ -]+", line):
            break
        heading.append(line)
    heading = " ".join(heading)
    if not re.fullmatch(r"MEMORANDUM|[A-Z&’',/ -]+ REPORT", heading):
        return "report does not open with a memo or department report heading"
    head = "\n".join(lines)
    overlap = title_overlap(title, head)
    if overlap < MIN_TITLE_OVERLAP:
        return "memo subject differs from the agenda item"
    evidence = {"reportHeading": heading, "titleOverlap": round(overlap, 2)}
    printed = _MEMO_DATE.search(head)
    if printed and meeting_date:
        month, day, year = re.match(r"(\w+)\s+(\d+),\s*(\d+)", printed.group(1)).groups()
        written = date(int(year), _MONTHS.split("|").index(month) + 1, int(day))
        if not meeting_date - timedelta(days=MEMO_WINDOW_DAYS) <= written <= meeting_date:
            return "memo is dated outside the weeks before this meeting"
        evidence["memoDate"] = written.isoformat()
    return evidence


RULES = {"tcpud": map_tcpud, "tcpud-parks-recreation-committee": map_tcpud}
