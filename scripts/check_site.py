#!/usr/bin/env python3
"""Check built public pages for missing routes, broken fragments, and page landmarks."""
from html.parser import HTMLParser
import argparse
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1] / "apps/web/dist"


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.ids = set()
        self.links = []
        self.h1 = self.main = 0
        self.language = None
        self.feed(path.read_text())

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "html":
            self.language = values.get("lang")
        if tag == "main":
            self.main += 1
        if tag == "h1":
            self.h1 += 1
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Also check served routes against this local server URL")
    args = parser.parse_args()
    if args.url and urlsplit(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("HTTP checks are restricted to a local preview")
    paths = list(ROOT.rglob("*.html"))
    if not paths:
        raise SystemExit("No built pages; run the web build first")
    pages = {p: Page(p) for p in paths}
    errors = []
    link_count = 0
    served_paths = {"/"}
    for path, page in pages.items():
        label = str(path.relative_to(ROOT))
        if page.h1 != 1 or page.main != 1 or page.language != "en":
            errors.append(f"{label}: expected one h1, one main, and lang=en")
        for href in page.links:
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc:
                continue
            if parsed.path.startswith("/"):
                served_paths.add(parsed.path)
            link_count += 1
            target_path = unquote(parsed.path)
            if not target_path:
                target = path
            elif target_path.startswith("/"):
                target = ROOT / target_path.lstrip("/")
            else:
                target = path.parent / target_path
            if target.is_dir():
                target = target / "index.html"
            if not target.exists():
                # Static routes may keep URL-encoded source IDs as directory names.
                raw = ROOT / parsed.path.lstrip("/")
                if raw.is_dir():
                    raw = raw / "index.html"
                if raw.exists():
                    target = raw
                else:
                    errors.append(f"{label}: broken internal link {href}")
                    continue
            if parsed.fragment and target in pages and unquote(parsed.fragment) not in pages[target].ids:
                errors.append(f"{label}: missing anchor {href}")
    if errors:
        raise SystemExit("Site check failed:\n" + "\n".join(f"- {e}" for e in errors))
    print(f"Built site valid: {len(pages)} pages, {link_count} internal links, page landmarks present.")
    if args.url:
        for route in sorted(served_paths):
            try:
                with urlopen(args.url.rstrip("/") + route, timeout=10) as response:
                    if response.status != 200:
                        raise ValueError(f"HTTP {response.status}")
                    content_type = response.headers.get("Content-Type", "")
                    if route.endswith(".ics"):
                        body = response.read(2 * 1024 * 1024)
                        if "text/calendar" not in content_type:
                            raise ValueError("calendar download has the wrong content type")
                        if not body.startswith(b"BEGIN:VCALENDAR\r\n") or not body.endswith(b"END:VCALENDAR\r\n"):
                            raise ValueError("calendar download is not an iCalendar file")
                        if body.count(b"BEGIN:VEVENT\r\n") != 1:
                            raise ValueError("calendar download must contain exactly one meeting")
                    elif "text/html" in content_type:
                        body = response.read(2 * 1024 * 1024).decode()
                        if "<main" not in body:
                            raise ValueError("page missing main landmark")
            except Exception as error:
                errors.append(f"{route}: {error}")
        if errors:
            raise SystemExit("Served route check failed:\n" + "\n".join(f"- {e}" for e in errors))
        print(f"Local server valid: {len(served_paths)} linked routes returned HTTP 200.")


if __name__ == "__main__":
    main()
