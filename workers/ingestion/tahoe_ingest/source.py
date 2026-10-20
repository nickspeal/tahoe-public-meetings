"""Bounded official-document retrieval and temporary PDF extraction."""
from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

MAX_PDF_BYTES = 80 * 1024 * 1024
MAX_PAGES = 800
from .http import USER_AGENT, fetch_bytes
from .html_source import html_text
from .html_agenda import parse_html_agenda


def download_extract(url: str, *, max_bytes: int = MAX_PDF_BYTES) -> dict:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("source URL must be HTTPS")
    raw, content_type = fetch_bytes(url, max_bytes=max_bytes, accept="application/pdf, text/html;q=0.9, text/plain;q=0.8")
    if raw.startswith(b"%PDF-"):
        media_type = "application/pdf"
    elif "html" in content_type.lower() or raw.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        media_type = "text/html"
    elif "text/plain" in content_type.lower():
        media_type = "text/plain"
    else:
        raise ValueError(f"source is not a supported PDF, HTML, or plain text document: {content_type}")
    sha256 = hashlib.sha256(raw).hexdigest()
    if media_type != "application/pdf":
        decoded = raw.decode("utf-8-sig", "replace")
        text = html_text(decoded) if media_type == "text/html" else decoded.strip()
        if any(marker in decoded.lower() for marker in ("<title>just a moment", "<title>access denied", "enable javascript and cookies to continue")):
            raise ValueError("source returned a browser challenge instead of document content")
        if not text.strip():
            raise ValueError("source extraction produced no text")
        items, complete = parse_html_agenda(decoded, text) if media_type == "text/html" else ([], False)
        return {"agenda_items": items, "items_complete": complete, "sha256": sha256, "bytes": len(raw), "page_count": 1,
                "media_type": media_type, "pages": [{"page_number": 1, "text": text}],
                "warnings": ["HTML/text source retained as one source section; it has no PDF page numbers."]}
    with tempfile.TemporaryDirectory(prefix="tahoe-pdf-") as scratch:
        path = Path(scratch) / "source.pdf"
        path.write_bytes(raw)
        with path.open("rb") as source:
            signature = source.read(5)
        if signature != b"%PDF-":
            raise ValueError("response is not a PDF")
        info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30, check=True).stdout
        page_line = next((line for line in info.splitlines() if line.startswith("Pages:")), None)
        if not page_line:
            raise ValueError("PDF has no page count")
        page_count = int(page_line.split(":", 1)[1].strip())
        if page_count < 1 or page_count > MAX_PAGES:
            raise ValueError(f"PDF page count outside 1..{MAX_PAGES}: {page_count}")
        extracted = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
                                   capture_output=True, timeout=120, check=True).stdout.decode("utf-8", "replace")
        texts = extracted.split("\f")
        if texts and not texts[-1].strip():
            texts.pop()
        pages = [{"page_number": n, "text": text.strip()} for n, text in enumerate(texts, 1) if text.strip()]
        warnings = []
        if len(pages) < page_count:
            warnings.append(f"Text extracted on {len(pages)} of {page_count} pages; scanned pages may require OCR.")
        if not pages:
            raise ValueError("PDF extraction produced no text; OCR/manual review required")
        return {"sha256": sha256, "bytes": len(raw), "media_type": media_type, "page_count": page_count, "pages": pages, "warnings": warnings}
