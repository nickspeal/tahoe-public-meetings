"""Conservative agenda segmentation.

Each agency entry in ``packages/domain/agencies.json`` lists the agenda layouts
its documents use (``"agenda": {"layouts": [...]}``); the layouts themselves are
data in ``config/agenda-layouts.json`` (see ``agenda_layouts``). Each layout
whose cover a document matches reads it in turn, with the shared outline
engine (``outline``) or the hand-written parser the layout names
(``agenda_custom``), until one validates. Only a complete, sequential agenda is
returned for automatic publication. The title quote is copied exactly from one
extracted page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .agenda_custom import PARSERS, UNVALIDATED
from .agenda_layouts import layouts
from .catalog import agency_catalog
from .outline import Layout, cover_matches, parse_layout


@dataclass
class ParseResult:
    items: list[dict]
    complete: bool
    warning: str | None = None


def _result(items: list[dict], complete: bool, warning: str | None = None) -> ParseResult:
    if not complete or not items or any(not i["source_pages"][0]["quote"] for i in items):
        return ParseResult([], False, warning or UNVALIDATED)
    return ParseResult(items, True)


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
        items, warning = PARSERS[layout.parser](document_id, pages) if layout.parser else parse_layout(layout, document_id, pages)
        if items:
            return _result(items, True), True
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
    failures = []
    for document in documents:
        doc_pages = [p for p in pages if p["document_id"] == document["id"]]
        if not doc_pages:
            continue
        doc_pages = sorted(doc_pages, key=lambda p: p["page_number"])
        result, recognized = _parse_document(formats, document["id"], doc_pages)
        if result.complete:
            successes.append(result)
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
        signatures = [tuple((item["source_number"], re.sub(r"\W+", "", item["title"].lower()))
                            for item in result.items) for result in successes]
        if all(signature == signatures[0] for signature in signatures[1:]):
            return successes[0]
        return _result([], False, "Multiple agenda PDFs contain differing item sequences; review required.")
    if failures:
        return _result([], False, failures[0].warning)
    return _result([], False, f"No complete {agency.upper()} agenda recognized in downloaded documents; review required.")
