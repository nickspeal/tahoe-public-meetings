"""Helpers shared by the Granicus rules (``douglas-packet``, ``slt-commission-report``, ``tcpud-memo``); not a rule module.

Granicus agenda pages (``GeneratedAgendaViewer.php``) carry each item's number in a table
cell beside its title, which ``Context.html_links`` drops, so these rules read the page's
markup itself (``Context.html``).
"""
from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse


def text(fragment: str) -> str:
    """Visible text of a markup fragment, whitespace collapsed."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def query(url: str, key: str) -> str | None:
    """One query parameter of a URL (``clip_id``, ``view_id``, ``meta_id``), or None."""
    values = parse_qs(urlparse(html.unescape(url)).query).get(key)
    return values[0] if values else None


# A MetaViewer document anchor: <a href="…MetaViewer.php?view_id=6&clip_id=2320&meta_id=218982" … class="Document …">name</a>
DOCUMENT = re.compile(r'<a\s+href="(?P<url>[^"]*MetaViewer\.php\?[^"]*)"[^>]*class="Document[^"]*"[^>]*>(?P<name>.*?)</a>', re.S | re.I)
