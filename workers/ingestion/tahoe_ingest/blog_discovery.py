"""The `squarespace-blog` discovery type: meeting posts in a Squarespace blog collection (config/README.md).

`indexUrl` is the collection's page (``https://www.ntfire.net/board-minutes``). Squarespace
answers the same URL with ``?format=json`` as the collection's newest posts, 20 to a page,
each with its title, page path, publication time, and body markup, so no post page is
read. Older posts come from the ``offset`` the answer names for its next page.

Each post whose title matches `bodyPattern` is a meeting: its source ID is the post's ID
and its official URL the post's page. The body's links labeled `documentPattern` (default
"Agenda") are its documents (agenda first, then packet), and a link labeled "Minutes" its
minutes. A title that says the meeting was canceled ("Regular Meeting 11-2026 - Canceled")
marks it canceled; a title with "special", "workshop", or "joint" puts that word before
`title`. A post title that states a full date ("Regular Meeting 10/27/2020") gives the
meeting's date; one that names only the month ("Regular Meeting 10-2026") leaves it to
capture, which reads it beside the start time on the agenda's first page, as for WordPress
meeting posts. Such posts are bounded by count in scheduled runs (`UNDATED_TYPES`), and a
backfill keeps those published inside its range.

Registered by format name in `source_discovery.TYPES`; no code here names an agency.
"""
from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from .discovery import anchors
from .file_discovery import _json, _title
from .records import listing_minutes
from .source_discovery import _QUALIFIER, agenda_then_packet, candidate, date_in

MAX_PAGES = 20
POST_MARGIN_DAYS = 60  # a backfill pages on until posts are this much older than its range
_CANCELED = re.compile(r'\bcancel(?:l?ed|l?ation)\b', re.I)
_MINUTES = re.compile(r'(?:Draft\s+)?Minutes', re.I)


def _page(config: dict, fetch, offset: int | None) -> tuple[list[dict], int | None]:
    """One page of the collection's posts and the offset of the next, or None after the last."""
    query = {'format': 'json'} | ({'offset': offset} if offset else {})
    data = _json(fetch, f"{config['indexUrl']}?{urllib.parse.urlencode(query)}")
    pagination = data.get('pagination') or {}
    following = pagination.get('nextPageOffset') if pagination.get('nextPage') else None
    return list(data.get('items') or []), following


def _posted(post: dict) -> str | None:
    stamp = post.get('publishOn') or post.get('addedOn')
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).date().isoformat() if isinstance(stamp, (int, float)) else None


def _row(agency: str, config: dict, post: dict) -> dict | None:
    title = ' '.join(html.unescape(post.get('title') or '').split())
    if not post.get('id') or not post.get('fullUrl') or not re.search(config.get('bodyPattern', '.'), title, re.I):
        return None
    page = urllib.parse.urljoin(config['indexUrl'], post['fullUrl'])
    wanted = re.compile(config.get('documentPattern', 'Agenda'), re.I)
    links = [(target, ' '.join(label.split())) for target, label in anchors(post.get('body') or '', page)
             if urllib.parse.urlparse(target).scheme == 'https']
    docs, seen = [], set()
    for target, label in links:
        if wanted.fullmatch(label) and target not in seen:
            seen.add(target)
            role = 'agenda-packet' if re.search(r'packet', urllib.parse.unquote(target) + ' ' + label, re.I) else 'agenda'
            docs.append({'id': f'{agency}-{role}', 'title': label, 'url': target})
    docs = agenda_then_packet(docs)
    roles = [d['id'] for d in docs]
    for doc in docs:  # two files of one role keep distinct IDs
        if roles.count(doc['id']) > 1:
            doc['id'] += '-' + hashlib.sha256(doc['url'].encode()).hexdigest()[:10]
    qualifier = _QUALIFIER.search(title)
    row = candidate(agency, str(post['id']), _title(agency, config, qualifier[1].title() if qualifier else None),
                    date_in(title), page, docs)
    row['status'] = 'canceled' if _CANCELED.search(title) else 'unknown'
    if minutes := next((target for target, label in links if _MINUTES.fullmatch(label)), None):
        row.update(listing_minutes(minutes, 'Minutes'))
    return row


def squarespace_blog(agency: str, config: dict, limit: int | None, fetch) -> list[dict]:
    """The newest matching posts on the collection's first page, newest first."""
    posts, _ = _page(config, fetch, None)
    rows = [row for post in posts if (row := _row(agency, config, post))]
    return rows[:limit]


def squarespace_blog_range(agency: str, config: dict, since: date, until: date, fetch) -> tuple[list[dict], str | None]:
    """Every matching post dated since..until, paging back until posts are `POST_MARGIN_DAYS` older than the range.

    A post whose title gives no full date is kept when it was published inside the range; capture
    dates it from its agenda.
    """
    lo, hi = since.isoformat(), until.isoformat()
    stop = (since - timedelta(days=POST_MARGIN_DAYS)).isoformat()
    rows, earliest, offset = [], None, None
    for _ in range(MAX_PAGES):
        posts, offset = _page(config, fetch, offset)
        for post in posts:
            posted = _posted(post)
            if posted:
                earliest = min(earliest or posted, posted)
            row = _row(agency, config, post)
            if not row:
                continue
            if (row['date'] and lo <= row['date'] <= hi) or (not row['date'] and posted and lo <= posted <= hi):
                rows.append(row)
        if not offset or not posts or min((_posted(p) or '9999') for p in posts) < stop:
            break
    return rows, earliest
