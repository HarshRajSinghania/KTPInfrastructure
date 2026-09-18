"""Offline tests for scripts/ktp-telemetry-export.py.

No MySQL, no network. The fake below captures the SQL and hands back rows, which
is enough to pin the three properties that would fail silently in production: the
window it asks for, the order it sends, and the refusal to push an empty document.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ktp-telemetry-export.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("_ktp_telemetry_export", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Db:
    """Captures the SQL instead of running it."""

    def __init__(self, rows=None, stamp="1758100000"):
        self.rows = rows if rows is not None else []
        self.stamp = stamp
        self.sql = []

    def json_rows(self, sql):
        self.sql.append(sql)
        return list(self.rows)

    def scalar(self, sql):
        self.sql.append(sql)
        return self.stamp


def test_window_is_the_requested_days(mod):
    db = _Db()
    mod.fetch_rows(db, 30)
    assert "ktp_telemetry_baselines" in db.sql[0]
    assert "INTERVAL 30 DAY" in db.sql[0]


def test_window_days_reaches_sql_as_an_int(mod):
    """The day count is coerced, so nothing a caller supplies lands in SQL verbatim."""
    db = _Db()
    mod.fetch_rows(db, "7")
    assert "INTERVAL 7 DAY" in db.sql[0]
    with pytest.raises(ValueError):
        mod.fetch_rows(db, "7; DROP TABLE x")


def test_rows_are_sorted_by_endpoint_then_day(mod):
    """JSON_ARRAYAGG promises no order, and an unordered payload still renders."""
    db = _Db(rows=[
        {"endpoint": "b", "day": "2026-09-02"},
        {"endpoint": "a", "day": "2026-09-03"},
        {"endpoint": "b", "day": "2026-09-01"},
        {"endpoint": "a", "day": "2026-09-01"},
    ])
    got = [(r["endpoint"], r["day"]) for r in mod.fetch_rows(db, 30)]
    assert got == [("a", "2026-09-01"), ("a", "2026-09-03"),
                   ("b", "2026-09-01"), ("b", "2026-09-02")]


def test_sort_survives_a_row_missing_its_keys(mod):
    """One malformed row must not take the export down with a TypeError."""
    db = _Db(rows=[{"endpoint": "a", "day": "2026-09-01"}, {}])
    assert len(mod.fetch_rows(db, 30)) == 2


def test_source_stamp_is_carried_separately_from_generated(mod):
    """A stalled rollup is only visible if the two stamps can disagree."""
    payload = mod.build([{"endpoint": "a"}], 1758100000, 30)
    assert payload["source_computed_at"] == 1758100000
    assert payload["generated"] >= 1758100000
    assert payload["window_days"] == 30


def test_source_stamp_degrades_to_none(mod):
    """An unreadable MAX(computed_at) must not be reported as an epoch of 0."""
    assert mod.fetch_source_stamp(_Db(stamp=None), 30) is None
    assert mod.fetch_source_stamp(_Db(stamp="not-a-number"), 30) is None


def test_empty_window_exits_non_zero(mod, monkeypatch):
    """The trap this exists for: no rows is a dead rollup, not an empty fleet.

    Exiting 0 would push an empty document and let the panel draw a blank fleet
    as though it had measured one.
    """
    monkeypatch.setattr(mod, "Db", lambda *a, **k: _Db(rows=[]))
    monkeypatch.setattr(mod, "load_config", lambda: {
        "TELEMETRY_INGEST_URL": "https://example.invalid/x",
        "TELEMETRY_INGEST_SECRET": "s",
    })
    monkeypatch.setattr(sys, "argv", ["ktp-telemetry-export.py"])
    monkeypatch.setattr(mod, "post", lambda *a, **k: pytest.fail("posted an empty document"))
    assert mod.main() == 1


def test_missing_config_exits_before_posting(mod, monkeypatch):
    """A half-configured host must fail loudly, not POST to nowhere."""
    monkeypatch.setattr(mod, "Db", lambda *a, **k: _Db(rows=[{"endpoint": "a", "day": "d"}]))
    monkeypatch.setattr(mod, "load_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["ktp-telemetry-export.py", "--quiet"])
    monkeypatch.setattr(mod, "post", lambda *a, **k: pytest.fail("posted without a secret"))
    assert mod.main() == 2


def test_dry_run_posts_nothing(mod, monkeypatch, capsys):
    """--dry-run is how the producer half is verified before the route exists."""
    monkeypatch.setattr(mod, "Db", lambda *a, **k: _Db(rows=[{"endpoint": "a", "day": "d"}]))
    monkeypatch.setattr(mod, "load_config", lambda: {})
    monkeypatch.setattr(sys, "argv", ["ktp-telemetry-export.py", "--dry-run", "--quiet"])
    monkeypatch.setattr(mod, "post", lambda *a, **k: pytest.fail("dry run posted"))
    assert mod.main() == 0
    assert '"rows"' in capsys.readouterr().out
