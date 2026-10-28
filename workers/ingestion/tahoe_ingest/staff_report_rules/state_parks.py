"""California State Parks (``california-state-parks``): State Park and Recreation Commission staff report cover pages.

Two commissions share this agency. The State Park and Recreation Commission's meeting
page lists, after each meeting's "Meeting Agenda" link, one "Agenda Item NN <title>"
link per staff report, and each report opens with a cover page:

    CALIFORNIA STATE PARK AND RECREATION COMMISSION MEETING
    STAFF REPORT COVER PAGE
    March 5, 2026
    AGENDA ITEM:   13A Consent Calendar          (13A is the agenda's sub-item 13.a)
    SUBJECT:       Memorial Grove Resolutions

Two signals must agree: the page's "Agenda Item NN" link (taken only between the
captured agenda's link and the next "Meeting Agenda") and the cover page's "AGENDA
ITEM:" number, its printed meeting date, and the item title. A link whose number covers
several agenda sub-items (07 for 7.a-7.d) is skipped.

The Off-Highway Motor Vehicle Recreation Commission (ohv.parks.ca.gov) names its staff
reports by topic and prints no agenda item number on the page or the report, so a title
would be the only signal; its meetings are not mapped.
"""
from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlparse

from ..staff_reports import MIN_TITLE_OVERLAP, Context, Mapping, Uncertain, linked_reports, long_date, norm_number, title_overlap

_ANCHOR = re.compile(r"^Agenda Item\s+(\d{1,2})\b\s*(.*)$", re.I)
_COVER = re.compile(r"^[ \t]*AGENDA ITEM:[ \t]+(\d{1,2})([A-Z]?)\b[ \t]*(.*)$", re.M)
_UNMAPPED_HOSTS = {"ohv.parks.ca.gov": "OHMVR Commission staff reports are named by topic and carry no agenda item number"}


def canonical(url: str) -> str:
    """One spelling for a parks.ca.gov file link: the page prints raw spaces, the captured agenda URL has "%20"."""
    return quote(unquote(url), safe=":/?&=#~+%")


def _item_parts(number: str) -> tuple[int, str] | None:
    """``"13.a"`` -> ``(13, "A")``; ``"9"`` -> ``(9, "")``; anything else None."""
    found = re.fullmatch(r"(\d{1,2})(?:\.([a-z]))?", number.strip(), re.I)
    return (int(found.group(1)), (found.group(2) or "").upper()) if found else None


def map_state_parks(ctx: Context) -> list[Mapping]:
    host = urlparse(ctx.meeting.official_url or "").netloc.lower()
    if host in _UNMAPPED_HOSTS:
        raise Uncertain(_UNMAPPED_HOSTS[host])
    agendas = {canonical(d.url) for d in ctx.meeting.documents if d.media_type == "application/pdf"}
    if len(agendas) != 1:
        raise Uncertain("no single agenda PDF")
    agenda = agendas.pop()
    links = ctx.html_links(ctx.meeting.official_url)
    own = [n for n, link in enumerate(links) if canonical(link["url"]) == agenda]
    if len(own) != 1:
        raise Uncertain(f"the meeting page links the captured agenda {len(own)} times")
    end = next((n for n in range(own[0] + 1, len(links)) if re.fullmatch(r"Meeting Agenda", links[n]["text"].strip(), re.I)),
               len(links))
    anchors: dict[int, dict] = {}
    for link in links[own[0] + 1:end]:
        named = _ANCHOR.match(" ".join(link["text"].split()))
        if named:
            anchors.setdefault(int(named.group(1)), {})[canonical(link["url"])] = link["text"].strip()
    if not anchors:
        raise Uncertain("the meeting page lists no \"Agenda Item NN\" staff report links")
    items = ctx.numbered()
    by_number: dict[int, list] = {}
    for item in items:
        if parts := _item_parts(item.number):
            by_number.setdefault(parts[0], []).append(item)
    candidates: dict[str, set] = {}
    pending = []
    for item in items:
        parts = _item_parts(item.number)
        found = anchors.get(parts[0], {}) if parts else {}
        if found and len(by_number[parts[0]]) > 1:
            ctx.skip(item, f"one report link for items {', '.join(i.number for i in by_number[parts[0]])}")
            continue
        if found:
            candidates[norm_number(item.number)] = set(found)
        pending.append(item)

    def verify(item, report):
        page = report["pages"][0]["text"] if report["pages"] else ""
        number, letter = _item_parts(item.number)
        cover = _COVER.search(page)
        if not re.search(r"STAFF REPORT COVER PAGE", page, re.I) or not cover:
            return "report has no staff report cover page"
        if int(cover.group(1)) != number or cover.group(2) != letter:
            return f"report does not name item {item.number}"
        if not re.search(rf"^\s*{re.escape(long_date(ctx.meeting.local_date))}\s*$", page[:cover.start()], re.M):
            return "report is for another meeting date"
        overlap = title_overlap(item.title, page[cover.start():cover.start() + 600])
        if overlap < MIN_TITLE_OVERLAP:
            return "report title differs from the agenda item"
        text = anchors[number][report["url"]]
        return {"agendaLink": text, "reportHeading": " ".join(cover.group().split()), "titleOverlap": round(overlap, 2),
                "meetingDate": ctx.meeting.local_date.isoformat()}
    return linked_reports(ctx, pending, candidates, verify=verify, kind="staff-report", title="Item {number} staff report",
                          missing="{count} \"Agenda Item\" links for item {number}")


RULES = {"california-state-parks": map_state_parks}
