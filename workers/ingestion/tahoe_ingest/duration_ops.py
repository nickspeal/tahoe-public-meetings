"""Database-backed duration operations: record, detect, and calibrate."""
from __future__ import annotations

from statistics import median

from .durations import OBSERVED_SOURCES, calibration_rows, detect_duration, estimate_duration

MINUTES_TITLE = "(minutes|synopsis)"


def set_duration(conn, meeting_id: str, minutes: int, source: str) -> None:
    if source not in OBSERVED_SOURCES:
        raise ValueError(f"source must be one of {', '.join(OBSERVED_SOURCES)}")
    if not 5 <= minutes <= 24 * 60:
        raise ValueError("minutes must be between 5 and 1440")
    if not conn.execute("SELECT 1 FROM meetings WHERE id=%s", (meeting_id,)).fetchone():
        raise ValueError(f"unknown meeting {meeting_id}")
    with conn.transaction():
        conn.execute("UPDATE meetings SET observed_duration_minutes=%s, duration_source=%s, updated_at=now() WHERE id=%s",
                     (minutes, source, meeting_id))


def detect_from_minutes(conn, *, apply: bool = False) -> dict:
    """Scan stored text of minutes documents. Writes only when apply=True, and never over an existing value."""
    results, written = [], 0
    meetings = conn.execute("""SELECT DISTINCT m.id, m.agency_id, m.observed_duration_minutes FROM meetings m
        JOIN document_current c ON c.meeting_id=m.id JOIN document_versions v ON v.id=c.version_id
        WHERE v.title ~* %s ORDER BY m.id""", (MINUTES_TITLE,)).fetchall()
    for m in meetings:
        text = "\n".join(r["text"] for r in conn.execute("""SELECT p.text FROM document_current c
            JOIN document_versions v ON v.id=c.version_id JOIN document_pages p ON p.document_version_id=v.id
            WHERE c.meeting_id=%s AND v.title ~* %s ORDER BY v.id, p.page_number""", (m["id"], MINUTES_TITLE)))
        found = detect_duration(text)
        results.append({"meeting": m["id"], "agency": m["agency_id"], **found, "existing": m["observed_duration_minutes"]})
        if apply and found["minutes"] and not m["observed_duration_minutes"]:
            with conn.transaction():
                conn.execute("UPDATE meetings SET observed_duration_minutes=%s, duration_source='minutes-text', updated_at=now() WHERE id=%s",
                             (found["minutes"], m["id"]))
            written += 1
    return {"meetingsScanned": len(meetings), "detected": sum(1 for r in results if r["minutes"]),
            "applied": written, "dryRun": not apply, "results": results}


def duration_report(conn) -> dict:
    items: dict[str, list] = {}
    for r in conn.execute("SELECT meeting_id,title,section FROM agenda_items WHERE active ORDER BY meeting_id,item_order"):
        items.setdefault(r["meeting_id"], []).append({"title": r["title"], "section": r["section"]})
    observed, estimates = [], {}
    for m in conn.execute("SELECT id,agency_id,observed_duration_minutes AS obs FROM meetings ORDER BY id"):
        estimate = estimate_duration(m["agency_id"], items.get(m["id"], []))["minutes"]
        estimates.setdefault(m["agency_id"], []).append(estimate)
        if m["obs"]:
            observed.append({"agency_id": m["agency_id"], "observed": m["obs"], "estimate": estimate})
    calibrated = calibration_rows(observed)
    have = {c["agency"] for c in calibrated}
    return {"calibrated": calibrated,
            "uncalibrated": [{"agency": a, "meetings": len(v), "estimateMedian": median(v)}
                             for a, v in sorted(estimates.items()) if a not in have]}


def format_report(report: dict) -> str:
    lines = ["Observed vs. estimated meeting length (minutes). Positive error = estimate longer than reality (preferred).", ""]
    if report["calibrated"]:
        lines.append(f"{'agency':28} {'n':>3} {'obs med':>8} {'obs p90':>8} {'est med':>8} {'mean err':>9} {'too short':>9}")
        for c in report["calibrated"]:
            lines.append(f"{c['agency']:28} {c['count']:>3} {c['observedMedian']:>8.0f} {c['observedP90']:>8.0f} "
                         f"{c['estimateMedian']:>8.0f} {c['meanErrorMinutes']:>+9.1f} {c['tooShort']:>9}")
    else:
        lines.append("No observed durations recorded yet. Use `tahoe-ingest set-duration` or `detect-durations --apply`.")
    if report["uncalibrated"]:
        lines += ["", "No observed data (config values are guesses biased long):"]
        lines += [f"  {u['agency']:28} {u['meetings']:>3} meetings, median estimate {u['estimateMedian']:.0f} min" for u in report["uncalibrated"]]
    return "\n".join(lines)
