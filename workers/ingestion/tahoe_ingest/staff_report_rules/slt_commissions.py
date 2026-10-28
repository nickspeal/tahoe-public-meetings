"""South Lake Tahoe commissions (``slt-planning``, ``slt-parks-recreation-commission``, Granicus view 6): the staff report each item links.

The commissions publish on the same Granicus site as the council (``slt``). Under each
item the agenda lists its files, and the item's staff report is the one whose printed
name says so ("01-Staff Report - Parks Foundation Update.docx", "01 - Staff Report",
"01_Mid-Town_PC_Rec_StaffReport.docx"). The file list is read from, in order:

1. the captured agenda PDF's link annotations (the council's template, read with
   ``slt.slt_attachments``), when the PDF carries them;
2. otherwise Granicus's HTML agenda for the meeting's video clip
   (``GeneratedAgendaViewer.php?view_id=6&clip_id=N``: an "N)" cell, the item title, then
   ``MetaViewer.php`` document links), whose titles must match the captured items; an
   empty page (an agenda posted before its clip exists) counts as unavailable;
3. otherwise the captured agenda packet, whose opening pages are the agenda with links,
   after checking that they print the captured agenda.

Two signals must agree: the agenda lists exactly one staff report under the item, and
that report's first page reads "Report to Planning Commission" (or "Parks and Recreation
Commission") with "Meeting Date:" this meeting and a "Title:" sharing
``MIN_TITLE_OVERLAP`` of the item title. Every file listed under an item is also kept as
its attachment list.
"""
from __future__ import annotations

import html
import re

from ..staff_reports import (MIN_TITLE_OVERLAP, Attachments, Context, Mapping, Uncertain, linked_reports, long_date,
                             norm_number, title_overlap, words)
from . import _granicus
from .slt import attachment_label, slt_attachments

BODIES = {"slt-planning": "Planning Commission", "slt-parks-recreation-commission": "Parks and Recreation Commission"}
STAFF_REPORT = re.compile(r"staff[\s_-]*report", re.I)
OPENS_WITH_AGENDA = 0.9  # share of a captured agenda page's words that the packet's page must print

_GAV_ROW = re.compile(r'<td[^>]*vAlign=top width=35>\s*(?P<number>\d+)\)\s*</td>\s*<td[^>]*>(?P<title>.*?)</td>', re.S | re.I)


def _agenda(ctx: Context):
    """The captured agenda PDF (discovery may hold the same file under two document IDs)."""
    pdfs = {(d.url, d.sha256): d for d in ctx.meeting.documents if d.media_type == "application/pdf"
            and not re.search(r"packet|minutes|supplement|addendum", d.source_document_id + " " + d.title, re.I)}
    if len(pdfs) != 1:
        raise Uncertain("no single agenda PDF")
    return next(iter(pdfs.values()))


def _packet(ctx: Context):
    packets = {(d.url, d.sha256): d for d in ctx.meeting.documents if d.media_type == "application/pdf"
               and re.search(r"packet", d.source_document_id + " " + d.title, re.I)}
    return next(iter(packets.values())) if len(packets) == 1 else None


def gav_rows(markup: str, clip: str) -> dict[str, dict]:
    """``{number: {"title", "files": [{"url", "name"}]}}`` from a GeneratedAgendaViewer page; files follow their "N)" row."""
    events = sorted([(m.start(), "row", m) for m in _GAV_ROW.finditer(markup)] +
                    [(m.start(), "doc", m) for m in _granicus.DOCUMENT.finditer(markup)], key=lambda e: e[0])
    rows: dict[str, dict] = {}
    current = None
    for _, kind, match in events:
        if kind == "row":
            current = match["number"]
            if current in rows:
                raise Uncertain(f"the HTML agenda repeats item {current})")
            rows[current] = {"title": _granicus.text(match["title"]), "files": []}
        elif current is not None:
            url = html.unescape(match["url"])
            if _granicus.query(url, "clip_id") != clip:
                raise Uncertain("the HTML agenda links another clip's documents")
            rows[current]["files"].append({"url": url, "name": _granicus.text(match["name"])})
    return rows


def _from_gav(ctx: Context, agenda) -> list[Attachments] | None:
    """Attachment lists from the clip's HTML agenda, or None when there is none (no clip, or an empty page)."""
    clip, view = _granicus.query(agenda.url, "clip_id"), _granicus.query(agenda.url, "view_id")
    if not clip or not view:
        return None
    host = re.match(r"https://[^/]+", agenda.url).group()
    page = ctx.html(f"{host}/GeneratedAgendaViewer.php?view_id={view}&clip_id={clip}")
    rows = gav_rows(page, clip)
    if not rows:
        return None
    found = []
    for item in ctx.meeting.items:
        row = rows.get(norm_number(item.number)) if item.number.strip() else None
        if not row or not row["files"]:
            continue
        if title_overlap(item.title, row["title"]) < MIN_TITLE_OVERLAP:
            ctx.skip(item, "the HTML agenda gives this number another title")
            continue
        found.append(Attachments(item.id, item.fingerprint, [{"url": f["url"], "name": f["name"], "label": attachment_label(f["name"])}
                                                             for f in row["files"]], {"heading": f"{item.number})"}))
    return found


def _from_packet(ctx: Context, agenda) -> list[Attachments]:
    """Attachment lists from the agenda pages at the front of the captured packet, which carry the file links."""
    packet = _packet(ctx)
    if packet is None:
        raise Uncertain("the agenda PDF has no file links, and there is no HTML agenda or single agenda packet")
    fetched = ctx.read(packet.url, last_page=agenda.page_count, links=True)
    if fetched["sha256"] != packet.sha256:
        raise Uncertain("the agenda packet changed since it was captured")
    stored = ctx.stored_pages(agenda.version_id)
    pages = {p["page_number"]: p["text"] for p in fetched["pages"]}
    for number, text in stored.items():
        wanted = set(words(text))
        if not wanted or len(wanted & set(words(pages.get(number, "")))) < OPENS_WITH_AGENDA * len(wanted):
            raise Uncertain("the agenda packet does not open with the captured agenda")
    last = max(stored)
    front = {**fetched, "links": [b for b in fetched["links"] if b["page"] <= last],
             "boxes": [b for b in fetched.get("boxes", []) if b["page"] <= last]}
    return slt_attachments(ctx, front, {n: t for n, t in pages.items() if n <= last})


def map_slt_commission(ctx: Context) -> list[Mapping]:
    body = BODIES[ctx.meeting.agency_id]
    agenda = _agenda(ctx)
    fetched = ctx.read(agenda.url, last_page=1, links=True)
    if fetched["sha256"] != agenda.sha256:
        raise Uncertain("the agenda PDF changed since it was captured")
    if any("/services/legistar/download/" in link["url"] for link in fetched["links"]):
        source, attachments = "agenda PDF", slt_attachments(ctx, fetched, ctx.stored_pages(agenda.version_id))
    else:
        attachments = _from_gav(ctx, agenda)
        source = "HTML agenda"
        if attachments is None:
            source, attachments = "agenda packet", _from_packet(ctx, agenda)
    # Two staff reports under one number means the list also holds an unnumbered item's files: keep neither.
    ctx.attachments = [a for a in attachments if sum(bool(STAFF_REPORT.search(f["name"])) for f in a.files) <= 1]
    files = {a.item_id: a.files for a in attachments}
    candidates, names = {}, {}
    for item in ctx.numbered():
        if item.id in ctx.skipped:
            continue
        reports = [f for f in files.get(item.id, []) if STAFF_REPORT.search(f["name"])]
        if len(reports) != 1:
            ctx.skip(item, f"{len(reports)} staff report files listed under item {item.number}")
            continue
        candidates[norm_number(item.number)] = {reports[0]["url"]}
        names[item.id] = reports[0]["name"]

    def verify(item, report):
        first = report["pages"][0]["text"] if report["pages"] else ""
        evidence = report_heading(first, item.title, body, ctx.meeting.local_date)
        return evidence if isinstance(evidence, str) else {"agendaLink": names[item.id], "agendaSource": source, **evidence}
    items = [i for i in ctx.meeting.items if i.id in names]
    return linked_reports(ctx, items, candidates, verify=verify, kind="staff-report", title="Staff report")


def report_heading(first_page: str, title: str, body: str, meeting_date) -> dict | str:
    """Evidence that a commission staff report's first page is for this item and meeting, or the reason it is not."""
    heading = re.search(rf"Report to\s+{re.escape(body)}", first_page)
    if not heading:
        return f"report is not addressed to the {body}"
    stated = re.search(r"Meeting Date:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", first_page)
    if not stated or not meeting_date or stated.group(1) != long_date(meeting_date):
        return "report is for another meeting date"
    named = re.search(r"Title:\s*(.+?)(?=\n\s*(?:Location|Responsible)|\n\s*\n|$)", first_page, re.S)
    overlap = title_overlap(title, named.group(1) if named else "")
    if overlap < MIN_TITLE_OVERLAP:
        return "report title differs from the agenda item"
    return {"reportHeading": " ".join(heading.group().split()), "titleOverlap": round(overlap, 2),
            "meetingDate": meeting_date.isoformat()}


RULES = {"slt-planning": map_slt_commission, "slt-parks-recreation-commission": map_slt_commission}
