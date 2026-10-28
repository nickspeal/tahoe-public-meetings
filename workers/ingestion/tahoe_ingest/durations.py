"""Meeting-length model: estimates, observed lengths, and minutes-text detection.

Tunable through config/meeting-durations.json (the defaults) and an agency entry's
``duration`` in packages/domain/agencies.json (that body's own settings). Estimates are
always biased long and rounded up, and are labelled as estimates wherever they are exported.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from functools import lru_cache
from pathlib import Path

NAME = "meeting-durations.json"
OBSERVED_SOURCES = ("minutes", "recording", "manual", "published", "minutes-text")
_NUMERIC = ("baseMinutes", "perItemMinutes", "fixedMinutes", "minMinutes", "maxMinutes", "roundUpToMinutes", "longBiasFactor")
_KEYS = {"mode", "sectionWeights", "notes", *_NUMERIC}


@lru_cache(maxsize=1)
def duration_config() -> dict:
    """The file's defaults plus ``agencies``: each registry entry's ``duration``, keyed by agency ID."""
    from .catalog import agency_catalog
    checkout = Path(__file__).resolve().parents[3] / "config" / NAME
    packaged = Path(__file__).with_name(NAME)
    cfg = json.loads((packaged if packaged.is_file() else checkout).read_text())
    if "agencies" in cfg:
        raise ValueError(f"{NAME}: per-agency settings belong in the agency's `duration` in packages/domain/agencies.json")
    cfg["agencies"] = {a["id"]: a["duration"] for a in agency_catalog() if "duration" in a}
    validate(cfg)
    return cfg


def validate_scope(scope: dict, where: str = "meeting-durations") -> None:
    """Check the defaults or one agency's ``duration`` block."""
    if not isinstance(scope, dict) or set(scope) - _KEYS:
        raise ValueError(f"invalid {where}: settings are {', '.join(sorted(_KEYS))}")
    if scope.get("mode", "estimated") not in ("fixed", "estimated"):
        raise ValueError("duration mode must be fixed or estimated")
    for key in _NUMERIC:
        if key in scope and not (isinstance(scope[key], (int, float)) and scope[key] > 0):
            raise ValueError(f"{key} must be a positive number")
    for pattern, weight in scope.get("sectionWeights", {}).items():
        re.compile(pattern, re.I)
        if not isinstance(weight, (int, float)) or weight < 0:
            raise ValueError(f"invalid weight for {pattern}")


def validate(cfg: dict) -> None:
    if cfg.get("version") != 1 or "defaults" not in cfg:
        raise ValueError("invalid meeting-durations config")
    validate_scope(cfg["defaults"])
    for agency, scope in cfg.get("agencies", {}).items():
        validate_scope(scope, f"duration of {agency}")
    if not all(key in cfg["defaults"] for key in _NUMERIC):
        raise ValueError("defaults must define every numeric setting")


def agency_settings(agency_id: str, cfg: dict | None = None) -> dict:
    """Defaults, overridden by the agency's own entry; keyword maps are merged."""
    cfg = cfg or duration_config()
    override = cfg.get("agencies", {}).get(agency_id, {})
    merged = {**cfg["defaults"], **{k: v for k, v in override.items() if k not in ("sectionWeights", "notes")}}
    merged["sectionWeights"] = {**cfg["defaults"].get("sectionWeights", {}), **override.get("sectionWeights", {})}
    return merged


def item_weight(item: dict, settings: dict) -> tuple[float, str | None]:
    """Heaviest matching keyword wins (section and title searched); otherwise perItemMinutes."""
    text = f"{item.get('section') or ''} {item.get('title') or ''}"
    best: tuple[float, str | None] | None = None
    for pattern, minutes in settings["sectionWeights"].items():
        if re.search(pattern, text, re.I) and (best is None or minutes > best[0]):
            best = (minutes, pattern)
    return best if best else (settings["perItemMinutes"], None)


def _round_up(value: float, step: float) -> int:
    return int(math.ceil(value / step - 1e-9) * step)


def _hours(minutes: int) -> str:
    hours, rest = divmod(int(minutes), 60)
    return f"{hours} h {rest} min" if hours and rest else f"{hours} h" if hours else f"{rest} min"


def estimate_duration(agency_id: str, items: list[dict], cfg: dict | None = None) -> dict:
    """Return {'minutes', 'basis'} for an agenda, always on the long side."""
    s = agency_settings(agency_id, cfg)
    step, bias = s["roundUpToMinutes"], s["longBiasFactor"]
    if s["mode"] == "fixed":
        minutes = _round_up(min(max(s["fixedMinutes"] * bias, s["minMinutes"]), s["maxMinutes"]), step)
        return {"minutes": minutes, "basis": f"This body usually runs about {_hours(s['fixedMinutes'])} regardless of agenda; "
                                             f"padded to {_hours(minutes)} so the calendar block is not too short."}
    weights = [item_weight(i, s) for i in items]
    raw = s["baseMinutes"] + sum(w for w, _ in weights)
    minutes = _round_up(min(max(raw * bias, s["minMinutes"]), s["maxMinutes"]), step)
    if not items:
        return {"minutes": minutes, "basis": f"No agenda items are available yet; using a typical length of {_hours(minutes)} for this body."}
    heavy: dict[str, int] = {}
    for weight, pattern in weights:
        if pattern and weight >= 15:
            heavy[pattern] = heavy.get(pattern, 0) + 1
    extra = ", ".join(f"{n} {re.sub(r'[()?]', '', p.split('|')[0])}" for p, n in sorted(heavy.items(), key=lambda kv: -kv[1]))
    noun = "item" if len(items) == 1 else "items"
    return {"minutes": minutes, "basis": f"Estimated from {len(items)} agenda {noun}" + (f" including {extra}" if extra else "") + "."}


def meeting_duration(row: dict, items: list[dict], cfg: dict | None = None) -> dict:
    """Public duration for a meeting row; published/observed values win over estimates."""
    minutes, source = row.get("observed_duration_minutes"), row.get("duration_source")
    if minutes:
        label = {"minutes": "the meeting minutes", "minutes-text": "the meeting minutes", "recording": "the meeting recording",
                 "manual": "an operator entry", "published": "the agency"}.get(source, "a recorded source")
        return {"minutes": int(minutes), "source": "published" if source == "published" else "observed",
                "basis": f"Length of {_hours(minutes)} taken from {label}."}
    return {**estimate_duration(row["agency_id"], items, cfg), "source": "estimated"}


# --- Minutes-text detector ---------------------------------------------------
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?"
_START = re.compile(r"(?:called\s+to\s+order|call\s+to\s+order|convened|commenced)\b[^.\n]{0,60}?" + _TIME, re.I)
_END = re.compile(r"(?:adjourned?|adjournment)\b[^.\n]{0,60}?" + _TIME, re.I)


def _minute_of_day(match: re.Match) -> int | None:
    hour, minute = int(match[1]), int(match[2] or 0)
    if not (1 <= hour <= 12 and minute < 60):
        return None
    return (hour % 12 + (12 if match[3].lower() == "p" else 0)) * 60 + minute


def detect_duration(text: str) -> dict:
    """Both times must parse and be unambiguous; otherwise no duration is reported."""
    starts = {_minute_of_day(m) for m in _START.finditer(text)} - {None}
    ends = {_minute_of_day(m) for m in _END.finditer(text)} - {None}
    if len(starts) != 1 or len(ends) != 1:
        return {"minutes": None, "reason": f"{len(starts)} start time(s) and {len(ends)} end time(s) found"}
    start, end = starts.pop(), ends.pop()
    minutes = end - start
    if not 10 <= minutes <= 16 * 60:
        return {"minutes": None, "reason": f"implausible span of {minutes} minutes"}
    return {"minutes": minutes, "start": f"{start // 60}:{start % 60:02d}", "end": f"{end // 60}:{end % 60:02d}"}


# --- Calibration ---------------------------------------------------------------
def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def calibration_rows(rows: list[dict]) -> list[dict]:
    """rows: [{'agency_id', 'observed', 'estimate'}] -> per-agency summary."""
    out = []
    for agency in sorted({r["agency_id"] for r in rows}):
        mine = [r for r in rows if r["agency_id"] == agency]
        obs, est = [r["observed"] for r in mine], [r["estimate"] for r in mine]
        errors = [e - o for e, o in zip(est, obs)]
        out.append({"agency": agency, "count": len(mine), "observedMedian": statistics.median(obs),
                    "observedP90": round(percentile(obs, 0.9), 1), "estimateMedian": statistics.median(est),
                    "meanErrorMinutes": round(statistics.mean(errors), 1), "tooShort": sum(1 for e in errors if e < 0)})
    return out
