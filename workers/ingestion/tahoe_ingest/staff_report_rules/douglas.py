"""Rule ``douglas-packet``: Douglas County's item cover pages in the agenda packet (every county board on its Granicus site).

Douglas County's Granicus site (view 1) posts the agenda without per-item links and an
"Agenda Packet" that opens with the agenda and then, for every item, a cover page and the
item's attachments. Each cover page prints::

    AGENDA ITEM NO. A.
    BOARD OF COUNTY COMMISSIONERS AGENDA ITEM / COVER PAGE
    MEETING DATE:   October 1, 2026
    AGENDA:         CONSENT ITEMS
    TITLE:          For possible action. Discussion on … (the agenda text, verbatim)
    RECOMMENDED MOTION: …

The packet is the captured one when discovery stored it; otherwise it is the "Agenda
Packet" link in the ViewPublisher row of the meeting's agenda clip, downloaded once
and used only when its opening pages print the captured agenda. Packets are far larger
than other agencies' documents, so this rule raises the download limits (``MAX_MEGABYTES``
and ``MAX_PAGES``; an agency entry may set ``maxMegabytes`` and ``maxPages`` instead).

Two signals must agree: the packet's cover page carries the item's number (a lettered
consent item "9.A" matches cover "A." only when the cover's ``AGENDA:`` section is the
item's section, since letters restart in each lettered section), and the cover's
``TITLE:`` repeats the item text, with ``MEETING DATE:`` this meeting. The report runs
from the cover to the page before the next cover.
"""
from __future__ import annotations

import re

from ..staff_reports import Context, Mapping, TooLarge, Uncertain, leading, long_date, norm_number, title_overlap, words
from . import _granicus

# Packet download limits. In 2026 the commissioners' packets reached 229 MiB and 1,539 pages (September 17) and the
# planning commission's 1,316 pages (October 13, 57 MiB). A packet streams to a temporary file, and reading one costs
# the job about its size in memory (/tmp is RAM on Cloud Run) plus about 65 MiB: under 400 MiB at these limits, well
# inside the 2 GiB job (workers/ingestion/README.md, "Memory"). A packet over a limit gets no staff reports.
MAX_MEGABYTES = 300  # MiB
MAX_PAGES = 2000
SAME_TITLE = 0.9  # covers print the agenda text verbatim
OPENS_WITH_AGENDA = 0.9  # share of each captured agenda page's words the packet's page must print

_COVER = re.compile(r"AGENDA ITEM\s*\n\s*COVER PAGE")
_NUMBER = re.compile(r"^\s*AGENDA ITEM NO\.\s*([0-9A-Z]+(?:\.[0-9A-Z]+)*)\.?[ \t]*$", re.M)
_DATE = re.compile(r"^\s*MEETING DATE:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", re.M)
_SECTION = re.compile(r"^\s*AGENDA:[ \t]*(\S.*)$", re.M)
_TITLE = re.compile(r"^\s*TITLE:\s*(.+?)\n\s*RECOMMENDED MOTION:", re.M | re.S)
_MINUTES = re.compile(r"\bapprove the (?:draft )?minutes\b", re.I)


def covers(pages: dict[int, str]) -> list[dict]:
    """Every cover page in packet order: ``{"page", "end", "number", "date", "section", "title"}`` (fields None when unprinted).

    A cover without an item number (the minutes) still ends the previous item's run.
    """
    found = []
    for page in sorted(pages):
        text = pages[page]
        if not _COVER.search(text[:600]):
            continue
        number, date, section, title = (_NUMBER.search(text[:300]), _DATE.search(text), _SECTION.search(text),
                                        _TITLE.search(text))
        found.append({"page": page, "number": norm_number(number.group(1)) if number else None,
                      "date": date.group(1) if date else None,
                      "section": " ".join(section.group(1).split()) if section else None,
                      "title": " ".join(title.group(1).split()) if title else ""})
    for cover, following in zip(found, found[1:] + [None]):
        cover["end"] = following["page"] - 1 if following else max(pages)
    return found


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b) and " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def cover_for(item, found: list[dict]) -> list[dict]:
    """The covers that carry this item's number: "12" or "11.1" as printed, or a lettered "9.A" as "A." in its own section."""
    number = norm_number(item.number)
    lettered = re.fullmatch(r"\d+\.([A-Z])", number)
    return [c for c in found if c["number"] == number
            or (lettered and c["number"] == lettered.group(1) and _same(c["section"], item.section))]


def _agendas(ctx: Context) -> list:
    """The captured agenda PDFs (one file may be held under two document IDs, or a meeting under two clips)."""
    return [d for d in ctx.meeting.documents if d.media_type == "application/pdf"
            and "AgendaViewer.php" in d.url and not re.search(r"packet|supplement", d.source_document_id, re.I)]


def _read_packet(read, max_megabytes: int):
    """Run a packet download, turning "too large" into Uncertain: retrying the same oversized file would never succeed."""
    try:
        return read()
    except TooLarge as exc:
        raise Uncertain(f"the agenda packet is larger than {max_megabytes} MB") from exc


def _listed_packet(ctx: Context, agendas: list, limits: dict) -> tuple[str, dict, int, str]:
    """Download the "Agenda Packet" the ViewPublisher listing gives in the row of the meeting's agenda clip."""
    clips = {c for d in agendas if (c := _granicus.query(d.url, "clip_id"))}
    if not clips:
        raise Uncertain("no agenda packet, and no agenda clip to find it from")
    official = ctx.meeting.official_url or ""
    if "ViewPublisher.php" not in official:
        host = re.match(r"https://[^/]+", agendas[0].url).group()
        official = f"{host}/ViewPublisher.php?view_id={_granicus.query(agendas[0].url, 'view_id') or 1}"
    links = ctx.html_links(official)
    packets = set()
    for clip in clips:
        rows = [i for i, link in enumerate(links) if "AgendaViewer.php" in link["url"] and _granicus.query(link["url"], "clip_id") == clip]
        if len(rows) != 1:
            raise Uncertain(f"agenda clip {clip} is listed {len(rows)} times")
        for link in links[rows[0] + 1:]:
            if "AgendaViewer.php" in link["url"]:
                break
            if link["text"].strip().casefold() == "agenda packet":
                packets.add(link["url"])
    if len(packets) != 1:
        raise Uncertain(f"{len(packets)} agenda packets listed for this meeting")
    url = next(iter(packets))
    fetched = _read_packet(lambda: ctx.read(url, **limits), limits["max_bytes"] // 2**20)
    return url, {p["page_number"]: p["text"] for p in fetched["pages"]}, fetched["page_count"], fetched["sha256"]


def map_douglas(ctx: Context, *, max_megabytes: int = MAX_MEGABYTES, max_pages: int = MAX_PAGES) -> list[Mapping]:
    limits = {"max_bytes": max_megabytes * 2**20, "max_pages": max_pages}
    agendas = _agendas(ctx)
    if not agendas:
        raise Uncertain("no captured agenda")
    stored = {(d.url, d.sha256): d for d in ctx.meeting.documents if d.media_type == "application/pdf"
              and re.search(r"agenda-packet", d.source_document_id, re.I)}
    if len(stored) > 1:
        raise Uncertain("more than one agenda packet")
    if stored:
        packet = next(iter(stored.values()))
        pages = _read_packet(lambda: ctx.pages(packet, needed=bool, **limits), max_megabytes)
        url, page_count, sha256 = packet.url, packet.page_count, packet.sha256
    else:
        url, pages, page_count, sha256 = _listed_packet(ctx, agendas, limits)
    if not any(_opens_with(ctx.stored_pages(d.version_id), pages) for d in agendas):
        raise Uncertain("the agenda packet does not open with the captured agenda")
    found = covers(pages)
    meeting_date = long_date(ctx.meeting.local_date) if ctx.meeting.local_date else None
    mappings = []
    for item in ctx.numbered():
        if _MINUTES.search(item.title):
            ctx.skip(item, "minutes approval")
            continue
        matches = cover_for(item, found)
        if len(matches) != 1:
            ctx.skip(item, f"{len(matches)} packet cover pages carry item {item.number}" if matches else "no packet cover page for this item")
            continue
        cover = matches[0]
        overlap = title_overlap(item.title, cover["title"])
        if overlap < SAME_TITLE:
            ctx.skip(item, "cover page title differs from the agenda item")
            continue
        if not meeting_date or cover["date"] != meeting_date:
            ctx.skip(item, "cover page is for another meeting date")
            continue
        start, end = cover["page"], cover["end"]
        printed = re.search(r"AGENDA ITEM NO\.\s*\S+", pages[start]).group()
        evidence = {"reportHeading": printed, "titleOverlap": round(overlap, 2), "meetingDate": ctx.meeting.local_date.isoformat(),
                    "pages": f"{start}-{end}"}
        if cover["number"] != norm_number(item.number):
            evidence["agendaSection"] = cover["section"]
        mappings.append(Mapping(item.id, item.fingerprint, "staff-report", "Item cover page and attachments in the agenda packet",
                                url, start, end, page_count, sha256, evidence, leading(pages, start, end)))
    return mappings


def _opens_with(agenda: dict[int, str], packet: dict[int, str]) -> bool:
    """The packet's first pages print every page of the captured agenda (page stamps aside)."""
    if not agenda:
        return False
    for number, text in agenda.items():
        wanted = set(words(text))
        if not wanted or len(wanted & set(words(packet.get(number, "")))) < OPENS_WITH_AGENDA * len(wanted):
            return False
    return True


RULES = {"douglas-packet": map_douglas}
