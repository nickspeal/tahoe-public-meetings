"""Meeting start times read from the first page of the meeting's own agenda.

Many discovery sources list only a date, while the agenda heading states the
time: "Wednesday, October 28, 2026 ... 9:15 a.m. TRPA Governing Board Meeting",
"DATE: Friday, August 21, 2026 TIME: 08:30 a.m.". This reads stored page text
at export time, like time-certain items, so no schema change or reparse is
needed and a revised agenda updates the time.

A time counts only when it is printed close to the meeting's own date on the
first page of an agenda document. That keeps out office hours, comment
deadlines, and sessions held on other days of a multi-day meeting. The first
such time is used; if two agenda documents disagree, no time is shown.

The same pairing gives the meeting date when a listing states none (some TTD
posts): the date beside the first page's start times, read once at capture.
"""
from __future__ import annotations

import re
from datetime import date

# An optional bare range start ("8:30-10:00 am") takes its meridiem from the end time.
TIME = re.compile(r"(?<![\d:])(?:(?P<h0>1[0-2]|0?[1-9])(?::(?P<m0>[0-5]\d))?\s*[-–—]\s*)?"
                  r"(?P<h>1[0-2]|0?[1-9])(?::(?P<m>[0-5]\d))?\s*(?P<ap>[ap])\.?\s?m\b\.?", re.I)
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december")
_MONTH = r"(?P<month>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATES = (re.compile(rf"\b{_MONTH}\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>20\d{{2}})\b", re.I),
         re.compile(r"(?<![\d/])(?P<mo>\d{1,2})/(?P<day>\d{1,2})/(?P<year>(?:20)?\d{2})(?![\d/])"))
NEAR = 150  # characters between a time and the date it belongs to
# Times that state a deadline, office hours, the end of a range, or a meal rather than a start.
NOT_START_BEFORE = re.compile(r"(?:\b(?:by|until|before|prior\s+to|later\s+than|earlier\s+than|hours\s+of|between|and|through|to)"
                              r"|\blunch\b[\s\S]{0,60}|\b[ap]\.?\s?m\b\.?\s*[-–—])\s*$", re.I)
NOT_START_AFTER = re.compile(r"^\s*\(?\s*no\s+earlier\s+than", re.I)
NOT_AGENDA = re.compile(r"minutes|synopsis|supplement|staff report|attachment|presentation|notice of decision", re.I)
COMMITTEE = re.compile(r"\b(?:sub)?committee\b", re.I)


def _dates(text: str) -> list[tuple[int, int, date]]:
    found = []
    for pattern in DATES:
        for match in pattern.finditer(text):
            try:
                month = (next(i for i, name in enumerate(MONTHS, 1) if name.startswith(match["month"].lower()[:3]))
                         if "month" in match.groupdict() else int(match["mo"]))
                year = int(match["year"]) + (2000 if len(match["year"]) == 2 else 0)
                found.append((match.start(), match.end(), date(year, month, int(match["day"]))))
            except ValueError:
                continue
    return found


def _start(match: re.Match) -> str:
    pm = match["ap"].lower() == "p"
    end = int(match["h"]) % 12 * 60 + int(match["m"] or 0)
    if match["h0"]:
        start = int(match["h0"]) % 12 * 60 + int(match["m0"] or 0)
        pm = pm and start <= end  # "11:30-1:00 pm" starts in the morning
    else:
        start = end
    minutes = start + (720 if pm else 0)
    return f"{minutes // 60:02}:{minutes % 60:02}"


def _heading_times(text: str):
    """Each start time on a page with the date printed nearest to it (within ``NEAR``), in page order."""
    text = " ".join(text.split())  # Column layouts pad the gap between a time and its date.
    dates = _dates(text)
    for match in TIME.finditer(text):
        if NOT_START_BEFORE.search(text[max(0, match.start() - 80):match.start()]) or NOT_START_AFTER.search(text[match.end():]):
            continue
        distance = [(max(start - match.end(), match.start() - end, 0), when) for start, end, when in dates]
        nearby = [d for d in distance if d[0] <= NEAR]
        if nearby:
            yield match, min(nearby, key=lambda d: d[0])[1]


def page_start_time(text: str, day: date) -> str | None:
    """The first start time on a page that sits next to ``day`` and no other date, as ``HH:MM``."""
    return next((_start(match) for match, when in _heading_times(text) if when == day), None)


def page_meeting_date(text: str) -> date | None:
    """The page's first date, when every start time on the page sits beside it.

    Requiring both keeps out multi-day notices and a time printed beside a
    posting or deadline date further down the page.
    """
    days = {when for _, when in _heading_times(text)}
    first = min(_dates(" ".join(text.split())), default=None)
    return first[2] if first and days == {first[2]} else None


def _agendas(meeting_title: str, documents: list[dict]) -> list[dict]:
    return [d for d in documents if not NOT_AGENDA.search(d.get("title") or "")
            and (COMMITTEE.search(meeting_title) or not COMMITTEE.search(d.get("title") or ""))]


def _first_page(document: dict) -> str | None:
    return next((p["text"] for p in document.get("pages", []) if p["number"] == 1), None)


def agenda_date(meeting_title: str, documents: list[dict]) -> date | None:
    """The meeting date printed beside the start time on the agenda's first page, if the agendas agree.

    For sources whose listing states no date (some TTD posts); a date written
    on the listing is always preferred.
    """
    days = {found for doc in _agendas(meeting_title, documents)
            if (first := _first_page(doc)) and (found := page_meeting_date(first))}
    return days.pop() if len(days) == 1 else None


def agenda_start_time(meeting_title: str, day: date | None, documents: list[dict]) -> str | None:
    """The start time printed on the meeting's agenda documents, if they state one and agree."""
    if not day:
        return None
    times = set()
    for doc in _agendas(meeting_title, documents):
        first = _first_page(doc)
        if first and (found := page_start_time(first, day)):
            times.add(found)
    return times.pop() if len(times) == 1 else None
