"""Explainable agenda-topic rules.

Classify the item title, which is bounded to one agenda item. Page excerpts may
contain neighboring rows or packet attachments, so they are evidence links,
not keyword input. The operator's overrides are stored separately from these rules.
"""
from __future__ import annotations

import hashlib
import json
import re

from .catalog import topic_ids, topic_catalog, expand_topics

# Include configuration in the version so rule edits are picked up by reclassify.
CLASSIFIER_VERSION = "title-rules-v4.1-" + hashlib.sha256(
    json.dumps(topic_catalog(), sort_keys=True).encode()).hexdigest()[:12]
RULES = {topic["id"]: [(rule["id"], re.compile(rule["pattern"], re.I))
                       for rule in topic.get("titlePatterns", [])]
         for topic in topic_catalog()}

TRAIL = re.compile(r"\btrails?\b", re.I)
TRAIL_CONTEXT = re.compile(r"\b(?:corridor|transportation|street|pedestrian|bike|transit|safety|multi[- ]use|shared[- ]use)\b", re.I)
TTD_FLEET = re.compile(r"\bfleet\b", re.I)
TTD_BUDGET = re.compile(r"\b(?:operating budget|transit budget|service budget)\b", re.I)
TTD_SERVICE = re.compile(r"\b(?:service (?:change|planning|expansion|reduction)|route \d+)\b", re.I)


def classify(agency_id: str, title: str, citations: list[dict]) -> dict[str, dict]:
    """Return topic evidence keyed by topic ID; never inspect neighboring page text."""
    matches: dict[str, dict] = {}
    citation = citations[0] if citations else None

    def add(topic: str, rule: str, matched: str):
        matches.setdefault(topic, {"classifierVersion": CLASSIFIER_VERSION, "rule": rule,
                                   "field": "item_title", "matchedText": matched,
                                   "citation": citation})

    if not set(RULES) <= topic_ids():
        raise ValueError("classifier rule refers to a topic absent from the canonical catalog")
    for topic, rules in RULES.items():
        for rule, pattern in rules:
            found = pattern.search(title)
            if found:
                add(topic, rule, found.group())
                break

    if "biking" in topic_ids() and "biking" not in matches and TRAIL.search(title) and TRAIL_CONTEXT.search(title):
        add("biking", "trail-with-transport-context", TRAIL.search(title).group())

    if "transit" in topic_ids() and agency_id == "ttd" and "transit" not in matches:
        for rule, pattern in (("ttd-fleet", TTD_FLEET), ("ttd-operating-budget", TTD_BUDGET),
                              ("ttd-service-action", TTD_SERVICE)):
            found = pattern.search(title)
            if found:
                add("transit", rule, found.group())
                break
    for topic in expand_topics(set(matches)) - set(matches):
        parent = next(t for t in matches if topic in expand_topics({t}))
        add(topic, "included-topic", matches[parent]["matchedText"])
        matches[topic]["includedFrom"] = parent
    return matches
