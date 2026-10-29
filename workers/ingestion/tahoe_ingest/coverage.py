"""The registry-derived parts of docs/operations/source-coverage.md.

The page mixes the maintainer's notes with tables that restate configuration. Those tables sit
between ``<!-- generated: NAME -->`` and ``<!-- /generated: NAME -->`` and are rewritten from the
agency registry and config/skipped-endpoints.json by ``tahoe-ingest coverage-doc``, so counts and
lists cannot drift from what the worker reads; a test fails when the page is stale. Everything
outside the markers is prose and is never touched.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from .catalog import agency_catalog, agency_catalog_path

DOC = Path("docs/operations/source-coverage.md")
BLOCK = re.compile(r"(<!-- generated: (?P<name>[a-z-]+) -->\n)(?P<body>.*?)(<!-- /generated: (?P=name) -->)", re.S)


def _code(text: str) -> str:
    return "`" + text.replace("|", "\\|") + "`"


def _ids(ids) -> str:
    return ", ".join(_code(i) for i in ids)


def _link(url: str | None) -> str:
    return f"[{urlparse(url).netloc}]({url})" if url else "—"


def _sentence(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _skipped(root: Path) -> list[dict]:
    path = root / "config/skipped-endpoints.json"
    return json.loads(path.read_text()).get("skipped", []) if path.is_file() else []


def _blocked_by(agency: dict, entry: dict) -> list[str]:
    """Which of an agency's pages a skip entry covers: its discovery index and its source page."""
    urls = {"discovery index": (agency.get("discovery") or {}).get("indexUrl"), "source page": agency.get("sourceUrl")}
    return [label for label, url in urls.items() if url and url.startswith(entry["prefix"])]


def sections(root: Path) -> dict[str, str]:
    """``{block name: markdown}`` for every generated block of the coverage page."""
    agencies = agency_catalog()
    parents = {e["id"]: e["extends"] for e in json.loads(agency_catalog_path().read_text()) if "extends" in e}
    skipped = _skipped(root)
    # An entry with a per-record host is not a body (it holds one-off meetings), so it is counted apart.
    hosted = [a for a in agencies if a["follow"] and a.get("meetingHost") == "per-record"]
    followed = [a for a in agencies if a["follow"] and a not in hosted]
    blocked = {a["id"] for a in followed for entry in skipped if _blocked_by(a, entry)}
    live = [a for a in followed if a.get("discovery") and a["id"] not in blocked]
    manual = [a for a in followed if not a.get("discovery") and a["id"] not in blocked]

    def label(agency: dict) -> str:
        return _code(agency["id"]) + (f" (extends {_code(parents[agency['id']])})" if agency["id"] in parents else "")

    disabled = sorted(a["id"] for a in agencies if not a["follow"])
    out = {"summary": (
        f"The registry has **{len(agencies)} entries**. **{len(followed)} are followed**; the "
        f"{_sentence(len(disabled), 'entry', 'entries')} disabled by the [source review](source-review.md) "
        f"({_ids(disabled)}) stay in the file for provenance and are left out below. Of the followed entries, "
        f"**{len(live)}** have live discovery, **{len(blocked)}** are blocked by a skipped endpoint, and "
        f"**{len(manual)}** have no live discovery. "
        + (f"{_sentence(len(parents), 'entry extends', 'entries extend')} a sibling body's entry: "
           + ", ".join(f"{_code(c)} ({_code(p)})" for c, p in parents.items()) + "." if parents else "")
        + (f" {_sentence(len(hosted), 'entry', 'entries')} ({_ids(a['id'] for a in hosted)}) "
           f"{'is' if len(hosted) == 1 else 'are'} not {'a body' if len(hosted) == 1 else 'bodies'}: "
           f"{'it holds' if len(hosted) == 1 else 'they hold'} one-off meetings whose host is named per record." if hosted else "")
        + "\n")}

    rows = ["| Registry ID | Type | Index | Body filter | Agenda layouts |", "| --- | --- | --- | --- | --- |"]
    for a in sorted(live, key=lambda a: (a["discovery"]["type"], a["id"])):
        d = a["discovery"]
        rows.append(f"| {label(a)} | `{d['type']}` | {_link(d['indexUrl'])} | "
                    f"{_code(d['bodyPattern']) if 'bodyPattern' in d else 'every row'} | "
                    f"{_ids(a.get('agenda', {}).get('layouts', [])) or '—'} |")
    out["discovery"] = "\n".join(rows) + "\n"

    lines = []
    for entry in skipped:
        hits = [(a, " and ".join(_blocked_by(a, entry))) for a in followed if _blocked_by(a, entry)]
        if not hits:
            continue
        issue = ""
        if entry.get("issue"):
            name = re.match(r"[A-Z]+-\d+|.*", Path(entry["issue"]).stem)[0]
            issue = f"; [{name}]({os.path.relpath(root / entry['issue'], root / DOC.parent)})"
        lines.append(f"- `{entry['prefix']}` (HTTP {entry.get('status', '?')} since {entry.get('since', '?')}{issue}): "
                     f"{entry.get('reason', '')} Affects " + ", ".join(f"{_code(a['id'])} ({what})" for a, what in hits) + ".")
    out["blocked"] = "\n".join(lines or ["No followed entry reads a skipped endpoint."]) + "\n"

    kinds = {("public-comment-period",): "comment periods only", ("public-comment-period", "meeting"): "comment periods and meetings"}
    rows = ["| Registry ID | Name | Records | Official page |", "| --- | --- | --- | --- |"]
    for a in manual:
        rows.append(f"| {label(a)} | {a['name']} | {kinds.get(tuple(a.get('participationTypes', ['meeting'])), 'meetings')} | "
                    f"{_link(a.get('sourceUrl') or a.get('officialUrl'))} |")
    out["no-discovery"] = "\n".join(rows) + "\n"

    by_rule: dict[str, list[str]] = {}
    without = []
    for a in followed:
        config = a.get("staffReports")
        if config and "rule" in config:
            note = f" ({config['note']})".replace("|", "\\|") if config.get("note") else ""
            by_rule.setdefault(config["rule"], []).append(label(a) + note)
        else:
            reports = ("—" if not config else "none published" if config.get("published") is False
                       else "published, not attached yet" if config.get("published") else "not known")
            without.append((reports, a["id"], (config or {}).get("note", "No `staffReports` entry.").replace("|", "\\|")))
    rows = ["| Rule | Registry IDs |", "| --- | --- |"]
    rows += [f"| `{rule}` | {', '.join(ids)} |" for rule, ids in sorted(by_rule.items())]
    rows += ["", "| Registry ID | Staff reports | Why there is no rule |", "| --- | --- | --- |"]
    rows += [f"| {_code(aid)} | {reports} | {note} |" for reports, aid, note in sorted(without)]
    out["staff-reports"] = "\n".join(rows) + "\n"
    return out


def render(text: str, root: Path) -> str:
    """``text`` with every generated block rewritten; an unknown or missing block is an error."""
    generated = sections(root)
    found = [m["name"] for m in BLOCK.finditer(text)]
    if sorted(found) != sorted(generated):
        raise ValueError(f"{DOC}: generated blocks {found} do not match {sorted(generated)}")
    return BLOCK.sub(lambda m: m[1] + generated[m["name"]] + m[4], text)
