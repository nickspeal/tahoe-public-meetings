"""Conservative agenda segmentation.

TTD, SLT, and Truckee keep their original hand-written parsers below; other
agencies use the declarative layouts in ``agenda_layouts`` (or the custom
parsers in ``agenda_custom``). Only a complete, sequential agenda is returned
for automatic publication. The title quote is copied exactly from one
extracted page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .agenda_custom import CUSTOM_PARSERS
from .agenda_layouts import LAYOUTS
from .outline import cover_matches, parse_layout, title_case


@dataclass
class ParseResult:
    items: list[dict]
    complete: bool
    warning: str | None = None


@dataclass
class Line:
    page: int
    text: str
    start: int
    end: int
    source: str


def _lines(pages: list[dict], *, first_pages: int = 12) -> list[Line]:
    rows = []
    for page in sorted(pages, key=lambda p: int(p["page_number"]))[:first_pages]:
        text = page["text"]
        for match in re.finditer(r"[^\n]+", text):
            if match.group().strip():
                rows.append(Line(int(page["page_number"]), match.group(), match.start(), match.end(), text))
    return rows


def _quote(lines: list[Line], start: int, end: int, prefix_start: int | None = None) -> str:
    first, last = lines[start], lines[end]
    if first.page != last.page:
        return first.text[(prefix_start or 0):].strip()[:300]
    return first.source[(first.start + (prefix_start or 0)):last.end].strip()[:300]


def _title(lines: list[Line], first: int, last: int, offset: int = 0) -> str:
    parts = [lines[first].text[offset:].strip()]
    for line in lines[first + 1:last + 1]:
        if line.page != lines[first].page:
            break
        parts.append(line.text.strip())
    return " ".join(" ".join(parts).split())


def _item(number: str | None, order: int, section: str | None, title: str,
          document_id: str, page: int, quote: str) -> dict:
    return {"source_number": number, "order": order, "section": section, "title": title,
            "source_pages": [{"document_id": document_id, "page_number": page, "quote": quote}]}


def _result(items: list[dict], complete: bool, warning: str | None = None) -> ParseResult:
    if not complete or not items or any(not i["source_pages"][0]["quote"] for i in items):
        return ParseResult([], False, warning or "Agenda structure or source quote could not be validated; items require review.")
    return ParseResult(items, True)


def parse_slt(document_id: str, pages: list[dict]) -> ParseResult:
    head = pages[0]["text"] if pages else ""
    if "City of South Lake Tahoe" not in head or "Meeting Agenda" not in head:
        return _result([], False, "South Lake Tahoe agenda cover was not recognized; items require review.")
    lines = _lines(pages)
    headings = {
        "Call to Order/Pledge of Allegiance", "Roll Call", "Closed Session Report", "Proclamations",
        "Public Communications", "City Commission Reports", "Consent Agenda", "Regular-Unfinished Business",
        "Regular-New Business", "Agenda Planning Calendar", "Updates and Announcements",
        "Adjournment", "Presentations", "Public Hearing", "Public Hearings", "Closed Session",
        # Special meetings use shorter headings.
        "Call to Order / Pledge of Allegiance", "Consent", "New Business", "Unfinished Business",
    }
    items = []
    section = "Opening"
    numbered = []
    seen_agenda = False
    for idx, line in enumerate(lines):
        content = line.text.strip()
        number_match = re.match(r"^(\d{1,2})\.\s+(.+)", content)
        number_only = re.fullmatch(r"(\d{1,2})\.", content)
        if number_match or number_only:
            num = int((number_match or number_only).group(1))
            if not 1 <= num <= 100 or (numbered and num != numbered[-1] + 1) or (not numbered and num != 1):
                return _result([], False, "South Lake Tahoe agenda numbering is not sequential; review required.")
            title_idx = idx
            first_title = number_match.group(2) if number_match else ""
            if number_only:
                title_idx = idx + 1
                if title_idx >= len(lines) or lines[title_idx].page != line.page:
                    return _result([], False, "South Lake Tahoe standalone item number has no same-page title; review required.")
                first_title = lines[title_idx].text.strip()
                if (not first_title or first_title in headings or
                        re.match(r"^(?:\d{1,2}\.\s*|Requested Action|Responsible Staff|CEQA Determination)", first_title, re.I)):
                    return _result([], False, "South Lake Tahoe standalone item title was not recognized; review required.")
            numbered.append(num)
            start_offset = lines[title_idx].text.index(first_title)
            end = title_idx
            for j in range(title_idx + 1, len(lines)):
                next_line = lines[j]
                if next_line.page != lines[title_idx].page:
                    break
                next_text = next_line.text.strip()
                if re.match(r"^\d{1,2}\.\s*", next_text) or next_text in headings:
                    break
                if re.match(r"^(Requested Action|Responsible Staff|CEQA Determination|Item \d+\s*[-–]|\d{2}[-_])", next_text, re.I):
                    break
                end = j
            title = _title(lines, title_idx, end, start_offset)
            quote = _quote(lines, title_idx, end, start_offset)
            items.append(_item(str(num), len(items) + 1, section, title, document_id, lines[title_idx].page, quote))
            seen_agenda = True
            continue
        if content.startswith("Councilmember Reports and Comments"):
            title = "Councilmember Reports and Comments - Assignments to Boards, Committees, and Commissions"
            items.append(_item(None, len(items) + 1, title, title, document_id, line.page, content))
            continue
        if content.startswith("Updates and Announcements"):
            items.append(_item(None, len(items) + 1, content, content, document_id, line.page, content))
            continue
        if content in headings:
            if content in {"Proclamations", "Presentations", "Public Communications", "City Commission Reports",
                           "Consent Agenda", "Consent", "Public Hearing", "Public Hearings", "Regular-Unfinished Business",
                           "Unfinished Business", "Regular-New Business", "New Business", "Agenda Planning Calendar",
                           "Closed Session", "Adjournment"}:
                section = content
            items.append(_item(None, len(items) + 1, section, content, document_id, line.page, content))
            if content == "Adjournment" and seen_agenda:
                break
    complete = bool(numbered and items and items[-1]["title"] == "Adjournment" and numbered == list(range(1, numbered[-1] + 1)))
    return _result(items, complete, "South Lake Tahoe agenda ending or numbered sequence was not found; review required.")


def parse_truckee(document_id: str, pages: list[dict]) -> ParseResult:
    head = pages[0]["text"] if pages else ""
    if "Agenda" not in head or "Truckee" not in head or "Town Council" not in head:
        return _result([], False, "Truckee Town Council agenda cover was not recognized; items require review.")
    lines = _lines(pages, first_pages=8)
    items = []
    top_numbers = []
    section = "Agenda"
    started = False
    for idx, line in enumerate(lines):
        content = line.text.strip()
        match = re.match(r"^(\d+(?:\.\d+)?)\.?\s{2,}(.+)", content)
        if not match:
            continue
        number, first_title = match.groups()
        # Restrict to the leading agenda, before the first adjournment.
        if "." not in number:
            n = int(number)
            if n > 100 or (top_numbers and n != top_numbers[-1] + 1) or (not top_numbers and n != 1):
                if started:
                    return _result([], False, "Truckee agenda numbering is not sequential; review required.")
                continue
            top_numbers.append(n)
        elif not top_numbers or not number.startswith(str(top_numbers[-1]) + "."):
            return _result([], False, "Truckee subitem does not follow its section; review required.")
        started = True
        offset = line.text.index(first_title)
        end = idx
        if (not first_title.rstrip().endswith((".", ":"))
                and not first_title.lower().startswith(("adjournment", "public comment:"))):
            for j in range(idx + 1, len(lines)):
                candidate = lines[j]
                if candidate.page != line.page:
                    break
                next_text = candidate.text.strip()
                if re.match(r"^\d+(?:\.\d+)?\.?\s{2,}", next_text) or re.match(r"^(Recommendation:|Town of Truckee:|Tahoe Forest Hospital District:)", next_text):
                    break
                end = j
                if next_text.endswith((".", ":")):
                    break
        title = _title(lines, idx, end, offset).rstrip(":")
        if title.startswith("Public Comment:"):
            title = "Public Comment"
        quote = _quote(lines, idx, end, offset)
        if number == "5" or title.lower() == "joint meeting":
            section = "Joint Meeting"
        items.append(_item(number, len(items) + 1, section if number.startswith("5") else "Agenda",
                           title, document_id, line.page, quote))
        if title.lower().startswith("adjournment"):
            break
    complete = bool(top_numbers and top_numbers == list(range(1, top_numbers[-1] + 1))
                    and items and items[-1]["title"].lower().startswith("adjournment"))
    return _result(items, complete, "Truckee agenda ending or numbered sequence was not found; review required.")


ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9,
         "X": 10, "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15}


def _ttd_title_part(raw: str) -> str:
    # TTD puts packet page numbers in a right-aligned column, sometimes on a
    # wrapped title line. Strip that column before joining lines into a title.
    return re.sub(r"\s{2,}(?:\d{1,3}|Page(?:\s+\d{1,3})?)$", "", raw).strip()


def parse_ttd(document_id: str, pages: list[dict]) -> ParseResult:
    head = pages[0]["text"] if pages else ""
    if "TAHOE TRANSPORTATION DISTRICT" not in head or "Notice of Agenda" not in head:
        return _result([], False, "TTD agenda cover was not recognized; items require review.")
    lines = _lines(pages, first_pages=6)
    entries = []
    section = None
    section_title = None
    roman_numbers = []
    started = False
    for idx, line in enumerate(lines):
        content = line.text.strip()
        roman = re.match(r"^(I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV)\.\s+(.+)", content)
        if roman:
            numeral, title_start = roman.groups()
            value = ROMAN[numeral]
            if roman_numbers and value != roman_numbers[-1] + 1:
                return _result([], False, "TTD agenda section numbering is not sequential; review required.")
            if not roman_numbers and value != 1:
                return _result([], False, "TTD agenda does not start with section I; review required.")
            roman_numbers.append(value)
            section = numeral
            start_offset = line.text.index(title_start)
            end = idx
            for j in range(idx + 1, len(lines)):
                if "ADJOURNMENT" in title_start.upper() or title_start.upper().strip() == "PUBLIC INTEREST COMMENTS":
                    break
                other = lines[j]
                if other.page != line.page:
                    break
                value_text = other.text.strip()
                if value_text.startswith(("At this time", "COMPLIANCE WITH")):
                    break
                if re.match(r"^(?:[A-Z]|[IVX]{1,5})\.\s+", value_text) or value_text.startswith("Agenda Page"):
                    break
                if value_text and value_text == value_text.upper() and len(value_text) < 8:
                    break
                end = j
                if len(value_text) > 140:
                    break
            section_title = " ".join(filter(None, (
                _ttd_title_part(part.text[start_offset:] if part is line else part.text)
                for part in lines[idx:end + 1] if part.page == line.page)))
            section_title = " ".join(section_title.split())
            entries.append(("section", numeral, section_title, line.page, _quote(lines, idx, end, start_offset)))
            started = True
            if "ADJOURNMENT" in section_title.upper():
                break
            continue
        if started and section and re.match(r"^[A-Z]\.\s+", content):
            match = re.match(r"^([A-Z])\.\s+(.+)", content)
            letter, first_title = match.groups()
            offset = line.text.index(first_title)
            end = idx
            for j in range(idx + 1, len(lines)):
                other = lines[j]
                if other.page != line.page:
                    break
                value_text = other.text.strip()
                if re.match(r"^(?:[A-Z]|[IVX]{1,5})\.\s+", value_text) or value_text.startswith("Agenda Page"):
                    break
                if re.fullmatch(r"\d{1,3}", value_text):
                    continue
                end = j
            pieces = []
            for part in lines[idx:end + 1]:
                if part.page != line.page:
                    break
                text = part.text[offset:] if part is line else part.text
                text = _ttd_title_part(text)
                if text not in {"Page", ""} and not re.fullmatch(r"\d{1,3}", text):
                    pieces.append(text)
            title = " ".join(" ".join(pieces).split())
            title = re.sub(r"^For Possible Action:\s*", "", title, flags=re.I)
            title = re.sub(r"\s+\d{1,3}$", "", title)
            entries.append(("item", f"{section}.{letter}", title, line.page, _quote(lines, idx, end, offset)))
    if not roman_numbers or not entries or "ADJOURNMENT" not in entries[-1][2].upper():
        return _result([], False, "TTD agenda ending was not found; review required.")
    sections_with_children = {number.split(".")[0] for kind, number, _, _, _ in entries if kind == "item"}
    items = []
    current_section_title = None
    for kind, number, title, page, quote in entries:
        if kind == "section":
            current_section_title = title_case(title)
            if number in sections_with_children:
                continue
        items.append(_item(number, len(items) + 1, current_section_title, title, document_id, page, quote))
    return _result(items, len(items) >= 5 and roman_numbers == list(range(1, roman_numbers[-1] + 1)))


LEGACY_PARSERS = {"ttd": parse_ttd, "slt": parse_slt, "truckee": parse_truckee}
LEGACY_COVERS = {"ttd": "TAHOE TRANSPORTATION DISTRICT", "slt": "City of South Lake Tahoe", "truckee": "Town Council"}


def has_parser(agency: str) -> bool:
    return agency in LEGACY_PARSERS or agency in LAYOUTS or agency in CUSTOM_PARSERS


def _parse_document(agency: str, document_id: str, pages: list[dict]) -> tuple[ParseResult, bool]:
    """Parse one document; the flag says whether it looked like this agency's agenda."""
    if agency in LEGACY_PARSERS:
        result = LEGACY_PARSERS[agency](document_id, pages)
        head = pages[0]["text"]
        return result, result.complete or ("Agenda" in head and LEGACY_COVERS[agency] in head)
    if agency in CUSTOM_PARSERS:
        items, warning = CUSTOM_PARSERS[agency](document_id, pages)
        return _result(items, bool(items), warning), bool(items) or "cover was not recognized" not in (warning or "")
    failure = None
    for layout in LAYOUTS[agency]:
        if not cover_matches(layout, pages):
            continue
        items, warning = parse_layout(layout, document_id, pages)
        if items:
            return _result(items, True), True
        failure = failure or warning
    if failure:
        return _result([], False, failure), True
    return _result([], False, f"{LAYOUTS[agency][0].name} cover was not recognized; agenda items require review."), False


def parse_agenda(agency: str, documents: list[dict], pages: list[dict]) -> ParseResult:
    if not has_parser(agency):
        return _result([], False, f"No parser exists for agency {agency}; review required.")
    successes = []
    failures = []
    for document in documents:
        doc_pages = [p for p in pages if p["document_id"] == document["id"]]
        if not doc_pages:
            continue
        doc_pages = sorted(doc_pages, key=lambda p: p["page_number"])
        result, recognized = _parse_document(agency, document["id"], doc_pages)
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
