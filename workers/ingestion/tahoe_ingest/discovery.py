"""Shared fetch and link helpers, and the `wordpress-meetings` and `granicus-row-ids` discovery types.

`source_discovery` dispatches every agency's `discovery` block by its `type`; these
two types live here beside the PDF link helper they use. Discovery returns
candidates for capture/review. It does not infer agenda items, votes, or exact
meeting logistics from arbitrary page text.
"""
from __future__ import annotations

import html
import hashlib
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta
from html.parser import HTMLParser

from .http import fetch_bytes
from .records import granicus_links

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


# ---------------------------------------------------------------- wordpress-meetings
# A WordPress REST collection of meeting posts (`indexUrl`, e.g. .../wp-json/wp/v2/meeting).
# A board and its committees can share one feed; `bodyPattern` selects one by post title.

def _wordpress_post(agency: str, config: dict, record: dict, title: str, fetch) -> dict:
    """One post as a candidate: its uploaded PDFs, with IDs prefixed by `documentIdPrefix` (default: the agency ID)."""
    page_url = record["link"]
    page = fetch(page_url)
    docs = [d for d in _pdf_links(page, page_url, config.get("documentIdPrefix", agency)) if "/wp-content/uploads/" in d["url"]]
    return {"agency": agency, "source_id": str(record["id"]), "title": title, "official_url": page_url,
            "documents": docs, "source_modified": record.get("modified"),
            "warnings": ["Meeting date and agenda items require source review."]}


def wordpress_meetings(agency: str, config: dict, *, limit: int = 5, fetch=fetch_text, delay: float = 2.0) -> list[dict]:
    """The newest matching posts. They state no meeting date (capture reads it from the title or agenda)."""
    if not 1 <= limit <= 20:
        raise ValueError("discovery limit must be 1..20")
    body = re.compile(config.get("bodyPattern", "."), re.I)
    raw = fetch(f"{config['indexUrl']}?per_page={min(40, max(10, limit * 4))}&page=1&_fields=id,link,title,modified")
    found = []
    for record in json.loads(raw):
        title = html.unescape(record["title"]["rendered"])
        if not body.search(title):
            continue
        time.sleep(delay)
        found.append(_wordpress_post(agency, config, record, title, fetch))
        if len(found) >= limit:
            break
    return found


def wordpress_meetings_range(agency: str, config: dict, *, since, until, fetch=fetch_text, delay: float = 2.0,
                             max_pages: int = 10, post_margin_days: int = 180) -> tuple[list[dict], str | None]:
    """Page the posts (newest first) until they are older than ``since``.

    Posts are often published months before the meeting, so paging continues
    until a post's publication date is ``post_margin_days`` before ``since``.
    The meeting date comes from the post title. An undated matching post is kept
    when it was published within the range; its date comes from the agenda.
    """
    from .source_discovery import date_in  # Local import: source_discovery imports this module.
    body = re.compile(config.get("bodyPattern", "."), re.I)
    stop = (since - timedelta(days=post_margin_days)).isoformat()
    lo, hi = since.isoformat(), until.isoformat()
    found, earliest = [], None
    for page_number in range(1, max_pages + 1):
        try:
            raw = fetch(f"{config['indexUrl']}?per_page=100&page={page_number}&_fields=id,link,title,modified,date")
        except Exception as exc:  # WordPress answers HTTP 400 past the last page.
            if page_number > 1 and "400" in str(exc):
                break
            raise
        records = json.loads(raw)
        if not records:
            break
        for record in records:
            title = html.unescape(record["title"]["rendered"])
            day = date_in(title)
            posted = (record.get("date") or "")[:10]
            if day:
                earliest = min(earliest or day, day)
            if not body.search(title):
                continue
            if (day and not lo <= day <= hi) or (not day and not (posted and lo <= posted <= hi)):
                continue
            if delay:
                time.sleep(delay)
            found.append({**_wordpress_post(agency, config, record, title, fetch), "date": day})
        if len(records) < 100 or min((r.get("date") or "9999")[:10] for r in records) < stop:
            break
    return found, earliest


# ---------------------------------------------------------------- granicus-row-ids
# A Granicus publisher whose rows are named by their first cell's ID, such as
# `10-06-2026-City-Council`. The ID gives the date and the title ("10 06 2026 City
# Council"), which `bodyPattern` matches. The row's agenda viewer link is the
# official URL and its clip_id the source ID. It redirects to the compact agenda
# PDF, which is the first document (`{agency}-agenda`, the ID stored meetings
# already hold their agenda under, so a capture is a new version of it, not a
# second agenda), ahead of the row's PDFs (the packet). An upcoming meeting's row
# has no ID cell and no clip yet: its name cell holds "11 03 2026 City Council" and
# its agenda link names an event, so the source ID is `event-<id>` until the clip
# row appears, when `capture.source_identity` bridges it by date and title. A row
# with no agenda link at all is skipped. (The `granicus` type reads rows by their
# text instead, and also captures the HTML agenda.)

GRANICUS_ROW_ID = re.compile(r'<td\b[^>]*\bid="(?P<key>\d{2}-\d{2}-\d{4}-[^"]+)"', re.I)
GRANICUS_UPCOMING_ROW = re.compile(r'<td\b(?![^>]*\bid=)[^>]*\bheaders="Name"[^>]*>\s*(?P<key>\d{2} \d{2} \d{4} [^<]*?)\s*</td>', re.I)


def _granicus_id_rows(agency: str, config: dict, markup: str):
    """(date, candidate) for each matching row; the candidate is None when the row links no agenda."""
    index = config["indexUrl"]
    body = re.compile(config.get("bodyPattern", "."), re.I)
    matches = [(m, m.group("key").replace("-", " ")) for m in GRANICUS_ROW_ID.finditer(markup)]
    matches += [(m, html.unescape(m.group("key"))) for m in GRANICUS_UPCOMING_ROW.finditer(markup)]
    for match, title in sorted(matches, key=lambda pair: pair[0].start()):
        if not body.search(title):
            continue
        day = datetime.strptime(title[:10].replace(" ", "-"), "%m-%d-%Y").date().isoformat()
        row_end = markup.find("</tr>", match.start())
        row = markup[match.start():row_end] if row_end >= 0 else ""
        links = anchors(row, index)
        agenda = next((url for url, _ in links if "AgendaViewer.php" in url), None)
        ident = re.search(r"[?&](clip_id|event_id)=(\d+)", agenda) if agenda else None
        if not ident:
            yield day, None
            continue
        source_id = ident.group(2) if ident.group(1) == "clip_id" else f"event-{ident.group(2)}"
        yield day, {"agency": agency, "source_id": source_id, "title": title, "official_url": agenda, "date": day,
                    "documents": [{"id": f"{agency}-agenda", "title": "Agenda", "url": agenda},
                                  *(d for d in _pdf_links(row, index, agency)
                                    if d["url"] != agenda and not re.fullmatch(rf"{re.escape(agency)}-agenda(-[0-9a-f]{{12}})?", d["id"]))],
                    "warnings": ["Publisher row date/time and agenda items require source review."],
                    **granicus_links(links, html.unescape(row), index)}


def granicus_row_ids(agency: str, config: dict, *, limit: int = 5, fetch=fetch_text) -> list[dict]:
    if not 1 <= limit <= 20:
        raise ValueError("discovery limit must be 1..20")
    found = []
    for _, row in _granicus_id_rows(agency, config, fetch(config["indexUrl"])):
        if row:
            found.append(row)
            if len(found) >= limit:
                break
    return found


def granicus_row_ids_range(agency: str, config: dict, *, since, until, fetch=fetch_text) -> tuple[list[dict], str | None]:
    """Every matching row of the publisher (a full archive) dated since..until."""
    found, earliest = {}, None
    lo, hi = since.isoformat(), until.isoformat()
    for day, row in _granicus_id_rows(agency, config, fetch(config["indexUrl"])):
        earliest = min(earliest or day, day)
        if row and lo <= day <= hi:
            found.setdefault(row["source_id"], row)
    return list(found.values()), earliest
