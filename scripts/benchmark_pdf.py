#!/usr/bin/env python3
"""Measure a temporary PDF download and Poppler extraction; retain only metrics."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

MAX_BYTES = 128 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        parser.error("Use an HTTPS official document URL")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tahoe-pdf-benchmark-") as scratch:
        pdf = Path(scratch) / "input.pdf"
        digest = hashlib.sha256()
        received = 0
        req = Request(args.url, headers={"User-Agent": "TahoePublicMeetings/0.1 (local source feasibility check)"})
        with urlopen(req, timeout=60) as response, pdf.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > MAX_BYTES:
                    raise SystemExit("Document exceeds 128 MiB processing limit")
                target.write(chunk)
                digest.update(chunk)
        downloaded = time.monotonic()
        output = Path(scratch) / "pages.txt"
        subprocess.run(["pdftotext", "-layout", str(pdf), str(output)], check=True, timeout=180)
        extracted = time.monotonic()
        text = output.read_text()
        pages = text.split("\f")
        if not pages[-1].strip():
            pages.pop()
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        peak_bytes = peak if sys.platform == "darwin" else peak * 1024
        print(json.dumps({
            "url": args.url, "pdfBytes": received, "sha256": digest.hexdigest(),
            "pages": len(pages), "blankTextPages": sum(not p.strip() for p in pages),
            "extractedTextBytes": output.stat().st_size,
            "downloadSeconds": round(downloaded - started, 3),
            "extractionSeconds": round(extracted - downloaded, 3),
            "extractionPeakRSSBytes": peak_bytes,
            "pdfRetention": "temporary directory removed on exit", "modelCalls": 0,
        }, indent=2))


if __name__ == "__main__":
    main()
