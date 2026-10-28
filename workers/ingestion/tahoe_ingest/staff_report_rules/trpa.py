"""Tahoe Regional Planning Agency (``trpa-governing``, ``trpa-advisory``): each item's staff report, its attachments,
and the board packet.

Every page of a TRPA staff report carries a right-aligned footer naming the item, with
the board's number after any committee's ("OPERATIONS AND GOVERNANCE COMMITTEE AGENDA
ITEM NO. 5.A & CONSENT CALENDAR ITEM NO. 6.A", "AGENDA ITEM NO. 8.A."). Committee
numbers never count. A report may serve several items ("CONSENT CALENDAR ITEM NOS. 6.B
AND 6.C"); each of them maps to it.

1. The item's own file. The agenda PDF links each item's title to its staff report, and
   the meeting page lists the same file with descriptive text ("Agenda Item No. 7.B ...
   Staff Report") followed by its "Attachment A ..." and "Exhibit 1 ..." links. The file
   comes from the item's agenda row, or else from the one meeting-page link that names the
   item's board number (a "Staff Report" link preferred over a presentation). It is used
   when its first page's footer names the item: the link and the footer are the two
   signals. Its attachment list is that file plus the attachments that follow it on the
   meeting page. A link whose file name or text names another board item makes the item
   uncertain, and nothing is stored for it.
2. The packet. Items without a verified file of their own fall back to the meeting packet
   ("September 23 Governing Board Packet"), which opens with the agenda, so the agenda's
   Page column ("For Possible Action   73") is a PDF page index into it. The Page value on
   the item's own row and the footer on that packet page naming the item are the two
   signals; the report runs while the footer keeps naming the number. The packet is the
   stored packet document when discovery captured one, else the meeting page's packet
   link, and is used only after its first pages reproduce the stored agenda. A page
   stamped with another number, or a mis-encoded text layer, maps nothing.

Downloads per meeting: the agenda (for its links), the meeting page, at most
``MAX_REPORTS`` report files (first pages only), and the packet only when an item still
needs it and it is not stored.
"""
from __future__ import annotations

import re
import subprocess

from ..http import EndpointSkipped
from ..staff_reports import (LEADING_PAGES, Attachments, Context, Document, FetchFailed, Item, Mapping, Uncertain, leading,
                             long_date, norm_number, title_overlap, words)
from .slt import attachment_label

MAX_REPORTS = 15  # report files read per meeting; a meeting has a handful
MIN_AGENDA_OVERLAP = 0.9  # word Jaccard of each agenda page against the packet's page; measured 0.97-1.00
_HOST = re.compile(r"^https://www\.trpa\.gov/", re.I)
_BODY = {"trpa-governing": r"Governing\s+Board", "trpa-advisory": r"APC(?:\s+Meeting)?"}
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_PURPOSE = r"(?:For\s+Possible\s+Action|Informational(?:\s+Only)?|For\s+Discussion(?:\s+Only)?|Report)"
# The Page value ends the item's first row: one space may separate it from "Informational", "--" means no page.
_ROW_PAGE = re.compile(rf"\S\s+{_PURPOSE}\s+(\d{{1,4}}|--)\s*$|\S\s{{2,}}(\d{{1,4}}|--)\s*$")
_FOOTER = re.compile(
    r"(?P<who>(?:TMPO\s+)?CONSENT\s+CALENDAR(?:\s+AGENDA)?|COMM?ITTEE\s+AGENDA|AGENDA)\s+ITEM\s+NOS?\.\s*"
    r"(?P<nums>\d{1,2}\s*\.\s*[A-Z]\b(?:\s*(?:,|AND|&)\s*\d{1,2}\s*\.\s*[A-Z]\b)*)")
_STAMP = re.compile(r"^\s*(\d{1,4})(?:\s{4,}|\s*$)")
# Board numbers in a file name or link text, after _plain(): "CC-Item-No-6-A", "Consent Calendar Item Nos. 6.B and 6.C",
# "LC-5-A-CC-6-D", "Agenda-Item-8A-...". A bare "Agenda Item" counts only at the start (committee names come first).
_NUM = r"\b(\d{1,2}) ?([A-Z])(?![A-Z0-9])"
_NAMED_CC = re.compile(rf"\b(?:CC|CONSENT CALENDAR)(?: AGENDA)?(?: ITEM)?(?: NOS?)? {_NUM}((?: (?:AND )?\d{{1,2}} ?[A-Z](?![A-Z0-9]))*)")
_NAMED_BARE = re.compile(rf"^AGENDA ITEM(?: NOS?)? {_NUM}")
_NOT_REPORT = re.compile(r"presentation|public comments|minutes|^(?:attachment|exhibit)\b", re.I)
_ATTACHMENT = re.compile(r"^(?:attachment|exhibit)\b", re.I)


def map_trpa(ctx: Context) -> list[Mapping]:
    items = []
    for item in ctx.numbered():
        if re.search(r"\bminutes$", item.title.strip(), re.I) or re.search(r"minutes", item.section or "", re.I):
            ctx.skip(item, "minutes, not a staff report")
        else:
            items.append(item)
    if not items:
        return []
    agenda = _cited_agenda(ctx, items)
    fetched = ctx.read(agenda.url, links=True)
    if fetched["sha256"] != agenda.sha256:
        raise Uncertain("the agenda PDF changed since it was captured")
    page_links = _merged(ctx.html_links(ctx.meeting.official_url)) if ctx.meeting.official_url else []
    reasons: dict[str, str] = {}
    chosen = _choose_reports(ctx, items, fetched, page_links, reasons)
    reports = _read_reports(ctx, chosen, reasons)
    mappings, attachments = [], []
    for item in items:
        if item.id not in chosen or chosen[item.id]["url"] not in reports:
            continue
        link, report = chosen[item.id], reports[chosen[item.id]["url"]]
        first = report["pages"][0]["text"] if report["pages"] else ""
        board, footer = footer_numbers(first)
        if norm_number(item.number) not in board:
            reasons[item.id] = f"linked report {_file(link['url'])} footer does not name item {item.number}"
            continue
        attachments.append(Attachments(item.id, item.fingerprint, _attachment_files(link, page_links),
                                       {k: link[k] for k in ("agendaLink", "agendaPage", "meetingPageLink") if link.get(k)}
                                       | {"footer": footer}))
        if garbled(first):
            reasons[item.id] = f"linked report {_file(link['url'])} text is not readable"
            continue
        pages = {p["page_number"]: p["text"] for p in report["pages"]}
        evidence = {k: link[k] for k in ("agendaLink", "agendaPage", "meetingPageLink") if link.get(k)}
        evidence |= {"footer": footer, "pages": f"1-{report['page_count']}", "titleOverlap": round(title_overlap(item.title, first), 2)}
        mappings.append(Mapping(item.id, item.fingerprint, "staff-report", "Staff report", link["url"], 1, report["page_count"],
                                report["page_count"], report["sha256"], evidence, leading(pages, 1, report["page_count"])))
    ctx.attachments = attachments
    mapped = {m.item_id for m in mappings}
    rest = [i for i in items if i.id not in mapped and not reasons.get(i.id, "").startswith("uncertain:")]
    wanted = {}
    for item in rest:
        if (page := _row_page(ctx, item)) is not None:
            wanted[item.id] = page
        else:
            reasons.setdefault(item.id, "no staff report link and the agenda gives no packet page")
    if wanted:
        mappings += _packet_reports(ctx, [i for i in rest if i.id in wanted], wanted, agenda, page_links, reasons)
    mapped = {m.item_id for m in mappings}
    for item in items:
        if item.id not in mapped:
            ctx.skip(item, reasons.get(item.id, "no staff report found").removeprefix("uncertain: "))
    return mappings


# --- The item's own report file ----------------------------------------------------------

def _choose_reports(ctx: Context, items: list[Item], fetched: dict, page_links: list[dict], reasons: dict) -> dict:
    """``{item id: {"url", "agendaLink"?, "agendaPage"?, "meetingPageLink"?}}``: the one report file each item links."""
    texts: dict[str, list] = {}
    named: dict[str, set] = {}
    for link in page_links:
        texts.setdefault(link["url"], []).append(link["text"])
        if _HOST.match(link["url"]) and re.search(r"\.pdf$", link["url"], re.I) and not _NOT_REPORT.search(link["text"]):
            for number in named_numbers(link["text"]):
                named.setdefault(number, set()).add(link["url"])
    chosen = {}
    for item in items:
        number = norm_number(item.number)
        row = _row_link(ctx, item, fetched)
        if row:
            link = {"url": row["url"], "agendaLink": row["text"], "agendaPage": row["page"]}
        else:
            found = named.get(number, set())
            if len(found) > 1:  # a staff report and its presentation: keep the one called a staff report
                found = {u for u in found if any(re.search(r"staff\s+report", t, re.I) for t in texts[u])}
            if len(found) != 1:
                continue
            link = {"url": next(iter(found))}
        if not _HOST.match(link["url"]):
            continue
        page_texts = [t for t in texts.get(link["url"], []) if not _ATTACHMENT.match(t)]
        if page_texts:
            link["meetingPageLink"] = max(page_texts, key=len)
        others = set().union(named_numbers(_plain_name(link["url"])), *(named_numbers(t) for t in page_texts))
        if others and number not in others:
            reasons[item.id] = f"uncertain: the linked file names item {', '.join(sorted(others))}, not {item.number}"
            continue
        chosen[item.id] = link
    return chosen


def _row_link(ctx: Context, item: Item, fetched: dict) -> dict | None:
    """The PDF link on the item's own title in the agenda: a link box on the cited page whose text is the title's first line.

    When several files' links start with the same words, the row letter printed level with
    the link decides.
    """
    letter = item.number.strip().rstrip(".").split(".")[-1]
    for citation in item.citations:
        quote = citation["quote"].strip()
        first = words(re.sub(r"\s{2,}\S.*$", "", quote.splitlines()[0].strip())) if quote else []
        page = int(citation["page"])
        if len(first) < 2:
            continue
        hits = [b for b in fetched.get("links", []) if b["page"] == page and b["text"] and re.search(r"\.pdf$", b["url"], re.I)
                and _same_start(words(b["text"]), first)]
        if len({b["url"] for b in hits}) > 1:
            marks = [b for b in fetched.get("boxes", []) + fetched.get("links", []) if b["page"] == page and b["text"] == letter]
            hits = [b for b in hits if any(abs(m["top"] - b["top"]) <= 4 and m["left"] < b["left"] for m in marks)]
        urls = {b["url"] for b in hits}
        if len(urls) == 1:
            box = hits[0]
            row, top, title = [], box["top"], set(words(item.title))
            for b in sorted((b for b in fetched["links"] if b["url"] == box["url"] and b["page"] == page and b["text"]
                             and b["top"] >= box["top"]), key=lambda b: (b["top"], b["left"])):
                # The title's wrapped lines; the next row (which may link the same file) has words of another title.
                if row and (b["top"] - top > 25 or not set(words(b["text"])) <= title):
                    break
                row.append(b["text"])
                top = b["top"]
            return {"url": box["url"], "text": " ".join(" ".join(row).split()), "page": page}
    return None


def _same_start(box: list[str], first: list[str]) -> bool:
    shorter = min(len(box), len(first))
    return shorter >= 2 and box[:shorter] == first[:shorter] and (box == first or shorter >= 3)


def _read_reports(ctx: Context, chosen: dict, reasons: dict) -> dict:
    """First pages of each chosen file, read once; failures leave the items to the packet."""
    reports, urls = {}, list(dict.fromkeys(link["url"] for link in chosen.values()))
    for url in urls[:MAX_REPORTS]:
        try:
            report = ctx.read(url, last_page=LEADING_PAGES)
        except (FetchFailed, EndpointSkipped) as exc:
            ctx.fetch_failures.append(str(exc)[:300])
            continue
        except (Uncertain, subprocess.SubprocessError):
            continue
        reports[url] = report
    for item_id, link in chosen.items():
        if link["url"] not in reports:
            reasons.setdefault(item_id, f"linked report {_file(link['url'])} could not be read"
                               if link["url"] in urls[:MAX_REPORTS] else "more report files than the per-meeting limit")
    return reports


def _attachment_files(link: dict, page_links: list[dict]) -> list[dict]:
    """The report itself, then the "Attachment ..."/"Exhibit ..." links that follow it on the meeting page."""
    files = [{"url": link["url"], "name": link.get("meetingPageLink") or _file(link["url"]), "label": "Staff report"}]
    seen = {link["url"]}
    for n, entry in enumerate(page_links):
        if entry["url"] != link["url"]:
            continue
        for after in page_links[n + 1:]:
            if not _ATTACHMENT.match(after["text"]):
                break
            if _HOST.match(after["url"]) and after["url"] not in seen:
                seen.add(after["url"])
                label = re.sub(r":\s+to\b", " to", attachment_label(after["text"]))
                files.append({"url": after["url"], "name": after["text"], "label": label})
    return files


def _merged(links: list[dict]) -> list[dict]:
    """Meeting-page links in order, with adjacent anchors to the same file joined ("Consent Calendar Item No. 6.A &" ...)."""
    merged = []
    for link in links:
        text = " ".join(link["text"].split())
        if merged and merged[-1]["url"] == link["url"]:
            merged[-1]["text"] = f"{merged[-1]['text']} {text}".strip()
        else:
            merged.append({"url": link["url"], "text": text})
    return merged


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", text).upper().split())


def _plain_name(url: str) -> str:
    return re.sub(r"\.pdf$", "", _file(url), flags=re.I)


def _file(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def named_numbers(text: str) -> set[str]:
    """Board item numbers a file name or link text names: consent calendar numbers anywhere, a bare "Agenda Item" at the start."""
    plain = _plain(text)
    found = set()
    for match in _NAMED_CC.finditer(plain):
        found.add(f"{match[1]}.{match[2]}")
        found.update(f"{a}.{b}" for a, b in re.findall(r"(\d{1,2}) ?([A-Z])(?![A-Z0-9])", match[3]))
    if bare := _NAMED_BARE.match(plain):
        found.add(f"{bare[1]}.{bare[2]}")
    return found


# --- The packet ------------------------------------------------------------------------------

def _packet_reports(ctx: Context, items: list[Item], wanted: dict, agenda: Document, page_links: list[dict],
                    reasons: dict) -> list[Mapping]:
    stored_agenda = ctx.stored_pages(agenda.version_id)
    try:
        if not stored_agenda:
            raise Uncertain("the cited agenda has no stored pages to check the packet against")
        name, url, sha256, count, pages = _packet(ctx, agenda, page_links, set(wanted.values()) | set(stored_agenda))
        if url != agenda.url:
            for number, text in stored_agenda.items():
                if _jaccard(text, pages.get(number, "")) < MIN_AGENDA_OVERLAP:
                    raise Uncertain(f"the packet's page {number} is not the captured agenda's page {number}")
    except (Uncertain, subprocess.SubprocessError) as exc:
        for item in items:
            reasons.setdefault(item.id, str(exc))
        return []
    mappings = []
    for item in items:
        start, label = wanted[item.id], norm_number(item.number)
        text = pages.get(start, "")
        board, footer = footer_numbers(text)
        stamps = _stamps(text)
        if not text.strip():
            problem = f"packet has no text on page {start}"
        elif stamps and start not in stamps:
            problem = f"packet page {start} is stamped {sorted(stamps)[0]}"
        elif label not in board:
            problem = f"packet page {start} footer does not name item {item.number}"
        elif garbled(text):
            problem = f"packet page {start} text is not readable"
        else:
            problem = None
        if problem:
            reasons[item.id] = problem if item.id not in reasons else f"{reasons[item.id]}; {problem}"
            continue
        end = start
        while label in footer_numbers(pages.get(end + 1, ""))[0]:
            end += 1
        evidence = {"packetLink": name, "agendaPageColumn": start, "footer": footer, "pages": f"{start}-{end}",
                    "titleOverlap": round(title_overlap(item.title, text), 2)}
        if stamps:
            evidence["packetStamp"] = str(start)
        mappings.append(Mapping(item.id, item.fingerprint, "staff-report", "Staff report in the board packet", url, start, end,
                                count, sha256, evidence, leading(pages, start, end)))
    return mappings


def _packet(ctx: Context, agenda: Document, page_links: list[dict], needed: set[int]) -> tuple:
    """``(name, url, sha256, page_count, pages)``: the stored packet document, or else the meeting page's packet link."""
    stored = [d for d in ctx.meeting.documents if d.media_type == "application/pdf" and d.version_id != agenda.version_id
              and re.search(r"packet", d.source_document_id + " " + d.title, re.I)]
    if len(stored) > 1:
        raise Uncertain("more than one stored packet")
    if stored:
        packet = stored[0]
        pages = ctx.pages(packet, needed=lambda texts: needed <= set(texts))
        return packet.title, packet.url, packet.sha256, packet.page_count, pages
    link = _packet_link(ctx, page_links)
    fetched = ctx.read(link["url"])
    return (link["text"], link["url"], fetched["sha256"], fetched["page_count"],
            {p["page_number"]: p["text"] for p in fetched["pages"]})


def _packet_link(ctx: Context, page_links: list[dict]) -> dict:
    """The one "<date> Governing Board Packet" / "<date> APC Meeting Packet" link on the meeting page."""
    if not ctx.meeting.official_url:
        raise Uncertain("meeting has no official page to find the packet on")
    body = _BODY.get(ctx.meeting.agency_id, r"Governing\s+Board|APC(?:\s+Meeting)?")
    name = re.compile(rf"^(?:(?P<month>{_MONTHS})\s+(?P<day>\d{{1,2}}),?(?:\s+(?P<year>\d{{4}}))?\s+)?(?:{body})\s+Packet$", re.I)
    found = {}
    for link in page_links:
        match = name.match(link["text"])
        if not match or not re.search(r"\.pdf(?:$|[?#])", link["url"], re.I):
            continue
        day = ctx.meeting.local_date
        if match["month"] and day and (f"{match['month']} {int(match['day'])}".casefold() != f"{day:%B} {day.day}".casefold()
                                       or (match["year"] and int(match["year"]) != day.year)):
            raise Uncertain(f"the meeting page's packet is dated {link['text']}, not {long_date(day)}")
        found.setdefault(link["url"], link["text"])
    if len(found) != 1:
        raise Uncertain(f"{len(found)} packet links on the meeting page")
    url, text = next(iter(found.items()))
    return {"url": url, "text": text}


# --- Shared checks -----------------------------------------------------------------------------

def _cited_agenda(ctx: Context, items: list[Item]) -> Document:
    """The agenda the items cite. A meeting may also carry a presentation captured as an agenda, so not "the only PDF"."""
    cited = {c["documentId"] for item in items for c in item.citations}
    found = [d for d in ctx.meeting.documents if d.version_id in cited and d.media_type == "application/pdf"]
    if len(found) != 1:
        raise Uncertain("items do not cite one current agenda PDF")
    return found[0]


def _row_page(ctx: Context, item: Item) -> int | None:
    """The Page value on the item's own row of its cited agenda page: the row letter followed by the title's first line."""
    letter = re.escape(item.number.strip().rstrip(".").split(".")[-1])
    for citation in item.citations:
        quote = citation["quote"].strip()
        first = re.sub(r"\s{2,}\S.*$", "", quote.splitlines()[0].strip()) if quote else ""
        if not first:
            continue
        row = re.compile(rf"\s*{letter}\s+{re.escape(first)}")
        for line in ctx.stored_pages(citation["documentId"]).get(int(citation["page"]), "").splitlines():
            if row.match(line):
                value = _ROW_PAGE.search(line)
                value = value and (value.group(1) or value.group(2))
                return int(value) if value and value != "--" else None
    return None


def footer_numbers(text: str) -> tuple[set[str], str]:
    """Board item numbers the page footer names, and the footer as printed (whitespace collapsed).

    The footer is right-aligned and may wrap over two or three lines, so each of the last
    six lines contributes its part after the last run of four or more spaces. Committee
    numbers ("LEGAL COMMITTEE AGENDA ITEM NO. 5.A", including the "COMITTEE" misspelling)
    are left out; a bare "AGENDA ITEM NO." and any consent calendar number count.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()][-6:]
    parts = [re.split(r"\s{4,}", line)[-1] for line in lines]
    joined = " ".join(re.sub(r"^\d{1,4}$", "", part) for part in parts)
    board, printed = set(), []
    for match in _FOOTER.finditer(joined):
        printed.append(" ".join(match.group().split()))
        if "COM" not in match["who"]:
            board.update(re.sub(r"\s", "", n) for n in re.findall(r"\d{1,2}\s*\.\s*[A-Z]\b", match["nums"]))
    return board, " & ".join(printed)


def _stamps(text: str) -> set[int]:
    """Bare page numbers printed at the foot of the page (alone, or set off from the footer by wide spacing)."""
    lines = [line for line in text.splitlines() if line.strip()][-3:]
    return {int(m.group(1)) for line in lines if (m := _STAMP.match(line))}


def garbled(text: str) -> bool:
    """True when under half the page's visible characters are ASCII letters (a text layer in a shifted glyph map)."""
    visible = [c for c in text if not c.isspace()]
    return sum(c.isascii() and c.isalpha() for c in visible) < 0.5 * max(1, len(visible))


def _jaccard(a: str, b: str) -> float:
    first, second = set(words(a)), set(words(b))
    if not first and not second:
        return 1.0
    return len(first & second) / len(first | second)


RULES = {"trpa-governing": map_trpa, "trpa-advisory": map_trpa}
