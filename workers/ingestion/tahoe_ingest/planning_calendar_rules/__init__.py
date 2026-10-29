"""Agenda planning calendar formats, one module per publishing format (see README.md; IDEA-018 R4).

Every module in this package exposes ``RULES = {"format-name": CalendarFormat(locate, parse)}``; the name describes
the format, not an agency. The modules are discovered and merged into ``REGISTRY`` here; two modules registering the
same name is an error. An agency picks a format in ``packages/domain/agencies.json``::

    "planningCalendar": {"format": "ttd-packet-pages", "heading": "Tentative Agenda Calendar",
                         "title": "TTD/C Board/Committees Tentative Agenda Calendar",
                         "bodies": {"TTD": {"id": "ttd", "meetingTitle": "TTD Board of Directors Meeting"}}}

A format is two deterministic callables (no model, no network):

- ``locate(source, *, params...) -> list[dict]``: where this meeting's calendar is. What ``source`` is depends on the
  format (an agenda's items with their attachments, or the pages of the agenda packet); the result says what to read
  (attachments ``[{"url", "name"}]``, or the calendar's pages ``[{"page_number", "text"}]``).
- ``parse(pages, *, config, carried_by, params...) -> dict``: the whole calendar, ``{"title", "published_on", "year",
  "blocks", "entries"}``. Each entry is a dict with exactly the ``previews.Preview`` field names (``FIELDS``); each
  block summarises one heading for one body label. ``carried_by`` is the date of the meeting whose agenda carries
  the calendar, used when the title prints no "(Updated MM DD YYYY)". It raises ``CalendarInvalid`` when the
  calendar fails validation, and then nothing of it is published.

The format's parameters are the keyword-only arguments of both callables other than ``config`` and ``carried_by``,
spelled in camelCase in the entry (``item_title`` -> ``itemTitle``); those without a default are required. The
annotation says what a value may be: ``str`` (non-empty text), ``staff_reports.Regex`` (a regular expression, compiled
when the registry loads), or ``Literal[...]`` (one of those values). Every entry also gives ``title`` (text the
calendar's title must contain) and ``bodies`` (printed body label -> ``{"id": followed registry ID, "meetingTitle"}``);
labels missing from ``bodies`` are skipped. A ``note`` is allowed.
"""
from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
import re
from collections.abc import Callable
from datetime import date, timedelta
from functools import lru_cache, partial
from typing import Literal, NamedTuple, get_args, get_origin, get_type_hints

from ..staff_reports import Regex

# The fields of previews.Preview, in its order; every parsed entry has exactly these keys.
FIELDS = ("agency_id", "local_date", "month", "pending", "subject", "section", "start_time", "location", "remote_quote",
          "quote", "page", "meeting_title", "special", "rejection", "topics")
RESERVED = frozenset({"config", "carried_by"})  # parse() arguments every format takes; never registry parameters
COMMON_KEYS = frozenset({"format", "title", "bodies", "note"})


class CalendarInvalid(Exception):
    """The whole calendar failed validation: store it with ``reason`` and keep the previous calendar's previews."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class CalendarFormat(NamedTuple):
    locate: Callable
    parse: Callable


class BoundCalendar(NamedTuple):
    """A format bound to one agency's ``planningCalendar`` entry: ``locate(source)``, ``parse(pages, carried_by=)``."""
    agency_id: str
    format: str
    config: dict
    locate: Callable
    parse: Callable


# ---- helpers shared by the format modules ----

MONTHS = {name: number for number, names in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
     ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
     ("december", "dec")], start=1) for name in names}
FULL_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"


def collapse(text: str) -> str:
    return " ".join(text.split())


def runs(line: str) -> list[tuple[int, str]]:
    """``[(column, text)]``: the line's pieces separated by two or more spaces (a single space joins words)."""
    return [(m.start(), m.group()) for m in re.finditer(r"\S+(?: \S+)*", line)]


def first_line(page: dict) -> str:
    return next((line.strip() for line in page["text"].splitlines() if line.strip()), "")


def title_facts(title: str, expected: str, carried_by: date) -> tuple[int, date]:
    """``(year, published_on)`` from a calendar title such as "2026 Agenda Planning Calendar (Updated 09 30 2026)"."""
    if expected.casefold() not in title.casefold():
        raise CalendarInvalid(f"title {title!r} does not contain {expected!r}")
    updated = re.search(r"\(Updated\s+(\d{1,2})[ ./-](\d{1,2})[ ./-](\d{4})\)", title, re.I)
    published = carried_by
    if updated:
        published = make_date(int(updated[3]), int(updated[1]), int(updated[2]), updated[0])
    year = re.search(r"\b(?:19|20)\d{2}\b", title[:updated.start()] if updated else title)
    if not year:
        raise CalendarInvalid(f"title {title!r} states no year")
    return int(year[0]), published


def make_date(year: int, month: int, day: int, printed: str) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        raise CalendarInvalid(f"date heading {printed!r} does not parse") from None


def month_number(word: str, printed: str) -> int:
    if (number := MONTHS.get(word.casefold().rstrip("."))) is None:
        raise CalendarInvalid(f"date heading {printed!r} does not parse")
    return number


def clock(hour: str, minute: str | None, meridiem: str) -> str:
    """'9', '30', 'p' -> '21:30'."""
    h, m = int(hour), int(minute or 0)
    if not (1 <= h <= 12 and 0 <= m < 60):
        raise ValueError
    return f"{h % 12 + (12 if meridiem.lower() == 'p' else 0):02d}:{m:02d}"


class YearCursor:
    """Dates for headings that print no year. Blocks run in date order, so a heading more than a month before the
    latest one so far belongs to the next year (a committee can meet a few days before the board block it follows)."""

    def __init__(self, year: int):
        self.latest = date(year, 1, 1)

    def place(self, month: int, day: int, printed: str, year: int | None = None) -> date:
        found = make_date(year or self.latest.year, month, day, printed)
        if year is None and found < self.latest - timedelta(days=31):
            found = make_date(self.latest.year + 1, month, day, printed)
        self.latest = max(self.latest, found)
        return found


# A trailing parenthetical of one capitalised word names a person ("Draft service plan (Kira)") unless the word is
# an agenda term or a month that calendars print in parentheses.
_NOT_NAMES = frozenset({"consent", "resolution", "action", "informational", "information", "discussion", "presentation",
                        "report", "update", "ordinance", "proclamation", "workshop", "draft", "final", "revised",
                        "continued", "tentative", "pending", "deleted", "moved", "postponed", "cancelled", "canceled",
                        "tbd", "virtual", "hybrid"}) | frozenset(MONTHS)
_TRAILING_NAME = re.compile(r"\s*\(([A-Z][a-z]+)\)\s*$")


def without_person(text: str) -> str:
    match = _TRAILING_NAME.search(text)
    return text[:match.start()] if match and match[1].casefold() not in _NOT_NAMES else text


def entry(*, agency_id: str, fragments: list[str], page: int, meeting_title: str, local_date: date | None = None,
          month: date | None = None, pending: bool = False, section: str | None = None, start_time: str | None = None,
          special: bool = False) -> dict:
    """One preview from the lines (``fragments``) a subject was printed on, with its trailing person removed."""
    lines = [collapse(f) for f in fragments if collapse(f)]
    if lines:
        lines[-1] = collapse(without_person(lines[-1]))
        lines = [line for line in lines if line]
    return dict(agency_id=agency_id, local_date=local_date, month=month, pending=pending, subject=" ".join(lines),
                section=section, start_time=start_time, location=None, remote_quote=None, quote="\n".join(lines),
                page=page, meeting_title=meeting_title, special=special, rejection=None, topics=[])


def check_quotes(entries: list[dict], pages: list[dict]) -> None:
    """Every subject is its quote with line wraps joined, and every quoted line appears word for word (whitespace
    collapsed) on the entry's page, or on the next one when a subject wraps across a page break."""
    text = {p["page_number"]: collapse(p["text"]) for p in pages}
    for e in entries:
        if not e["subject"] or collapse(e["quote"]) != e["subject"]:
            raise CalendarInvalid(f"subject {e['subject']!r} on page {e['page']} is not its quoted text")
        for line in e["quote"].split("\n"):
            if line not in text.get(e["page"], "") and line not in text.get(e["page"] + 1, ""):
                raise CalendarInvalid(f"subject {e['subject']!r} is not a quote of page {e['page']}")


def calendar(title: str, published_on: date, year: int, blocks: list[dict], entries: list[dict], pages: list[dict]) -> dict:
    """The parse result, after the checks every format shares."""
    check_quotes(entries, pages)
    if any(set(e) != set(FIELDS) for e in entries):
        raise TypeError("a planning calendar entry has fields other than previews.Preview's")
    return {"title": title, "published_on": published_on, "year": year, "blocks": blocks,
            "entries": [{k: e[k] for k in FIELDS} for e in entries]}


# ---- registry ----

@lru_cache(maxsize=None)
def _params(name: str, rule: CalendarFormat) -> dict[str, tuple[str, object, bool]]:
    """``{configKey: (keyword, annotation, required)}``: the keyword-only parameters of ``locate`` and ``parse``."""
    found: dict = {}
    for fn in rule:
        hints = get_type_hints(fn)
        for keyword, parameter in list(inspect.signature(fn).parameters.items())[1:]:
            if keyword in RESERVED:
                continue
            kind = hints.get(keyword)
            if parameter.kind is not parameter.KEYWORD_ONLY:
                raise TypeError(f"planning calendar format {name}: parameters after the first must be keyword-only")
            if kind not in (str, Regex) and get_origin(kind) is not Literal:
                raise TypeError(f"planning calendar format {name}: parameter {keyword} needs a str, Regex, or Literal annotation")
            key = re.sub(r"_([a-z])", lambda m: m[1].upper(), keyword)
            if key in COMMON_KEYS or (key in found and found[key][1] != kind):
                raise TypeError(f"planning calendar format {name}: parameter {keyword} clashes")
            required = parameter.default is parameter.empty or (key in found and found[key][2])
            found[key] = (keyword, kind, required)
    return found


def _discover() -> dict:
    merged: dict = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        for name, rule in getattr(module, "RULES", {}).items():
            if name in merged:
                raise RuntimeError(f"planning calendar format {name} is defined in more than one module ({info.name})")
            if not isinstance(rule, CalendarFormat) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
                raise TypeError(f"{info.name}.RULES[{name!r}] is not a CalendarFormat with a lower-case hyphenated name")
            _params(name, rule)
            merged[name] = rule
    return merged


REGISTRY = _discover()


def _followed_ids() -> set[str]:
    """Followed registry IDs, from the raw file: ``follow`` is never inherited, and this runs while the catalog loads."""
    from .. import catalog
    return {e.get("id") for e in json.loads(catalog.agency_catalog_path().read_text()) if e.get("follow") is True}


def _check_text(where: str, key: str, value) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: {key} must be non-empty text")


def check_config(agency: dict) -> None:
    """Raise ValueError when an agency's ``planningCalendar`` names no known format, maps a body wrongly, or gives
    the format's parameters wrongly."""
    config = agency.get("planningCalendar")
    if config is None:
        return
    where = f"planningCalendar of {agency.get('id')}"
    if not isinstance(config, dict):
        raise ValueError(f"invalid {where}")
    if config.get("format") not in REGISTRY:
        raise ValueError(f"unknown planning calendar format {config.get('format')!r} in {where} (known: {', '.join(sorted(REGISTRY))})")
    _check_text(where, "title", config.get("title"))
    if "note" in config:
        _check_text(where, "note", config["note"])
    bodies = config.get("bodies")
    if not isinstance(bodies, dict) or not bodies:
        raise ValueError(f"{where}: bodies must map printed body labels to followed registry entries")
    followed = _followed_ids()
    for label, body in bodies.items():
        _check_text(where, "a body label", label)
        if not isinstance(body, dict) or set(body) != {"id", "meetingTitle"}:
            raise ValueError(f"{where}: bodies[{label!r}] must be {{\"id\", \"meetingTitle\"}}")
        _check_text(where, f"bodies[{label!r}].meetingTitle", body["meetingTitle"])
        if body["id"] not in followed:
            raise ValueError(f"{where}: bodies[{label!r}] names {body['id']!r}, which is not a followed registry entry")
    spec = _params(config["format"], REGISTRY[config["format"]])
    given = {key: value for key, value in config.items() if key not in COMMON_KEYS}
    if unknown := set(given) - set(spec):
        raise ValueError(f"{where}: format {config['format']} takes no {', '.join(sorted(unknown))}")
    if missing := {key for key, (_, _, required) in spec.items() if required} - set(given):
        raise ValueError(f"{where}: format {config['format']} needs {', '.join(sorted(missing))}")
    for key, value in given.items():
        kind = spec[key][1]
        if get_origin(kind) is Literal:
            if value not in get_args(kind):
                raise ValueError(f"{where}: {key} must be one of {', '.join(map(str, get_args(kind)))}")
            continue
        _check_text(where, key, value)
        if kind is Regex:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"{where}: {key} is not a regular expression ({exc})") from exc


def bind(agency_id: str, config: dict) -> BoundCalendar:
    """A format with one agency's parameters (and, for ``parse``, its whole entry as ``config``) bound."""
    rule = REGISTRY[config["format"]]
    spec = _params(config["format"], rule)
    params = {spec[k][0]: v for k, v in config.items() if k in spec}

    def taking(fn):
        accepted = set(inspect.signature(fn).parameters)
        return {k: v for k, v in params.items() if k in accepted}
    return BoundCalendar(agency_id, config["format"], config, partial(rule.locate, **taking(rule.locate)),
                         partial(rule.parse, config=config, **taking(rule.parse)))


@lru_cache(maxsize=1)
def agency_rules() -> dict[str, BoundCalendar]:
    """``{agency ID: BoundCalendar}`` for every registry entry with its own ``planningCalendar`` (never inherited)."""
    from ..catalog import agency_catalog  # the catalog validates entries against REGISTRY, so it loads after it
    return {a["id"]: bind(a["id"], a["planningCalendar"]) for a in agency_catalog() if "planningCalendar" in a}
