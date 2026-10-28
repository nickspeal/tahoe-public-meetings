"""Rule ``slt-executive-summary``: South Lake Tahoe City Council (Granicus with Legistar files), one executive summary
PDF per item, and its attachments.

The agenda PDF links "Item N- Executive Summary.pdf" for each item. That file opens with
an "Agenda Item Executive Summary" page that names the item number and title, followed
by the staff report and attachments. Every other file the agenda links under an item is
kept as that item's attachment list.
"""
from __future__ import annotations

import re

from ..staff_reports import (MIN_TITLE_OVERLAP, Attachments, Context, Mapping, Uncertain, clean_text, linked_reports, long_date,
                             norm_number, pdf_agenda, title_overlap)


def map_slt(ctx: Context) -> list[Mapping]:
    agenda = pdf_agenda(ctx, prefer=r"agenda")
    fetched = ctx.read(agenda.url, last_page=1, links=True)
    if fetched["sha256"] != agenda.sha256:
        raise Uncertain("the agenda PDF changed since it was captured")
    ctx.attachments = slt_attachments(ctx, fetched, ctx.stored_pages(agenda.version_id))
    links: dict[str, set] = {}
    link_pages: dict[str, set] = {}
    for link in fetched["links"]:
        label = re.fullmatch(r"Item\s*(\d+)\s*-\s*Executive Summary\.pdf", link["text"], re.I)
        named = re.search(r"/Item_(\d+)-_Executive_Summary\.pdf$", link["url"], re.I)
        if label and named and label.group(1) == named.group(1):
            links.setdefault(label.group(1), set()).add(link["url"])
            link_pages.setdefault(link["url"], set()).add(link["page"])

    def next_to_item(item, url):
        """The agenda page the link sits on, when it is on (or just after) a page the item's title is cited on."""
        cited = {int(c["page"]) for c in item.citations}
        near = sorted(p for p in link_pages[url] if not cited or min(cited) <= p <= max(cited) + 1)
        return near[0] if near else None

    def precheck(item, url):
        return None if next_to_item(item, url) is not None else "executive summary link is not next to the item"

    def verify(item, report):
        first = report["pages"][0]["text"] if report["pages"] else ""
        if not re.search(r"Agenda Item Executive Summary", first, re.I) or not re.search(
                rf"Agenda Item #:\s*{re.escape(norm_number(item.number))}\b", first):
            return "executive summary does not name this item number"
        named_title = re.search(r"Agenda Item:\s*(.+?)\n\s*\n", first, re.S)
        if title_overlap(item.title, named_title.group(1) if named_title else first) < MIN_TITLE_OVERLAP:
            return "executive summary title differs from the agenda item"
        stated = re.search(r"Meeting Date:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", first)
        if stated and ctx.meeting.local_date and stated.group(1) != long_date(ctx.meeting.local_date):
            return "executive summary is for another meeting date"
        return {"agendaLink": f"Item {item.number}- Executive Summary.pdf", "agendaPage": next_to_item(item, report["url"]),
                "reportHeading": f"Agenda Item #:{item.number}"}
    return linked_reports(ctx, ctx.numbered(), links, verify=verify, precheck=precheck, kind="executive-summary",
                          title="Item {number} executive summary", missing="{count} executive summary links for item {number}")


_KINDS = re.compile(r"^(staff\s*report|SR\b|resolution|ordinance|presentation|proclamation|(?:attachment|exhibit|att)\s*[A-Z0-9]+\b)"
                    r"\s*[-–:]?\s*(.*)$", re.I)


def attachment_label(name: str) -> str:
    """A printed attachment file name made readable: "01-SR_Paradise_Ave_Abandonment.docx" -> "Staff report: Paradise Ave Abandonment"."""
    base = re.sub(r"\.(?:pdf|docx?|xlsx?|pptx?|csv|txt)$", "", name.strip(), flags=re.I).replace("_", " ")
    base = re.sub(r"^\s*Item\s*\d+\s*-\s*", "", base, flags=re.I)
    base = " ".join(re.sub(r"^\s*\d{1,2}\s*[-–.]\s*", "", base).split())
    kind = _KINDS.match(base)
    if not kind:
        return base or name
    head = kind.group(1)
    if re.fullmatch(r"staff\s*report|SR", head, re.I):
        head = "Staff report"
    elif re.match(r"(attachment|exhibit|att)\s*", head, re.I):
        word, ident = re.match(r"(attachment|exhibit|att)\s*(.+)", head, re.I).groups()
        head = f"{'Exhibit' if word.lower() == 'exhibit' else 'Attachment'} {ident}"
    else:
        head = head.capitalize()
    rest = kind.group(2).strip(" -–:")
    return f"{head}: {rest}" if rest else head


SLT_MARGIN = 110  # item numbers and section headings sit at the left margin; item text and links are indented


def slt_attachments(ctx: Context, fetched: dict, agenda_pages: dict[int, str]) -> list[Attachments]:
    """Each numbered item's attachment links: those between its "N." heading and the next left-margin heading.

    Every printed file name must also appear on that page of the stored agenda text, and an
    "Item M- Executive Summary.pdf" under item N (M != N) makes the whole item uncertain.
    """
    margin = [b for b in fetched.get("boxes", []) if b["left"] < SLT_MARGIN and b["text"]]
    headings: dict[str, list] = {}
    for box in margin:
        if number := re.fullmatch(r"(\d+)\.", box["text"]):
            headings.setdefault(number.group(1), []).append(box)
    page_text = {n: " ".join(clean_text(t).split()) for n, t in agenda_pages.items()}
    found = []
    for item in ctx.meeting.items:
        heads = headings.get(norm_number(item.number), []) if item.number.strip() else []
        if len(heads) != 1:
            continue
        start = (heads[0]["page"], heads[0]["top"])
        following = [(b["page"], b["top"]) for b in margin if (b["page"], b["top"]) > start]
        end = min(following) if following else (10**6, 0)
        files = []
        for link in fetched["links"]:
            if not start < (link["page"], link["top"]) < end:
                continue
            if files and files[-1]["url"] == link["url"]:  # a file name wrapped onto a second line
                files[-1]["name"] = f"{files[-1]['name']} {link['text']}".strip()
                continue
            files.append({"url": link["url"], "name": link["text"], "page": link["page"]})
        if not files:
            continue
        problem = None
        for f in files:
            other = re.match(r"Item\s*(\d+)\s*-\s*Executive Summary", f["name"], re.I)
            if not f["name"] or not f["url"].startswith("https://"):
                problem = "an attachment link has no printed name"
            elif other and other.group(1) != norm_number(item.number):
                problem = f"lists the executive summary of item {other.group(1)}"
            elif f["name"] not in page_text.get(f["page"], ""):
                problem = f"“{f['name']}” is not printed on agenda page {f['page']}"
            if problem:
                break
        if problem:
            ctx.skip(item, f"attachments: {problem}")
            continue
        found.append(Attachments(item.id, item.fingerprint, [
            {"url": f["url"], "name": f["name"], "label": attachment_label(f["name"])}
            for f in files], {"agendaPages": sorted({f["page"] for f in files}), "heading": f"{item.number}."}))
    return found


RULES = {"slt-executive-summary": map_slt}
