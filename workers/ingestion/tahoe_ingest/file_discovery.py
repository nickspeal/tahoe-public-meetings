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
With `matchLabels`, each link's label is read instead of its file name, for a page that
names its files loosely but labels every link with the body and date ("Regular Meeting of
the Board of Directors – 10/22/2026 – Agenda Packet").

`tribe-events`: The Events Calendar's REST API (`indexUrl`, ``.../wp-json/tribe/events/v1/events``),
one candidate per event whose title matches `bodyPattern`, dated and timed by the event.
PDF and Word links in the event's description are its documents (agenda, then packet).
An event titled "CANCELLED - ..." is a canceled meeting. Scheduled discovery asks for
events from 60 days back to `horizonDays` (default 120) ahead.

`evogov-meetings`: an EvoGov site's meeting dashboard (`indexUrl`, ``.../meetingdashboard``),
whose year headings load each calendar's meetings from the site's ``/meetings/year_events/``
endpoint, one table row per event. An event whose name matches `bodyPattern` is a meeting,
its source ID the event ID and its official URL the event's page. The row's sort key is the
start in UTC, read as a local date and time. Files under the row's "Agendas" heading are
agendas, a "packet" under "Attachments" is the packet, and a file under "Minutes" is the
minutes; other attachments are left out. A name ending "- Cancelled" is a canceled meeting.
Scheduled discovery reads the listed years that meet the window of 60 days back to 120 ahead.

In the first three types, a file name or title with "special", "workshop", or "joint" gives that
meeting its own `<date>-<word>` ID, so it never merges with a regular meeting on the same day.
All four types are registered by format name in `source_discovery.TYPES`; no code here names
an agency.
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
from .records import listing_minutes
from .source_discovery import _QUALIFIER, agenda_then_packet, candidate, date_in

FILE_TYPES = ('.pdf', '.docx')
MEDIA_PAGE_SIZE = 100
EVENTS_PAGE_SIZE = 50
EVENTS_BACK_DAYS = 60
EVENTS_AHEAD_DAYS = 120
MAX_PAGES = 20
_CANCELED_TITLE = re.compile(r'^\s*CANCELL?ED\b[^–—-]*[–—-]\s*', re.I)


def _json(fetch, url: str):
    """Parse a JSON API answer; name what came back instead when it is not JSON (a bot challenge page, say)."""
    text = fetch(url)
    try:
        return json.loads(text)
    except ValueError:
        title = re.search(r'<title[^>]*>(.*?)</title>', text or '', re.I | re.S)
        shown = re.sub(r'\s+', ' ', html.unescape(title.group(1) if title else (text or '')[:120])).strip()
        raise ValueError(f'{urllib.parse.urlsplit(url).netloc} answered with non-JSON content: '
                         f'{shown or "an empty body"!r}') from None


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

    ``files`` are (url, label) pairs. A file counts when its name (its link label, with `matchLabels`)
    matches `bodyPattern` and states a full date. ``official_url`` is the meeting's official page,
    else its first document.
    """
    body = re.compile(config.get('bodyPattern', '.'), re.I)
    grouped: dict[str, list] = {}
    for url, label in files:
        if not _is_file(url):
            continue
        name = ' '.join(label.split()) if config.get('matchLabels') else file_name(url)
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
    records = _json(fetch, f"{config['indexUrl']}?{query}")
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
        data = _json(fetch, f"{config['indexUrl']}?{query}")
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


# ---------------------------------------------------------------- evogov-meetings

EVOGOV_YEAR_EVENTS = '/meetings/year_events/'
_EVOGOV_HEADING = re.compile(r'<div class="event_details_heading">(.*?)</div>', re.S)
_EVOGOV_CANCELED = re.compile(r'\s*[–—-]\s*Cancell?ed\s*$', re.I)
_FILE_SIZE = re.compile(r'\s*\(\d[\d.,]*\s*[KMG]?B\)\s*$', re.I)


def evogov_calendars(markup: str) -> dict[str, list[int]]:
    """Calendar ID -> the years the dashboard lists for it, read from its year headings."""
    found: dict[str, list[int]] = {}
    for node in document(markup).find():
        calendar, year = node.attrs.get('data-calendar-id', ''), node.attrs.get('data-year', '')
        if calendar.isdigit() and year.isdigit():
            found.setdefault(calendar, []).append(int(year))
    if not found:
        raise ValueError('the EvoGov meeting dashboard lists no calendar years')
    return found


def _evogov_files(agency: str, cell: str, base: str) -> tuple[list[dict], dict]:
    """A row's documents (agendas, then the packet) and minutes link, by the heading each file is listed under."""
    files, minutes = [], {}
    parts = _EVOGOV_HEADING.split(cell)
    for heading, section in zip(parts[1::2], parts[2::2]):
        kind = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', heading)).split()).lower()
        for url, label in anchors(section, base):
            label = _FILE_SIZE.sub('', ' '.join(label.split()))
            if urllib.parse.urlparse(url).scheme != 'https':
                continue
            if kind == 'agendas' or (kind == 'attachments' and re.search(r'\bpacket\b', label, re.I)):
                files.append((url, label))
            elif kind == 'minutes':
                minutes = listing_minutes(url, label)
    return _documents(agency, files), minutes


def evogov_rows(agency: str, config: dict, markup: str, base: str) -> list[dict]:
    """Meetings in one ``year_events`` answer: table rows of the start, the linked event name, and its files."""
    body = re.compile(config.get('bodyPattern', '.'), re.I)
    local = ZoneInfo('America/Los_Angeles')
    rows = []
    for tr in re.findall(r'<tr\b.*?</tr>', markup, re.S):
        cells = re.findall(r'<td\b(.*?)</td>', tr, re.S)
        key = re.search(r'data-evo_sort_key="(\d{14})"', cells[0]) if cells else None
        link = re.search(r'<a[^>]*class="evo_meeting_name_link"[^>]*href="([^"]*/events/(\d+)/?)"[^>]*>(.*?)</a>', tr, re.S)
        if not key or not link:
            continue
        name = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', link[3])).split())
        if not body.search(name):
            continue
        start = datetime.strptime(key[1], '%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo('UTC')).astimezone(local)
        docs, minutes = _evogov_files(agency, cells[2] if len(cells) > 2 else '', base)
        title = config.get('title') or _EVOGOV_CANCELED.sub('', name)
        row = candidate(agency, link[2], title, start.date().isoformat(), urllib.parse.urljoin(base, link[1]), docs, name)
        row['start_time'] = start.strftime('%H:%M')
        row.update(minutes)
        rows.append(row)
    return rows


def _evogov_years(agency: str, config: dict, years: set[int], fetch) -> tuple[list[dict], str | None]:
    """Matching meetings of the given years (those the dashboard lists), newest first, and the earliest date read."""
    base = config['indexUrl']
    rows = []
    for calendar, listed in evogov_calendars(fetch(base)).items():
        for year in sorted(years & set(listed)):
            query = urllib.parse.urlencode({'calendar_id': calendar, 'year': year})
            data = _json(fetch, urllib.parse.urljoin(base, EVOGOV_YEAR_EVENTS) + '?' + query)
            rows += evogov_rows(agency, config, (data.get('html') if isinstance(data, dict) else None) or '', base)
    unique = {}
    for row in rows:
        unique.setdefault(row['source_id'], row)
    rows = sorted(unique.values(), key=lambda r: (r['date'], r['source_id']), reverse=True)
    return rows, min((r['date'] for r in rows), default=None)


def evogov_meetings(agency: str, config: dict, limit: int | None, fetch) -> list[dict]:
    """Matching meetings from 60 days back to 120 ahead, newest first."""
    today = _today()
    since, until = today - timedelta(days=EVENTS_BACK_DAYS), today + timedelta(days=EVENTS_AHEAD_DAYS)
    rows, _ = _evogov_years(agency, config, set(range(since.year, until.year + 1)), fetch)
    return [r for r in rows if since.isoformat() <= r['date'] <= until.isoformat()][:limit]


def evogov_meetings_range(agency: str, config: dict, since: date, until: date, fetch) -> tuple[list[dict], str | None]:
    """Every matching meeting dated since..until, reading each listed year in the range."""
    rows, earliest = _evogov_years(agency, config, set(range(since.year, until.year + 1)), fetch)
    return [r for r in rows if since.isoformat() <= r['date'] <= until.isoformat()], earliest
