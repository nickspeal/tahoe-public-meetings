"""Map agenda items to their own staff reports (IDEA-013), one verified rule per agency.

Each agency publishes an item's staff report differently:

- South Lake Tahoe (``slt``): the agenda PDF links "Item N- Executive Summary.pdf" for
  each item. That file opens with an "Agenda Item Executive Summary" page that names
  the item number and title, followed by the staff report and attachments.
- Tahoe Transportation District (``ttd``): the agenda's Page column refers to the
  packet's stamped "~ Page N ~" footer, and every page of a staff memo and its
  attachments carries an "AGENDA ITEM: IV.A." footer.
- Truckee (``truckee``): staff report pages in the agenda packet carry an "Item 5.1"
  header.
- Lahontan Water Board (``lahontan-board``): the agenda links ``itemN.pdf``, whose
  first page is headed "ITEM N" with the item's title.

A mapping is stored only when two independent signals agree (the agenda's link or
page reference, and the item number printed on the staff report itself), plus a
title check where the report repeats the title. Anything uncertain stores nothing
for that item, so no link or summary is shown.

PDFs are temporary inputs (decision 0004). Only the official URL, page range,
document hash, the evidence used, and the text of the report's first pages (for
summary citations) are kept, in ``item_staff_reports``. A row is used only while
the item's source fingerprint is unchanged. ``staff_report_scans`` remembers which
version of each meeting's items was scanned, so an unchanged meeting is never
downloaded again; every hosted statement is a billed operation (decision 0020).
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .http import EndpointSkipped, fetch_bytes

METHOD_VERSION = "staff-report-map-v1"
SUPPORTED = ("slt", "ttd", "truckee", "lahontan-board")
LEADING_PAGES = 3  # pages of each report kept for summaries and their citations
MAX_PAGE_CHARS = 8_000
MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_PDF_PAGES = 800
RETRY_HOURS = 24  # a meeting whose downloads failed is scanned again after this long
DEFAULT_MAX_MEETINGS = 6  # per scheduled run, nearest to today first; bounds downloads and statements
MIN_TITLE_OVERLAP = 0.6

# Meeting mechanics and records that have no staff report worth linking or summarizing.
PROCEDURAL = re.compile(
    r"^(?:call(?: meeting)? to order|roll call|pledge of allegiance|flag salute|invocation|adjourn(?:ment)?|"
    r"(?:approval|adoption|approve|adopt)(?: of)?(?: the)? (?:agenda|minutes|meeting minutes|action minutes)|"
    r"(?:city council |board |draft )?(?:meeting )?minutes|public (?:interest )?comments?|public communications|"
    r"public forum|closed session(?: report)?|consent (?:agenda|calendar)|proclamations?|"
    r"(?:board|council)(?: ?member)? (?:requests|reports|comments)|reports? by (?:the )?(?:\w+ )?(?:board|council|chair)|"
    r"agenda planning calendar)\b", re.I)


class Uncertain(Exception):
    """The mapping cannot be verified; nothing is stored for the item or meeting."""


class FetchFailed(Exception):
    """A download failed; the meeting is scanned again after ``RETRY_HOURS``."""


@dataclass
class Item:
    id: str
    number: str
    title: str
    section: str | None
    fingerprint: str
    citations: list


@dataclass
class Document:
    version_id: str
    source_document_id: str
    title: str
    url: str
    sha256: str
    page_count: int
    media_type: str


@dataclass
class Mapping:
    item_id: str
    fingerprint: str
    kind: str  # "executive-summary", "staff-report", or "staff-memo"
    title: str
    url: str
    page_start: int
    page_end: int
    page_count: int
    sha256: str
    evidence: dict
    pages: list = field(default_factory=list)  # [(page number, text)] in the document at ``url``


@dataclass
class Attachments:
    """Every file the agenda links under one item, in agenda order (``url``, printed ``name``, ``label``)."""
    item_id: str
    fingerprint: str
    files: list
    evidence: dict


@dataclass
class Meeting:
    id: str
    agency_id: str
    title: str
    local_date: date | None
    items: list
    documents: list


# --- Text helpers ------------------------------------------------------------

_MOJIBAKE = re.compile("[ÂÃâ][\u0080-¿ŒœŠšŸŽžƒˆ˜"
                       "–—‘-„†-•…‰‹›€™]{1,2}")


def clean_text(text: str) -> str:
    """Undo UTF-8 read as Windows-1252 ("Cityâ€™s" -> "City’s"), which some agency PDFs carry in their text layer."""
    # A closing quote's last byte (0x9D) has no Windows-1252 character, so it arrives as an invisible one.
    text = re.sub("\u00e2\u20ac[\u009d\u200b]", "\u201d", text)

    def fix(match):
        try:
            return match.group().encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return match.group()
    return _MOJIBAKE.sub(fix, text)


_STOP = {"with", "from", "that", "this", "through", "into", "their", "there", "these", "those", "under", "upon",
         "item", "items", "possible", "action", "approve", "approval", "review", "consider", "consideration",
         "resolution", "adopt", "adoption", "authorize", "authorizing", "authorization", "amount", "exceed"}


def words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", clean_text(text).casefold())


def title_overlap(title: str, text: str) -> float:
    """Share of the title's distinctive words that appear in ``text``."""
    significant = {w for w in words(title) if len(w) > 3 and w not in _STOP}
    if not significant:
        return 0.0
    return len(significant & set(words(text))) / len(significant)


def procedural(item: Item) -> bool:
    return bool(PROCEDURAL.match(" ".join(re.sub(r"^for (?:possible )?action:\s*", "", item.title, flags=re.I).split())))


def _norm_number(value: str) -> str:
    return re.sub(r"\s+", "", value).strip(".").upper()


# --- Temporary PDF reading ------------------------------------------------------

def read_pdf(url: str, *, last_page: int | None = None, links: bool = False) -> dict:
    """Download a PDF into a temporary directory and return its hash, page count, text, and (optionally) link annotations.

    Pages are ``[{"page_number", "text"}]`` from ``pdftotext -layout`` (all pages, or
    pages 1..``last_page``); links are ``[{"page", "url", "text"}]`` from ``pdftohtml -xml``.
    Nothing is kept after the call returns.
    """
    try:
        raw, _ = fetch_bytes(url, max_bytes=MAX_PDF_BYTES, accept="application/pdf")
    except EndpointSkipped:
        raise
    except Exception as exc:  # Network, HTTP, TLS, and size failures are all retried later.
        raise FetchFailed(f"{url}: {exc}") from exc
    if not raw.startswith(b"%PDF-"):
        raise Uncertain(f"{url} is not a PDF")
    with tempfile.TemporaryDirectory(prefix="tahoe-report-") as scratch:
        path = Path(scratch) / "source.pdf"
        path.write_bytes(raw)
        info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30, check=True).stdout
        count = int(next(line for line in info.splitlines() if line.startswith("Pages:")).split(":", 1)[1])
        if not 1 <= count <= MAX_PDF_PAGES:
            raise Uncertain(f"{url} has {count} pages")
        args = ["pdftotext", "-layout", "-enc", "UTF-8"] + (["-l", str(last_page)] if last_page else []) + [str(path), "-"]
        texts = subprocess.run(args, capture_output=True, timeout=180, check=True).stdout.decode("utf-8", "replace").split("\f")
        found, boxes = [], []
        if links:
            xml = subprocess.run(["pdftohtml", "-xml", "-i", "-q", "-stdout", str(path)], capture_output=True,
                                 timeout=180, check=True).stdout.decode("utf-8", "replace")
            boxes = pdf_boxes(xml)
            found = [b for b in boxes if b["url"]]
    pages = [{"page_number": n, "text": t} for n, t in enumerate(texts[:last_page or count], 1)]
    return {"sha256": hashlib.sha256(raw).hexdigest(), "page_count": count, "pages": pages, "links": found,
            "boxes": [b for b in boxes if not b["url"]]}


def pdf_boxes(xml: str) -> list[dict]:
    """Every text element from ``pdftohtml -xml`` output, in reading order, with its page, position, and link (or None)."""
    found = []
    for chunk in re.split(r"(?=<page number=\")", xml):
        number = re.match(r'<page number="(\d+)"', chunk)
        if not number:
            continue
        for match in re.finditer(r'<text top="(\d+)" left="(\d+)"[^>]*>(.*?)</text>', chunk, re.S):
            link = re.search(r'<a href="([^"]*)">', match.group(3))
            text = " ".join(html.unescape(re.sub(r"<[^>]+>", "", match.group(3))).split())
            found.append({"page": int(number.group(1)), "top": int(match.group(1)), "left": int(match.group(2)),
                          "url": html.unescape(link.group(1)) if link else None, "text": text})
    return sorted(found, key=lambda b: (b["page"], b["top"], b["left"]))


def pdf_links(xml: str) -> list[dict]:
    """Link annotations from ``pdftohtml -xml`` output, with the page and position each sits at."""
    return [b for b in pdf_boxes(xml) if b["url"]]


# --- Per-agency rules -----------------------------------------------------------

class Context:
    """What a rule may use: the meeting, its stored pages (``stored(version_id) -> {page: text}``), and a temporary PDF reader."""

    def __init__(self, stored, meeting: Meeting, reader, delay: float = 0.0):
        self._stored, self.meeting, self._reader, self.delay = stored, meeting, reader, delay
        self.downloads = 0
        self.skipped: dict[str, str] = {}
        self.fetch_failures: list[str] = []
        self.attachments: list[Attachments] = []
        self._cache: dict[str, dict] = {}

    def read(self, url: str, **kwargs) -> dict:
        if self.downloads and self.delay:
            time.sleep(self.delay)
        self.downloads += 1
        return self._reader(url, **kwargs)

    def read_report(self, item: Item, url: str) -> dict | None:
        """One item's own report, or None (the item is skipped) when it cannot be read."""
        try:
            return self.read(url, last_page=LEADING_PAGES)
        except Uncertain as exc:
            self.skip(item, str(exc))
        except (FetchFailed, EndpointSkipped) as exc:
            self.skip(item, "download failed")
            self.fetch_failures.append(str(exc)[:300])
        except subprocess.SubprocessError:
            self.skip(item, "report PDF could not be read")
        return None

    def stored_pages(self, version_id: str) -> dict[int, str]:
        if version_id not in self._cache:
            self._cache[version_id] = self._stored(version_id)
        return self._cache[version_id]

    def pages(self, document: Document, *, needed) -> dict[int, str]:
        """All page texts of a stored document, downloading it only when stored text lacks what ``needed`` looks for."""
        stored = self.stored_pages(document.version_id)
        if needed(stored):
            return stored
        fetched = self.read(document.url)
        if fetched["sha256"] != document.sha256:
            raise Uncertain(f"{document.title} changed since it was captured")
        return {p["page_number"]: p["text"] for p in fetched["pages"]}

    def skip(self, item: Item, reason: str) -> None:
        self.skipped[item.id] = reason

    def numbered(self) -> list[Item]:
        """Numbered, non-procedural items with a unique number in this meeting."""
        counts = {}
        for item in self.meeting.items:
            counts[_norm_number(item.number)] = counts.get(_norm_number(item.number), 0) + 1
        chosen = []
        for item in self.meeting.items:
            if not item.number.strip():
                continue
            if procedural(item):
                self.skip(item, "procedural")
            elif counts[_norm_number(item.number)] > 1:
                self.skip(item, "item number repeats in this agenda")
            else:
                chosen.append(item)
        return chosen


def _leading(pages: dict[int, str], start: int, end: int) -> list:
    return [(n, clean_text(pages.get(n, "")).strip()[:MAX_PAGE_CHARS]) for n in range(start, min(end, start + LEADING_PAGES - 1) + 1)
            if pages.get(n, "").strip()]


def _pdf_agenda(ctx: Context, *, prefer: str) -> Document:
    pdfs = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
            and not re.search(r"packet|minutes|supplement|addendum", d.source_document_id + " " + d.title, re.I)]
    chosen = [d for d in pdfs if re.search(prefer, d.source_document_id + " " + d.title, re.I)] or pdfs
    if len(chosen) != 1:
        raise Uncertain("no single agenda PDF")
    return chosen[0]


def _long_date(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def map_slt(ctx: Context) -> list[Mapping]:
    agenda = _pdf_agenda(ctx, prefer=r"agenda")
    fetched = ctx.read(agenda.url, last_page=1, links=True)
    if fetched["sha256"] != agenda.sha256:
        raise Uncertain("the agenda PDF changed since it was captured")
    ctx.attachments = slt_attachments(ctx, fetched, ctx.stored_pages(agenda.version_id))
    links: dict[str, set] = {}
    for link in fetched["links"]:
        label = re.fullmatch(r"Item\s*(\d+)\s*-\s*Executive Summary\.pdf", link["text"], re.I)
        named = re.search(r"/Item_(\d+)-_Executive_Summary\.pdf$", link["url"], re.I)
        if label and named and label.group(1) == named.group(1):
            links.setdefault(label.group(1), set()).add((link["url"], link["page"]))
    mappings = []
    for item in ctx.numbered():
        found = links.get(_norm_number(item.number), set())
        if len({url for url, _ in found}) != 1:
            ctx.skip(item, f"{len(found)} executive summary links for item {item.number}")
            continue
        url, link_page = next(iter(found))
        cited = {int(c["page"]) for c in item.citations}
        if cited and not min(cited) <= link_page <= max(cited) + 1:
            ctx.skip(item, "executive summary link is not next to the item")
            continue
        report = ctx.read_report(item, url)
        if report is None:
            continue
        first = report["pages"][0]["text"] if report["pages"] else ""
        if not re.search(r"Agenda Item Executive Summary", first, re.I) or not re.search(
                rf"Agenda Item #:\s*{re.escape(_norm_number(item.number))}\b", first):
            ctx.skip(item, "executive summary does not name this item number")
            continue
        named_title = re.search(r"Agenda Item:\s*(.+?)\n\s*\n", first, re.S)
        if title_overlap(item.title, named_title.group(1) if named_title else first) < MIN_TITLE_OVERLAP:
            ctx.skip(item, "executive summary title differs from the agenda item")
            continue
        stated = re.search(r"Meeting Date:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", first)
        if stated and ctx.meeting.local_date and stated.group(1) != _long_date(ctx.meeting.local_date):
            ctx.skip(item, "executive summary is for another meeting date")
            continue
        pages = {p["page_number"]: p["text"] for p in report["pages"]}
        mappings.append(Mapping(item.id, item.fingerprint, "executive-summary", f"Item {item.number} executive summary",
                                url, 1, report["page_count"], report["page_count"], report["sha256"],
                                {"agendaLink": f"Item {item.number}- Executive Summary.pdf", "agendaPage": link_page,
                                 "reportHeading": f"Agenda Item #:{item.number}"},
                                _leading(pages, 1, report["page_count"])))
    return mappings


_KINDS = re.compile(r"^(staff\s*report|SR\b|resolution|ordinance|presentation|proclamation|(?:attachment|exhibit|att)\s*[A-Z0-9]+\b)"
                    r"\s*[-–:]?\s*(.*)$", re.I)


def attachment_label(name: str) -> str:
    """A printed attachment file name made readable: "01-SR_Paradise_Ave_Abandonment.docx" -> "Staff report: Paradise Ave Abandonment"."""
    base = re.sub(r"\.(?:pdf|docx?|xlsx?|pptx?|csv|txt)$", "", name.strip(), flags=re.I).replace("_", " ")
    base = re.sub(r"^\s*Item\s*\d+\s*-\s*", "", base, flags=re.I)
    base = " ".join(re.sub(r"^\s*\d{1,2}\s*[-–.]\s*", "", base).split())
    kind = _KINDS.match(base)
    if not kind:
        return base or name
    head = kind.group(1)
    if re.fullmatch(r"staff\s*report|SR", head, re.I):
        head = "Staff report"
    elif re.match(r"(attachment|exhibit|att)\s*", head, re.I):
        word, ident = re.match(r"(attachment|exhibit|att)\s*(.+)", head, re.I).groups()
        head = f"{'Exhibit' if word.lower() == 'exhibit' else 'Attachment'} {ident}"
    else:
        head = head.capitalize()
    rest = kind.group(2).strip(" -–:")
    return f"{head}: {rest}" if rest else head


SLT_MARGIN = 110  # item numbers and section headings sit at the left margin; item text and links are indented


def slt_attachments(ctx: Context, fetched: dict, agenda_pages: dict[int, str]) -> list[Attachments]:
    """Each numbered item's attachment links: those between its "N." heading and the next left-margin heading.

    Every printed file name must also appear on that page of the stored agenda text, and an
    "Item M- Executive Summary.pdf" under item N (M != N) makes the whole item uncertain.
    """
    margin = [b for b in fetched.get("boxes", []) if b["left"] < SLT_MARGIN and b["text"]]
    headings: dict[str, list] = {}
    for box in margin:
        if number := re.fullmatch(r"(\d+)\.", box["text"]):
            headings.setdefault(number.group(1), []).append(box)
    page_text = {n: " ".join(clean_text(t).split()) for n, t in agenda_pages.items()}
    found = []
    for item in ctx.meeting.items:
        heads = headings.get(_norm_number(item.number), []) if item.number.strip() else []
        if len(heads) != 1:
            continue
        start = (heads[0]["page"], heads[0]["top"])
        following = [(b["page"], b["top"]) for b in margin if (b["page"], b["top"]) > start]
        end = min(following) if following else (10**6, 0)
        files = []
        for link in fetched["links"]:
            if not start < (link["page"], link["top"]) < end:
                continue
            if files and files[-1]["url"] == link["url"]:  # a file name wrapped onto a second line
                files[-1]["name"] = f"{files[-1]['name']} {link['text']}".strip()
                continue
            files.append({"url": link["url"], "name": link["text"], "page": link["page"]})
        if not files:
            continue
        problem = None
        for f in files:
            other = re.match(r"Item\s*(\d+)\s*-\s*Executive Summary", f["name"], re.I)
            if not f["name"] or not f["url"].startswith("https://"):
                problem = "an attachment link has no printed name"
            elif other and other.group(1) != _norm_number(item.number):
                problem = f"lists the executive summary of item {other.group(1)}"
            elif f["name"] not in page_text.get(f["page"], ""):
                problem = f"“{f['name']}” is not printed on agenda page {f['page']}"
            if problem:
                break
        if problem:
            ctx.skip(item, f"attachments: {problem}")
            continue
        found.append(Attachments(item.id, item.fingerprint, [
            {"url": f["url"], "name": f["name"], "label": attachment_label(f["name"])}
            for f in files], {"agendaPages": sorted({f["page"] for f in files}), "heading": f"{item.number}."}))
    return found


def map_lahontan(ctx: Context) -> list[Mapping]:
    agenda = _pdf_agenda(ctx, prefer=r"agenda")
    fetched = ctx.read(agenda.url, last_page=1, links=True)
    if fetched["sha256"] != agenda.sha256:
        raise Uncertain("the agenda PDF changed since it was captured")
    base = agenda.url.rsplit("/", 1)[0] + "/"
    links: dict[str, set] = {}
    for link in fetched["links"]:
        named = re.fullmatch(re.escape(base) + r"item(\d+)\.pdf", link["url"], re.I)
        if named:
            links.setdefault(named.group(1), set()).add(link["url"])
    mappings = []
    for item in ctx.numbered():
        found = links.get(_norm_number(item.number), set())
        if len(found) != 1:
            ctx.skip(item, f"{len(found)} item links for item {item.number}")
            continue
        url = next(iter(found))
        report = ctx.read_report(item, url)
        if report is None:
            continue
        first = report["pages"][0]["text"] if report["pages"] else ""
        heading = re.search(rf"^\s*ITEM\s+{re.escape(_norm_number(item.number))}\s*$", first, re.M)
        if not heading:
            ctx.skip(item, "report does not name this item number")
            continue
        if title_overlap(item.title, first[heading.end():heading.end() + 400]) < MIN_TITLE_OVERLAP:
            ctx.skip(item, "report title differs from the agenda item")
            continue
        meeting_of = re.search(r"MEETING OF\s+([A-Z]+)\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?,\s*(\d{4})", first)
        day = ctx.meeting.local_date
        if meeting_of and day and (meeting_of.group(1).title(), int(meeting_of.group(2))) != (f"{day:%B}", day.year):
            ctx.skip(item, "report is for another meeting")
            continue
        pages = {p["page_number"]: p["text"] for p in report["pages"]}
        mappings.append(Mapping(item.id, item.fingerprint, "staff-report", f"Item {item.number} staff report",
                                url, 1, report["page_count"], report["page_count"], report["sha256"],
                                {"agendaLink": url.rsplit("/", 1)[1], "reportHeading": f"ITEM {item.number}"},
                                _leading(pages, 1, report["page_count"])))
    return mappings


_TTD_STAMP = re.compile(r"~\s*Page\s+(\d+)\s*~")
# The board's own footer, not a committee's ("FPC AGENDA ITEM: III.B." follows it on finance memos).
_TTD_FOOTER = re.compile(r"(?:^|\s{2,})AGENDA ITEM:?\s*([IVXLC]+(?:\.[A-Z0-9]+)*)\.?", re.M)


def _ttd_agenda_page(item: Item, ctx: Context) -> int | None:
    """The packet page the agenda's Page column gives on the item's first line."""
    for citation in item.citations:
        text = ctx.stored_pages(citation["documentId"]).get(int(citation["page"]), "")
        quote = citation["quote"].strip()
        first_line = re.sub(r"\s{2,}(?:Page\s+)?\d{1,4}$", "", quote.splitlines()[0].strip()) if quote else ""
        for line in text.splitlines():
            if first_line and first_line in line:
                number = re.search(r"\S\s{2,}(?:Page\s+)?(\d{1,4})\s*$", line)
                return int(number.group(1)) if number else None
    return None


def map_ttd(ctx: Context) -> list[Mapping]:
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
        page = _ttd_agenda_page(item, ctx)
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
        labels = _TTD_FOOTER.findall(text)
        if labels:
            footers[number] = _norm_number(labels[-1])
    mappings = []
    for item in candidates:
        if item.id not in wanted:
            continue
        start = stamp_to_page.get(wanted[item.id], [])
        if len(start) != 1:
            ctx.skip(item, f"packet page {wanted[item.id]} is stamped on {len(start)} pages")
            continue
        start = start[0]
        label = _norm_number(item.number)
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
                                _leading(pages, start, end)))
    return mappings


_TRUCKEE_HEADER = re.compile(r"(?:^|\s{2,})Item\s+(\d+(?:\.\d+)*)\s*$")


def map_truckee(ctx: Context) -> list[Mapping]:
    candidates = ctx.numbered()
    packets = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
               and re.search(r"packet", d.source_document_id + " " + d.title + " " + d.url, re.I)]
    wanted = {_norm_number(i.number) for i in candidates}

    def has_headers(pages):
        return wanted <= {_norm_number(m.group(1)) for text in pages.values()
                          if (m := _TRUCKEE_HEADER.search("\n".join(text.strip().splitlines()[:1])))}
    if len(packets) == 1:
        packet = packets[0]
        pages = ctx.pages(packet, needed=has_headers)
    elif not packets:
        packet, pages = _municode_packet(ctx)
    else:
        raise Uncertain("more than one agenda packet")
    # Pages headed with the same item number form one report, as long as no other item's pages fall between them.
    # Charts and slides may lack the header or carry it beside other text on the first line.
    labels = {}
    for number, text in pages.items():
        header = _TRUCKEE_HEADER.search("\n".join(text.strip().splitlines()[:1]))
        if header:
            labels[number] = _norm_number(header.group(1))
    blocks: dict[str, list] = {}
    for label in set(labels.values()):
        numbers = sorted(n for n, other in labels.items() if other == label)
        interrupted = any(labels.get(n, label) != label for n in range(numbers[0], numbers[-1] + 1))
        blocks[label] = [] if interrupted else [[numbers[0], numbers[-1]]]
    mappings = []
    for item in candidates:
        found = blocks.get(_norm_number(item.number), [])
        if len(found) != 1:
            ctx.skip(item, f"no single run of pages headed Item {item.number}")
            continue
        start, end = found[0]
        if title_overlap(item.title, pages[start]) < MIN_TITLE_OVERLAP:
            ctx.skip(item, "staff report title differs from the agenda item")
            continue
        mappings.append(Mapping(item.id, item.fingerprint, "staff-report", "Staff report in the agenda packet",
                                packet.url, start, end, packet.page_count, packet.sha256,
                                {"header": f"Item {item.number}", "pages": f"{start}-{end}"},
                                _leading(pages, start, end)))
    return mappings


def _municode_packet(ctx: Context) -> tuple[Document, dict[int, str]]:
    """The packet Municode publishes beside a captured agenda PDF, verified to open with that agenda.

    Municode names a meeting's files MEET-Agenda-<id>.pdf and MEET-Packet-<id>.pdf (its
    meeting list gives both); scheduled discovery captures only the agenda.
    """
    agendas = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
               and re.search(r"/MEET-Agenda-[0-9a-f]{32}\.pdf", d.url)]
    if len(agendas) != 1:
        raise Uncertain("no agenda packet, and no single Municode agenda PDF to find it from")
    agenda = agendas[0]
    url = agenda.url.replace("/MEET-Agenda-", "/MEET-Packet-", 1).replace("&n=Agenda-", "&n=AgendaPacket-", 1)
    fetched = ctx.read(url)
    pages = {p["page_number"]: p["text"] for p in fetched["pages"]}
    opening = ctx.stored_pages(agenda.version_id).get(1, "")
    agenda_words = set(words(opening))
    if not agenda_words or len(agenda_words & set(words(pages.get(1, "")))) < 0.9 * len(agenda_words):
        raise Uncertain("the Municode packet does not open with the captured agenda")
    return Document("", "municode-packet", "Agenda packet", url, fetched["sha256"], fetched["page_count"], "application/pdf"), pages


RULES = {"slt": map_slt, "ttd": map_ttd, "truckee": map_truckee, "lahontan-board": map_lahontan}


# --- Run ----------------------------------------------------------------------------

def _load(conn, *, agency: str | None, meeting: str | None) -> dict[str, Meeting]:
    agencies = [agency] if agency else list(SUPPORTED)
    meetings: dict[str, Meeting] = {}
    for r in conn.execute("""SELECT i.id,i.meeting_id,i.number,i.title,i.section,i.source_fingerprint,i.citations,
            m.agency_id,m.title AS meeting_title,m.local_date
            FROM agenda_items i JOIN meetings m ON m.id=i.meeting_id
            WHERE i.active AND m.quarantine_reason IS NULL AND m.agency_id=ANY(%s) AND (%s::text IS NULL OR m.id=%s)
            ORDER BY i.meeting_id,i.item_order,i.id""", (agencies, meeting, meeting)):
        m = meetings.setdefault(r["meeting_id"], Meeting(r["meeting_id"], r["agency_id"], r["meeting_title"], r["local_date"], [], []))
        citations = r["citations"] if isinstance(r["citations"], list) else json.loads(r["citations"])
        m.items.append(Item(r["id"], r["number"] or "", r["title"], r["section"], r["source_fingerprint"], citations))
    if meetings:
        for r in conn.execute("""SELECT c.meeting_id,v.id,v.source_document_id,v.title,v.url,v.sha256,v.page_count,v.media_type
                FROM document_current c JOIN document_versions v ON v.id=c.version_id WHERE c.meeting_id=ANY(%s)
                ORDER BY c.meeting_id,v.source_document_id""", (list(meetings),)):
            meetings[r["meeting_id"]].documents.append(Document(r["id"], r["source_document_id"], r["title"], r["url"],
                                                                r["sha256"], r["page_count"], r["media_type"]))
    return meetings


def scan_key(meeting: Meeting) -> str:
    """Changes whenever an item, a current document, or the mapping rules change."""
    payload = {"rules": METHOD_VERSION, "items": sorted((i.id, i.fingerprint) for i in meeting.items),
               "documents": sorted(d.version_id for d in meeting.documents)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _pending(conn, meetings: dict[str, Meeting], *, force: bool, today: date) -> list[Meeting]:
    scans = {r["meeting_id"]: r for r in conn.execute(
        """SELECT meeting_id,items_sha256,status,scanned_at < now() - make_interval(hours => %s) AS retry
        FROM staff_report_scans""", (RETRY_HOURS,))}
    pending = []
    for meeting in meetings.values():
        if not meeting.documents:
            continue
        scan = scans.get(meeting.id)
        if force or not scan or scan["items_sha256"] != scan_key(meeting) or (scan["status"] == "retry" and scan["retry"]):
            pending.append(meeting)
    # Upcoming and recent meetings first; the archive fills in over later runs.
    return sorted(pending, key=lambda m: (abs((m.local_date - today).days) if m.local_date else 10_000, m.id))


def map_staff_reports(conn, *, agency: str | None = None, meeting: str | None = None, max_meetings: int | None = None,
                      force: bool = False, dry_run: bool = False, reader=read_pdf, delay: float = 1.0,
                      today: date | None = None) -> dict:
    """Find and store each pending meeting's item-to-staff-report mappings."""
    if agency and agency not in RULES:
        raise SystemExit(f"{agency}: no staff report mapping rule (supported: {', '.join(SUPPORTED)})")
    meetings = _load(conn, agency=agency, meeting=meeting)
    pending = _pending(conn, meetings, force=force, today=today or date.today())
    report = {"meetingsPending": len(pending), "dryRun": dry_run, "meetingsScanned": 0, "itemsMapped": 0,
              "byAgency": {}, "uncertain": {}, "retryLater": [], "downloads": 0}
    if max_meetings is not None and len(pending) > max_meetings:
        report["deferred"] = len(pending) - max_meetings
        pending = pending[:max_meetings]
    conn.commit()  # No transaction stays open while documents download.
    if dry_run:
        report["wouldScan"] = [m.id for m in pending]
        return report
    def stored(version_id):
        return {r["page_number"]: r["text"] for r in conn.execute(
            "SELECT page_number,text FROM document_pages WHERE document_version_id=%s", (version_id,))}
    for m in pending:
        ctx = Context(stored, m, reader, delay)
        status, mappings, problem = "done", [], None
        try:
            mappings = RULES[m.agency_id](ctx)
        except Uncertain as exc:
            problem = str(exc)
        except (FetchFailed, EndpointSkipped) as exc:
            status, problem = "retry", str(exc)[:300]
            report["retryLater"].append(f"{m.id}: {problem}")
        except subprocess.SubprocessError as exc:
            problem = f"PDF could not be read: {exc}"[:300]
        if ctx.fetch_failures and status == "done":
            status = "retry"  # Keep what was verified; try the failed downloads again later.
            report["retryLater"].append(f"{m.id}: {ctx.fetch_failures[0]}")
        report["downloads"] += ctx.downloads
        detail = {"mapped": len(mappings), "skipped": ctx.skipped}
        if problem:
            detail["problem"] = problem
            report["uncertain"][m.id] = problem
        detail["attachments"] = len(ctx.attachments)
        _store(conn, m, mappings, status, detail, ctx.attachments)
        conn.commit()
        report["meetingsScanned"] += 1
        report["itemsMapped"] += len(mappings)
        report["itemsWithAttachments"] = report.get("itemsWithAttachments", 0) + len(ctx.attachments)
        totals = report["byAgency"].setdefault(m.agency_id, {"meetings": 0, "items": 0})
        totals["meetings"] += 1
        totals["items"] += len(mappings)
    return report


def _store(conn, meeting: Meeting, mappings: list[Mapping], status: str, detail: dict, attachments=()) -> None:
    """One statement per meeting: replace its mappings and attachment lists and record the scan (decision 0020)."""
    rows = [{"item_id": m.item_id, "source_fingerprint": m.fingerprint, "kind": m.kind, "title": m.title, "url": m.url,
             "page_start": m.page_start, "page_end": m.page_end, "page_count": m.page_count, "sha256": m.sha256,
             "evidence": m.evidence, "pages": [{"number": n, "text": t} for n, t in m.pages]} for m in mappings]
    # A retried scan keeps mappings it could not recheck; a completed one removes those it no longer verifies.
    conn.execute("""WITH incoming AS (
            SELECT * FROM jsonb_to_recordset(%(rows)s::jsonb) AS r(item_id text,source_fingerprint text,kind text,title text,
              url text,page_start int,page_end int,page_count int,sha256 text,evidence jsonb,pages jsonb)),
        removed AS (
            DELETE FROM item_staff_reports s USING agenda_items i
            WHERE %(complete)s AND s.item_id=i.id AND i.meeting_id=%(meeting)s AND s.item_id NOT IN (SELECT item_id FROM incoming)),
        stored AS (
            INSERT INTO item_staff_reports(item_id,source_fingerprint,method,kind,title,url,page_start,page_end,page_count,
              sha256,evidence,pages)
            SELECT item_id,source_fingerprint,%(method)s,kind,title,url,page_start,page_end,page_count,sha256,evidence,pages FROM incoming
            ON CONFLICT(item_id) DO UPDATE SET source_fingerprint=excluded.source_fingerprint,method=excluded.method,
              kind=excluded.kind,title=excluded.title,url=excluded.url,page_start=excluded.page_start,page_end=excluded.page_end,
              page_count=excluded.page_count,sha256=excluded.sha256,evidence=excluded.evidence,pages=excluded.pages,
              mapped_at=CASE WHEN item_staff_reports.sha256 IS DISTINCT FROM excluded.sha256
                OR item_staff_reports.pages IS DISTINCT FROM excluded.pages THEN now() ELSE item_staff_reports.mapped_at END),
        files AS (
            SELECT * FROM jsonb_to_recordset(%(files)s::jsonb) AS f(item_id text,source_fingerprint text,files jsonb,evidence jsonb)),
        files_removed AS (
            DELETE FROM item_attachments t USING agenda_items i
            WHERE %(complete)s AND t.item_id=i.id AND i.meeting_id=%(meeting)s AND t.item_id NOT IN (SELECT item_id FROM files)),
        files_stored AS (
            INSERT INTO item_attachments(item_id,source_fingerprint,method,files,evidence)
            SELECT item_id,source_fingerprint,%(method)s,files,evidence FROM files
            ON CONFLICT(item_id) DO UPDATE SET source_fingerprint=excluded.source_fingerprint,method=excluded.method,
              files=excluded.files,evidence=excluded.evidence,
              mapped_at=CASE WHEN item_attachments.files IS DISTINCT FROM excluded.files THEN now() ELSE item_attachments.mapped_at END)
        INSERT INTO staff_report_scans(meeting_id,items_sha256,status,report) VALUES (%(meeting)s,%(key)s,%(status)s,%(detail)s)
        ON CONFLICT(meeting_id) DO UPDATE SET items_sha256=excluded.items_sha256,status=excluded.status,
          report=excluded.report,scanned_at=now()""",
                 {"rows": json.dumps(rows), "files": json.dumps([{"item_id": a.item_id, "source_fingerprint": a.fingerprint,
                                                                  "files": a.files, "evidence": a.evidence} for a in attachments]),
                  "complete": status == "done", "meeting": meeting.id, "method": METHOD_VERSION,
                  "key": scan_key(meeting), "status": status, "detail": json.dumps(detail)})


# --- Export --------------------------------------------------------------------------

def report_document_id(item_id: str) -> str:
    return f"{item_id}:staff-report"


def public_report(item_id: str, row: dict, cited_pages: set[int], iso) -> dict:
    """The item's staff report for the public export, carrying only the pages its summary cites."""
    pages = row["pages"] if isinstance(row["pages"], list) else json.loads(row["pages"])
    return {"id": report_document_id(item_id), "kind": row["kind"], "title": row["title"], "url": row["url"],
            "mediaType": "application/pdf", "page": row["page_start"], "pageEnd": row["page_end"],
            "pageCount": row["page_count"], "sha256": row["sha256"], "fetchedAt": iso(row["mapped_at"]),
            "pages": [{"number": p["number"], "text": clean_text(p["text"])} for p in pages if p["number"] in cited_pages]}
