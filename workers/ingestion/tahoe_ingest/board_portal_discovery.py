"""Discovery types for board-management portals that load their meetings through the portal's own API (config/README.md).

`simbli`: a Simbli (eBOARDsolutions) meeting listing, `SB_MeetingListing.aspx?S=<site>`.
The listing page is an empty grid that its script fills by posting the page's own
listing request (a public site token printed in the page) to `/Services/api/GetMeetingListing`;
discovery reads the page once and makes the same request, newest meeting first, 50 rows
a page. Each row gives the meeting's ID, title, local start, and address. A row's meeting
page, `ViewMeeting.aspx?S=<site>&MID=<meeting>`, is the official URL and its one document
(`<agency>-agenda-html`); published minutes open on the same page with `&T=1`, which the
listing's Minutes button links. `bodyPattern` is matched against the meeting title
("Board of Education Regular Meeting", "Citizen's Bond Oversight Committee Meeting").

The meeting page is a script shell: its agenda is drawn in the browser from the portal's
agenda tree, `/Services/api/GetAgendaTree/`, keyed by the encrypted site and meeting IDs
the page prints. `portal_agenda` reads that tree when capture downloads a Simbli meeting
page (`source.download_extract`), so the stored text is the agenda itself, one line per
item as the portal numbers it ("1. CALL TO ORDER - 5:00 PM", "a. Pledge of Allegiance"),
and the items come from the tree's structure, like the structured HTML agendas in
`html_agenda.py`, with no `agenda.layouts` entry.

Registered by format name in `source_discovery.TYPES`; no code here names an agency.
Only the portal's public, anonymous endpoints are read, with the project's user agent.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse

from .http import fetch_bytes
from .records import listing_minutes
from .source_discovery import candidate

PAGE_ROWS = 50  # the listing page's own page size
MAX_PAGES = 20  # a whole listing (backfill) of up to 1,000 meetings
MAX_JSON = 4 * 1024 * 1024
MAX_TITLE = 280
_LISTING_API = '/Services/api/GetMeetingListing'
_TREE_API = '/Services/api/GetAgendaTree/'
_MEETING_PAGE = '/sb_meetings/viewmeeting.aspx'


def _js_string(markup, name):
    found = re.search(rf"\bvar\s+{re.escape(name)}\s*=\s*'([^'\\]*)'", markup)
    return found[1] if found else None


def listing_request(markup):
    """The listing page's own request body (its public site token and settings), or ValueError."""
    found = re.search(r"\bvar\s+meetingCustGrd\s*=\s*JSON\.parse\('((?:[^'\\]|\\.)*)'\)", markup)
    if not found:
        raise ValueError('Simbli listing page has no meeting listing request')
    body = json.loads(found[1])
    site = _js_string(markup, 'schoolID')
    if not body.get('SecurityToken') or not site:
        raise ValueError('Simbli listing page has no site token')
    body.update(ListingType='0', SortColName=None, IsSortDesc=False, FilterExp='', IsUserLoggedIn=False,
                SchoolID=site, UserID='', TimeZone=_js_string(markup, 'timezoneDiff'), RecordCount=PAGE_ROWS)
    if connection := _js_string(markup, 'constr'):
        body['ConnectionString'] = connection
    return body


def post_json(url, body):
    """POST a JSON body and decode the JSON answer (the listing grid's own request)."""
    data, _ = fetch_bytes(url, max_bytes=MAX_JSON, accept='application/json', payload=json.dumps(body).encode())
    return json.loads(data.decode('utf-8-sig'))


def _location(row):
    parts = [' '.join(str(row.get(k) or '').split()) for k in ('MM_Address1', 'MM_Address2', 'MM_Address3')]
    return ', '.join(p for p in parts if p) or None


def simbli(agency, config, limit, fetch, *, post=None):
    """Meetings from a Simbli listing, newest first (``limit=None``: every listed meeting)."""
    post = post or post_json
    url = config['indexUrl']
    site = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('S', [''])[0]
    body = listing_request(fetch(url))
    if site and body['SchoolID'] != site:
        raise ValueError(f'Simbli listing page is for site {body["SchoolID"]}, not {site}')
    body_pattern = re.compile(config.get('bodyPattern', '.'), re.I)
    api = urllib.parse.urljoin(url, _LISTING_API)
    rows, seen = [], set()
    for page in range(MAX_PAGES):
        listed = post(api, {**body, 'RecordStart': page * PAGE_ROWS + 1})
        if not isinstance(listed, list):
            raise ValueError('Simbli listing answer is not a list of meetings')
        for row in listed:
            mid = str(row.get('Master_MeetingID') or '')
            title = ' '.join(str(row.get('MM_MeetingTitle') or '').split())
            when = str(row.get('MM_DateTime') or '')
            if not mid.isdigit() or mid in seen or row.get('ML_IsPublic') is False:
                continue
            seen.add(mid)
            found = re.fullmatch(r'(20\d{2}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::\d{2})?', when)
            if not found or not body_pattern.search(title):
                continue
            page_url = urllib.parse.urljoin(url, f'/SB_Meetings/ViewMeeting.aspx?S={body["SchoolID"]}&MID={mid}')
            meeting = candidate(agency, mid, config.get('title') or title, found[1], page_url,
                                [{'id': f'{agency}-agenda-html', 'title': 'Official agenda (Simbli)', 'url': page_url}], title)
            meeting['start_time'] = f'{found[2]}:{found[3]}'
            meeting['location'] = _location(row)
            if str(row.get('MinutesStatus') or '').upper() == 'PUBLISHED':
                meeting.update(listing_minutes(page_url + '&T=1', 'Minutes'))
            rows.append(meeting)
        total = max((int(r.get('MeetingsCount') or 0) for r in listed), default=0)
        if len(listed) < PAGE_ROWS or (page + 1) * PAGE_ROWS >= total or (limit is not None and len(rows) >= limit):
            break
    rows.sort(key=lambda r: (r['date'], r['start_time'] or ''), reverse=True)
    return rows if limit is None else rows[:limit]


def is_meeting_page(url):
    parsed = urllib.parse.urlparse(url)
    return parsed.hostname == 'simbli.eboardsolutions.com' and parsed.path.lower() == _MEETING_PAGE


def agenda_tree_url(page_url, markup):
    """The portal's agenda tree for a downloaded meeting page, from the IDs the page prints for its own script."""
    site, meeting = _js_string(markup, 'enDID'), _js_string(markup, 'enMeetingID')
    if not site or not meeting:
        raise ValueError('Simbli meeting page does not name its agenda (no encrypted meeting ID)')
    token = _js_string(markup, 'sToken') or ''
    return urllib.parse.urljoin(page_url, f'{_TREE_API}?sct={token}&endid={site}&enmid={meeting}&enuid=&v=')


_LABEL = re.compile(r'^\s*([0-9]+|[A-Za-z]+)\.\s+(\S.*)$', re.S)


def agenda_from_tree(tree):
    """(text, items, complete) from a Simbli agenda tree: a flat list of items in order, each with its parent's ID.

    The text is one line per item as the portal prints it. Items take the printed labels joined
    by level ("7", "7.a", "7.a.i"); a top-level item is its own section, a child is under its
    top-level item. The items are complete only when every item carries a label and the top-level
    numbers run 1, 2, 3 ...; otherwise the text is kept and no items are published.
    """
    if not isinstance(tree, list):
        raise ValueError('Simbli agenda tree is not a list of items')
    entries = [e for e in tree if isinstance(e, dict) and not e.get('Deleted') and ' '.join(str(e.get('Title') or '').split())]
    lines = [' '.join(str(e['Title']).split()) for e in entries]
    text = '\n'.join(lines)
    by_id, items, tops, complete = {}, [], [], bool(entries)
    for entry, line in zip(entries, lines):
        found = _LABEL.match(line)
        parent = by_id.get(entry.get('ParentID'))
        if not found or (parent is None and not found[1].isdigit()):
            complete = False
            break
        label, title = found[1], found[2]
        number = f'{parent["number"]}.{label}' if parent else label
        if parent is None:
            tops.append(int(label))
        section = parent['section'] if parent else title
        by_id[entry.get('ID')] = {'number': number, 'section': section[:MAX_TITLE]}
        item = {'source_number': number, 'order': len(items) + 1, 'section': section[:MAX_TITLE],
                'title': title[:MAX_TITLE].strip(),
                'source_pages': [{'document_id': '__html__', 'page_number': 1, 'quote': line}]}
        if len(title) > MAX_TITLE:
            item['full_title'] = title
        items.append(item)
    if not complete or tops != list(range(1, len(tops) + 1)) or len(items) < 3:
        return text, [], False
    return text, items, True


def portal_agenda(url, markup, *, fetch_json=None):
    """The agenda of a downloaded Simbli meeting page, read from the portal's agenda tree; None for any other page.

    Returns the same shape as `source.download_extract`. The hash is the tree's, so a changed
    agenda is a new version while the page's per-request tokens are not. A meeting page whose
    tree is empty or unreadable raises ValueError, so capture keeps the notice and retries.
    """
    if not is_meeting_page(url):
        return None
    tree_url = agenda_tree_url(url, markup)
    if fetch_json is None:
        def fetch_json(target):
            data, _ = fetch_bytes(target, max_bytes=MAX_JSON, accept='application/json')
            return data
    raw = fetch_json(tree_url)
    text, items, complete = agenda_from_tree(json.loads(raw.decode('utf-8-sig')))
    if not text:
        raise ValueError('Simbli meeting has no published agenda items yet')
    return {'agenda_items': items, 'items_complete': complete, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'page_count': 1, 'media_type': 'text/html', 'pages': [{'page_number': 1, 'text': text}],
            'warnings': ['HTML/text source retained as one source section; it has no PDF page numbers.']}
