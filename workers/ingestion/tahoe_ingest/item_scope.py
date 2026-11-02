"""Which agenda items get staff-report mapping and Claude summaries, from the registry's ``itemScope`` block.

Most agencies are Tahoe bodies, and every item of their agendas is processed. A few are
statewide bodies whose agendas are mostly about other places (the State Lands Commission:
55 to 110 items a meeting, a handful at Lake Tahoe). Their entry says so::

    "itemScope": {"mode": "tahoe-only", "terms": ["Tahoe", "Truckee", ...], "note": "..."}

An item is in scope when one of the terms appears, as whole words and ignoring case and
line breaks, in its title or in the agenda text it cites (the captured row, which for
State Lands names the water body, the nearest town, and the county). Items out of scope
keep their deterministic agenda excerpt; ``map-staff-reports`` and ``summarize-items``
leave them out silently. Topic classification still reads every item, because its
``tahoeRelevant`` answer is what keeps far-away items out of the topic filters.
Without the block, or with ``"mode": "whole-agenda"``, every item is in scope.
"""
from __future__ import annotations

import re
from functools import lru_cache

MODES = ("whole-agenda", "tahoe-only")


def check_config(agency: dict) -> None:
    """Raise ValueError when an agency's ``itemScope`` block is malformed."""
    config = agency.get("itemScope")
    if config is None:
        return
    where = f"itemScope of {agency.get('id')}"
    if not isinstance(config, dict) or set(config) - {"mode", "terms", "note"}:
        raise ValueError(f"invalid {where}: give mode, terms, and an optional note")
    if config.get("mode") not in MODES:
        raise ValueError(f"{where}: mode must be one of {', '.join(MODES)}")
    if "note" in config and not (isinstance(config["note"], str) and config["note"].strip()):
        raise ValueError(f"{where}: note must be non-empty text")
    terms = config.get("terms")
    if config["mode"] == "whole-agenda":
        if terms is not None:
            raise ValueError(f"{where}: whole-agenda takes no terms")
    elif not isinstance(terms, list) or not terms or not all(isinstance(t, str) and t.strip() for t in terms):
        raise ValueError(f"{where}: {config['mode']} needs a non-empty list of terms")


def pattern(terms) -> re.Pattern:
    """Any of ``terms`` as whole words, case-insensitive, with any run of whitespace between their words."""
    alternatives = sorted({r"\s+".join(map(re.escape, t.split())) for t in terms}, key=len, reverse=True)
    return re.compile(rf"(?<![^\W_])(?:{'|'.join(alternatives)})(?![^\W_])", re.I)


@lru_cache(maxsize=None)
def _matcher(agency_id: str):
    from .catalog import agency_catalog
    config = next((a.get("itemScope") for a in agency_catalog() if a["id"] == agency_id), None)
    if not config or config["mode"] == "whole-agenda":
        return None
    return pattern(config["terms"])


def item_text(title: str, citations) -> str:
    """The item's title and the agenda text it cites, which is what the terms are matched against."""
    quotes = [c.get("quote", "") for c in citations or () if isinstance(c, dict)]
    return "\n".join([title or "", *quotes])


def in_scope(agency_id: str, title: str, citations=()) -> bool:
    """True when the agency processes every item, or when the item's text names one of its terms."""
    found = _matcher(agency_id)
    return found is None or bool(found.search(item_text(title, citations)))
