"""Agenda layouts: the formats agencies print their agendas in.

Layouts are data in ``config/agenda-layouts.json``, keyed by format name. An
agency entry in ``packages/domain/agencies.json`` lists the layouts its
agendas use, tried in order::

    "agenda": {"layouts": ["trpa-governing-board"], "agendaBeforePacket": true}

so a body that publishes in a format that is already supported (another
committee of the same district) needs only that registry entry. A layout
describes a document format, not an agency: bodies whose agendas look alike
share one (the TTD board and its committee share ``ttd``). Its cover must
match only documents in that format, because the cover is what tells a body's
agenda from an attachment or a sibling body's agenda.

This module holds the building blocks that layouts name: outline ``LEVELS``,
shared regular expressions (``FRAGMENTS``, written ``"@NAME"``), and the
hand-written parsers in ``agenda_custom.PARSERS``. It loads the file and
validates it when the agency registry loads. config/README.md lists every
layout key.

To add a layout:

1. Capture one of the agency's agendas and describe its format in
   ``config/agenda-layouts.json``: label, cover, outline levels, ending
   marker, and the page furniture to skip. Use the registry names where they
   fit and an inline level (``{"kind": "free", "pattern": "..."}``) for a
   marker only one layout uses. Read the extracted items against the PDF, not
   just their count.
2. Name the layout in the agency's ``agenda.layouts``.
3. Add the agenda's pages and reviewed items to
   ``tests/fixtures/agenda-layouts/AGENCY--MEETING.json`` (``"expected": null``
   for a document that must be rejected). ``tests/test_agenda_layouts.py``
   replays every fixture and requires one for every layout.
4. Run ``tahoe-ingest reparse --agency AGENCY --dry-run`` to re-parse stored
   text with no source fetch.

Add a building block here only when several layouts need it, and a parser to
``agenda_custom`` only for a format the outline engine cannot describe.
"""
from __future__ import annotations

import json
import re
from dataclasses import fields, replace
from functools import lru_cache
from pathlib import Path

from .agenda_custom import PARSERS
from .outline import LOWER, NUM, NUM_LOOSE, ROMAN_LEVEL, UPPER, Appendix, Layout, Level

NAME = "agenda-layouts.json"

# A wrapped title stops at a staff attribution such as "Matt Click, Executive Director".
STAFF_LINE = r"^[A-Z][\w.'’-]+(?: [A-Z][\w.'’-]+){1,3},\s+(?:and\s+)?[A-Z]"
# Unnumbered ALL-CAPS section headings ("CONSENT CALENDAR", "ACTION ITEMS:").
CAPS_HEADING = r"^[A-Z][A-Z0-9 &/,'’()\-–]{3,}:?$"
ACTION_TAGS = (r"(?i)\s*-?\s*\[(?:non-action item|for possible action)\]", "")
PAREN_DESCRIPTION = (r"^([^a-z]+?)\s+\((?=[A-Z][a-z]).*$", r"\1")  # "COMMENTS FROM THE PUBLIC (This is an..." -> heading

SLT_HEADINGS = r"^(?:New Business|Unfinished Business|Old Business|Consent(?: Agenda)?|Presentations?|Public Hearings?)$"
FOR_ACTION = (r"(?i)^For (?:possible action|presentation only|discussion only|information only)[.:]\s*", "")

LEGISTAR_FILE = Level("num", r"(?P<v>\d{1,3})\.\s+\d{2}-\d{3,5}(?=\s|$)")
# Douglas County children: "A." letters, or dotted "11.1" (whose "11." parent may be unprinted).
DOUGLAS_CHILD = Level("free", r"(?:(?P<p>\d{1,2})\.)?(?P<v>(?<=\.)\d{1,2}|[A-Z](?=\.))\.?(?=\s)")
# TRPA tables: "A   Title   Staff   Purpose"; a single space is accepted only on a table row.
UPPER_BARE = Level("upper", r"(?P<v>[A-Z])(?=\s{2,}\S|\s\S.*\S\s{3,}\S)")
UPPER_ANY = Level("upper", r"\(?(?P<v>[A-Z])[.)](?=\s|$)")
NUM_TIGHT = Level("num", r"(?P<v>\d{1,2})\.(?=\s|[A-Z])")
LOWER_ROMAN = Level("lroman", r"(?P<v>[ivx]{1,4})\.(?=\s)", item=False)

LEVELS = {"NUM": NUM, "NUM_LOOSE": NUM_LOOSE, "NUM_TIGHT": NUM_TIGHT, "UPPER": UPPER, "UPPER_ANY": UPPER_ANY,
          "UPPER_BARE": UPPER_BARE, "LOWER": LOWER, "ROMAN_LEVEL": ROMAN_LEVEL, "LOWER_ROMAN": LOWER_ROMAN,
          "LEGISTAR_FILE": LEGISTAR_FILE, "DOUGLAS_CHILD": DOUGLAS_CHILD}
# A regular expression, or a (pattern, replacement) pair for ``titleCleanup``.
FRAGMENTS = {"STAFF_LINE": STAFF_LINE, "CAPS_HEADING": CAPS_HEADING, "SLT_HEADINGS": SLT_HEADINGS,
             "ACTION_TAGS": ACTION_TAGS, "PAREN_DESCRIPTION": PAREN_DESCRIPTION, "FOR_ACTION": FOR_ACTION}

KINDS = ("num", "upper", "lower", "roman", "lroman", "free")
WRAPS = ("indent", "lowercase", "any", "none")
_FIELDS = {f.name for f in fields(Layout)} - {"name"}
_PATTERNS = {"start", "end", "skip", "prefix", "suffix", "column_header", "headings", "heading_items", "stop",
             "title_end", "addendum"}
_COUNTS = {"cover_pages": 1, "first_pages": 1, "max_title_lines": 1, "min_items": 1, "max_gap": 0, "indent_tolerance": 0}
_FLAGS = {"caps_colon", "column_gap", "caps_lead", "end_item", "restart", "section_restart"}
_PARSER_FIELDS = {"label", "cover", "cover_pages", "recognize", "parser"}


@lru_cache(maxsize=1)
def layouts() -> dict[str, Layout]:
    """Every configured layout by name: a wheel's bundled copy, else the checkout's config file."""
    packaged = Path(__file__).with_name(NAME)
    path = packaged if packaged.is_file() else Path(__file__).resolve().parents[3] / "config" / NAME
    config = json.loads(path.read_text())
    if not isinstance(config.get("layouts"), dict) or not config["layouts"]:
        raise ValueError(f"invalid agenda layouts: {path}")
    return {name: _layout(name, spec) for name, spec in config["layouts"].items()}


def validate_agency_agendas(agencies: list[dict]) -> None:
    """Check each agency's ``agenda`` block against the layouts (called when the registry loads)."""
    known = layouts()
    for agency in agencies:
        if "agenda" not in agency:
            continue
        agenda = agency["agenda"]
        names = agenda.get("layouts") if isinstance(agenda, dict) else None
        if not isinstance(names, list) or not names or len(set(names)) != len(names) \
                or set(agenda) - {"layouts", "agendaBeforePacket"} or not isinstance(agenda.get("agendaBeforePacket", False), bool):
            raise ValueError(f"invalid agenda: {agency['id']}")
        for name in names:
            if name not in known:
                raise ValueError(f"unknown agenda layout {name!r}: {agency['id']}")


class _Invalid(ValueError):
    pass


def _layout(name: str, spec: dict) -> Layout:
    try:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or not isinstance(spec, dict):
            raise _Invalid("invalid name or entry")
        values = {}
        for key, value in spec.items():
            field = re.sub(r"[A-Z]", lambda m: "_" + m.group().lower(), key)
            if field not in _FIELDS:
                raise _Invalid(f"unknown key {key}")
            values[field] = _value(field, value)
        if not values.get("label") or not values.get("cover"):
            raise _Invalid("label and cover are required")
        if "parser" in values and set(values) - _PARSER_FIELDS:
            raise _Invalid("a parser layout takes only label, cover, coverPages, recognize, and parser")
        if "parser" not in values and not values.get("levels"):
            raise _Invalid("levels are required")
        return Layout(name=name, **values)
    except (_Invalid, re.error) as exc:
        raise ValueError(f"agenda layout {name}: {exc}") from None


def _value(field: str, value):
    if field == "label":
        return _text(value)
    if field == "parser":
        if value not in PARSERS:
            raise _Invalid(f"unknown parser {value!r}")
        return value
    if field in ("cover", "recognize"):
        return tuple(_pattern(v) for v in _list(value))
    if field in _PATTERNS:
        return _pattern(value)
    if field in _COUNTS:
        if type(value) is not int or value < _COUNTS[field]:
            raise _Invalid(f"{field} must be an integer of at least {_COUNTS[field]}")
        return value
    if field in _FLAGS:
        if not isinstance(value, bool):
            raise _Invalid(f"{field} must be true or false")
        return value
    if field == "wrap":
        if value not in WRAPS:
            raise _Invalid(f"wrap must be one of {', '.join(WRAPS)}")
        return value
    if field == "levels":
        return tuple(_level(v) for v in _list(value))
    if field == "title_cleanup":
        return tuple(_cleanup(v) for v in _list(value))
    if field == "after_end":
        return tuple(_appendix(v) for v in _list(value))
    raise _Invalid(f"unsupported key {field}")


def _list(value) -> list:
    if not isinstance(value, list) or not value:
        raise _Invalid(f"expected a non-empty list, not {value!r}")
    return value


def _text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Invalid(f"expected text, not {value!r}")
    return value


def _fragment(value: str):
    if value[1:] not in FRAGMENTS:
        raise _Invalid(f"unknown fragment {value}")
    return FRAGMENTS[value[1:]]


def _pattern(value) -> str:
    """A regular expression; "@NAME" is a shared fragment, and a list matches any of its patterns."""
    parts = [_fragment(v) if isinstance(v, str) and v.startswith("@") else _text(v) for v in
             (_list(value) if isinstance(value, list) else [value])]
    if not all(isinstance(part, str) for part in parts):
        raise _Invalid(f"{value!r} is not a pattern")
    pattern = "|".join(parts)
    re.compile(pattern)
    return pattern


def _level(value) -> Level:
    if isinstance(value, str):
        if value not in LEVELS:
            raise _Invalid(f"unknown level {value}")
        return LEVELS[value]
    if not isinstance(value, dict):
        raise _Invalid(f"invalid level {value!r}")
    if set(value) - {"level", "kind", "pattern", "item"} or ("level" in value) == ("kind" in value or "pattern" in value):
        raise _Invalid(f"a level names a registry level or gives kind and pattern: {value!r}")
    if not isinstance(value.get("item", True), bool):
        raise _Invalid("level item must be true or false")
    if "level" in value:
        level = replace(_level(value["level"]), **({"item": value["item"]} if "item" in value else {}))
    else:
        if value.get("kind") not in KINDS:
            raise _Invalid(f"level kind must be one of {', '.join(KINDS)}")
        level = Level(value["kind"], _text(value.get("pattern")), value.get("item", True))
    if "v" not in re.compile(r"^(?P<indent>\s*)" + level.pattern + r"\s*(?P<t>.*)$").groupindex:
        raise _Invalid(f"level pattern must capture its label as group v: {level.pattern}")
    return level


def _cleanup(value) -> tuple[str, str]:
    if isinstance(value, str) and value.startswith("@"):
        pair = _fragment(value)
        if not isinstance(pair, tuple):
            raise _Invalid(f"{value} is not a cleanup pair")
        return pair
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, str) for v in value):
        raise _Invalid(f"titleCleanup entries are [pattern, replacement] or @NAME, not {value!r}")
    re.compile(value[0]).sub(value[1], "")  # also checks the replacement's group references
    return value[0], value[1]


def _appendix(value) -> Appendix:
    if not isinstance(value, dict) or set(value) - {"heading", "level", "parent", "end"} \
            or "heading" not in value or "level" not in value:
        raise _Invalid(f"afterEnd entries need heading and level: {value!r}")
    return Appendix(_pattern(value["heading"]), _level(value["level"]),
                    _text(value["parent"]) if "parent" in value else None,
                    _pattern(value["end"]) if "end" in value else None)
