"""Render the summary as one self-contained HTML page (inline SVG, no network requests)."""
import datetime as dt
import html
import math

from report import STATUS, fmt
from store import SERVICES

STATUS_ICON = {"ok": "●", "watch": "▲", "risk": "▲", "over": "■", "none": "○"}

CSS = """
.viz-root { color-scheme: light; --page: #f9f9f7; --surface-1: #fcfcfb; --text-primary: #0b0b0b;
  --text-secondary: #52514e; --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --series-1: #2a78d6;
  --track: #e1e0d9; --border: rgba(11,11,11,0.10); --good: #0ca30c; --warning: #fab219; --serious: #ec835a;
  --critical: #d03b3b; }
@media (prefers-color-scheme: dark) { .viz-root { color-scheme: dark; --page: #0d0d0d; --surface-1: #1a1a19;
  --text-primary: #f0efec; --text-secondary: #c3c2b7; --grid: #2c2c2a; --axis: #383835; --series-1: #3987e5;
  --track: #383835; --border: rgba(255,255,255,0.10); } }
* { box-sizing: border-box; }
body { margin: 0; }
.viz-root { background: var(--page); color: var(--text-primary); font: 14px/1.45 system-ui, -apple-system,
  "Segoe UI", sans-serif; padding: 24px; min-height: 100vh; }
main { max-width: 1180px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 16px; margin: 0 0 4px; }
.sub, .note { color: var(--text-secondary); } .note { font-size: 13px; margin: 0 0 12px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px;
  margin: 16px 0; }
.tiles { display: flex; flex-wrap: wrap; gap: 28px; margin: 8px 0 14px; }
.tile .label { color: var(--text-secondary); font-size: 12px; }
.tile .value { font-size: 22px; font-weight: 600; }
.tile .delta { color: var(--muted); font-size: 12px; }
.charts { display: grid; grid-template-columns: 3fr 2fr; gap: 18px; }
@media (max-width: 800px) { .charts { grid-template-columns: 1fr; } }
.chart-title { color: var(--text-secondary); font-size: 12px; margin-bottom: 2px; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
svg text { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.bar { fill: var(--series-1); pointer-events: none; } .bar.partial { opacity: 0.45; }
.hit { fill: transparent; } .hit:hover + .bar, .hit:hover + .bar.partial { opacity: 0.8; }
.grid { stroke: var(--grid); stroke-width: 1; } .base { stroke: var(--axis); stroke-width: 1; }
.quota { stroke: var(--critical); stroke-width: 1; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 5px 8px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; white-space: normal; }
th { color: var(--text-secondary); font-weight: 500; font-size: 12px; }
.meter { position: relative; height: 8px; width: 120px; background: var(--track); border-radius: 4px;
  display: inline-block; vertical-align: middle; overflow: hidden; }
.meter span { position: absolute; inset: 0 auto 0 0; border-radius: 4px; }
.st-ok { color: var(--good); } .st-watch { color: var(--warning); } .st-risk { color: var(--serious); }
.st-over { color: var(--critical); } .st-none { color: var(--muted); }
.fill-ok { background: var(--good); } .fill-watch { background: var(--warning); }
.fill-risk { background: var(--serious); } .fill-over { background: var(--critical); } .fill-none { background: var(--muted); }
.status-label { color: var(--text-primary); }
details summary { cursor: pointer; color: var(--text-secondary); font-size: 13px; margin-top: 10px; }
.breakdowns { display: grid; grid-template-columns: repeat(auto-fit, minmax(520px, 1fr)); gap: 18px; margin-top: 12px; }
.fail { color: var(--critical); }
#tip { position: fixed; pointer-events: none; background: var(--surface-1); color: var(--text-primary);
  border: 1px solid var(--border); border-radius: 6px; padding: 6px 9px; font-size: 12px; display: none;
  box-shadow: 0 2px 8px rgba(0,0,0,0.12); white-space: pre; }
"""

SCRIPT = """
const tip = document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el => {
  el.addEventListener('mousemove', e => { tip.textContent = el.dataset.tip; tip.style.display = 'block';
    tip.style.left = Math.min(e.clientX + 12, innerWidth - tip.offsetWidth - 8) + 'px';
    tip.style.top = (e.clientY + 14) + 'px'; });
  el.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
});
"""


def esc(value):
    return html.escape(str(value))


def nice_max(value):
    if value <= 0:
        return 1
    magnitude = 10 ** math.floor(math.log10(value))
    return next(m * magnitude for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10) if m * magnitude >= value)


def column_chart(points, unit, quota=None, width=560, height=170):
    """points: [(axis label, tooltip label, value, partial)]. Single series, so no legend."""
    left, bottom, top = 46, 22, 10
    plot_w, plot_h = width - left - 6, height - bottom - top
    peak = max([p[2] for p in points] + [0])
    show_quota = bool(quota) and peak >= 0.2 * quota
    y_max = nice_max(max(peak, quota if show_quota else 0) * 1.05)
    y = lambda v: top + plot_h - plot_h * v / y_max
    out = [f'<svg viewBox="0 0 {width} {height}" role="img">']
    for tick in (0, y_max / 2, y_max):
        out.append(f'<line class="{"base" if tick == 0 else "grid"}" x1="{left}" x2="{width - 6}" '
                   f'y1="{y(tick):.1f}" y2="{y(tick):.1f}"/>')
        out.append(f'<text x="{left - 6}" y="{y(tick) + 4:.1f}" text-anchor="end">{esc(fmt(tick, unit))}</text>')
    band = plot_w / max(len(points), 1)
    bar_w = min(24, band * 0.62)
    every = max(1, math.ceil(len(points) / 8))
    for i, (axis, label, value, partial) in enumerate(points):
        cx = left + band * i + band / 2
        x0, top_y, base_y = cx - bar_w / 2, y(value), y(0)
        r = min(4, bar_w / 2, max(base_y - top_y, 0))
        tip = f"{label}{' (to date)' if partial else ''}\n{fmt(value, unit, True)}"
        if quota:
            tip += f"  ({100 * value / quota:.1f}% of quota)"
        out.append(f'<rect class="hit" data-tip="{esc(tip)}" x="{left + band * i:.1f}" y="{top}" '
                   f'width="{band:.1f}" height="{plot_h}"/>')
        if value > 0:
            out.append(f'<path class="bar{" partial" if partial else ""}" d="M{x0:.1f},{base_y:.1f} '
                       f'V{top_y + r:.1f} Q{x0:.1f},{top_y:.1f} {x0 + r:.1f},{top_y:.1f} H{x0 + bar_w - r:.1f} '
                       f'Q{x0 + bar_w:.1f},{top_y:.1f} {x0 + bar_w:.1f},{top_y + r:.1f} V{base_y:.1f} Z"/>')
        if i % every == 0:
            out.append(f'<text x="{cx:.1f}" y="{height - 6}" text-anchor="middle">{esc(axis)}</text>')
    if show_quota:
        out.append(f'<line class="quota" x1="{left}" x2="{width - 6}" y1="{y(quota):.1f}" y2="{y(quota):.1f}"/>')
        out.append(f'<text x="{width - 8}" y="{y(quota) - 4:.1f}" text-anchor="end">quota {esc(fmt(quota, unit))}</text>')
    out.append("</svg>")
    return "".join(out)


def meter(used_pct, state):
    if used_pct is None:
        return ""
    width = min(used_pct, 100)
    return f'<span class="meter" title="{used_pct}%"><span class="fill-{state}" style="width:{width}%"></span></span>'


def status_cell(state):
    return f'<span class="st-{state}">{STATUS_ICON[state]}</span> <span class="status-label">{STATUS[state]}</span>'


def tile(label, value, delta=""):
    return (f'<div class="tile"><div class="label">{esc(label)}</div><div class="value">{esc(value)}</div>'
            f'<div class="delta">{esc(delta)}</div></div>')


def short_day(iso):
    return dt.date.fromisoformat(iso).strftime("%b %-d")


def breakdown_tables(x):
    blocks = []
    for dim, rows in x.get("breakdown", {}).items():
        body = "".join(f"<tr><td>{esc(r['name'])}</td><td>{esc(fmt(r['past7'], x['unit']))}</td>"
                       f"<td>{esc(fmt(r['past30'], x['unit']))}</td><td>{esc(fmt(r['month_to_date'], x['unit']))}</td>"
                       f"<td>{esc(fmt(r['all_time'], x['unit']))}</td></tr>" for r in rows)
        blocks.append(f'<div><div class="chart-title">By {esc(dim)}</div><table><tr><th>{esc(dim)}</th>'
                      f"<th>Past 7 days</th><th>Past 30 days</th><th>This month</th><th>All time</th></tr>{body}</table></div>")
    return f'<div class="breakdowns">{"".join(blocks)}</div>' if blocks else ""


def metric_card(x):
    unit = x["unit"]
    tiles = [tile("Past 7 days", fmt(x["past7"], unit)), tile("Past 30 days", fmt(x["past30"], unit))]
    month = tile("This month", fmt(x["month_to_date"], unit),
                 f"projected {fmt(x['projected_month'], unit)}" if x["projected_month"] is not None else "")
    tiles.append(month)
    if x["quota"]:
        basis = "last 30 days" if x["window"] == "rolling30" else "this month"
        proj = f", projected {x['projected_pct']}%" if x.get("projected_pct") is not None else ""
        tiles.append(f'<div class="tile"><div class="label">Quota {esc(fmt(x["quota"], unit))} ({basis})</div>'
                     f'<div class="value">{x["quota_used_pct"]}%</div><div class="delta">{meter(x["quota_used_pct"], x["status"])} '
                     f'{status_cell(x["status"])}{esc(proj)}</div></div>')
    weekly = [(short_day(w["start"]), f"Week of {short_day(w['start'])}", w["value"], w["partial"]) for w in x["weekly"][-26:]]
    monthly = [(dt.date.fromisoformat(m["month"] + "-01").strftime("%b %Y"),
                dt.date.fromisoformat(m["month"] + "-01").strftime("%B %Y"), m["value"], m["partial"]) for m in x["monthly"]]
    month_quota = x["quota"] if x["window"] == "month" else None
    table_rows = "".join(f"<tr><td>{esc(p[1])}{' (to date)' if p[3] else ''}</td><td>{esc(fmt(p[2], unit, True))}</td></tr>"
                         for p in monthly[::-1] + weekly[::-1])
    return f"""<section class="card"><h2>{esc(x['label'])}</h2>
<p class="note">{esc(x['note'] or '')} Data from {esc(x['data_from'])}.{' <b>Anomaly:</b> yesterday was more than 3x the 7-day median.' if x['anomaly'] else ''}</p>
<div class="tiles">{''.join(tiles)}</div>
<div class="charts"><div><div class="chart-title">Weekly (Monday to Sunday, UTC; lighter = to date)</div>{column_chart(weekly, unit)}</div>
<div><div class="chart-title">Monthly{' (red line = quota)' if month_quota else ''}</div>{column_chart(monthly, unit, month_quota, width=380)}</div></div>
{breakdown_tables(x)}
<details><summary>Table view</summary><table><tr><th>Period</th><th>Total</th></tr>{table_rows}</table></details>
</section>"""


def gauge_card(x, tables):
    quota = (f'<div class="tile"><div class="label">Limit {esc(fmt(x["quota"], x["unit"]))}</div>'
             f'<div class="value">{x["quota_used_pct"]}%</div><div class="delta">{meter(x["quota_used_pct"], x["status"])} '
             f'{status_cell(x["status"])}</div></div>') if x["quota"] else ""
    history = "".join(f"<tr><td>{esc(h['day'])}</td><td>{esc(fmt(h['value'], x['unit']))}</td></tr>" for h in x["history"][::-1])
    extra = ""
    if x["service"] == "postgres" and tables:
        extra = ('<div class="chart-title">Largest tables</div><table><tr><th>Table</th><th>Size</th></tr>' +
                 "".join(f"<tr><td>{esc(t)}</td><td>{esc(fmt(v, 'bytes'))}</td></tr>" for t, v in list(tables.items())[:10]) +
                 "</table>")
    return f"""<section class="card"><h2>{esc(x['label'])}</h2><p class="note">{esc(x['note'] or '')}
Point-in-time readings, kept only from collections (history cannot be recomputed).</p>
<div class="tiles">{tile('Current', fmt(x['latest'], x['unit']), 'read ' + x['latest_at'])}{quota}</div>{extra}
<details><summary>History ({len(x['history'])} readings)</summary><table><tr><th>Day</th><th>Value</th></tr>{history}</table></details>
</section>"""


def render(summary):
    metrics = summary["metrics"]
    status_rows = "".join(
        f"<tr><td>{esc(s['name'])}</td><td>{esc(s['last_collection'] or 'never')}</td>"
        f"<td>{'ok' if s['ok'] else '<span class=fail>failed</span>' if s['last_collection'] else '-'}</td>"
        f"<td>{esc(s['message'] or '')}</td></tr>" for s in summary["services"])
    quota_rows = []
    for x in metrics:
        if not x["quota"]:
            continue
        used = x["latest"] if x["kind"] == "gauge" else x["past30"] if x["window"] == "rolling30" else x["month_to_date"]
        window = "current" if x["kind"] == "gauge" else "last 30 days" if x["window"] == "rolling30" else "this month"
        proj = f"{x['projected_pct']}%" if x.get("projected_pct") is not None else "-"
        quota_rows.append(f"<tr><td>{esc(x['label'])}</td><td>{window}</td><td>{esc(fmt(used, x['unit'], True))}</td>"
                          f"<td>{esc(fmt(x['quota'], x['unit'], True))}</td><td>{meter(x['quota_used_pct'], x['status'])} "
                          f"{x['quota_used_pct']}%</td><td>{proj}</td><td>{status_cell(x['status'])}</td></tr>")
    cards = []
    for service in SERVICES:
        for x in metrics:
            if x["service"] == service and (x["primary"] or x["kind"] == "gauge"):
                cards.append(gauge_card(x, summary["largest_tables"]) if x["kind"] == "gauge" else metric_card(x))
    others = "".join(f"<tr><td>{esc(x['label'])}</td><td>{esc(fmt(x['past7'], x['unit']))}</td>"
                     f"<td>{esc(fmt(x['past30'], x['unit']))}</td><td>{esc(fmt(x['month_to_date'], x['unit']))}</td>"
                     f"<td>{esc(fmt(x['projected_month'], x['unit']))}</td></tr>"
                     for x in metrics if not x["primary"] and x["kind"] != "gauge")
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Tahoe usage</title><style>{CSS}</style></head><body><div class="viz-root"><main>
<h1>Tahoe Public Meetings usage</h1>
<div class="sub">Generated {esc(summary['generated_at'])}. Days are UTC; today ({esc(summary['today_utc'])}) is partial.
Refresh with <code>python3 scripts/usage/usage.py</code>.</div>
<section class="card"><h2>Quotas</h2><p class="note">Monthly quotas reset on the 1st (UTC). Vercel Hobby limits are judged on the last 30 days. Projection extends this month's pace to month end.</p>
<table><tr><th>Metric</th><th>Window</th><th>Used</th><th>Quota</th><th>Used</th><th>Projected</th><th>Status</th></tr>{''.join(quota_rows)}</table></section>
{''.join(cards)}
<section class="card"><h2>Other metrics</h2><table><tr><th>Metric</th><th>Past 7 days</th><th>Past 30 days</th><th>This month</th><th>Projected month</th></tr>{others}</table></section>
<section class="card"><h2>Collection status</h2><table><tr><th>Service</th><th>Last collection (UTC)</th><th>Result</th><th>Detail</th></tr>{status_rows}</table></section>
</main></div><div id="tip"></div><script>{SCRIPT}</script></body></html>"""
