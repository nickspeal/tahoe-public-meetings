"""Discovery types for agencies that publish meetings as dated files or as calendar events (config/README.md).

`wordpress-media`: a WordPress media library (`indexUrl`, ``.../wp-json/wp/v2/media``)
searched for `search` (default "agenda"). Each uploaded file whose name matches
`bodyPattern` and states a full date ("TDVA-Agenda-November-11-2026-Packet.pdf",
"Stakeholder-Agenda-July-14-2026.pdf") is a document of the meeting on that date: the
agenda, or the packet when the name says "Packet". Files that name only a month are
skipped, because a meeting needs its date. An optional `noticeUrl` is a page whose
paragraphs announce cancellations ("the meeting scheduled for Wednesday, November 11,
2026 ... has been canceled"); a meeting on a date such a paragraph names is canceled.

`file-links`: a page of download links (`indexUrl`) whose file names carry the body and
the date ("TID Agenda 10.29.2026.docx", "TID Special meeting 9-24-26 (002).docx"), read
the same way; the page is each meeting's official URL. PDFs and Word (.docx) files qualify.

`tribe-events`: The Events Calendar's REST API (`indexUrl`, ``.../wp-json/tribe/events/v1/events``),
one candidate per event whose title matches `bodyPattern`, dated and timed by the event.
PDF and Word links in the event's description are its documents (agenda, then packet).
An event titled "CANCELLED - ..." is a canceled meeting. Scheduled discovery asks for
events from 60 days back to `horizonDays` (default 120) ahead.

A file name or title with "special", "workshop", or "joint" gives that meeting its own
`<date>-<word>` ID, so it never merges with a regular meeting on the same day. All three
types are registered by format name in `source_discovery.TYPES`; no code here names an agency.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .discovery import anchors
from .html_dom import document
from .source_discovery import _QUALIFIER, agenda_then_packet, candidate, date_in

FILE_TYPES = ('.pdf', '.docx')
MEDIA_PAGE_SIZE = 100
EVENTS_PAGE_SIZE = 50
EVENTS_BACK_DAYS = 60
EVENTS_AHEAD_DAYS = 120
MAX_PAGES = 20
_CANCELED_TITLE = re.compile(r'^\s*CANCELL?ED\b[^–—-]*[–—-]\s*', re.I)


def _today() -> date:
    return datetime.now(ZoneInfo('America/Los_Angeles')).date()


def file_name(url: str) -> str:
    """A file's readable name: the last path segment, unquoted, with separators as spaces and no extension."""
    name = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit('/', 1)[-1])
    name = re.sub(r'\.(?:pdf|docx?)$', '', name, flags=re.I)
    # "9-24-26" -> "9.24.26" before dashes become spaces, so a two-digit year still reads as a date.
    name = re.sub(r'(?<!\d)(\d{1,2})-(\d{1,2})-(\d{2})(?!\d)', r'\1.\2.\3', name)
    return ' '.join(re.sub(r'[-_+]+', ' ', name).split())


def _is_file(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == 'https' and parsed.path.lower().endswith(FILE_TYPES)


def _documents(agency: str, files: list[tuple[str, str]]) -> list[dict]:
    """(url, label) files as one meeting's documents: agenda first, then its packet; repeated roles get a URL hash."""
    docs, seen = [], set()
    for url, label in files:
        if url in seen:
            continue
        seen.add(url)
        role = 'agenda-packet' if re.search(r'\bpacket\b', file_name(url) + ' ' + label, re.I) else 'agenda'
        docs.append({'id': f'{agency}-{role}', 'title': label or file_name(url), 'url': url})
    docs = agenda_then_packet(docs)
    counts = {i: sum(d['id'] == i for d in docs) for i in {d['id'] for d in docs}}
    for d in docs:
        if counts[d['id']] > 1:
            d['id'] += '-' + hashlib.sha256(d['url'].encode()).hexdigest()[:10]
    return docs


def _qualified(day: str, text: str) -> tuple[str, str | None]:
    qualifier = _QUALIFIER.search(text)
    return (day + '-' + qualifier[1].lower(), qualifier[1].title()) if qualifier else (day, None)


def _title(agency: str, config: dict, qualifier: str | None) -> str:
    title = config.get('title') or agency.replace('-', ' ').title() + ' Meeting'
    return f'{qualifier} {title}' if qualifier and not title.lower().startswith(qualifier.lower()) else title


def meetings_from_files(agency: str, config: dict, files: list[tuple[str, str]], official_url: str | None) -> list[dict]:
    """Candidates from dated files, newest first: one per date (and qualifier), its files as documents.

    ``files`` are (url, label) pairs. A file counts when its name matches `bodyPattern` and states a
    full date. ``official_url`` is the meeting's official page, else its first document.
    """
    body = re.compile(config.get('bodyPattern', '.'), re.I)
    grouped: dict[str, list] = {}
    for url, label in files:
        if not _is_file(url):
            continue
        name = file_name(url)
        if not body.search(name):
            continue
        day = date_in(name)
        if not day:
            continue
        sid, qualifier = _qualified(config.get('idPrefix', '') + day, name)
        grouped.setdefault(sid, [day, qualifier, []])[2].append((url, label if label and label.lower() != 'download' else name))
    rows = []
    for sid, (day, qualifier, found) in grouped.items():
        docs = _documents(agency, found)
        rows.append(candidate(agency, sid, _title(agency, config, qualifier), day, official_url or docs[0]['url'], docs))
    return sorted(rows, key=lambda r: (r['date'], r['source_id']), reverse=True)


def canceled_dates(markup: str) -> set[str]:
    """Dates named in a page's paragraphs that announce a cancellation."""
    found = set()
    for node in document(markup).find():
        if node.tag not in ('p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            continue
        text = ' '.join(node.text().split())
        if re.search(r'\bcancel(?:l?ed|lation)\b', text, re.I) and (day := date_in(text)):
            found.add(day)
    return found


def _mark_canceled(agency: str, config: dict, rows: list[dict], fetch) -> None:
    if not config.get('noticeUrl'):
        return
    try:
        dates = canceled_dates(fetch(config['noticeUrl']))
    except (OSError, RuntimeError, ValueError):  # the files alone still give the meetings
        return
    for row in rows:
        if row['date'] in dates:
            row['status'] = 'canceled'


# ---------------------------------------------------------------- wordpress-media

def _media_files(config: dict, fetch, page: int) -> list[tuple[str, str]]:
    query = urllib.parse.urlencode({'search': config.get('search', 'agenda'), 'per_page': MEDIA_PAGE_SIZE, 'page': page,
                                    'orderby': 'date', 'order': 'desc', '_fields': 'id,date,source_url,title'})
    records = json.loads(fetch(f"{config['indexUrl']}?{query}"))
    return [(r.get('source_url') or '', html.unescape((r.get('title') or {}).get('rendered') or '')) for r in records]


def wordpress_media(agency: str, config: dict, limit: int | None, fetch) -> list[dict]:
    """The newest meetings among the library's newest matching uploads (one page of 100)."""
    rows = meetings_from_files(agency, config, _media_files(config, fetch, 1), None)[:limit]
    _mark_canceled(agency, config, rows, fetch)
    return rows


def wordpress_media_range(agency: str, config: dict, since: date, until: date, fetch) -> tuple[list[dict], str | None]:
    """Every meeting dated since..until, paging the search results (newest uploads first) until they run out."""
    files = []
    for page in range(1, MAX_PAGES + 1):
        try:
            found = _media_files(config, fetch, page)
        except Exception as exc:  # WordPress answers HTTP 400 past the last page.
            if page > 1 and '400' in str(exc):
                break
            raise
        files.extend(found)
        if len(found) < MEDIA_PAGE_SIZE:
            break
    rows = meetings_from_files(agency, config, files, None)
    _mark_canceled(agency, config, rows, fetch)
    earliest = min((r['date'] for r in rows), default=None)
    return [r for r in rows if since.isoformat() <= r['date'] <= until.isoformat()], earliest


# ---------------------------------------------------------------- file-links

def file_links(agency: str, config: dict, limit: int | None, fetch) -> list[dict]:
    """Meetings from a page's dated download links, newest first; the page is each meeting's official URL."""
    url = config['indexUrl']
    files = [(target, label) for target, label in anchors(fetch(url), url)]
    return meetings_from_files(agency, config, files, url)[:limit]


# ---------------------------------------------------------------- tribe-events

def _event_rows(agency: str, config: dict, events: list[dict]) -> list[dict]:
    body = re.compile(config.get('bodyPattern', '.'), re.I)
    rows = []
    for event in events:
        title = ' '.join(html.unescape(event.get('title') or '').split())
        start = event.get('start_date') or ''
        day = start[:10]
        if not body.search(title) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or not event.get('id'):
            continue
        link = event.get('url') or config['indexUrl']
        files = [(u, l) for u, l in anchors(event.get('description') or '', link) if _is_file(u)]
        canceled = _CANCELED_TITLE.match(title)
        shown = config.get('title') or (title[canceled.end():] if canceled else title)
        row = candidate(agency, str(event['id']), shown, day, link, _documents(agency, files), title)
        row['start_time'] = None if event.get('all_day') or len(start) < 16 else start[11:16]
        venue = event.get('venue') if isinstance(event.get('venue'), dict) else {}
        place = [html.unescape(str(venue.get(k) or '')).strip() for k in ('venue', 'address', 'city')]
        row['location'] = ', '.join(p for p in place if p) or None
        rows.append(row)
    return rows


def _events(config: dict, since: date, until: date, fetch) -> list[dict]:
    found = []
    for page in range(1, MAX_PAGES + 1):
        query = urllib.parse.urlencode({'start_date': since.isoformat(), 'end_date': until.isoformat(),
                                        'per_page': EVENTS_PAGE_SIZE, 'page': page})
        data = json.loads(fetch(f"{config['indexUrl']}?{query}"))
        found.extend(data.get('events') or [])
        if page >= int(data.get('total_pages') or 1):
            break
    return found


def tribe_events(agency: str, config: dict, limit: int | None, fetch) -> list[dict]:
    """Matching events from 60 days back to `horizonDays` ahead, newest first."""
    today = _today()
    events = _events(config, today - timedelta(days=EVENTS_BACK_DAYS),
                     today + timedelta(days=config.get('horizonDays', EVENTS_AHEAD_DAYS)), fetch)
    rows = sorted(_event_rows(agency, config, events), key=lambda r: r['date'], reverse=True)
    return rows[:limit]


def tribe_events_range(agency: str, config: dict, since: date, until: date, fetch) -> tuple[list[dict], str | None]:
    """Every matching event dated since..until (the API takes a date range)."""
    rows = _event_rows(agency, config, _events(config, since, until, fetch))
    return sorted(rows, key=lambda r: r['date'], reverse=True), min((r['date'] for r in rows), default=None)
