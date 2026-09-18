"""trends.shape and the sparkline geometry, without a database or a clock."""

from datetime import date

from app.poller import Instance
from app.trends import SPARK_H, SPARK_PAD, SPARK_W, shape, sparkline_points

DAL1 = Instance("Dallas", "Dallas 1", "74.91.126.55", 27015)
DAL2 = Instance("Dallas", "Dallas 2", "74.91.126.55", 27016)


def row(ep, d, fps, spikes=0, warn_fps=0, warn_spikes=0):
    return {"server_endpoint": ep, "day": date(2026, 9, d), "fps_p50_today": fps,
            "spike_total_today": spikes, "warn_fps": warn_fps, "warn_spikes": warn_spikes}


def test_servers_come_from_the_fleet_in_fleet_order_not_from_the_rows():
    """An instance that stopped reporting still gets a row -- that IS the
    finding -- and an endpoint the fleet does not list is not a server."""
    rows = [row("74.91.126.55:27016", 1, 999.0), row("10.0.0.9:27015", 1, 500.0)]
    out = shape(rows, [DAL1, DAL2])
    assert [t["label"] for t in out] == ["Dallas 1", "Dallas 2"]
    assert out[0]["days"] == 0 and out[0]["latest"] is None and out[0]["points"] == ""
    assert out[1]["days"] == 1


def test_days_sort_and_summaries_are_over_plotted_values_only():
    rows = [row("74.91.126.55:27015", 3, 997.5, spikes=2, warn_fps=1),
            row("74.91.126.55:27015", 1, 999.2),
            row("74.91.126.55:27015", 2, None, spikes=1)]
    t = shape(rows, [DAL1])[0]
    assert [d["day"] for d in t["series"]] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert t["latest"] == 997.5 and t["min"] == 997.5 and t["max"] == 999.2
    assert t["spikes"] == 3 and t["warn_days"] == 1
    assert len(t["warn_points"]) == 1


def test_unreadable_values_degrade_the_row_not_the_page():
    rows = [row("74.91.126.55:27015", 1, "not a number", spikes=None)]
    t = shape(rows, [DAL1])[0]
    assert t["latest"] is None and t["spikes"] == 0 and t["points"] == ""


def test_sparkline_needs_two_points_and_spans_the_box():
    assert sparkline_points([999.0]) == ""
    assert sparkline_points([None, 999.0]) == ""
    pts = sparkline_points([990.0, 1000.0]).split()
    x0, y0 = map(float, pts[0].split(","))
    x1, y1 = map(float, pts[1].split(","))
    assert (x0, x1) == (SPARK_PAD, SPARK_W - SPARK_PAD)
    assert y0 == SPARK_H - SPARK_PAD and y1 == SPARK_PAD   # low draws low, high draws high


def test_a_flat_healthy_server_draws_flat_not_as_noise():
    """998.9 vs 999.4 is not a trend. Below the minimum span the scale widens
    so the line sits mid-box instead of swinging edge to edge."""
    pts = [float(p.split(",")[1]) for p in sparkline_points([998.9, 999.4, 999.1]).split()]
    assert max(pts) - min(pts) < (SPARK_H - 2 * SPARK_PAD) / 2


def test_a_missing_day_is_a_gap_not_a_line():
    pts = sparkline_points([990.0, None, 1000.0]).split()
    assert len(pts) == 2
    assert float(pts[1].split(",")[0]) == SPARK_W - SPARK_PAD   # third slot, not second
