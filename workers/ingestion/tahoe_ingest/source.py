"""Bounded official-document retrieval and temporary PDF extraction."""
from __future__ import annotations

import re
import subprocess
from urllib.parse import urlparse

from .http import USER_AGENT, SourceTooLarge, download

MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_PAGES = 800
LINK_PAGES = 3  # opening pages whose embedded links are kept (with the last page): where agendas print join links
MAX_LINKS_PER_PAGE = 20


class TooManyPages(SourceTooLarge):
    """The PDF has more than ``MAX_PAGES`` pages; like a byte-limit failure, a retry would fail the same way."""

from .html_source import html_text
from .html_agenda import parse_html_agenda


def download_extract(url: str, *, max_bytes: int = MAX_PDF_BYTES) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("source URL must be HTTPS")
    # The body streams to a scratch file (removed on return or error), so a large PDF is never held in memory whole.
    with download(url, max_bytes=max_bytes, accept="application/pdf, text/html;q=0.9, text/plain;q=0.8") as fetched:
        head, content_type = fetched.head(), fetched.content_type
        if head.startswith(b"%PDF-"):
            return _extract_pdf(fetched)
        if "html" in content_type.lower() or head.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            media_type = "text/html"
        elif "text/plain" in content_type.lower():
            media_type = "text/plain"
        else:
            raise ValueError(f"source is not a supported PDF, HTML, or plain text document: {content_type}")
        raw = fetched.path.read_bytes()  # pages and plain text are small, and bounded by max_bytes
    decoded = raw.decode("utf-8-sig", "replace")
    text = html_text(decoded) if media_type == "text/html" else decoded.strip()
    if any(marker in decoded.lower() for marker in ("<title>just a moment", "<title>access denied", "enable javascript and cookies to continue")):
        raise ValueError("source returned a browser challenge instead of document content")
    if not text.strip():
        raise ValueError("source extraction produced no text")
    items, complete = parse_html_agenda(decoded, text) if media_type == "text/html" else ([], False)
    return {"agenda_items": items, "items_complete": complete, "sha256": fetched.sha256, "bytes": fetched.size, "page_count": 1,
            "media_type": media_type, "pages": [{"page_number": 1, "text": text}],
            "warnings": ["HTML/text source retained as one source section; it has no PDF page numbers."]}


def _extract_pdf(fetched) -> dict:
    """Page count and per-page text of a downloaded PDF (``http.Download``), within ``MAX_PAGES``."""
    path = str(fetched.path)
    info = subprocess.run(["pdfinfo", path], capture_output=True, text=True, timeout=30, check=True).stdout
    page_line = next((line for line in info.splitlines() if line.startswith("Pages:")), None)
    if not page_line:
        raise ValueError("PDF has no page count")
    page_count = int(page_line.split(":", 1)[1].strip())
    if page_count > MAX_PAGES:
        raise TooManyPages(f"PDF page count outside 1..{MAX_PAGES}: {page_count}")
    if page_count < 1:
        raise ValueError(f"PDF page count outside 1..{MAX_PAGES}: {page_count}")
    extracted = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", path, "-"],
                               capture_output=True, timeout=120, check=True).stdout.decode("utf-8", "replace")
    texts = extracted.split("\f")
    if texts and not texts[-1].strip():
        texts.pop()
    pages = [{"page_number": n, "text": text.strip()} for n, text in enumerate(texts, 1) if text.strip()]
    links = link_annotations(path, page_count)
    for page in pages:
        if links.get(page["page_number"]):
            page["links"] = links[page["page_number"]]
    warnings = []
    if len(pages) < page_count:
        warnings.append(f"Text extracted on {len(pages)} of {page_count} pages; scanned pages may require OCR.")
    if not pages:
        raise ValueError("PDF extraction produced no text; OCR/manual review required")
    return {"sha256": fetched.sha256, "bytes": fetched.size, "media_type": "application/pdf", "page_count": page_count,
            "pages": pages, "warnings": warnings}


def link_annotations(path: str, page_count: int) -> dict[int, list[str]]:
    """Web links embedded in the first ``LINK_PAGES`` pages and the last page, by page number.

    A "Zoom Link" or "Click here to join the Microsoft Teams meeting" is a link
    annotation whose URL pdftotext never prints; ``pdfinfo -url`` lists them. Only
    http(s) links are kept, in page order without repeats. Listing them is best
    effort: a PDF that pdfinfo cannot list keeps its text and simply has no links.
    """
    wanted = set(range(1, min(LINK_PAGES, page_count) + 1)) | {page_count}
    try:
        listed = subprocess.run(["pdfinfo", "-url", path], capture_output=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found: dict[int, list[str]] = {}
    for line in listed.decode("utf-8", "replace").splitlines():
        match = re.match(r"^\s*(\d+)\s+\S+\s+(https?://\S+)\s*$", line)
        if not match or int(match.group(1)) not in wanted:
            continue
        page = found.setdefault(int(match.group(1)), [])
        if match.group(2) not in page and len(page) < MAX_LINKS_PER_PAGE:
            page.append(match.group(2))
    return found
