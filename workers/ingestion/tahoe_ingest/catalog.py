"""Load the single source topic catalog in a checkout or installed wheel."""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from .schedules import validate_schedule
from .source_discovery import validate_discovery


@lru_cache(maxsize=1)
def topic_catalog() -> tuple[dict, ...]:
    checkout = Path(__file__).resolve().parents[3] / "packages/domain/topics.json"
    packaged = Path(__file__).with_name("topics.json")
    path = packaged if packaged.is_file() else checkout
    topics = json.loads(path.read_text())
    ids = [topic["id"] for topic in topics]
    if len(ids) != len(set(ids)) or any(not all(topic.get(key) for key in ("id", "name", "description")) for topic in topics):
        raise ValueError(f"invalid topic catalog: {path}")
    for topic in topics:
        if not set(topic.get("includes", [])) <= set(ids):
            raise ValueError(f"unknown included topic: {topic['id']}")
        for rule in topic.get("titlePatterns", []):
            re.compile(rule["pattern"], re.I)
    return tuple(topics)


def public_topics() -> list[dict]:
    return [{key: topic[key] for key in ("id", "name", "description")} for topic in topic_catalog()]


def topic_ids() -> frozenset[str]:
    return frozenset(topic["id"] for topic in topic_catalog())


def expand_topics(ids: set[str]) -> set[str]:
    """Hide retired tags, retain stored overrides, and enforce topic inclusion."""
    active = ids & topic_ids()
    while True:
        expanded = active | {included for t in topic_catalog() if t["id"] in active
                             for included in t.get("includes", [])}
        if expanded == active:
            return active
        active = expanded


def agency_catalog_path() -> Path:
    """The registry in use: a wheel's bundled copy, else the checkout's canonical file."""
    packaged = Path(__file__).with_name("agencies.json")
    return packaged if packaged.is_file() else Path(__file__).resolve().parents[3] / "packages/domain/agencies.json"


@lru_cache(maxsize=1)
def agency_catalog() -> tuple[dict, ...]:
    agencies = json.loads(agency_catalog_path().read_text())
    ids = [a["id"] for a in agencies]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate agency IDs")
    for agency in agencies:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", agency["id"]) or not all(
                agency.get(k) for k in ("name", "shortName")) or not isinstance(agency.get("follow"), bool):
            raise ValueError(f"invalid agency: {agency}")
        # Other names readers search for (acronyms, full names); the website's agency filter matches them.
        aliases = agency.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ValueError(f"invalid aliases: {agency['id']}")
        validate_discovery(agency)
        if "schedule" in agency:
            validate_schedule(agency["id"], agency["schedule"])
        from .staff_report_rules import check_config  # imported here: rule modules load only with the registry
        check_config(agency)
        from urllib.parse import urlparse
        for key in ("officialUrl", "sourceUrl"):
            if agency.get(key) is not None:
                url = urlparse(agency[key])
                if url.scheme not in ("http", "https") or not url.netloc:
                    raise ValueError(f"invalid {key}: {agency['id']}")
        # Standing participation details, from the agency page named in sourceUrl, fill gaps the agenda leaves.
        if "participation" in agency:
            standing = agency["participation"]
            if set(standing) - {"watchUrl", "url", "email", "instructions", "sourceUrl", "checkedOn"} or not standing.get("sourceUrl") \
                    or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", standing.get("checkedOn", "")):
                raise ValueError(f"invalid participation: {agency['id']}")
            for key in ("watchUrl", "url", "sourceUrl"):
                if key in standing and (urlparse(standing[key]).scheme != "https" or not urlparse(standing[key]).netloc):
                    raise ValueError(f"invalid participation.{key}: {agency['id']}")
    return tuple(agencies)


def followed_agencies() -> dict[str, dict]:
    return {a["id"]: a for a in agency_catalog() if a["follow"]}
