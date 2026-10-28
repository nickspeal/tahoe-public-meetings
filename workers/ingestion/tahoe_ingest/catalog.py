"""Load the topic catalog and the agency registry in a checkout or installed wheel."""
from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path

from .agenda_layouts import validate_agency_agendas
from .durations import validate_scope as validate_duration
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


# Every key an agency entry may have, in the order the registry file keeps them (`registry_text`).
AGENCY_KEYS = ("id", "name", "shortName", "aliases", "extends", "follow", "officialUrl", "sourceUrl", "participation",
               "participationTypes", "discovery", "agenda", "staffReports", "topicHints", "schedule", "duration", "legacy")
# What an entry says about its own body, never inherited through `extends`: its identity, the decision to follow it,
# how readers take part, when and how long it meets, and its research record. The rest describes the publisher's
# website and document formats, which sibling bodies share.
OWN_KEYS = frozenset({"id", "name", "aliases", "extends", "follow", "participation", "schedule", "duration", "legacy"})


def registry_text(entries: list[dict]) -> str:
    """The registry file's canonical text: two-space JSON, each entry's keys in AGENCY_KEYS order."""
    return json.dumps([{key: entry[key] for key in sorted(entry, key=AGENCY_KEYS.index)} for entry in entries],
                      indent=2, ensure_ascii=False) + "\n"


def _merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        both = isinstance(value, dict) and isinstance(merged.get(key), dict)
        merged[key] = _merge(merged[key], value) if both else copy.deepcopy(value)
    return merged


def resolve_extends(entries: list[dict]) -> list[dict]:
    """Registry entries with ``extends`` applied, so no consumer needs to know about it.

    A child starts from its parent's resolved entry minus OWN_KEYS. Objects merge key by key,
    recursively, the child's value winning; lists and other values replace the parent's.
    A child cannot remove an inherited key, so a body whose blocks differ in shape, not only
    in values, should not extend. The parent must exist; chains resolve parent first and
    cycles are an error. ``extends`` itself is dropped, and keys follow AGENCY_KEYS.
    """
    by_id = {entry["id"]: entry for entry in entries}
    done: dict[str, dict] = {}

    def resolve(aid: str, path: tuple[str, ...]) -> dict:
        if aid not in done:
            entry, parent = by_id[aid], by_id[aid].get("extends")
            if parent is None:
                resolved = copy.deepcopy(entry)
            elif not isinstance(parent, str) or parent not in by_id:
                raise ValueError(f"{aid}: extends unknown agency {parent!r}")
            elif parent in path:
                raise ValueError(f"agency entries extend each other: {' -> '.join((*path, parent))}")
            else:
                inherited = {k: v for k, v in resolve(parent, (*path, parent)).items() if k not in OWN_KEYS}
                resolved = _merge(inherited, {k: v for k, v in entry.items() if k != "extends"})
            done[aid] = {key: resolved[key] for key in sorted(resolved, key=AGENCY_KEYS.index)}
        return done[aid]
    return [resolve(entry["id"], (entry["id"],)) for entry in entries]


@lru_cache(maxsize=1)
def agency_catalog() -> tuple[dict, ...]:
    """The validated registry, with ``extends`` resolved (see config/README.md, "Sibling bodies")."""
    raw = json.loads(agency_catalog_path().read_text())
    ids = [a.get("id") for a in raw]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate agency IDs")
    for entry in raw:
        if not isinstance(entry.get("id"), str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", entry["id"]):
            raise ValueError(f"invalid agency: {entry}")
        if unknown := set(entry) - set(AGENCY_KEYS):
            raise ValueError(f"{entry['id']}: unknown registry keys {', '.join(sorted(unknown))}")
    agencies = resolve_extends(raw)
    for agency in agencies:
        if not all(agency.get(k) for k in ("name", "shortName")) or not isinstance(agency.get("follow"), bool):
            raise ValueError(f"invalid agency: {agency}")
        # Other names readers search for (acronyms, full names); the website's agency filter matches them.
        aliases = agency.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(a, str) and a.strip() for a in aliases):
            raise ValueError(f"invalid aliases: {agency['id']}")
        validate_discovery(agency)
        if "schedule" in agency:
            validate_schedule(agency["id"], agency["schedule"])
        if "duration" in agency:
            validate_duration(agency["duration"], f"duration of {agency['id']}")
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
    validate_agency_agendas(agencies)
    return tuple(agencies)


def followed_agencies() -> dict[str, dict]:
    return {a["id"]: a for a in agency_catalog() if a["follow"]}
