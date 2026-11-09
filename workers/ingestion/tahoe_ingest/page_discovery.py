"""Discovery types for meeting pages that are neither tables nor feeds (config/README.md).

`dated-page-links`: an index page whose list items are meeting dates ("June 18, 2026",
"March 19 and 20, 2026"). A linked date opens the meeting's own page, which is its HTML
agenda and its one document (`<agency>-agenda-html`); an unlinked date from today on is a
scheduled meeting whose agenda is not posted yet (California Tahoe Conservancy).

`dated-sections`: a page with one section per meeting under its own heading (an
accordion panel such as "Commission Meeting: August 6 and 7"). Inside a section, the
line that names the session (`bodyPattern`, matched against the whole line, for example
"Commission Meeting" and not "Commission Tour") is followed by "Date:", "Time:",
"Location:", and "Address:" lines, and the section's link whose label matches
`documentPattern` (default "Meeting Agenda") is the agenda (California State Parks
commissions). The page itself stays the official URL, because staff reports are listed
there beside the agenda.

Both are registered by format name in `source_discovery.TYPES`; no code here names an agency.
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from .html_dom import document
from .source_discovery import _MONTH, candidate, date_in, multi_day_note, time_in

_WEEKDAY = r'(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s+)?'
# "June 18, 2026", "Thursday, June 18, 2026", "March 19 and 20, 2026", "March 19-20, 2026".
_DATE_ONLY = re.compile(rf'^{_WEEKDAY}({_MONTH})\s+(\d{{1,2}})(?:\s*(?:and|&|-|–|through)\s*(\d{{1,2}}))?,?\s+(20\d{{2}})$', re.I)
_HEADINGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}


def _today():
    return datetime.now(ZoneInfo('America/Los_Angeles')).date().isoformat()


def dated_page_links(agency, config, limit, fetch):
    """Meetings from the list items of an index page that are only a date, newest first."""
    url = config['indexUrl']
    heading_pattern = re.compile(config.get('headingPattern', '.'), re.I)
    title = config.get('title') or agency.replace('-', ' ').title() + ' Meeting'
    today = _today()
    rows, heading = [], ''
    for node in document(fetch(url)).find():  # document order: a list item's heading comes before it
        if node.tag in _HEADINGS:
            heading = node.text()
            continue
        if node.tag != 'li' or not heading_pattern.search(heading):
            continue
        found = _DATE_ONLY.match(node.text())
        if not found:
            continue
        day = date_in(f'{found[1]} {found[2]} {found[4]}')
        if not day:
            continue
        target = next((urllib.parse.urljoin(url, a.attrs['href']) for a in node.find('a') if a.attrs.get('href')), None)
        if target and urllib.parse.urlparse(target).scheme == 'https':
            row = candidate(agency, day, title, day, target,
                            [{'id': agency + '-agenda-html', 'title': 'Official agenda (HTML)', 'url': target}])
        elif not target and day >= today:
            row = candidate(agency, day, title, day, url, [])  # scheduled; the empty document list is the state
        else:
            continue
        if found[3]:
            multi_day_note(row, found[3])  # "Meets March 19–20, 2026."
        rows.append(row)
    unique = {}
    for row in rows:
        unique.setdefault(row['source_id'], row)
    return sorted(unique.values(), key=lambda r: r['date'], reverse=True)[:limit]


class _Flow(HTMLParser):
    """A page as ordered events: ('heading', text), ('line', text), and ('link', url, label)."""
    BREAKS = {'br', 'p', 'div', 'li', 'ul', 'ol', 'tr', 'td', 'th', 'table', 'section', 'article', 'dd', 'dt'} | _HEADINGS

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.events, self.buffer, self.link, self.hidden, self.heading = [], [], None, 0, 0

    def flush(self):
        text = ' '.join(''.join(self.buffer).split())
        if text:
            self.events.append(('heading' if self.heading else 'line', text))
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript'}:
            self.hidden += 1
        if tag in self.BREAKS:
            self.flush()
        if tag in _HEADINGS:
            self.heading += 1
        if tag == 'a':
            self.link = (dict(attrs).get('href') or '', [])

    def handle_startendtag(self, tag, attrs):
        if tag in self.BREAKS:
            self.flush()

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript'}:
            self.hidden = max(0, self.hidden - 1)
        if tag == 'a' and self.link:
            self.events.append(('link', self.link[0], ' '.join(''.join(self.link[1]).split())))
            self.link = None
        if tag in self.BREAKS:
            self.flush()
        if tag in _HEADINGS:
            self.heading = max(0, self.heading - 1)

    def handle_data(self, data):
        if self.hidden:
            return
        self.buffer.append(data)
        if self.link:
            self.link[1].append(data)


def _sections(markup):
    flow = _Flow()
    flow.feed(markup)
    flow.close()
    flow.flush()
    sections = [('', [])]
    for event in flow.events:
        if event[0] == 'heading':
            sections.append((event[1], []))
        else:
            sections[-1][1].append(event)
    return sections


_FIELD = re.compile(r'^(Date|Time|Location|Address)\s*:\s*(.*)$', re.I)
_MAP_NOTE = re.compile(r'\s*\([^)]*\b(?:pin|map)\b[^)]*\)', re.I)


SECTION_PAGE_KEYS = frozenset({'indexUrl', 'bodyPattern', 'title', 'idPrefix', 'documentPattern'})


def validate_section_pages(aid, pages):
    """`pages` (dated-sections): further pages in the same layout, each with its own https `indexUrl`."""
    if not isinstance(pages, list) or not pages or any(
            not isinstance(p, dict) or not set(p) <= SECTION_PAGE_KEYS
            or not str(p.get('indexUrl', '')).startswith('https://') for p in pages):
        raise ValueError(f'invalid source configuration: {aid}: pages are objects with an https indexUrl and '
                         f'only {", ".join(sorted(SECTION_PAGE_KEYS - {"indexUrl"}))}')
    for page in pages:
        for key in ('bodyPattern', 'documentPattern'):
            re.compile(page.get(key, '.'), re.I)


def dated_sections(agency, config, limit, fetch):
    """One meeting per page section that names the session and gives its date, newest first.

    `pages` lists further pages read the same way (another commission of the same
    agency on a sibling site); each page's keys replace the block's for that page.
    """
    rows = []
    for page in [config, *config.get('pages', [])]:
        rows += _section_meetings(agency, {**config, **page}, fetch)
    unique = {}
    for row in rows:
        unique.setdefault(row['source_id'], row)
    return sorted(unique.values(), key=lambda r: r['date'], reverse=True)[:limit]


def _section_meetings(agency, config, fetch):
    url = config['indexUrl']
    session = re.compile(config.get('bodyPattern', 'Meeting'), re.I)
    agenda = re.compile(config.get('documentPattern', 'Meeting Agenda'), re.I)
    title = config.get('title') or agency.replace('-', ' ').title() + ' Meeting'
    prefix = config.get('idPrefix', '')
    rows = []
    for heading, events in _sections(fetch(url)):
        fields, started = {}, False
        for event in events:
            if event[0] != 'line':
                continue
            if session.fullmatch(event[1]):
                if 'date' in fields:
                    break  # a second session of the same kind in one section is left to its own section
                started, fields = True, {}
                continue
            field = _FIELD.match(event[1]) if started else None
            if not field:
                continue
            name = field[1].lower()
            if name == 'date' and 'date' in fields:
                break  # the next session's date (a tour after the meeting)
            fields.setdefault(name, field[2].strip())
        day = date_in(fields.get('date', ''))
        if not day:
            continue
        docs = []
        for event in events:
            if event[0] == 'link' and agenda.fullmatch(event[2]) and event[1]:
                target = urllib.parse.quote(urllib.parse.urljoin(url, event[1]), safe=':/?&=#~+%')
                if urllib.parse.urlparse(target).scheme == 'https' and all(d['url'] != target for d in docs):
                    docs.append({'id': f'{prefix or agency + "-"}agenda-{day}', 'title': event[2], 'url': target})
        if len(docs) > 1:  # Two documents match: keep both, with distinct IDs.
            for n, doc in enumerate(docs[1:], 2):
                doc['id'] += f'-{n}'
        text = ' '.join([heading, *(f'{k}: {v}' for k, v in fields.items())])
        row = candidate(agency, prefix + day, title, day, url, docs, text)
        row['start_time'] = time_in(fields.get('time', ''))
        location = ', '.join(_MAP_NOTE.sub('', fields[k]).strip() for k in ('location', 'address') if fields.get(k))
        row['location'] = location or None
        rows.append(row)
    return rows
