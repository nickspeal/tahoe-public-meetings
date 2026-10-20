"""Bounded discovery of official meeting pages and linked PDFs.

Discovery returns candidates for capture/review. It does not infer agenda items,
votes, or exact meeting logistics from arbitrary page text.
"""
from __future__ import annotations

import html
import hashlib
import json
import re
import time
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

from .http import fetch_bytes

TTD_API = "https://tahoetransportation.org/wp-json/wp/v2"
SLT_INDEX = "https://slt.granicus.com/ViewPublisher.php?view_id=6"
TRUCKEE_INDEX = "https://townoftruckee.gov/Calendar.aspx?CID=0&month={month}&view=list&year={year}"
MAX_HTML = 6 * 1024 * 1024


def fetch_text(url: str, *, max_bytes=MAX_HTML) -> str:
    data, _ = fetch_bytes(url, max_bytes=max_bytes, accept="text/html, application/xml, application/json")
    return data.decode("utf-8-sig", "replace")


class Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.href = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data):
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.rows.append((self.href, " ".join(" ".join(self.text).split())))
            self.href = None


def anchors(markup: str, base: str) -> list[tuple[str, str]]:
    parser = Anchors()
    parser.feed(markup)
    return [(urllib.parse.urljoin(base, href), label) for href, label in parser.rows]


def _pdf_links(markup: str, base: str, agency: str) -> list[dict]:
    found = []
    seen = set()
    for url, label in anchors(markup, base):
        parsed = urllib.parse.urlparse(url)
        inner = urllib.parse.parse_qs(parsed.query).get("u", [""])[0]
        if parsed.scheme != "https" or not (parsed.path.lower().endswith(".pdf") or inner.lower().endswith(".pdf")):
            continue
        if url not in seen:
            lower = (label + " " + urllib.parse.unquote(url)).lower()
            role = ("agenda-packet" if "agenda packet" in lower or "packet" in lower else
                    "agenda" if "agenda" in lower else "minutes" if "minutes" in lower else "attachment")
            found.append({"id": f"{agency}-{role}", "title": label or "Official PDF", "url": url})
            seen.add(url)
    counts = {role: sum(d["id"] == role for d in found) for role in {d["id"] for d in found}}
    for document in found:
        if document["id"].endswith("-attachment") or counts[document["id"]] > 1:
            suffix = hashlib.sha256(document["url"].encode()).hexdigest()[:12]
            document["id"] += f"-{suffix}"
    return found


def discover_ttd(*, limit: int = 5, fetch=fetch_text, delay: float = 2.0) -> list[dict]:
    if not 1 <= limit <= 20:
        raise ValueError("TTD discovery limit must be 1..20")
    raw = fetch(f"{TTD_API}/meeting?per_page={min(40, max(10, limit * 4))}&page=1&_fields=id,link,title,modified")
    records = json.loads(raw)
    found = []
    for record in records:
        title = html.unescape(record["title"]["rendered"])
        if "board" not in title.lower():
            continue
        time.sleep(delay)
        page_url = record["link"]
        page = fetch(page_url)
        docs = [d for d in _pdf_links(page, page_url, "ttd") if "/wp-content/uploads/" in d["url"]]
        found.append({"agency": "ttd", "source_id": str(record["id"]),
                      "title": title, "official_url": page_url,
                      "documents": docs, "source_modified": record.get("modified"),
                      "warnings": ["Meeting date and agenda items require source review."]})
        if len(found) >= limit:
            break
    return found


def discover_slt(*, limit: int = 5, fetch=fetch_text) -> list[dict]:
    if not 1 <= limit <= 20:
        raise ValueError("SLT discovery limit must be 1..20")
    markup = fetch(SLT_INDEX)
    found = []
    # Granicus publisher rows have IDs such as 10-06-2026-City-Council.
    for match in re.finditer(r'<td\b[^>]*\bid="(?P<key>\d{2}-\d{2}-\d{4}-[^"]+)"', markup, re.I):
        if "City-Council" not in match.group("key"):
            continue
        row_end = markup.find("</tr>", match.start())
        if row_end < 0:
            continue
        row = markup[match.start():row_end]
        links = anchors(row, SLT_INDEX)
        clip = next((re.search(r"[?&]clip_id=(\d+)", url) for url, _ in links if "AgendaViewer.php" in url), None)
        if not clip:
            continue
        title = match.group("key").replace("-", " ")
        found.append({"agency": "slt", "source_id": clip.group(1), "title": title,
                      "official_url": next(url for url, _ in links if "AgendaViewer.php" in url),
                      "date": datetime.strptime(match.group("key")[:10], "%m-%d-%Y").date().isoformat(),
                      "documents": _pdf_links(row, SLT_INDEX, "slt"),
                      "warnings": ["Publisher row date/time and agenda items require source review."]})
        if len(found) >= limit:
            break
    return found


def discover_truckee(*, year: int, month: int, limit: int = 5, fetch=fetch_text) -> list[dict]:
    if not 1 <= month <= 12 or not 2020 <= year <= 2100 or not 1 <= limit <= 20:
        raise ValueError("Truckee year/month/limit are outside bounds")
    index_url = TRUCKEE_INDEX.format(year=year, month=month)
    markup = fetch(index_url)
    event_links = []
    seen = set()
    for match in re.finditer(r'<a\b(?=[^>]*\bid="eventTitle_(\d+)")[^>]*>(.*?)</a>', markup, re.I | re.S):
        eid = match.group(1)
        label = html.unescape(re.sub(r"<[^>]+>", " ", match.group(2))).strip()
        if eid in seen or "town council" not in label.lower():
            continue
        href = re.search(r'\bhref="([^"]+)"', match.group(0), re.I)
        if not href:
            continue
        event_links.append((eid, label, urllib.parse.urljoin(index_url, html.unescape(href.group(1)))))
        seen.add(eid)
        if len(event_links) >= limit:
            break
    found = []
    for eid, label, event_url in event_links:
        page = fetch(event_url)
        start_match = re.search(r'itemprop="startDate"[^>]*>\s*(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})', page, re.I)
        found.append({"agency": "truckee", "source_id": eid, "title": label,
                      "official_url": event_url, "documents": _pdf_links(page, event_url, "truckee"),
                      "date": start_match.group(1) if start_match else None,
                      "start_time": start_match.group(2) if start_match else None,
                      "status": "canceled" if re.search(r"cancel(l)?ation|canceled|cancelled", label, re.I) else "scheduled",
                      "warnings": ["Calendar event date/time and agenda items require source review."]})
    return found


# ---------------------------------------------------------------- date-range discovery (backfill)
# Variants of the adapters above, not bounded by a candidate count, for a one-time
# history backfill. Each returns (candidates dated since..until, earliest date the
# source listed or None).

def _title_date(title: str):
    from .source_discovery import date_in  # Local import: source_discovery imports this module.
    return date_in(title)


def discover_ttd_range(*, since, until, fetch=fetch_text, delay: float = 2.0, max_pages: int = 10,
                       post_margin_days: int = 180) -> tuple[list[dict], str | None]:
    """Page TTD's WordPress meeting posts (newest first) until posts are older than ``since``.

    Posts are often published months before the meeting, so paging continues
    until a post's publication date is ``post_margin_days`` before ``since``.
    The meeting date comes from the post title. An undated board post is kept
    when it was published within the range; its date comes from the agenda.
    """
    from datetime import timedelta
    stop = (since - timedelta(days=post_margin_days)).isoformat()
    lo, hi = since.isoformat(), until.isoformat()
    found, earliest = [], None
    for page_number in range(1, max_pages + 1):
        try:
            raw = fetch(f"{TTD_API}/meeting?per_page=100&page={page_number}&_fields=id,link,title,modified,date")
        except Exception as exc:  # WordPress answers HTTP 400 past the last page.
            if page_number > 1 and "400" in str(exc):
                break
            raise
        records = json.loads(raw)
        if not records:
            break
        for record in records:
            title = html.unescape(record["title"]["rendered"])
            day = _title_date(title)
            posted = (record.get("date") or "")[:10]
            if day:
                earliest = min(earliest or day, day)
            if "board" not in title.lower():
                continue
            if (day and not lo <= day <= hi) or (not day and not (posted and lo <= posted <= hi)):
                continue
            if delay:
                time.sleep(delay)
            page_url = record["link"]
            page = fetch(page_url)
            docs = [d for d in _pdf_links(page, page_url, "ttd") if "/wp-content/uploads/" in d["url"]]
            found.append({"agency": "ttd", "source_id": str(record["id"]), "title": title, "official_url": page_url,
                          "date": day, "documents": docs, "source_modified": record.get("modified"),
                          "warnings": ["Meeting date and agenda items require source review."]})
        if len(records) < 100 or min((r.get("date") or "9999")[:10] for r in records) < stop:
            break
    return found, earliest


def discover_slt_range(*, since, until, fetch=fetch_text) -> tuple[list[dict], str | None]:
    """Every SLT City Council row in the Granicus publisher (a full archive) dated since..until."""
    markup = fetch(SLT_INDEX)
    found, earliest = {}, None
    lo, hi = since.isoformat(), until.isoformat()
    for match in re.finditer(r'<td\b[^>]*\bid="(?P<key>\d{2}-\d{2}-\d{4}-[^"]+)"', markup, re.I):
        if "City-Council" not in match.group("key"):
            continue
        day = datetime.strptime(match.group("key")[:10], "%m-%d-%Y").date().isoformat()
        earliest = min(earliest or day, day)
        row_end = markup.find("</tr>", match.start())
        if row_end < 0 or not lo <= day <= hi:
            continue
        row = markup[match.start():row_end]
        links = anchors(row, SLT_INDEX)
        clip = next((re.search(r"[?&]clip_id=(\d+)", url) for url, _ in links if "AgendaViewer.php" in url), None)
        if not clip:
            continue
        found.setdefault(clip.group(1), {
            "agency": "slt", "source_id": clip.group(1), "title": match.group("key").replace("-", " "),
            "official_url": next(url for url, _ in links if "AgendaViewer.php" in url), "date": day,
            "documents": _pdf_links(row, SLT_INDEX, "slt"),
            "warnings": ["Publisher row date/time and agenda items require source review."]})
    return list(found.values()), earliest


def discover_truckee_range(*, since, until, fetch=fetch_text, delay: float = 1.0) -> tuple[list[dict], str | None]:
    """Read the Truckee calendar month by month from ``since`` through ``until``."""
    found, earliest = [], None
    year, month = since.year, since.month
    while (year, month) <= (until.year, until.month):
        if (year, month) != (since.year, since.month) and delay:
            time.sleep(delay)
        for candidate in discover_truckee(year=year, month=month, limit=20, fetch=fetch):
            day = candidate.get("date")
            if day:
                earliest = min(earliest or day, day)
            if not day or since.isoformat() <= day <= until.isoformat():
                found.append(candidate)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return found, earliest
