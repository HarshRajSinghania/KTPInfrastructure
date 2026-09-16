"""Per-server performance trends, shaped for the admin page.

The data is already in MySQL and already rolled to a day: `ktp_telemetry_baselines`
carries one row per server per day with the p50 FPS, the spike count and the two
warn flags the aggregator computed. 139 consecutive days, 24 servers on every one
of them. The 30-day dashboard TEST_INFRASTRUCTURE_PLAN.md deferred pending Grafana
is that table and a GROUP BY -- so this module is shaping, not measuring, and the
page renders it with the auth and tier gating the site already has.

Pure: `shape()` takes the rows `store.telemetry_days()` returns plus the fleet
list, and returns what the template draws. No connection, no clock, so it is
tested without either. Anything unreadable in a row degrades that row, never the
page -- a status surface that 500s on its data source defeats its own purpose.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from .poller import Instance

# Sparkline geometry. Width fits the table cell at every breakpoint the page
# uses; height leaves room for a 2px line plus 3px warn markers without
# clipping at either edge.
SPARK_W = 160
SPARK_H = 28
SPARK_PAD = 3

# ponytail: a per-row autoscale with a floor. p50 FPS sits at 998-1000 on a
# healthy server, so a raw autoscale would draw sub-FPS noise as a cliff. Any
# span narrower than this many FPS is widened to it, centred, so a flat server
# draws flat. Widen if the fleet ever runs a legitimately narrower band.
SPARK_MIN_SPAN = 2.0


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _day(v) -> str:
    return v.isoformat() if isinstance(v, date) else str(v)


def sparkline_points(values: list[float | None]) -> str:
    """SVG polyline `points` for a value series, left to right.

    Nones break the line rather than being interpolated: a missing day is a gap,
    and drawing across it would invent a reading. Returns "" for fewer than two
    plottable points -- a single dot is not a trend.
    """
    plotted = [v for v in values if v is not None]
    if len(plotted) < 2 or len(values) < 2:
        return ""
    lo, hi = min(plotted), max(plotted)
    if hi - lo < SPARK_MIN_SPAN:
        mid = (hi + lo) / 2
        lo, hi = mid - SPARK_MIN_SPAN / 2, mid + SPARK_MIN_SPAN / 2
    step = (SPARK_W - 2 * SPARK_PAD) / (len(values) - 1)
    usable = SPARK_H - 2 * SPARK_PAD
    out = []
    for i, v in enumerate(values):
        if v is None:
            continue
        x = SPARK_PAD + i * step
        y = SPARK_PAD + usable * (1 - (v - lo) / (hi - lo))
        out.append(f"{x:.1f},{y:.1f}")
    return " ".join(out)


def shape(rows: list[dict], fleet: list[Instance]) -> list[dict]:
    """Group daily rows per server, in the fleet's own order.

    Servers come from `fleet`, not from the rows: an instance that has stopped
    reporting still gets a row -- with no data -- which is the finding, and the
    order stays geographic rather than whatever MySQL returned. An endpoint in
    the rows that is not in the fleet is ignored; the page is about the fleet.
    """
    by_endpoint: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        ep = r.get("server_endpoint")
        if isinstance(ep, str):
            by_endpoint[ep].append(r)

    out = []
    for inst in fleet:
        days = sorted(by_endpoint.get(f"{inst.ip}:{inst.port}", []), key=lambda r: _day(r.get("day")))
        series = []
        for r in days:
            series.append({
                "day": _day(r.get("day")),
                "fps": _f(r.get("fps_p50_today")),
                "spikes": int(r.get("spike_total_today") or 0),
                "warn": bool(r.get("warn_fps")) or bool(r.get("warn_spikes")),
            })
        fps = [d["fps"] for d in series]
        plotted = [v for v in fps if v is not None]
        out.append({
            "region": inst.region,
            "label": inst.label,
            "days": len(series),
            "latest": plotted[-1] if plotted else None,
            "min": min(plotted) if plotted else None,
            "max": max(plotted) if plotted else None,
            "spikes": sum(d["spikes"] for d in series),
            "warn_days": sum(1 for d in series if d["warn"]),
            "points": sparkline_points(fps),
            # Marker coordinates for warn days, so the reader can see WHICH day
            # without hovering; the count beside it is the non-colour channel.
            "warn_points": _warn_points(fps, [d["warn"] for d in series]),
            "series": series,
        })
    return out


def _warn_points(fps: list[float | None], warn: list[bool]) -> list[tuple[float, float]]:
    pts = sparkline_points(fps).split()
    if not pts:
        return []
    # sparkline_points skipped the None days; walk both lists in step.
    coords = iter(pts)
    out = []
    for v, w in zip(fps, warn):
        if v is None:
            continue
        x, y = next(coords).split(",")
        if w:
            out.append((float(x), float(y)))
    return out
