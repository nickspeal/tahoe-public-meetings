"""Draft minutes found through a later meeting's agenda (IDEA-017, decision 0025).

Most agencies never post approved minutes. The draft goes into the packet of the
same body's next meeting as a consent item: "Approval of Minutes of June 3, 2026",
"Minutes of August 25, 2026, Regular Meeting.", "City Council Meeting Minutes".
This step links each earlier meeting to that item, labelled as a draft.

Matching uses stored agenda items only, so it reaches meetings older than the
scheduled discovery window:

- A dated item (in its title or cited agenda text) belongs to the same agency's
  meeting on each named date, up to MAX_GAP_DAYS earlier. Dates on which the agency
  has no meeting (another body's minutes, such as an advisory committee's) are skipped.
- An undated item belongs to the agency's immediately preceding meeting, only when
  exactly one meeting was held that day and the item names no other body.
- The earliest later meeting wins when several agendas list the same minutes.

The link opens the packet page where those minutes begin when the stored text
shows one (a page outside the agenda that says "minutes" and the date near its top);
otherwise it opens the agenda at the item. Listing links (tahoe_ingest.records)
always outrank these.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from .records import apply_links, dates_in, merged, qualifiers, FIELDS

MINUTES_ITEM = re.compile(r"\bminutes\b", re.I)
SPEAKING_TIME = re.compile(r"\b(?:\d+|one|two|three|four|five|ten)[- ]minutes?\b", re.I)
UNDATED_WORDS = re.compile(r"\b(?:approv(?:e|al)|adopt(?:ion)?|consider(?:ation)?|of|the|draft|regular|special|meeting|minutes|and)\b", re.I)
MAX_GAP_DAYS = 200
UNDATED_MIN_GAP_DAYS = 10  # minutes are seldom ready for a meeting a week later
UNDATED_MAX_GAP_DAYS = 120
HEAD_CHARS = 600
FOLLOWING_CHARS = 300


def _json(value):
    return json.loads(value) if isinstance(value, str) else (value or [])


def _long(day: date) -> str:
    return f"{day:%B} {day.day}, {day.year}"


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}


def minutes_targets(item: dict, meetings: list[dict]) -> list[dict]:
    """The earlier meetings of the item's agency whose minutes this agenda item presents."""
    if not MINUTES_ITEM.search(item["title"]) or SPEAKING_TIME.search(item["title"]):
        return []
    later = item["local_date"]
    earlier = [m for m in meetings if m["local_date"] and m["local_date"] < later and m.get("status") != "canceled"
               and "closed" not in qualifiers(m["title"])]
    text = " ".join([item["title"], *(c.get("quote", "") for c in _json(item["citations"])), item.get("following", "")])
    named = [d for d in dates_in(text) if timedelta(days=1) <= later - d <= timedelta(days=MAX_GAP_DAYS)]
    if named:
        found = []
        for day in named:
            same = [m for m in earlier if m["local_date"] == day]
            if len(same) > 1:  # "Minutes: May 20, 2026, Special Meeting" next to the regular meeting's item
                same = [m for m in same if qualifiers(m["title"]) == qualifiers(item["title"])]
            if len(same) == 1:
                found.append(same[0])
        return found
    # Undated: words other than "approval of the draft regular meeting minutes" must
    # all appear in the later meeting's title ("City Council Meeting Minutes" on a City
    # Council agenda), so another body's minutes are never taken for this body's.
    rest = _words(UNDATED_WORDS.sub(" ", item["title"]))
    if rest - _words(item["meeting_title"]) or not earlier:
        return []
    previous = max(m["local_date"] for m in earlier)
    same = [m for m in earlier if m["local_date"] == previous]
    if len(same) != 1 or not timedelta(days=UNDATED_MIN_GAP_DAYS) <= later - previous <= timedelta(days=UNDATED_MAX_GAP_DAYS):
        return []
    return same


def following_text(page_text: str, quote: str, next_title: str | None) -> str:
    """Agenda text after an item's cited line, up to the next item's title or FOLLOWING_CHARS.

    Sub-items often carry the dates: "1. Minutes a. Parks and Recreation Committee –
    September 8, 2026 … c. Board of Directors Meeting – September 18, 2026".
    """
    text = re.sub(r"\s+", " ", page_text or "")
    quote = re.sub(r"\s+", " ", quote or "").strip()
    start = text.find(quote) if quote else -1
    if start < 0:
        return ""
    rest = text[start + len(quote):start + len(quote) + FOLLOWING_CHARS]
    if next_title and len(next_title.strip()) >= 15:  # a short title ("Finance") can be a sub-item's first word
        cut = rest.find(re.sub(r"\s+", " ", next_title).strip()[:40])
        if cut >= 0:
            rest = rest[:cut]
    return rest


def minutes_heading(head: str, day: date) -> bool:
    """True when a page's opening text is the start of minutes of ``day``.

    The word "minutes" must come early and not after "agenda" or an approval verb, so an
    agenda page listing "Approve the Regular Meeting Minutes of August 11, 2026" is not taken
    for the minutes themselves.
    """
    found = MINUTES_ITEM.search(head[:HEAD_CHARS])
    if not found:
        return False
    before = head[:found.start()]
    if re.search(r"\bagenda\b", before, re.I) or re.search(r"\b(?:approv\w*|adopt\w*)\b(?:\W+\w+){0,6}\W*$", before, re.I):
        return False
    return day in dates_in(head)


def _minutes_page(pages: list[dict], day: date, agenda_pages: set) -> dict | None:
    """The first packet page outside the agenda that opens the minutes of ``day``."""
    for page in sorted(pages, key=lambda p: (p["page_number"], p["version_id"])):
        if (page["version_id"], page["page_number"]) in agenda_pages or page["media_type"] != "application/pdf":
            continue
        if minutes_heading(page["head"], day):
            return page
    return None


def find_draft_minutes(conn, *, since: date) -> tuple[dict[str, dict], dict[str, dict]]:
    """(meeting ID → draft-minutes links, stored meetings by ID) from agendas of meetings dated ``since`` or later.

    Five queries in all, whatever the number of meetings.
    """
    from .core import stored_page_texts
    items = list(conn.execute("""SELECT i.id,i.meeting_id,i.number,i.item_order,i.title,i.citations FROM agenda_items i
        WHERE i.active AND i.meeting_id IN (SELECT i2.meeting_id FROM agenda_items i2 JOIN meetings m ON m.id=i2.meeting_id
          WHERE i2.active AND i2.title ~* '\\mminutes\\M' AND m.local_date >= %s AND m.quarantine_reason IS NULL)""", (since,)))
    if not items:
        return {}, {}
    meetings = {m["id"]: dict(m) for m in conn.execute("""SELECT id,agency_id,title,local_date,status,official_url,
        minutes_url,minutes_status,minutes_note,recording_url FROM meetings
        WHERE quarantine_reason IS NULL AND local_date >= %s""", (since - timedelta(days=MAX_GAP_DAYS),))}
    later_ids = sorted({i["meeting_id"] for i in items if i["meeting_id"] in meetings})
    agenda_pages: dict[str, set] = {}
    for item in items:
        for citation in _json(item["citations"]):
            agenda_pages.setdefault(item["meeting_id"], set()).add((citation.get("documentId"), int(citation.get("page") or 0)))
    pages: dict[str, list[dict]] = {}
    # Extracted text keeps layout whitespace, so a page's opening words are read with spaces collapsed.
    for p in conn.execute("""SELECT * FROM (SELECT c.meeting_id,v.id AS version_id,v.url,v.media_type,p.page_number,
              left(regexp_replace(left(p.text,4000),'\\s+',' ','g'),%s) AS head
            FROM document_current c JOIN document_versions v ON v.id=c.version_id
            JOIN document_pages p ON p.document_version_id=v.id WHERE c.meeting_id=ANY(%s)) pages
            WHERE head ~* '\\mminutes\\M'""", (HEAD_CHARS, later_ids)):
        pages.setdefault(p["meeting_id"], []).append(dict(p))
    cited = sorted({c.get("documentId") for i in items for c in _json(i["citations"]) if c.get("documentId")})
    versions = {v["id"]: v for v in conn.execute("SELECT id,url,media_type FROM document_versions WHERE id=ANY(%s)", (cited,))}

    minutes_items = [i for i in items if MINUTES_ITEM.search(i["title"])]
    first_citation = {i["id"]: _json(i["citations"])[0] for i in minutes_items if _json(i["citations"])}
    page_texts = stored_page_texts(conn, list(first_citation.values()))
    ordered: dict[str, list[dict]] = {}
    for item in sorted(items, key=lambda i: i["item_order"]):
        ordered.setdefault(item["meeting_id"], []).append(item)

    by_agency: dict[str, list[dict]] = {}
    for m in meetings.values():
        by_agency.setdefault(m["agency_id"], []).append(m)
    chosen: dict[str, tuple] = {}
    for item in sorted(minutes_items, key=lambda i: i["id"]):
        later = meetings.get(item["meeting_id"])
        if not later or not later["local_date"] or later["local_date"] < since:
            continue
        siblings = ordered[item["meeting_id"]]
        after = next((s["title"] for s in siblings[siblings.index(item) + 1:]), None)
        citation = first_citation.get(item["id"])
        following = following_text(page_texts.get((citation["documentId"], int(citation["page"])), ""),
                                   citation.get("quote", ""), after) if citation else ""
        context = {**item, "local_date": later["local_date"], "meeting_title": later["title"], "following": following}
        for target in minutes_targets(context, by_agency.get(later["agency_id"], [])):
            key = (later["local_date"], later["id"], item["id"])
            if target["id"] in chosen and chosen[target["id"]][0] <= key:
                continue
            chosen[target["id"]] = (key, _link(item, later, target, pages.get(later["id"], []),
                                               agenda_pages.get(later["id"], set()), versions))
    return {mid: links for mid, (_, links) in chosen.items()}, meetings


def _link(item, later, target, pages, agenda_pages, versions) -> dict:
    when = _long(later["local_date"])
    page = _minutes_page(pages, target["local_date"], agenda_pages)
    if page:
        return {"minutes_url": f"{page['url']}#page={page['page_number']}", "minutes_status": "draft",
                "minutes_note": f"In the {when} agenda packet, page {page['page_number']}"}
    label = f"Item {item['number']} on the {when} agenda" if item["number"] else f"On the {when} agenda"
    citation = next(iter(_json(item["citations"])), None)
    version = versions.get(citation.get("documentId")) if citation else None
    if version:
        url = version["url"] + (f"#page={int(citation['page'])}" if version["media_type"] == "application/pdf" and citation.get("page") else "")
    else:
        url = later["official_url"]
    return {"minutes_url": url, "minutes_status": "draft", "minutes_note": label}


def link_draft_minutes(conn, *, since: date, dry_run: bool = False) -> dict:
    """Store draft-minutes links where no better minutes link exists."""
    found, meetings = find_draft_minutes(conn, since=since)
    updated = 0
    for meeting_id, links in sorted(found.items()):
        meeting = meetings[meeting_id]
        if dry_run:
            updated += merged(meeting, links) != {k: meeting.get(k) for k in FIELDS}
        elif apply_links(conn, meeting, links):
            updated += 1
    return {"matched": len(found), "updated": updated}
