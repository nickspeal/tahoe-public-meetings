"""Conservative agenda segmentation.

Each agency entry in ``packages/domain/agencies.json`` lists the agenda layouts
its documents use (``"agenda": {"layouts": [...]}``); the layouts themselves are
data in ``config/agenda-layouts.json`` (see ``agenda_layouts``). Each layout
whose cover a document matches reads it in turn, with the shared outline
engine (``outline``) or the hand-written parser the layout names
(``agenda_custom``), until one validates. The title quote is copied exactly
from one extracted page.

Irregular numbering does not void an agenda (decision 0030): the items that
could be read are published with operator notes, and a number the
agenda skips is listed as a placeholder (``outline.PLACEHOLDER_TITLE``) with no
quote. When several agenda PDFs read differently, the agenda is preferred over
the packet, then the copy with the most items.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .agenda_custom import PARSERS, UNVALIDATED
from .agenda_layouts import layouts
from .catalog import agency_catalog
from .outline import Layout, cover_matches, is_placeholder, parse_layout_notes

DIFFERING = "Multiple agenda PDFs contain differing item sequences"
# A packet's copy of the agenda adds its page references: "(Pages 4-5)", "– pages 4 - 22".
PAGE_REFERENCE = re.compile(r"(?i)\s*(?:\(\s*pages?\s+[\d\s,&–-]+\)|[–—-]\s*pages?\s+[\d\s,&–-]+)$")


@dataclass
class ParseResult:
    items: list[dict]
    complete: bool
    warning: str | None = None
    # Operator notes on a published agenda whose numbering was irregular.
    notes: tuple[str, ...] = ()


def _result(items: list[dict], complete: bool, warning: str | None = None, notes: tuple[str, ...] = ()) -> ParseResult:
    # A placeholder for a skipped number has no quote by design; every other item needs one.
    if not complete or all(is_placeholder(i) for i in items) or \
            any(not i["source_pages"][0]["quote"] and not is_placeholder(i) for i in items):
        return ParseResult([], False, warning or UNVALIDATED)
    return ParseResult(items, True, notes=tuple(notes))


def agenda_config(agency: str) -> dict:
    """The agency's ``agenda`` registry block; empty when no layout reads its agendas."""
    return next((a.get("agenda", {}) for a in agency_catalog() if a["id"] == agency), {})


def has_parser(agency: str) -> bool:
    return bool(agenda_config(agency).get("layouts"))


def _parse_document(formats: tuple[Layout, ...], document_id: str, pages: list[dict]) -> tuple[ParseResult, bool]:
    """Parse one document; the flag says whether it looked like this agency's agenda."""
    failure = None
    for layout in formats:
        if not cover_matches(layout, pages):
            continue
        if layout.parser:
            items, warning = PARSERS[layout.parser](document_id, pages)
            # A custom parser's warnings alongside items are notes for the operator.
            notes = ([warning] if isinstance(warning, str) else list(warning)) if items and warning else []
        else:
            items, warning, notes = parse_layout_notes(layout, document_id, pages)
        if items:
            return _result(items, True, notes=tuple(notes)), True
        failure = failure or warning
    if failure:
        return _result([], False, failure), True
    redesigned = next((layout for layout in formats if layout.recognize and cover_matches(layout, pages, layout.recognize)), None)
    warning = f"{(redesigned or formats[0]).label} cover was not recognized; items require review."
    return _result([], False, warning), redesigned is not None


def parse_agenda(agency: str, documents: list[dict], pages: list[dict]) -> ParseResult:
    config = agenda_config(agency)
    if not config.get("layouts"):
        return _result([], False, f"No parser exists for agency {agency}; review required.")
    formats = tuple(layouts()[name] for name in config["layouts"])
    if config.get("agendaBeforePacket"):
        # The meeting page links the agenda and a full packet that reprints it, captured as
        # "<agency>-agenda-<date>" and "<agency>-agenda-packet-<date>". Items come from the
        # agenda; the packet is read only when no agenda document was captured, so an older
        # packet copy can never void or replace the items of a revised agenda.
        captured = {p["document_id"] for p in pages}
        agendas = [d for d in documents if "-agenda-packet" not in str(d["id"]) and d["id"] in captured]
        documents = agendas or documents
    successes = []
    sources = []
    failures = []
    for document in documents:
        doc_pages = [p for p in pages if p["document_id"] == document["id"]]
        if not doc_pages:
            continue
        doc_pages = sorted(doc_pages, key=lambda p: p["page_number"])
        result, recognized = _parse_document(formats, document["id"], doc_pages)
        if result.complete:
            successes.append(result)
            sources.append(document)
        elif recognized:
            failures.append((result, document.get("media_type", "application/pdf") != "application/pdf"))
    # Legistar's accessible HTML copy of a PDF agenda (View.ashx?M=AADA) is text the PDF
    # layouts were not written for; its failure must not discard a validated PDF parse.
    failures = [result for result, html_copy in failures if not (html_copy and successes)]
    if failures and successes:
        return _result([], False, "An agenda PDF has an unvalidated structure; review all agenda documents.")
    if len(successes) == 1:
        return successes[0]
    if len(successes) > 1:
        signatures = [_signature(result) for result in successes]
        if all(signature == signatures[0] for signature in signatures[1:]):
            return successes[0]
        # Prefer the agenda to the packet that reprints it, then the copy that has the most items.
        best = min(range(len(successes)), key=lambda k: ("-agenda-packet" in str(sources[k]["id"]),
                                                         -sum(not is_placeholder(i) for i in successes[k].items), k))
        chosen = successes[best]
        if all(_same_items(chosen, result) for result in successes):
            return chosen  # the copies differ only in page references, where a long title was cut, or placeholders
        name = sources[best].get("title") or sources[best]["id"]
        return replace(chosen, notes=chosen.notes + (f"{DIFFERING}; items are taken from “{name}”; review required.",))
    if failures:
        return _result([], False, failures[0].warning)
    return _result([], False, f"No complete {agency.upper()} agenda recognized in downloaded documents; review required.")


def _signature(result: ParseResult) -> tuple:
    return tuple((item["source_number"], re.sub(r"\W+", "", item["title"].lower())) for item in result.items)


def _same_items(a: ParseResult, b: ParseResult) -> bool:
    """Whether two copies list the same real items, ignoring the packet's page references and where a title was cut."""
    def items(result: ParseResult) -> list[tuple[str, str]]:
        return [(item["source_number"], re.sub(r"\W+", "", PAGE_REFERENCE.sub("", item["title"]).lower()))
                for item in result.items if not is_placeholder(item)]
    first, second = items(a), items(b)
    return len(first) == len(second) and all(
        x[0] == y[0] and (x[1].startswith(y[1]) or y[1].startswith(x[1])) for x, y in zip(first, second))
