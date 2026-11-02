"""Turn the daily store into windows, series, quota status, and text summaries."""
from collections import defaultdict
import calendar
import datetime as dt
import statistics

from store import METRICS, SERVICES, SOURCES, now

STATUS = {"ok": "On track", "watch": "Watch", "risk": "At risk", "over": "Over quota", "none": "No quota"}


def fmt(value, unit, precise=False):
    if value is None:
        return "-"
    if unit == "usd":
        return f"${value:,.2f}" if value >= 0.1 or value == 0 else f"${value:,.3f}"
    if unit == "bytes":
        for size, suffix in ((1e9, "GB"), (1e6, "MB"), (1e3, "KB")):
            if abs(value) >= size:
                return f"{value / size:,.{2 if value / size < 10 else 1}f} {suffix}"
        return f"{value:,.0f} B"
    if precise or abs(value) < 10_000:
        return f"{value:,.0f}" if value >= 10 or value == int(value) else f"{value:,.1f}"
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return f"{value / size:,.{1 if value / size < 100 else 0}f}{suffix}"
    return f"{value:,.0f}"


def pct(part, whole):
    return round(100 * part / whole, 1) if whole else None


def status(used_pct, projected_pct):
    worst = max(p for p in (used_pct, projected_pct, -1) if p is not None)
    if worst < 0:
        return "none"
    if used_pct is not None and used_pct >= 100:
        return "over"
    return "risk" if worst >= 90 else "watch" if worst >= 70 else "ok"


def load(conn):
    totals = defaultdict(lambda: defaultdict(float))
    breakdown = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float))))  # key, dim, name, day
    for r in conn.execute("SELECT service, metric, dims, day, value FROM daily"):
        key = (r["service"], r["metric"])
        totals[key][r["day"]] += r["value"]
        for part in filter(None, r["dims"].split("|")):
            dim, name = part.split("=", 1)
            breakdown[key][dim][name][r["day"]] += r["value"]
    gauges = defaultdict(dict)  # key -> {taken_at: total}
    for r in conn.execute("SELECT service, metric, taken_at, SUM(value) AS value FROM snapshots "
                          "GROUP BY service, metric, taken_at ORDER BY taken_at"):
        gauges[(r["service"], r["metric"])][r["taken_at"]] = r["value"]
    tables = {}
    latest = conn.execute("SELECT MAX(taken_at) FROM snapshots WHERE metric='table_bytes'").fetchone()[0]
    for r in conn.execute("SELECT dims, value FROM snapshots WHERE metric='table_bytes' AND taken_at=? "
                          "ORDER BY value DESC", (latest,)):
        tables[r["dims"].split("=", 1)[1]] = r["value"]
    collections = {r["service"]: dict(r) for r in conn.execute(
        "SELECT c.* FROM collections c JOIN (SELECT service, MAX(run_at) AS run_at FROM collections GROUP BY service) l "
        "USING (service, run_at)")}
    last_ok = {r["service"]: r["run_at"] for r in conn.execute(
        "SELECT service, MAX(run_at) AS run_at FROM collections WHERE ok=1 GROUP BY service")}
    return totals, breakdown, gauges, tables, collections, last_ok


def window_sum(series, start, end):
    return sum(v for day, v in series.items() if start.isoformat() <= day <= end.isoformat())


def summarize(conn):
    totals, breakdown, gauges, tables, collections, last_ok = load(conn)
    moment = now()
    today = moment.date()
    month_start = today.replace(day=1)
    month_days = calendar.monthrange(today.year, today.month)[1]
    elapsed = (moment - dt.datetime.combine(month_start, dt.time(), dt.timezone.utc)).total_seconds() / 86400
    w7, w30 = today - dt.timedelta(days=6), today - dt.timedelta(days=29)
    metrics = []
    for key, meta in METRICS.items():
        service, metric = key
        base = dict(service=service, metric=metric, label=meta["label"], unit=meta["unit"],
                    kind=meta.get("kind", "sum"), quota=meta.get("quota"), window=meta.get("window"),
                    note=meta.get("note"), primary=bool(meta.get("primary")))
        if base["kind"] == "gauge":
            history = gauges.get(key, {})
            if not history:
                continue
            taken_at, value = max(history.items())
            per_day = {}
            for at, v in sorted(history.items()):
                per_day[at[:10]] = v
            used = pct(value, base["quota"])
            metrics.append(dict(base, latest=value, latest_at=taken_at, quota_used_pct=used,
                                status=status(used, None), history=[dict(day=d, value=v) for d, v in per_day.items()]))
            continue
        series = totals.get(key)
        if not series:
            continue
        first = min(series)
        mtd = window_sum(series, month_start, today)
        projected = mtd / elapsed * month_days if elapsed > 0.25 else None
        past30 = window_sum(series, w30, today)
        if base["window"] == "rolling30":
            used, projected_pct = pct(past30, base["quota"]), None
        else:
            used, projected_pct = pct(mtd, base["quota"]), pct(projected, base["quota"]) if projected else None
        yesterday = today - dt.timedelta(days=1)
        prior = [series.get((yesterday - dt.timedelta(days=i)).isoformat(), 0) for i in range(1, 8)]
        median = statistics.median(prior)
        last_full = series.get(yesterday.isoformat(), 0)
        anomaly = bool(first <= (yesterday - dt.timedelta(days=7)).isoformat() and last_full > 3 * max(median, 1e-9)
                       and last_full > 0 and (base["quota"] is None or last_full > 0.01 * base["quota"]))
        week_start = dt.date.fromisoformat(first) - dt.timedelta(days=dt.date.fromisoformat(first).weekday())
        weekly = []
        while week_start <= today:
            end = week_start + dt.timedelta(days=6)
            weekly.append(dict(start=week_start.isoformat(), value=window_sum(series, week_start, end),
                               partial=end >= today))
            week_start = end + dt.timedelta(days=1)
        monthly, month = [], dt.date.fromisoformat(first).replace(day=1)
        while month <= today:
            end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
            monthly.append(dict(month=month.isoformat()[:7], value=window_sum(series, month, end), partial=end >= today))
            month = end + dt.timedelta(days=1)
        parts = {}
        for dim, values in breakdown.get(key, {}).items():
            rows = []
            for name, own in values.items():
                rows.append(dict(name=name, past7=window_sum(own, w7, today), past30=window_sum(own, w30, today),
                                 month_to_date=window_sum(own, month_start, today), all_time=sum(own.values())))
            if len(rows) > 1 or dim in ("model", "purpose"):
                parts[dim] = sorted(rows, key=lambda r: -r["all_time"])
        metrics.append(dict(base, data_from=first, past7=window_sum(series, w7, today), past30=past30,
                            month_to_date=mtd, projected_month=projected, quota_used_pct=used,
                            projected_pct=projected_pct, status=status(used, projected_pct),
                            yesterday=last_full, prior7_median=median, anomaly=anomaly,
                            weekly=weekly, monthly=monthly, breakdown=parts))
    services = []
    for source, name in SOURCES.items():
        c = collections.get(source)
        services.append(dict(collector=source, name=name, last_collection=c and c["run_at"], ok=bool(c and c["ok"]),
                             message=c and c["message"], last_success=last_ok.get(source)))
    return dict(generated_at=moment.isoformat(timespec="seconds"), today_utc=today.isoformat(),
                windows={"past7": f"{w7} to {today}", "past30": f"{w30} to {today}",
                         "month_to_date": f"{month_start} to {today}"},
                services=services, metrics=metrics, largest_tables=tables)


def markdown(summary):
    m = [f"# Usage summary\n",
         f"Generated {summary['generated_at']} (UTC days; today, {summary['today_utc']}, is partial). "
         "Refresh with `python3 scripts/usage/usage.py`. Machine-readable: `summary.json` beside this file.\n",
         "## Collection status\n", "| Service | Last collection | Result |", "|---|---|---|"]
    for s in summary["services"]:
        result = "ok" if s["ok"] else f"FAILED: {s['message']}" if s["last_collection"] else "never collected"
        if s["ok"] and s["message"]:
            result = f"ok ({s['message']})"
        m.append(f"| {s['name']} | {s['last_collection'] or '-'} | {result} |")
    m += ["", "## Quotas\n", "Monthly quotas reset on the 1st (UTC); Vercel's are judged on the last 30 days.\n",
          "| Metric | Window | Used | Quota | Used % | Projected % | Status |", "|---|---|---|---|---|---|---|"]
    for x in summary["metrics"]:
        if not x["quota"]:
            continue
        if x["kind"] == "gauge":
            used, window = x["latest"], "current"
        else:
            used, window = (x["past30"], "last 30 days") if x["window"] == "rolling30" else (x["month_to_date"], "this month")
        proj = f"{x['projected_pct']}%" if x.get("projected_pct") is not None else "-"
        m.append(f"| {x['label']} | {window} | {fmt(used, x['unit'], True)} | {fmt(x['quota'], x['unit'], True)} | "
                 f"{x['quota_used_pct']}% | {proj} | {STATUS[x['status']]} |")
    anomalies = [x for x in summary["metrics"] if x.get("anomaly")]
    if anomalies:
        m += ["", "## Anomalies\n"]
        m += [f"- {x['label']}: yesterday {fmt(x['yesterday'], x['unit'], True)} vs a 7-day median of "
              f"{fmt(x['prior7_median'], x['unit'], True)}." for x in anomalies]
    m += ["", "## All metrics\n", "| Metric | Past 7 days | Past 30 days | This month | Projected month | Data from |",
          "|---|---|---|---|---|---|"]
    for x in summary["metrics"]:
        if x["kind"] == "gauge":
            m.append(f"| {x['label']} | current: {fmt(x['latest'], x['unit'])} | | | | {x['history'][0]['day']} |")
        else:
            m.append(f"| {x['label']} | {fmt(x['past7'], x['unit'])} | {fmt(x['past30'], x['unit'])} | "
                     f"{fmt(x['month_to_date'], x['unit'])} | {fmt(x['projected_month'], x['unit'])} | {x['data_from']} |")
    for x in summary["metrics"]:
        if not x["primary"] or x["kind"] == "gauge":
            continue
        m += ["", f"## {x['label']}\n"]
        if x["note"]:
            m.append(x["note"] + "\n")
        m += ["| Week starting | Total |", "|---|---|"]
        m += [f"| {w['start']}{' (to date)' if w['partial'] else ''} | {fmt(w['value'], x['unit'], True)} |"
              for w in x["weekly"][-8:]]
        m += ["", "| Month | Total |", "|---|---|"]
        m += [f"| {mo['month']}{' (to date)' if mo['partial'] else ''} | {fmt(mo['value'], x['unit'], True)} |"
              for mo in x["monthly"]]
        for dim, rows in x["breakdown"].items():
            m += ["", f"By {dim}:\n", f"| {dim} | Past 7 days | Past 30 days | This month | All time |", "|---|---|---|---|---|"]
            m += [f"| {r['name']} | {fmt(r['past7'], x['unit'])} | {fmt(r['past30'], x['unit'])} | "
                  f"{fmt(r['month_to_date'], x['unit'])} | {fmt(r['all_time'], x['unit'])} |" for r in rows]
    if summary["largest_tables"]:
        m += ["", "## Largest production tables\n", "| Table | Size |", "|---|---|"]
        m += [f"| {t} | {fmt(v, 'bytes')} |" for t, v in list(summary["largest_tables"].items())[:10]]
    return "\n".join(m) + "\n"
