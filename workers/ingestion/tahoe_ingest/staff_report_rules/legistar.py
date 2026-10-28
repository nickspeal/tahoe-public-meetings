"""Rule ``legistar``: each item's report, found through the open Legistar Web API.

The captured agenda PDF prints every item's number, and Legistar keeps the same agenda
as rows (``/Events/{id}/EventItems``) that carry the item's file and its attachments.
The event is the one the legistar1 agenda file name starts with (``5288_A_...pdf``) or,
without one, the only event that day for the agency's body. An agency's entry gives
its Legistar ``client`` (the site and API name, ``eldorado`` for eldorado.legistar.com),
its ``body`` (the API's EventBodyName), and the ``report`` it publishes, which says how
the report is found and checked:

- ``legislation-text`` (El Dorado Board of Supervisors): the file's own Legislation Text
  (``/Matters/{id}/Versions`` then ``/Texts/{id}``), the department's memo. The agenda
  prints "7. 26-1680" and the text carries that file and the agenda item's own title. It
  is linked at the file's LegislationDetail page (from the meeting page, else the
  agenda's gateway link), so its media type is text/html, and kept as pseudo-pages; its
  hash is of the text, since the rendered PDF prints the day it was rendered.
- ``planning-attachment`` (El Dorado Planning Commission): the attachment named
  "A - Staff Report" or "B - Staff Memo", whose first page prints the meeting date
  ("Date:" or "Agenda of:") and a case number from the item title; otherwise the
  Legislation Text as above.
- ``staff-report`` (Washoe Board of County Commissioners): the attachment named as a
  staff report (or the item's only Word file), which the agenda's "Attachments:" list
  under the item names too. It opens "STAFF REPORT / BOARD MEETING DATE: <date> /
  SUBJECT: <item title>". Most are Word files: .docx is read here, legacy .doc only when
  ``antiword`` is installed.
- ``agenda-item`` (Washoe Planning Commission): the "... - Staff Report" PDF listed under
  the item, which prints "Meeting Date: <date>  Agenda Item: 8A". Agendas from before
  Legistar (washoecounty.gov) are out of reach.

API responses and Word files come through ``Context.fetch_json`` and ``Context.fetch_bytes``;
each counts as a download and honours the context's delay. Per meeting, at most
``MAX_REPORTS`` PDF or Word reports are downloaded and at most ``MAX_TEXTS`` Legislation
Texts (two small API calls each) are read; later items are skipped.
"""
from __future__ import annotations

import hashlib
import io
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from ..http import EndpointSkipped
from ..staff_reports import (MAX_PAGE_CHARS, MIN_TITLE_OVERLAP, Context, FetchFailed, Item, Mapping, TooLarge, Uncertain,
                             clean_text, leading, long_date, norm_number, title_overlap)

API = "https://webapi.legistar.com/v1"
MAX_WORD_BYTES = 30 * 1024 * 1024
MAX_REPORTS = 40  # PDF and Word report downloads per meeting; later items are skipped
MAX_TEXTS = 100  # Legislation Text reads per meeting (two small API calls each); later items are skipped
WORD_TYPES = {"doc": "application/msword", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
TEXT_TITLE_OVERLAP = 0.8  # the Legislation Text repeats the agenda title word for word


def antiword_available() -> bool:
    """Whether legacy .doc files can be read (the worker image installs antiword)."""
    return shutil.which("antiword") is not None


class _Api:
    """Legistar API calls for one meeting through ``Context.fetch_json`` (cached, counted; failures raise ``FetchFailed``)."""

    def __init__(self, ctx: Context, client: str):
        self.ctx, self.client = ctx, client

    def get(self, path: str):
        return self.ctx.fetch_json(f"{API}/{self.client}/{path}")


# --- Agenda side --------------------------------------------------------------------

# An item's number at the agenda's left margin: "7.", "7.A.1.", "2. 26-1601", or "A." under "8.".
_PRINTED = re.compile(r"^[ \t]{0,10}(\d{1,3}\.(?:[A-Z]\.(?:\d{1,2}\.)?)?|[A-Z]\.)(?=[ \t]+\S)", re.M)
_FILE = re.compile(r"^\s*\d{1,3}\.\s+(\d{2}-\d{4})\b")
_DATE = r"[A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4}"


def agenda_blocks(ctx: Context, agendas: list) -> dict:
    """``{number: {"page", "line", "text"} | None}``: the text each printed item number heads in the stored agenda.

    A lone letter ("A.") composes with the last top-level number ("8." -> "8.A"). A number
    printed twice maps to None. The first agenda document that prints a number is used.
    """
    found: dict = {}
    for document in agendas:
        pages = ctx.stored_pages(document.version_id)
        text, starts = "", []
        for number in sorted(pages):
            starts.append((len(text), number))
            text += pages[number] + "\n"
        matches = list(_PRINTED.finditer(text))
        mine: dict = {}
        parent = None
        for i, m in enumerate(matches):
            raw = m.group(1)
            if re.fullmatch(r"\d+\.", raw):
                parent, number = raw[:-1], raw[:-1]
            elif re.fullmatch(r"[A-Z]\.", raw):
                if not parent:
                    continue
                number = f"{parent}.{raw[0]}"
            else:
                number = norm_number(raw)
                parent = number.split(".")[0]
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            page = max(n for offset, n in starts if offset <= m.start())
            block = {"page": page, "line": " ".join(text[m.start():text.find("\n", m.start())].split()),
                     "text": text[m.start():end]}
            mine[number] = None if number in mine else block
        for number, block in mine.items():
            found.setdefault(number, block)
    return found


def listed_attachments(block: dict | None) -> str:
    """The item's printed "Attachments:" list as one line, or ''."""
    m = re.search(r"Attachments:\s*(.*)", block["text"], re.S) if block else None
    return " ".join(m.group(1).split()) if m else ""


def find_event(api: _Api, agendas: list, ctx: Context, body: str) -> dict:
    """The Legistar event: the one the legistar1 agenda file name starts with, or the body's only event that day."""
    day = ctx.meeting.local_date.isoformat()
    ids = {m.group(1) for d in agendas if (m := re.search(r"/meetings/\d{4}/\d{1,2}/(\d+)_A_", d.url))}
    if len(ids) > 1:
        raise Uncertain(f"agenda files name {len(ids)} Legistar events")
    if ids:
        event = api.get(f"Events/{next(iter(ids))}")
    else:
        events = api.get("Events?$filter=" + urllib.parse.quote(f"EventDate eq datetime'{day}'"))
        same = [e for e in events if (e.get("EventBodyName") or "").strip() == body]
        if len(same) != 1:
            raise Uncertain(f"{len(same)} Legistar {body} events on {day}")
        event = same[0]
    if (event.get("EventDate") or "")[:10] != day or (event.get("EventBodyName") or "").strip() != body:
        raise Uncertain(f"Legistar event {event.get('EventId')} is not the {body} meeting of {day}")
    return event


def agenda_rows(rows: list) -> dict:
    """``{normalised number: event item | None}`` from the API's agenda rows, in agenda order.

    Numbers lose their trailing dot ("7.A.1." -> "7.A.1"). A lone letter composes with the
    last top-level number ("8." then "A." -> "8.A"), and a numbered row without a file
    takes the file of an unnumbered row that directly follows it (Washoe Planning prints
    the hearing as "A." and lists its file on the next row). A repeated number maps to None.
    """
    found: dict = {}
    parent = previous = None
    for row in sorted(rows, key=lambda r: r.get("EventItemAgendaSequence") or 0):
        raw = norm_number(row.get("EventItemAgendaNumber") or "")
        if not raw:
            if previous and row.get("EventItemMatterId") and found.get(previous) and not found[previous].get("EventItemMatterId"):
                header = found[previous]
                found[previous] = {**row, "EventItemAgendaNumber": header["EventItemAgendaNumber"],
                                   "EventItemTitle": f"{header.get('EventItemTitle') or ''}\n{row.get('EventItemTitle') or ''}"}
            previous = None
            continue
        if re.fullmatch(r"\d+(?:\.[A-Z0-9]+)*", raw):
            parent, number = raw.split(".")[0], raw
        elif re.fullmatch(r"[A-Z]", raw) and parent:
            number = f"{parent}.{raw}"
        else:
            number = raw
        found[number] = None if number in found else row
        previous = number
    return found


# --- Report text ---------------------------------------------------------------------

def pseudo_pages(text: str) -> dict[int, str]:
    """Text without pages split at line breaks into ``{n: text}`` chunks of at most ``MAX_PAGE_CHARS``."""
    pages, current = [], ""
    for line in text.split("\n"):
        while len(line) > MAX_PAGE_CHARS:
            if current:
                pages.append(current)
                current = ""
            pages.append(line[:MAX_PAGE_CHARS])
            line = line[MAX_PAGE_CHARS:]
        if current and len(current) + 1 + len(line) > MAX_PAGE_CHARS:
            pages.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current.strip():
        pages.append(current)
    return {n: page for n, page in enumerate(pages, 1)}


def tidy(text: str) -> str:
    """Tabs and runs of spaces become one space; blank lines collapse to one."""
    lines = [" ".join(line.replace("\xa0", " ").split()) for line in clean_text(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_text(raw: bytes) -> str:
    """The body text of a .docx file: ``word/document.xml`` paragraphs, one per line (standard library only)."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = []
    for paragraph in root.iter(f"{_W}p"):
        parts = []
        for element in paragraph.iter():
            if element.tag == f"{_W}t":
                parts.append(element.text or "")
            elif element.tag == f"{_W}tab":
                parts.append("\t")
            elif element.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return tidy("\n".join(paragraphs))


def doc_text(raw: bytes) -> str:
    """The text of a legacy .doc file through ``antiword`` (callers check that it is installed)."""
    with tempfile.TemporaryDirectory(prefix="tahoe-report-") as scratch:
        path = Path(scratch) / "source.doc"
        path.write_bytes(raw)
        out = subprocess.run(["antiword", "-w", "0", str(path)], capture_output=True, timeout=120, check=True).stdout
    return tidy(out.decode("utf-8", "replace"))


def _extension(url: str) -> str:
    return urllib.parse.urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urllib.parse.urlparse(url).path else ""


class _Run:
    """One meeting's run: the context, API, agenda blocks, event rows, and the report budget."""

    def __init__(self, ctx: Context, client: str, api: _Api, blocks: dict):
        self.ctx, self.client, self.api, self.blocks = ctx, client, api, blocks
        self.reports = self.texts = 0
        self._detail_links = None

    def spend(self, item: Item) -> bool:
        """Count one PDF or Word download; False (the item skipped) once ``MAX_REPORTS`` were made."""
        if self.reports >= MAX_REPORTS:
            self.ctx.skip(item, f"more than {MAX_REPORTS} reports in one meeting")
            return False
        self.reports += 1
        return True

    def spend_text(self, item: Item) -> bool:
        """Count one Legislation Text read; False (the item skipped) once ``MAX_TEXTS`` were read."""
        if self.texts >= MAX_TEXTS:
            self.ctx.skip(item, f"more than {MAX_TEXTS} legislation texts in one meeting")
            return False
        self.texts += 1
        return True

    def failed(self, item: Item, exc: Exception) -> None:
        self.ctx.skip(item, "download failed")
        self.ctx.fetch_failures.append(str(exc)[:300])

    def word(self, item: Item, url: str) -> tuple[str, bytes] | None:
        """A Word attachment's text and bytes, or None (the item skipped)."""
        ext = _extension(url)
        if ext == "doc" and not antiword_available():
            self.ctx.skip(item, "legacy Word file")
            return None
        if not self.spend(item):
            return None
        try:
            raw = self.ctx.fetch_bytes(url, accept="*/*", max_bytes=MAX_WORD_BYTES)
        except TooLarge:  # Retrying would download the same oversized file every day.
            self.ctx.skip(item, "report is larger than the download limit")
            return None
        except (FetchFailed, EndpointSkipped) as exc:
            self.failed(item, exc)
            return None
        try:
            return (docx_text(raw) if ext == "docx" else doc_text(raw)), raw
        except (zipfile.BadZipFile, KeyError, ET.ParseError, subprocess.SubprocessError, OSError):
            self.ctx.skip(item, "Word file could not be read")
            return None

    def pdf(self, item: Item, url: str) -> dict | None:
        """The PDF's leading pages (``Context.read_report``), counted against ``MAX_REPORTS``."""
        return self.ctx.read_report(item, url) if self.spend(item) else None

    def detail_url(self, file_number: str, matter_id: int) -> str:
        """The file's LegislationDetail page from the meeting page's links, else the agenda's own gateway link to it."""
        if self._detail_links is None:
            self._detail_links = {}
            official = self.ctx.meeting.official_url or ""
            if re.search(r"\.legistar\.com/MeetingDetail\.aspx", official, re.I):
                try:
                    for link in self.ctx.html_links(official):
                        if "LegislationDetail.aspx" in link["url"]:
                            self._detail_links.setdefault(link["text"].strip(), link["url"])
                except (FetchFailed, Uncertain, EndpointSkipped):
                    pass
        return self._detail_links.get(file_number) or (
            f"https://{self.client}.legistar.com/gateway.aspx?m=l&id=/matter.aspx?key={matter_id}")


# --- Reports, by the ``report`` an agency publishes ---------------------------------------

_BOILERPLATE = re.compile(r"^SPACING IN LEGISTAR\s*$.*?^DO NOT USE THE AUTOMATIC NUMBERING SPACING IN WORD\s*$", re.M | re.S)


def legislation_text(run: _Run, item: Item, row: dict, block: dict) -> Mapping | None:
    """The file's Legislation Text (title and body) as the item's report, when the agenda prints "N. <file>" and the text repeats its title."""
    ctx, matter, file_number = run.ctx, row["EventItemMatterId"], row.get("EventItemMatterFile") or ""
    if re.search(r"\bapprove the (?:draft )?minutes\b", item.title, re.I):
        ctx.skip(item, "minutes")
        return None
    if not run.spend_text(item):
        return None
    try:
        versions = run.api.get(f"Matters/{matter}/Versions")
        wanted = str(row.get("EventItemVersion") or "").strip()
        chosen = [v for v in versions if str(v.get("Value")).strip() == wanted] if wanted else []
        if len(chosen) != 1:
            ctx.skip(item, f"no Legislation Text for version {wanted or '?'} of file {file_number}")
            return None
        text = run.api.get(f"Matters/{matter}/Texts/{chosen[0]['Key']}")
    except (FetchFailed, EndpointSkipped) as exc:
        run.failed(item, exc)
        return None
    if text.get("MatterTextMatterId") != matter:
        ctx.skip(item, f"Legislation Text is not for file {file_number}")
        return None
    plain = (text.get("MatterTextPlain") or "").replace("\r\n", "\n")
    parts = re.split(r"^\.\.Body\s*$", plain, maxsplit=1, flags=re.M)
    title = re.sub(r"^\.\.Title\s*$", "", parts[0], flags=re.M)
    body = _BOILERPLATE.sub("", parts[1]) if len(parts) == 2 else ""
    if not body.strip():
        ctx.skip(item, "Legislation Text has no body")
        return None
    overlap = title_overlap(item.title, title)
    if overlap < TEXT_TITLE_OVERLAP:
        ctx.skip(item, "Legislation Text title differs from the agenda item")
        return None
    heading = f"File #: {file_number}, Version: {wanted}"
    composed = tidy(f"{heading}\n\n{title}\n\n{body}")
    pages = pseudo_pages(composed)
    return Mapping(item.id, item.fingerprint, "staff-report", "Legislation text", run.detail_url(file_number, matter), 1, len(pages),
                   len(pages), hashlib.sha256(composed.encode()).hexdigest(),
                   {"agendaLine": _FILE.match(block["line"]).group().strip(), "agendaPage": block["page"], "fileNumber": file_number,
                    "reportHeading": heading, "titleOverlap": round(overlap, 2)}, leading(pages, 1, len(pages)), "text/html")


def _file_line(run: _Run, item: Item, row: dict, block: dict | None) -> bool:
    """El Dorado's agenda signal: the item's printed line is "N. <file>" with the API row's file number."""
    printed = _FILE.match(block["line"]) if block else None
    if not printed or printed.group(1) != row.get("EventItemMatterFile"):
        run.ctx.skip(item, f"agenda does not print item {item.number} with file {row.get('EventItemMatterFile')}")
        return False
    return True


def _pdf_mapping(item: Item, report: dict, url: str, kind: str, title: str, evidence: dict) -> Mapping:
    pages = {p["page_number"]: p["text"] for p in report["pages"]}
    return Mapping(item.id, item.fingerprint, kind, title, url, 1, report["page_count"], report["page_count"], report["sha256"],
                   evidence, leading(pages, 1, report["page_count"]))


def _same_day(printed: str, day) -> bool:
    """A printed "October 13, 2026" (any case and spacing) is the meeting day."""
    return " ".join(printed.replace(".", "").replace(",", ", ").split()).replace(" ,", ",").casefold() == long_date(day).casefold()


def text_report(run: _Run, item: Item, row: dict, block: dict | None) -> Mapping | None:
    """The Legislation Text, when the agenda prints the item's file number."""
    if _file_line(run, item, row, block):
        return legislation_text(run, item, row, block)
    return None


_CASE = re.compile(r"\b[A-Z]{2,5}(?:-[A-Z]{1,2})?\d{2}-\d{4}\b")
_PLANNING_REPORT = re.compile(r"[A-Z](?:\.\d+)?\s*-\s*Staff\s+(Report|Memo)", re.I)


def planning_attachment(run: _Run, item: Item, row: dict, block: dict | None) -> Mapping | None:
    """The "A - Staff Report" / "B - Staff Memo" PDF when there is one, else the Legislation Text."""
    if not _file_line(run, item, row, block):
        return None
    named = [a for a in row.get("EventItemMatterAttachments") or []
             if _PLANNING_REPORT.fullmatch((a.get("MatterAttachmentName") or "").strip())]
    if not named:
        return legislation_text(run, item, row, block)
    if len(named) > 1:
        run.ctx.skip(item, f"{len(named)} staff report attachments")
        return None
    name, url = named[0]["MatterAttachmentName"].strip(), named[0].get("MatterAttachmentHyperlink") or ""
    if _extension(url) != "pdf":
        run.ctx.skip(item, "staff report is not a PDF")
        return None
    report = run.pdf(item, url)
    if report is None:
        return None
    first = "\n".join(p["text"] for p in report["pages"][:2])
    stated = re.findall(rf"Agenda\s+of:\s*({_DATE})", first) or re.findall(rf"(?<![A-Za-z])Date:\s*({_DATE})", first)[:1]
    if not stated:
        run.ctx.skip(item, "staff report prints no meeting date")
        return None
    if not any(_same_day(d, run.ctx.meeting.local_date) for d in stated):
        run.ctx.skip(item, f"staff report is dated {' '.join(stated[0].split())}")
        return None
    cases = sorted(set(_CASE.findall(item.title)) & set(_CASE.findall(first)))
    if not cases:
        run.ctx.skip(item, "staff report does not print the item's case number")
        return None
    memo = name.lower().endswith("memo")
    return _pdf_mapping(item, report, url, "staff-memo" if memo else "staff-report", "Staff memo" if memo else "Staff report",
                        {"agendaLine": _FILE.match(block["line"]).group().strip(), "agendaPage": block["page"], "fileNumber": row["EventItemMatterFile"],
                         "attachment": name, "caseNumber": cases[0], "meetingDate": run.ctx.meeting.local_date.isoformat()})


def _listed(run: _Run, item: Item, name: str, block: dict | None) -> bool:
    """Washoe's agenda signal: the item's printed "Attachments:" list names this file."""
    if " ".join(name.split()) not in listed_attachments(block):
        run.ctx.skip(item, "the agenda does not list the staff report under the item")
        return False
    return True


def staff_report_attachment(run: _Run, item: Item, row: dict, block: dict | None) -> Mapping | None:
    """The attachment named as a staff report (else the only Word file), listed under the item, for this board meeting."""
    attachments = row.get("EventItemMatterAttachments") or []
    named = [a for a in attachments if re.search(r"staff\s*report", a.get("MatterAttachmentName") or "", re.I)
             and not re.search(r"\b(?:attachment|exhibit|PC)\b", a.get("MatterAttachmentName") or "", re.I)]
    word = [a for a in attachments if _extension(a.get("MatterAttachmentHyperlink") or "") in ("doc", "docx")]
    chosen = named or word
    if len(chosen) != 1:
        run.ctx.skip(item, f"{len(chosen)} staff report attachments" if chosen else "no attachment is a staff report")
        return None
    name, url = " ".join((chosen[0].get("MatterAttachmentName") or "").split()), chosen[0].get("MatterAttachmentHyperlink") or ""
    if not _listed(run, item, name, block):
        return None
    ext = _extension(url)
    if ext in ("doc", "docx"):
        read = run.word(item, url)
        if read is None:
            return None
        text, raw = read
        pages, sha, count, media_type = pseudo_pages(text), hashlib.sha256(raw).hexdigest(), None, WORD_TYPES[ext]
    elif ext == "pdf":
        report = run.pdf(item, url)
        if report is None:
            return None
        pages, sha, count = {p["page_number"]: p["text"] for p in report["pages"]}, report["sha256"], report["page_count"]
        media_type = "application/pdf"
        text = "\n".join(pages[n] for n in sorted(pages))
    else:
        run.ctx.skip(item, f"staff report is a .{ext or '?'} file")
        return None
    head = text[:8000]
    if not re.search(r"\bstaff\s+report\b", head[:1500], re.I):
        run.ctx.skip(item, "attachment does not open as a staff report")
        return None
    date = re.search(rf"BOARD\s+MEETING\s+DATE:\s*(?:[A-Z][a-z]+day,?\s+)?({_DATE})", head, re.I)
    if not date:
        run.ctx.skip(item, "staff report prints no board meeting date")
        return None
    if not _same_day(date.group(1), run.ctx.meeting.local_date):
        run.ctx.skip(item, f"staff report is for the {' '.join(date.group(1).split())} meeting")
        return None
    subject = re.search(r"SUBJECT:\s*(.+?)(?:\n\s*SUMMARY\b|$)", head, re.S)
    overlap = title_overlap(item.title, subject.group(1)[:len(item.title) + 300] if subject else "")
    if overlap < MIN_TITLE_OVERLAP:
        run.ctx.skip(item, "staff report subject differs from the agenda item")
        return None
    count = count or len(pages)
    return Mapping(item.id, item.fingerprint, "staff-report", "Staff report", url, 1, count, count, sha,
                   {"attachment": name, "agendaPage": block["page"], "fileNumber": row.get("EventItemMatterFile"),
                    "reportHeading": f"BOARD MEETING DATE: {long_date(run.ctx.meeting.local_date)}",
                    "titleOverlap": round(overlap, 2), "meetingDate": run.ctx.meeting.local_date.isoformat()},
                   leading(pages, 1, count), media_type)


def agenda_item_report(run: _Run, item: Item, row: dict, block: dict | None) -> Mapping | None:
    """The "... - Staff Report" PDF listed under the item, printing "Meeting Date: <date>  Agenda Item: 8A"."""
    named = [a for a in row.get("EventItemMatterAttachments") or []
             if re.search(r"staff\s*report", a.get("MatterAttachmentName") or "", re.I)
             and _extension(a.get("MatterAttachmentHyperlink") or "") == "pdf"]
    if len(named) != 1:
        run.ctx.skip(item, f"{len(named)} staff report attachments" if named else "no attachment is a staff report")
        return None
    name, url = " ".join(named[0]["MatterAttachmentName"].split()), named[0]["MatterAttachmentHyperlink"]
    if not _listed(run, item, name, block):
        return None
    report = run.pdf(item, url)
    if report is None:
        return None
    first = report["pages"][0]["text"] if report["pages"] else ""
    heading = re.search(rf"Meeting\s+Date:\s*({_DATE})\s+Agenda\s+Item:\s*(\d+)\.?\s*([A-Z])\b", first)
    if not heading or f"{heading.group(2)}.{heading.group(3)}" != norm_number(item.number):
        run.ctx.skip(item, f"staff report does not name agenda item {item.number}")
        return None
    if not _same_day(heading.group(1), run.ctx.meeting.local_date):
        run.ctx.skip(item, f"staff report is for the {' '.join(heading.group(1).split())} meeting")
        return None
    overlap = title_overlap(item.title, first)
    if overlap < MIN_TITLE_OVERLAP:
        run.ctx.skip(item, "staff report title differs from the agenda item")
        return None
    return _pdf_mapping(item, report, url, "staff-report", "Staff report",
                        {"attachment": name, "agendaPage": block["page"], "fileNumber": row.get("EventItemMatterFile"),
                         "reportHeading": " ".join(heading.group().split()), "titleOverlap": round(overlap, 2),
                         "meetingDate": run.ctx.meeting.local_date.isoformat()})


_REPORTS = {"legislation-text": text_report, "planning-attachment": planning_attachment,
            "staff-report": staff_report_attachment, "agenda-item": agenda_item_report}


# --- Rule ------------------------------------------------------------------------------

def map_legistar(ctx: Context, *, client: str, body: str,
                 report: Literal["legislation-text", "planning-attachment", "staff-report", "agenda-item"]) -> list[Mapping]:
    agendas = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
               and re.match(rf"https://{re.escape(client)}\.legistar1?\.com/", d.url, re.I)
               and not re.search(r"minutes|packet", d.source_document_id + " " + d.title, re.I)]
    if not agendas:
        raise Uncertain("the agenda is not a Legistar agenda (meetings before Legistar are not in its API)")
    items = ctx.numbered()
    if not items or not ctx.meeting.local_date:
        return []
    blocks = agenda_blocks(ctx, agendas)
    api = _Api(ctx, client)
    event = find_event(api, agendas, ctx, body)
    rows = agenda_rows(api.get(f"Events/{event['EventId']}/EventItems?AgendaNote=1&MinutesNote=1&Attachments=1"))
    run = _Run(ctx, client, api, blocks)
    check = _REPORTS[report]
    mappings = []
    for item in items:
        number = norm_number(item.number)
        row = rows.get(number)
        if row is None:
            ctx.skip(item, f"Legistar lists item {item.number} {'twice' if number in rows else 'nowhere'}")
            continue
        if not row.get("EventItemMatterId"):
            ctx.skip(item, "no Legistar file for this item")
            continue
        if re.search(r"closed session", row.get("EventItemMatterType") or "", re.I):
            ctx.skip(item, "closed session")
            continue
        if title_overlap(item.title, row.get("EventItemTitle") or "") < MIN_TITLE_OVERLAP:
            ctx.skip(item, f"Legistar's item {item.number} has another title")
            continue
        if (mapping := check(run, item, row, blocks.get(number))) is not None:
            mappings.append(mapping)
    return mappings


RULES = {"legistar": map_legistar}
