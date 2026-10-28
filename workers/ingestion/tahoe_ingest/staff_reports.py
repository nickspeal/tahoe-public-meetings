"""Shared core for mapping agenda items to their own staff reports (IDEA-013); the rules, one per publishing format, and their guide are in ``staff_report_rules/`` (README.md), and each agency picks one in its registry entry."""
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
from typing import NewType

from . import http
from .http import EndpointSkipped

METHOD_VERSION = "staff-report-map-v1"
LEADING_PAGES = 3  # pages of each report kept for summaries and their citations
MAX_PAGE_CHARS = 8_000
MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_PDF_PAGES = 800
RETRY_HOURS = 24  # a meeting whose downloads failed is scanned again after this long
DEFAULT_MAX_MEETINGS = 6  # per scheduled run, nearest to today first; bounds downloads and statements
MIN_TITLE_OVERLAP = 0.6
TITLE_MATCH_FLOOR = 0.8  # by-title matching has no number to agree with, so it needs more of the title

# A rule parameter that holds a regular expression: the registry compiles it when the agency catalog loads.
Regex = NewType("Regex", str)

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


class TooLarge(FetchFailed):
    """A download exceeded its byte limit. Fetching it again would fail the same way, so ``Context.read_report``
    skips the item for good instead of retrying the meeting."""


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
    media_type: str = "application/pdf"  # of the document at ``url``: "text/html" or a Word type when not a PDF


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
    official_url: str | None = None  # the meeting's official page, for rules that read its links


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


def norm_number(value: str) -> str:
    """An item number as rules compare it: no spaces or surrounding dots, upper case ("iv.a." -> "IV.A")."""
    return re.sub(r"\s+", "", value).strip(".").upper()


# --- Temporary PDF reading ------------------------------------------------------

def read_pdf(url: str, *, last_page: int | None = None, links: bool = False, max_bytes: int = MAX_PDF_BYTES,
             max_pages: int = MAX_PDF_PAGES) -> dict:
    """Download a PDF into a temporary directory and return its hash, page count, text, and (optionally) link annotations.

    Pages are ``[{"page_number", "text"}]`` from ``pdftotext -layout`` (all pages, or
    pages 1..``last_page``); links are ``[{"page", "url", "text"}]`` from ``pdftohtml -xml``.
    ``max_bytes`` and ``max_pages`` bound the download; a rule may raise them for one
    agency's known packet sizes. Nothing is kept after the call returns.
    """
    try:
        raw, _ = http.fetch_bytes(url, max_bytes=max_bytes, accept="application/pdf")
    except EndpointSkipped:
        raise
    except http.SourceTooLarge as exc:
        raise TooLarge(f"{url}: {exc}") from exc
    except Exception as exc:  # Network, HTTP, TLS, and size failures are all retried later.
        raise FetchFailed(f"{url}: {exc}") from exc
    if not raw.startswith(b"%PDF-"):
        raise Uncertain(f"{url} is not a PDF")
    with tempfile.TemporaryDirectory(prefix="tahoe-report-") as scratch:
        path = Path(scratch) / "source.pdf"
        path.write_bytes(raw)
        info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30, check=True).stdout
        count = int(next(line for line in info.splitlines() if line.startswith("Pages:")).split(":", 1)[1])
        if not 1 <= count <= max_pages:
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


# --- What a rule may use --------------------------------------------------------

def _fetch_html(url: str) -> str:
    from . import discovery  # looked up per call, so tests that replace discovery.fetch_text stay offline
    return discovery.fetch_text(url)


def _connection_reset(exc: Exception) -> bool:
    return isinstance(exc, ConnectionResetError) or isinstance(getattr(exc, "reason", None), ConnectionResetError)


def _fetch_json(url: str):
    """One JSON response (``Accept: application/json``), parsed; a reset connection is retried once."""
    for attempt in (1, 2):
        try:
            raw, _ = http.fetch_bytes(url, max_bytes=MAX_JSON_BYTES, accept="application/json")
            return json.loads(raw.decode("utf-8-sig"))
        except Exception as exc:
            if attempt == 2 or not _connection_reset(exc):
                raise


def _fetch_bytes(url: str, *, accept: str, max_bytes: int) -> bytes:
    raw, _ = http.fetch_bytes(url, max_bytes=max_bytes, accept=accept)
    return raw


class Context:
    """What a rule may use: the meeting, its stored pages (``stored(version_id) -> {page: text}``), and temporary reads.

    ``reader`` is the PDF reader (``read_pdf``). ``fetch_html(url) -> markup``, ``fetch_json(url) -> object``, and
    ``fetch_bytes(url, *, accept, max_bytes) -> bytes`` replace the network fetchers (tests pass fixture data);
    left out, the module's own fetchers are looked up at call time.
    """

    def __init__(self, stored, meeting: Meeting, reader, delay: float = 0.0, fetch_html=None, fetch_json=None,
                 fetch_bytes=None):
        self._stored, self.meeting, self._reader, self.delay = stored, meeting, reader, delay
        self._fetchers = {"html": fetch_html, "json": fetch_json, "bytes": fetch_bytes}
        self.downloads = 0
        self.skipped: dict[str, str] = {}
        self.fetch_failures: list[str] = []
        self.attachments: list[Attachments] = []
        self._cache: dict[str, dict] = {}
        self._html: dict[str, list] = {}
        self._fetched: dict[tuple, object] = {}

    def _pause(self) -> None:
        if self.downloads and self.delay:
            time.sleep(self.delay)
        self.downloads += 1

    def _fetch(self, kind: str, url: str, fetch):
        """One download of ``url``, cached per kind and URL: counted, delayed, and with failures mapped to ours."""
        key = (kind, url)
        if key not in self._fetched:
            self._pause()
            try:
                self._fetched[key] = fetch()
            except (EndpointSkipped, FetchFailed, Uncertain):
                raise
            except http.SourceTooLarge as exc:
                raise TooLarge(f"{url}: {exc}") from exc
            except Exception as exc:  # Network, HTTP, TLS, size, and parse failures are all retried later.
                raise FetchFailed(f"{url}: {exc}") from exc
        return self._fetched[key]

    def read(self, url: str, *, max_bytes: int | None = None, max_pages: int | None = None, **kwargs) -> dict:
        """A temporary PDF read (``read_pdf``); ``max_bytes``/``max_pages`` replace its default limits when given."""
        self._pause()
        limits = {k: v for k, v in (("max_bytes", max_bytes), ("max_pages", max_pages)) if v is not None}
        return self._reader(url, **kwargs, **limits)

    def read_report(self, item: Item, url: str) -> dict | None:
        """One item's own report, or None (the item is skipped) when it cannot be read.

        A report over the download limit (``TooLarge``) is skipped for good; other download
        failures are recorded so the meeting is retried after ``RETRY_HOURS``.
        """
        try:
            return self.read(url, last_page=LEADING_PAGES)
        except Uncertain as exc:
            self.skip(item, str(exc))
        except TooLarge:
            self.skip(item, "report is larger than the download limit")
        except (FetchFailed, EndpointSkipped) as exc:
            self.skip(item, "download failed")
            self.fetch_failures.append(str(exc)[:300])
        except subprocess.SubprocessError:
            self.skip(item, "report PDF could not be read")
        return None

    def html(self, url: str) -> str:
        """The raw markup of an HTML page (``""`` for an empty one).

        Counts as a download, honours the delay, and is fetched at most once per context. A
        failed fetch raises ``FetchFailed`` (the meeting is retried later) and a skipped
        endpoint ``EndpointSkipped``.
        """
        return self._fetch("html", url, lambda: (self._fetchers["html"] or _fetch_html)(url))

    def html_links(self, url: str) -> list[dict]:
        """Every http(s) link on an HTML page (``html``), absolute and in document order: ``[{"url", "text"}]``.

        Markup with no links at all raises ``Uncertain``.
        """
        if url not in self._html:
            from .discovery import anchors
            links = [{"url": href, "text": text} for href, text in anchors(self.html(url), url)
                     if re.match(r"https?://", href, re.I)]
            if not links:
                raise Uncertain(f"{url} has no links")
            self._html[url] = links
        return [dict(link) for link in self._html[url]]

    def fetch_json(self, url: str):
        """A JSON API response, parsed (one retry on a reset connection); same accounting and failures as ``html``.

        The cached object is returned on later calls for the same URL: don't modify it.
        """
        return self._fetch("json", url, lambda: (self._fetchers["json"] or _fetch_json)(url))

    def fetch_bytes(self, url: str, *, accept: str, max_bytes: int) -> bytes:
        """A file's bytes (a Word report, say); same accounting and failures as ``html``. Over ``max_bytes`` raises
        ``TooLarge``."""
        return self._fetch("bytes", url, lambda: (self._fetchers["bytes"] or _fetch_bytes)(url, accept=accept,
                                                                                          max_bytes=max_bytes))

    def stored_pages(self, version_id: str) -> dict[int, str]:
        if version_id not in self._cache:
            self._cache[version_id] = self._stored(version_id)
        return self._cache[version_id]

    def pages(self, document: Document, *, needed, max_bytes: int | None = None, max_pages: int | None = None) -> dict[int, str]:
        """All page texts of a stored document, downloading it only when stored text lacks what ``needed`` looks for."""
        stored = self.stored_pages(document.version_id)
        if needed(stored):
            return stored
        fetched = self.read(document.url, max_bytes=max_bytes, max_pages=max_pages)
        if fetched["sha256"] != document.sha256:
            raise Uncertain(f"{document.title} changed since it was captured")
        return {p["page_number"]: p["text"] for p in fetched["pages"]}

    def skip(self, item: Item, reason: str) -> None:
        self.skipped[item.id] = reason

    def numbered(self) -> list[Item]:
        """Numbered, non-procedural items with a unique number in this meeting.

        Items printed without a number are left out silently (no skip reason): a rule that
        can only match them by title uses ``by_title``.
        """
        counts = {}
        for item in self.meeting.items:
            counts[norm_number(item.number)] = counts.get(norm_number(item.number), 0) + 1
        chosen = []
        for item in self.meeting.items:
            if not item.number.strip():
                continue
            if procedural(item):
                self.skip(item, "procedural")
            elif counts[norm_number(item.number)] > 1:
                self.skip(item, "item number repeats in this agenda")
            else:
                chosen.append(item)
        return chosen

    def by_title(self, texts: dict[str, str], items: list[Item] | None = None, *,
                 floor: float = TITLE_MATCH_FLOOR) -> dict[str, str]:
        """``{item id: key}`` pairing items with the one candidate whose printed text names them, for rules with no number.

        ``texts`` maps a key (usually a report URL) to its printed text (link text, file
        name, or report heading). An item matches a key when at least ``floor`` (0.8) of the
        title's distinctive words appear in the text, the title has at least two such words,
        exactly one key matches the item, and no other item matches that key. Items are the
        meeting's non-procedural items (numbered or not) unless given; the rest are skipped
        with a reason. Title-only matching is one signal: the rule must still check the
        report itself (``report_names_item`` or a heading check) before storing a mapping.
        """
        items = [i for i in self.meeting.items if not procedural(i)] if items is None else items
        matches = {}
        for item in items:
            if len({w for w in words(item.title) if len(w) > 3 and w not in _STOP}) < 2:
                matches[item.id] = []
                continue
            matches[item.id] = [key for key, text in texts.items() if title_overlap(item.title, text) >= floor]
        claimed: dict[str, int] = {}
        for keys in matches.values():
            for key in keys:
                claimed[key] = claimed.get(key, 0) + 1
        found = {}
        for item in items:
            keys = matches[item.id]
            if len(keys) != 1:
                self.skip(item, f"{len(keys)} report titles match the item title" if keys else "no report title matches the item title")
            elif claimed[keys[0]] > 1:
                self.skip(item, "the matching report title also matches another item")
            else:
                found[item.id] = keys[0]
        return found


# --- Generic helpers for rules -----------------------------------------------------

def leading(pages: dict[int, str], start: int, end: int) -> list:
    """The report's kept pages: up to ``LEADING_PAGES`` non-blank pages from ``start`` (and no later than ``end``), cleaned."""
    return [(n, clean_text(pages.get(n, "")).strip()[:MAX_PAGE_CHARS]) for n in range(start, min(end, start + LEADING_PAGES - 1) + 1)
            if pages.get(n, "").strip()]


def pdf_agenda(ctx: Context, *, prefer: str) -> Document:
    """The meeting's single agenda PDF (not a packet, minutes, supplement, or addendum), preferring titles matching ``prefer``."""
    pdfs = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
            and not re.search(r"packet|minutes|supplement|addendum", d.source_document_id + " " + d.title, re.I)]
    chosen = [d for d in pdfs if re.search(prefer, d.source_document_id + " " + d.title, re.I)] or pdfs
    if len(chosen) != 1:
        raise Uncertain("no single agenda PDF")
    return chosen[0]


def long_date(day: date) -> str:
    """``October 1, 2026``: the way most reports print a meeting date."""
    return f"{day:%B} {day.day}, {day.year}"


def agenda_page_column(item: Item, ctx: Context) -> int | None:
    """The packet page number an agenda's Page column prints at the end of the item's first title line, or None.

    Reads the stored agenda page each citation points to and finds the line holding the
    citation's first line; the number must be the line's last field, set off by two or
    more spaces (optionally "Page 12").
    """
    for citation in item.citations:
        text = ctx.stored_pages(citation["documentId"]).get(int(citation["page"]), "")
        quote = citation["quote"].strip()
        first_line = re.sub(r"\s{2,}(?:Page\s+)?\d{1,4}$", "", quote.splitlines()[0].strip()) if quote else ""
        for line in text.splitlines():
            if first_line and first_line in line:
                number = re.search(r"\S\s{2,}(?:Page\s+)?(\d{1,4})\s*$", line)
                return int(number.group(1)) if number else None
    return None


_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_MEETING_DATE = re.compile(
    r"\bmeeting(?:\s+date)?\s*(?:of|:|[-–])?\s*(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+)?"
    rf"(?:(?P<month>{_MONTHS})\.?\s+(?P<day>\d{{1,2}})(?:\s*[-–]\s*(?P<last>\d{{1,2}}))?,?\s+(?P<year>\d{{4}})"
    r"|(?P<m>\d{1,2})/(?P<d>\d{1,2})/(?P<y>\d{2}(?:\d{2})?))", re.I)


def printed_meeting_dates(text: str) -> list[tuple[date, date]]:
    """Each date range printed after a "Meeting", "Meeting Date:", or "Meeting of" label, as (first day, last day)."""
    found = []
    for m in _MEETING_DATE.finditer(text):
        try:
            if m["month"]:
                month = [x.casefold() for x in _MONTHS.split("|")].index(m["month"].casefold()) + 1
                first = date(int(m["year"]), month, int(m["day"]))
                found.append((first, first.replace(day=int(m["last"])) if m["last"] else first))
            else:
                year = int(m["y"]) + (2000 if len(m["y"]) == 2 else 0)
                first = date(year, int(m["m"]), int(m["d"]))
                found.append((first, first))
        except ValueError:
            continue
    return found


_MONTH_NAMES = [m.casefold() for m in _MONTHS.split("|")]
# A date as reports print it after a label: "October 21, 2026", "Oct. 21, 2026", "JUNE 24, 2026", "September 03, 2026".
PRINTED_DATE = r"[A-Za-z]{3,9}\.?\s+\d{1,2}(?:,\s*|\s+)\d{4}"
_PRINTED_DATE = re.compile(r"\s*([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:,\s*|\s+)(\d{4})\b")


def parse_printed_date(text: str) -> date | None:
    """The date ``text`` starts with (``PRINTED_DATE``: a full or three-letter month name, any case, "Sept", and
    zero-padded days allowed), or None. Rules find it after their own label, e.g.
    ``re.search(rf"MEETING DATE:\s*({PRINTED_DATE})", page)``."""
    found = _PRINTED_DATE.match(text)
    if not found:
        return None
    name = found.group(1).casefold()
    month = next((n for n, full in enumerate(_MONTH_NAMES, 1) if name in (full, full[:3]) or (name == "sept" and n == 9)), None)
    try:
        return date(int(found.group(3)), month, int(found.group(2))) if month else None
    except ValueError:
        return None


def stored_pdf_packets(ctx: "Context", pattern=None, *, documents=None) -> list[Document]:
    """The meeting's captured PDFs, one per file (sha256), that look like a packet.

    ``documents`` defaults to the meeting's current documents. Discovery may store one file
    under two URLs; the copy kept is the first by (URL with an encoded "+", source document
    ID), so a plain URL wins. With ``pattern`` (a regex), only files whose stored pages match it
    somewhere are kept.
    """
    found: dict[str, Document] = {}
    for document in sorted(ctx.meeting.documents if documents is None else documents,
                           key=lambda d: ("%2B" in d.url, d.source_document_id)):
        if document.media_type != "application/pdf" or document.sha256 in found:
            continue
        if pattern is None or any(re.search(pattern, text) for text in ctx.stored_pages(document.version_id).values()):
            found[document.sha256] = document
    return list(found.values())


def report_names_item(first_page_text: str, item: Item, *, number_patterns, meeting_date: date | None = None,
                      window: int = 600, min_overlap: float = MIN_TITLE_OVERLAP) -> dict | str:
    """Check that a report's first page names this item: an evidence dict, or the reason to skip it.

    - Number: one of ``number_patterns`` (regex templates; ``{n}`` stands for the escaped,
      normalised item number, e.g. ``r"^\\s*ITEM\\s+{n}\\s*$"`` or ``r"Agenda Item No\\.?\\s*{n}\\b"``)
      must match, searched with ``re.M`` (add ``(?i)`` in the template for case-insensitive).
    - Title: at least ``min_overlap`` of the item title's distinctive words appear within
      ``window`` characters from the start of the matched heading.
    - Date: when ``meeting_date`` is given and the page prints a meeting date (a
      "Meeting Date:", "Meeting of", or "Board Meeting" label), it must cover that day.
      An undated report passes without a ``meetingDate`` entry.

    Evidence is ``{"reportHeading", "titleOverlap", "meetingDate"?}``. Items without a
    number cannot pass; use ``Context.by_title`` and a title-only check for those.
    """
    number = norm_number(item.number)
    if not number:
        return "item has no number"
    heading = None
    for template in number_patterns:
        heading = re.search(template.replace("{n}", re.escape(number)), first_page_text, re.M)
        if heading:
            break
    if not heading:
        return f"report does not name item {item.number}"
    overlap = title_overlap(item.title, first_page_text[heading.start():heading.end() + window])
    if overlap < min_overlap:
        return "report title differs from the agenda item"
    evidence = {"reportHeading": " ".join(heading.group().split()), "titleOverlap": round(overlap, 2)}
    if meeting_date:
        printed = printed_meeting_dates(first_page_text)
        if printed and not any(first <= meeting_date <= last for first, last in printed):
            return "report is for another meeting date"
        if printed:
            evidence["meetingDate"] = meeting_date.isoformat()
    return evidence


def _url_keyed(candidates: dict) -> bool:
    return bool(candidates) and all(re.match(r"https?://", key, re.I) for key in candidates)


def linked_reports(ctx: Context, items: list[Item], candidates: dict[str, set], *, verify, kind: str, title,
                   precheck=None, missing: str = "{count} report links for item {number}") -> list[Mapping]:
    """Map items that each link their own report file (one PDF per item, or one PDF covering several items).

    ``candidates`` maps a normalised item number to the set of report URLs found for it,
    or (when every key is a URL) a report URL to the set of normalised item numbers it
    covers, so one report may serve several items. An item needs exactly one URL, else it
    is skipped with ``missing`` (``{count}`` URLs, item ``{number}``). ``precheck(item,
    url) -> skip reason | None`` runs before any download. Each URL is read once (its
    first ``LEADING_PAGES`` pages); ``verify(item, report) -> evidence dict | skip reason``
    then checks the report itself (``report["url"]`` is set; ``report_names_item`` covers
    the usual heading, title, and date checks). A verified item maps to the whole file:
    pages 1..page_count, keeping its leading pages. ``title`` is a string formatted with
    ``{number}`` or a callable ``title(item) -> str``.
    """
    if _url_keyed(candidates):
        by_number: dict[str, set] = {}
        for url, numbers in candidates.items():
            for number in numbers:
                by_number.setdefault(norm_number(number), set()).add(url)
        candidates = by_number
    read: dict[str, dict | str] = {}
    mappings = []
    for item in items:
        found = candidates.get(norm_number(item.number), set()) if item.number.strip() else set()
        if len(found) != 1:
            ctx.skip(item, missing.format(count=len(found), number=item.number))
            continue
        url = next(iter(found))
        if precheck and (problem := precheck(item, url)):
            ctx.skip(item, problem)
            continue
        if url not in read:
            report = ctx.read_report(item, url)
            read[url] = {**report, "url": url} if report is not None else ctx.skipped[item.id]
            if report is None:
                continue
        report = read[url]
        if isinstance(report, str):
            ctx.skip(item, report)
            continue
        evidence = verify(item, report)
        if isinstance(evidence, str):
            ctx.skip(item, evidence)
            continue
        pages = {p["page_number"]: p["text"] for p in report["pages"]}
        mappings.append(Mapping(item.id, item.fingerprint, kind, title(item) if callable(title) else title.format(number=item.number),
                                url, 1, report["page_count"], report["page_count"], report["sha256"], evidence,
                                leading(pages, 1, report["page_count"])))
    return mappings


def packet_reports(ctx: Context, packet: Document, pages: dict[int, str], items: list[Item], *, page_label,
                   label_name: str = "Item {number}", kind: str = "staff-report", title: str = "Staff report in the agenda packet",
                   min_overlap: float = MIN_TITLE_OVERLAP) -> list[Mapping]:
    """Map items whose reports are runs of packet pages labelled with the item number (a header or footer on each page).

    ``page_label(page_text) -> normalised item number | None`` reads one page's label.
    Pages with the same label form one report from its first to its last labelled page,
    as long as no page between them carries another label (unlabelled pages, such as
    charts and slides, are included). The first page must also share ``min_overlap`` of
    the item title's distinctive words. ``label_name`` (formatted with ``{number}``)
    describes the label in skip reasons and evidence.
    """
    labels = {}
    for number, text in pages.items():
        if label := page_label(text):
            labels[number] = label
    blocks: dict[str, list] = {}
    for label in set(labels.values()):
        numbers = sorted(n for n, other in labels.items() if other == label)
        interrupted = any(labels.get(n, label) != label for n in range(numbers[0], numbers[-1] + 1))
        blocks[label] = [] if interrupted else [[numbers[0], numbers[-1]]]
    mappings = []
    for item in items:
        named = label_name.format(number=item.number)
        found = blocks.get(norm_number(item.number), [])
        if len(found) != 1:
            ctx.skip(item, f"no single run of pages headed {named}")
            continue
        start, end = found[0]
        if title_overlap(item.title, pages[start]) < min_overlap:
            ctx.skip(item, "staff report title differs from the agenda item")
            continue
        mappings.append(Mapping(item.id, item.fingerprint, kind, title, packet.url, start, end, packet.page_count, packet.sha256,
                                {"header": named, "pages": f"{start}-{end}"}, leading(pages, start, end)))
    return mappings


# --- Rule registry ---------------------------------------------------------------------

def _rules() -> dict:
    from .staff_report_rules import agency_rules  # imported on first use: rule modules import this module
    return agency_rules()


def _supported() -> tuple[str, ...]:
    return tuple(sorted(_rules()))


def __getattr__(name: str):
    """``RULES`` (agency ID -> its configured rule, parameters bound) and ``SUPPORTED`` (sorted agency IDs): every
    agency whose ``staffReports`` entry in the registry names a rule from ``staff_report_rules``."""
    if name == "RULES":
        return _rules()
    if name == "SUPPORTED":
        return _supported()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# --- Run ----------------------------------------------------------------------------

def _load(conn, *, agency: str | None, meeting: str | None) -> dict[str, Meeting]:
    agencies = [agency] if agency else list(_supported())
    meetings: dict[str, Meeting] = {}
    for r in conn.execute("""SELECT i.id,i.meeting_id,i.number,i.title,i.section,i.source_fingerprint,i.citations,
            m.agency_id,m.title AS meeting_title,m.local_date,m.official_url
            FROM agenda_items i JOIN meetings m ON m.id=i.meeting_id
            WHERE i.active AND m.quarantine_reason IS NULL AND m.agency_id=ANY(%s) AND (%s::text IS NULL OR m.id=%s)
            ORDER BY i.meeting_id,i.item_order,i.id""", (agencies, meeting, meeting)):
        m = meetings.setdefault(r["meeting_id"], Meeting(r["meeting_id"], r["agency_id"], r["meeting_title"], r["local_date"], [], [],
                                                                  r["official_url"]))
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
                      today: date | None = None, fetch_html=None) -> dict:
    """Find and store each pending meeting's item-to-staff-report mappings."""
    rules = _rules()
    if agency and agency not in rules:
        raise SystemExit(f"{agency}: no staff report mapping rule (supported: {', '.join(_supported())})")
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
        ctx = Context(stored, m, reader, delay, fetch_html)
        status, mappings, problem = "done", [], None
        try:
            mappings = rules[m.agency_id](ctx)
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
             "evidence": m.evidence, "pages": [{"number": n, "text": t} for n, t in m.pages], "media_type": m.media_type}
            for m in mappings]
    # A retried scan keeps mappings it could not recheck; a completed one removes those it no longer verifies.
    conn.execute("""WITH incoming AS (
            SELECT * FROM jsonb_to_recordset(%(rows)s::jsonb) AS r(item_id text,source_fingerprint text,kind text,title text,
              url text,page_start int,page_end int,page_count int,sha256 text,evidence jsonb,pages jsonb,media_type text)),
        removed AS (
            DELETE FROM item_staff_reports s USING agenda_items i
            WHERE %(complete)s AND s.item_id=i.id AND i.meeting_id=%(meeting)s AND s.item_id NOT IN (SELECT item_id FROM incoming)),
        stored AS (
            INSERT INTO item_staff_reports(item_id,source_fingerprint,method,kind,title,url,page_start,page_end,page_count,
              sha256,evidence,pages,media_type)
            SELECT item_id,source_fingerprint,%(method)s,kind,title,url,page_start,page_end,page_count,sha256,evidence,pages,media_type
            FROM incoming
            ON CONFLICT(item_id) DO UPDATE SET source_fingerprint=excluded.source_fingerprint,method=excluded.method,
              kind=excluded.kind,title=excluded.title,url=excluded.url,page_start=excluded.page_start,page_end=excluded.page_end,
              page_count=excluded.page_count,sha256=excluded.sha256,evidence=excluded.evidence,pages=excluded.pages,
              media_type=excluded.media_type,mapped_at=CASE WHEN item_staff_reports.sha256 IS DISTINCT FROM excluded.sha256
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
            "mediaType": row.get("media_type") or "application/pdf", "page": row["page_start"], "pageEnd": row["page_end"],
            "pageCount": row["page_count"], "sha256": row["sha256"], "fetchedAt": iso(row["mapped_at"]),
            "pages": [{"number": p["number"], "text": clean_text(p["text"])} for p in pages if p["number"] in cited_pages]}
