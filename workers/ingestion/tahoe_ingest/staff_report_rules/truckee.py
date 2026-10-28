"""Rule ``truckee-packet``: Town of Truckee (Municode) staff report pages in the agenda packet carry an "Item 5.1" header.

When discovery captured only the agenda, the packet Municode publishes beside it is
read instead, after checking that it opens with the captured agenda.
"""
from __future__ import annotations

import re

from ..staff_reports import Context, Document, Mapping, Uncertain, norm_number, packet_reports, words

_TRUCKEE_HEADER = re.compile(r"(?:^|\s{2,})Item\s+(\d+(?:\.\d+)*)\s*$")


def map_truckee(ctx: Context) -> list[Mapping]:
    candidates = ctx.numbered()
    packets = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
               and re.search(r"packet", d.source_document_id + " " + d.title + " " + d.url, re.I)]
    wanted = {norm_number(i.number) for i in candidates}

    def has_headers(pages):
        return wanted <= {label for text in pages.values() if (label := _label(text))}
    if len(packets) == 1:
        packet = packets[0]
        pages = ctx.pages(packet, needed=has_headers)
    elif not packets:
        packet, pages = _municode_packet(ctx)
    else:
        raise Uncertain("more than one agenda packet")
    # Pages headed with the same item number form one report, as long as no other item's pages fall between them.
    # Charts and slides may lack the header or carry it beside other text on the first line.
    return packet_reports(ctx, packet, pages, candidates, page_label=_label, label_name="Item {number}")


def _label(text: str) -> str | None:
    header = _TRUCKEE_HEADER.search("\n".join(text.strip().splitlines()[:1]))
    return norm_number(header.group(1)) if header else None


def _municode_packet(ctx: Context) -> tuple[Document, dict[int, str]]:
    """The packet Municode publishes beside a captured agenda PDF, verified to open with that agenda.

    Municode names a meeting's files MEET-Agenda-<id>.pdf and MEET-Packet-<id>.pdf (its
    meeting list gives both); scheduled discovery captures only the agenda.
    """
    agendas = [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
               and re.search(r"/MEET-Agenda-[0-9a-f]{32}\.pdf", d.url)]
    if len(agendas) != 1:
        raise Uncertain("no agenda packet, and no single Municode agenda PDF to find it from")
    agenda = agendas[0]
    url = agenda.url.replace("/MEET-Agenda-", "/MEET-Packet-", 1).replace("&n=Agenda-", "&n=AgendaPacket-", 1)
    fetched = ctx.read(url)
    pages = {p["page_number"]: p["text"] for p in fetched["pages"]}
    opening = ctx.stored_pages(agenda.version_id).get(1, "")
    agenda_words = set(words(opening))
    if not agenda_words or len(agenda_words & set(words(pages.get(1, "")))) < 0.9 * len(agenda_words):
        raise Uncertain("the Municode packet does not open with the captured agenda")
    return Document("", "municode-packet", "Agenda packet", url, fetched["sha256"], fetched["page_count"], "application/pdf"), pages


RULES = {"truckee-packet": map_truckee}
