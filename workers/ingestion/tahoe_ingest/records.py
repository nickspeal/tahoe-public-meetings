"""Links to each meeting's minutes and recording (IDEA-017, decision 0025).

Three sources fill `meetings.minutes_url` and `meetings.recording_url`:

1. Listing links. The official index row that lists a meeting's agenda often
   links its minutes or video once posted (Granicus, Legistar, CivicClerk,
   dated tables, TRPA pages, link indexes). Discovery adapters put these on
   candidates; a link for an earlier meeting becomes a records-only entry
   matched to a stored meeting by date.
2. Configured feeds (`config/meeting-records.json`): YouTube channel or playlist
   feeds matched by body and the date in the video title, and Laserfiche
   minutes folders matched by the date in the document name.
3. Draft minutes in a later agenda (`draft_minutes.py`).

A run that lacks a link never removes one. A link is replaced only by an equal
or better one: approved minutes, then draft minutes posted on the listing, then
draft minutes found through a later agenda; and YouTube before any other host.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path

from .http import fetch_bytes

CONFIG = "config/meeting-records.json"
FIELDS = ("minutes_url", "minutes_status", "minutes_note", "recording_url")
COVERAGE_START = date(2026, 1, 1)
LOOKBACK_DAYS = 400  # stored meetings this recent can take a feed link
FEED_BYTES = 2 * 1024 * 1024
YOUTUBE_API = "https://www.googleapis.com/youtube/v3/playlistItems"
LASERFICHE_LISTING = "/Portal/FolderListingService.aspx/GetFolderListing2"

# ---------------------------------------------------------------- link values and precedence


def youtube_id(url: str | None) -> str | None:
    parsed = urllib.parse.urlparse(url or "")
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        found = parsed.path.strip("/").split("/")[0]
    elif host == "youtube.com":
        found = (urllib.parse.parse_qs(parsed.query).get("v", [""])[0] if parsed.path == "/watch" else
                 parsed.path.split("/")[2] if re.match(r"^/(?:live|embed|shorts)/", parsed.path) else "")
    else:
        return None
    return found if re.fullmatch(r"[A-Za-z0-9_-]{11}", found or "") else None


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def unwrap(url: str) -> str:
    """The destination of an Outlook SafeLinks or Google redirect wrapper; other URLs unchanged."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    query = urllib.parse.parse_qs(parsed.query)
    if host.endswith("safelinks.protection.outlook.com") and query.get("url"):
        return query["url"][0]
    if host in ("www.google.com", "google.com") and parsed.path == "/url" and query.get("q"):
        return query["q"][0]
    return url


def recording_link(url: str) -> str | None:
    """A per-meeting recording URL, or None for folder and channel pages that list many meetings."""
    url = unwrap(url)
    if video := youtube_id(url):
        return watch_url(video)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host.endswith("youtube.com") or (host.endswith("cal-span.org") and not parsed.path.startswith("/meeting/")):
        return None
    return url


def listing_minutes(url: str, label: str = "") -> dict:
    """Minutes linked from an official listing; a label or file name that says draft marks a draft."""
    url = unwrap(url)
    if urllib.parse.urlparse(url).scheme != "https":
        return {}
    draft = re.search(r"\bdraft|for review|for approval", label + " " + urllib.parse.unquote_plus(url), re.I)
    return {"minutes_url": url, "minutes_status": "draft" if draft else "approved", "minutes_note": None}


def granicus_links(links: list[tuple[str, str]], markup: str, base: str) -> dict:
    """Minutes and video on a Granicus publisher row.

    ``links`` are the row's (href, label) pairs; ``markup`` is text holding its
    attribute values, because the video opens from an onclick handler. An
    "Action Summary" in the minutes column is not minutes, and a clip of zero
    length has no recording.
    """
    found = {}
    minutes = next((url for url, label in links if "MinutesViewer.php" in url and re.search(r"\bminutes\b", label, re.I)), None)
    if minutes:
        found.update(listing_minutes(urllib.parse.urljoin(base, minutes), "Minutes"))
    media = re.search(r"(?:https?:)?//[^'\"\s]+/MediaPlayer\.php\?[^'\"\s]*clip_id=\d+", markup)
    if media and not re.search(r"\b00h(?:\s|&nbsp;)+00m\b", markup):
        found["recording_url"] = urllib.parse.urljoin(base, media[0])
    return found


def records_entry(agency: str, day: str, title: str, links: dict) -> dict:
    """A link for an earlier meeting found on an index: matched to a stored meeting by date, never captured."""
    return {"agency": agency, "records_only": True, "date": day, "title": title, **links}


def minutes_rank(links: dict) -> int:
    if not links.get("minutes_url"):
        return 0
    if links.get("minutes_status") == "approved":
        return 3
    return 1 if links.get("minutes_note") else 2


def recording_rank(links: dict) -> int:
    url = links.get("recording_url")
    return 0 if not url else 2 if youtube_id(url) else 1


def merged(old: dict, new: dict) -> dict:
    """The stored link fields after offering ``new``; nothing is removed, and only equal or better links replace."""
    result = {key: old.get(key) for key in FIELDS}
    if new.get("minutes_url") == old.get("minutes_url") and not new.get("minutes_status"):
        new = {**new, "minutes_status": old.get("minutes_status"), "minutes_note": old.get("minutes_note")}
    if new.get("minutes_url") and minutes_rank(new) >= minutes_rank(old):
        result.update(minutes_url=new["minutes_url"], minutes_status=new.get("minutes_status") or "approved",
                      minutes_note=new.get("minutes_note"))
    if new.get("recording_url") and recording_rank(new) >= recording_rank(old):
        result["recording_url"] = new["recording_url"]
    return result


def links_of(row: dict) -> dict:
    return {key: row.get(key) for key in FIELDS if row.get(key)}


def apply_links(conn, meeting: dict, new: dict) -> bool:
    """Offer links to one stored meeting (a row with the FIELDS); update it only when something changes."""
    result = merged(meeting, new)
    if all(result[key] == meeting.get(key) for key in FIELDS):
        return False
    conn.execute("""UPDATE meetings SET minutes_url=%s,minutes_status=%s,minutes_note=%s,recording_url=%s,
        updated_at=clock_timestamp() WHERE id=%s""",
                 (result["minutes_url"], result["minutes_status"], result["minutes_note"], result["recording_url"], meeting["id"]))
    meeting.update(result)
    return True


# ---------------------------------------------------------------- dates and qualifiers in titles

_MONTH = r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_MONTH_DATE = re.compile(_MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*[-–]\s*\d{1,2})?,?\s+(\d{4})\b", re.I)
_MONTH_DAY = re.compile(_MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b(?!,?\s+\d{4}\b|\s*[-–]\s*\d)", re.I)
_NUMERIC_DATE = re.compile(r"(?<![\d/.-])(\d{1,2})([/.-])(\d{1,2})\2(\d{4}|\d{2})(?![\d/.-]*\d)")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})[-.](\d{2})[-.](\d{2})(?!\d)")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"\b([A-Za-z]{3,9})(\d{1,2},)", r"\1 \2", text)  # "December4, 2025"
    return re.sub(r"\s+", " ", text)


def dates_in(text: str) -> list[date]:
    """Every calendar date written in ``text``, in order: "Sept. 22, 2026", "10/15/26", "8.11.26", "2026-09-03"."""
    text = _normalize(text)
    found = []
    for match in _MONTH_DATE.finditer(text):
        found.append((match.start(), int(match[3]), _MONTHS[match[1].lower()[:3]], int(match[2])))
    # "September 17, September 23, and October 1, 2026": a list shares the year of its last date.
    for match in _MONTH_DAY.finditer(text):
        following = next((f for f in sorted(found) if 0 < f[0] - match.start() <= 80), None)
        if following:
            found.append((match.start(), following[1], _MONTHS[match[1].lower()[:3]], int(match[2])))
    for match in _ISO_DATE.finditer(text):
        found.append((match.start(), int(match[1]), int(match[2]), int(match[3])))
    for match in _NUMERIC_DATE.finditer(text):
        if any(start <= match.start() < start + 10 for start, *_ in found):
            continue  # part of an ISO date
        year = int(match[4]) + (2000 if len(match[4]) == 2 else 0)
        found.append((match.start(), year, int(match[1]), int(match[3])))
    days = []
    for _, year, month, day in sorted(found):
        try:
            value = date(year, month, day)
        except ValueError:
            continue
        if value not in days:
            days.append(value)
    return days


def title_date(text: str) -> date | None:
    days = dates_in(text)
    return days[0] if days else None


def qualifiers(text: str) -> set[str]:
    """Words that tell same-day meetings apart: special, joint, workshop, and closed session."""
    text = text or ""
    found = {word for word in ("special", "joint", "workshop") if re.search(r"\b" + word + r"\b", text, re.I)}
    if re.search(r"\bclosed session\b", text, re.I):
        found.add("closed")
    return found


def match_meeting(meetings: list[dict], day: date, quals: set[str]) -> dict | None:
    """The one stored meeting on ``day`` that a record with these qualifiers belongs to, or None when ambiguous.

    Closed-session records match only closed-session meetings. Qualifiers must
    agree when several meetings share the day; otherwise special and regular are
    not told apart (sources relabel a meeting), but joint and non-joint are.
    """
    same = [m for m in meetings if m["local_date"] == day and m.get("status") != "canceled"
            and ("closed" in qualifiers(m["title"])) == ("closed" in quals)]
    if len(same) == 1:
        return same[0]
    exact = [m for m in same if qualifiers(m["title"]) == quals]
    if exact:
        return exact[0] if len(exact) == 1 else None
    loose = [m for m in same if ("joint" in qualifiers(m["title"])) == ("joint" in quals)]
    return loose[0] if len(loose) == 1 else None


# ---------------------------------------------------------------- configuration


def load_config(root: Path | str = ".") -> dict:
    path = Path(root) / CONFIG
    if not path.is_file():
        return {}
    config = json.loads(path.read_text())
    validate_config(config)
    return config


def validate_config(config: dict) -> None:
    from .catalog import followed_agencies
    followed = followed_agencies()
    for entry in config.get("youtube", []):
        if entry["agency"] not in followed:
            raise ValueError(f"meeting-records youtube: {entry['agency']} is not a followed agency")
        if bool(entry.get("channelId")) == bool(entry.get("playlistId")):
            raise ValueError(f"meeting-records youtube {entry['agency']}: give exactly one of channelId and playlistId")
        if entry.get("channelId") and not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", entry["channelId"]):
            raise ValueError(f"meeting-records youtube {entry['agency']}: invalid channelId")
        for key in ("include", "exclude", "meetingPattern"):
            if key in entry:
                re.compile(entry[key])
        if "include" not in entry:
            raise ValueError(f"meeting-records youtube {entry['agency']}: include pattern required")
    for entry in config.get("laserfiche", []):
        if entry["agency"] not in followed:
            raise ValueError(f"meeting-records laserfiche: {entry['agency']} is not a followed agency")
        if not isinstance(entry.get("folderId"), int) or not entry.get("repo"):
            raise ValueError(f"meeting-records laserfiche {entry['agency']}: repo and integer folderId required")


# ---------------------------------------------------------------- YouTube

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"


def feed_url(entry: dict) -> str:
    key, value = ("channel_id", entry["channelId"]) if entry.get("channelId") else ("playlist_id", entry["playlistId"])
    return f"https://www.youtube.com/feeds/videos.xml?{key}={value}"


def parse_feed(xml: str) -> list[dict]:
    """Videos in a YouTube Atom feed: id, title, and publication time."""
    videos = []
    for node in ET.fromstring(xml).findall(_ATOM + "entry"):
        video = node.findtext(_YT + "videoId")
        if video:
            videos.append({"id": video, "title": node.findtext(_ATOM + "title") or "",
                           "published": node.findtext(_ATOM + "published") or ""})
    return videos


def api_videos(entry: dict, api_key: str, *, since: date, fetch=None, max_pages: int = 40) -> list[dict]:
    """Videos from the YouTube Data API: a channel's uploads playlist (newest first) or a configured playlist.

    Each page costs one quota unit. Uploads stop paging once videos predate ``since``.
    The key is a secret and never appears in errors.
    """
    playlist = entry.get("playlistId") or "UU" + entry["channelId"][2:]
    fetch = fetch or (lambda url: fetch_bytes(url, max_bytes=FEED_BYTES, accept="application/json")[0].decode())
    videos, token = [], ""
    for _ in range(max_pages):
        query = {"part": "snippet", "maxResults": "50", "playlistId": playlist, "key": api_key}
        if token:
            query["pageToken"] = token
        try:
            data = json.loads(fetch(YOUTUBE_API + "?" + urllib.parse.urlencode(query)))
        except Exception as exc:
            raise RuntimeError(f"YouTube Data API request for {playlist} failed: {type(exc).__name__}") from None
        page = [{"id": item["snippet"]["resourceId"]["videoId"], "title": item["snippet"]["title"],
                 "published": item["snippet"].get("publishedAt", "")} for item in data.get("items", [])
                if item.get("snippet", {}).get("resourceId", {}).get("videoId")]
        videos.extend(page)
        token = data.get("nextPageToken")
        oldest = min((v["published"][:10] for v in page if v["published"]), default="")
        if not token or (not entry.get("playlistId") and oldest and oldest < since.isoformat()):
            break
    return videos


_PENALTY = re.compile(r"\b(?:original|untrimmed|raw|test|part\s*\d+|day\s*(?:two|three|2|3))\b", re.I)


def youtube_matches(entry: dict, videos: list[dict], meetings: list[dict], *, today: date,
                    coverage_start: date = COVERAGE_START) -> dict[str, str]:
    """Meeting ID → YouTube watch URL for one configured body.

    A video counts only when its title matches the body pattern (and not the
    exclusion), names a date, and that date has passed. Among several videos for
    one meeting, a title marked original, untrimmed, or as a later part loses,
    then the latest published wins.
    """
    include = re.compile(entry["include"], re.I)
    exclude = re.compile(entry["exclude"], re.I) if entry.get("exclude") else None
    pattern = re.compile(entry["meetingPattern"], re.I) if entry.get("meetingPattern") else None
    pool = [m for m in meetings if not pattern or pattern.search(m["title"])]
    chosen: dict[str, tuple] = {}
    for video in videos:
        title = _normalize(video["title"])
        if not include.search(title) or (exclude and exclude.search(title)):
            continue
        day = title_date(title)
        if not day or not coverage_start <= day < today:
            continue
        meeting = match_meeting(pool, day, qualifiers(title))
        if not meeting:
            continue
        rank = (bool(_PENALTY.search(title)), _negated(video["published"]), video["id"])
        if meeting["id"] not in chosen or rank < chosen[meeting["id"]]:
            chosen[meeting["id"]] = rank
    return {mid: watch_url(rank[2]) for mid, rank in chosen.items()}


def _negated(stamp: str) -> float:
    try:
        return -datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- Laserfiche


def _laserfiche_listing(entry: dict, folder_id: int, fetch_json) -> list[dict]:
    payload = {"repoName": entry["repo"], "folderId": folder_id, "getNewListing": True, "start": 0, "end": 500,
               "sortColumn": "", "sortAscending": True}
    data = fetch_json(entry.get("host", "https://portal.laserfiche.com") + LASERFICHE_LISTING, payload)
    return (data.get("data") or {}).get("results") or []


def laserfiche_minutes(entry: dict, meetings: list[dict], *, today: date, fetch_json=None,
                       coverage_start: date = COVERAGE_START) -> dict[str, str]:
    """Meeting ID → minutes document URL from a Laserfiche folder of year folders.

    Documents are named "YYYY-MM-DD - Regular", "… - Special and Joint", and so on.
    A document for a closed session alone is never matched.
    """
    fetch_json = fetch_json or _post_json
    host = entry.get("host", "https://portal.laserfiche.com")
    years = {str(y) for y in range(coverage_start.year, today.year + 1)}
    found = {}
    for folder in _laserfiche_listing(entry, entry["folderId"], fetch_json):
        if folder.get("type") != 0 or folder.get("name") not in years:
            continue
        for document in _laserfiche_listing(entry, folder["entryId"], fetch_json):
            named = re.match(r"^(\d{4}-\d{2}-\d{2})\s*-\s*(.+)$", document.get("name") or "")
            if document.get("type") != -2 or not named or re.fullmatch(r"\s*closed\s*", named[2], re.I):
                continue
            day = date.fromisoformat(named[1])
            if day < coverage_start or day >= today:
                continue
            meeting = match_meeting(meetings, day, qualifiers(named[2]) - {"closed"})
            if meeting and meeting["id"] not in found:
                found[meeting["id"]] = f"{host}/Portal/DocView.aspx?id={document['entryId']}&repo={entry['repo']}"
    return found


def _post_json(url: str, payload: dict) -> dict:
    raw, _ = fetch_bytes(url, max_bytes=FEED_BYTES, accept="application/json", payload=json.dumps(payload).encode())
    return json.loads(raw)


# ---------------------------------------------------------------- the scheduled step


def stored_for_records(conn, agencies: list[str], *, today: date) -> dict[str, list[dict]]:
    """Recent stored meetings of these agencies with their link fields, in one query."""
    by_agency: dict[str, list[dict]] = {}
    if not agencies:
        return by_agency
    for row in conn.execute("""SELECT id,agency_id,title,local_date,status,minutes_url,minutes_status,minutes_note,recording_url
            FROM meetings WHERE agency_id=ANY(%s) AND local_date IS NOT NULL AND local_date >= %s
            AND quarantine_reason IS NULL""", (agencies, today - timedelta(days=LOOKBACK_DAYS))):
        by_agency.setdefault(row["agency_id"], []).append(dict(row))
    return by_agency


def update_meeting_records(conn, root: Path | str = ".", *, today: date, fetch_text=None, fetch_json=None,
                           api_key: str | None = None, since: date | None = None, dry_run: bool = False) -> dict:
    """Match configured YouTube and Laserfiche records to stored meetings and store better links.

    Feeds are read once each even when several bodies share a channel. With
    ``api_key`` and ``since`` the YouTube Data API replaces the 15-video feed
    (a one-time backfill). One failing feed never stops the others.
    """
    from .discovery import fetch_text as default_fetch
    fetch_text = fetch_text or (lambda url: default_fetch(url, max_bytes=FEED_BYTES))
    config = load_config(root)
    entries = config.get("youtube", []) + config.get("laserfiche", [])
    report = {"youtubeBodies": len(config.get("youtube", [])), "laserficheBodies": len(config.get("laserfiche", [])),
              "matched": 0, "updated": 0, "errors": [], "byAgency": {}}
    if not entries:
        return report
    meetings = stored_for_records(conn, sorted({e["agency"] for e in entries}), today=today)
    feeds: dict[str, list[dict] | Exception] = {}
    for entry in config.get("youtube", []):
        key = feed_url(entry) + ("#api" if api_key and since else "")
        if key not in feeds:
            try:
                feeds[key] = (api_videos(entry, api_key, since=since) if api_key and since else parse_feed(fetch_text(feed_url(entry))))
            except Exception as exc:
                feeds[key] = exc
                report["errors"].append(f"{entry['agency']}: {feed_url(entry)}: {type(exc).__name__}: {str(exc)[:200]}")
        if isinstance(feeds[key], Exception):
            continue
        found = youtube_matches(entry, feeds[key], meetings.get(entry["agency"], []), today=today)
        _store(conn, report, entry["agency"], meetings, {mid: {"recording_url": url} for mid, url in found.items()}, dry_run)
    for entry in config.get("laserfiche", []):
        try:
            found = laserfiche_minutes(entry, meetings.get(entry["agency"], []), today=today, fetch_json=fetch_json)
        except Exception as exc:
            report["errors"].append(f"{entry['agency']}: laserfiche: {type(exc).__name__}: {str(exc)[:200]}")
            continue
        _store(conn, report, entry["agency"], meetings,
               {mid: {"minutes_url": url, "minutes_status": "approved"} for mid, url in found.items()}, dry_run)
    return report


def _store(conn, report: dict, agency: str, meetings: dict, found: dict[str, dict], dry_run: bool) -> None:
    by_id = {m["id"]: m for m in meetings.get(agency, [])}
    counts = report["byAgency"].setdefault(agency, {"matched": 0, "updated": 0})
    for meeting_id, links in found.items():
        counts["matched"] += 1
        report["matched"] += 1
        meeting = by_id[meeting_id]
        changed = (merged(meeting, links) != {k: meeting.get(k) for k in FIELDS}) if dry_run else apply_links(conn, meeting, links)
        if changed:
            counts["updated"] += 1
            report["updated"] += 1


def youtube_api_key() -> str | None:
    return (os.getenv("YOUTUBE_API_KEY") or "").strip() or None
