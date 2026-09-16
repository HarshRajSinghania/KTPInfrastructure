"""ktp-spike-digest: every severity branch must be reachable by this fleet.

The digest shipped with floors set above the fleet's observed range, so the
σ-breach branch and the ≥100ms promotion could not fire at all. The embed had
exactly one live path to a non-green colour (a never-seen fingerprint), and a
digest that is always green is as uninformative as a check that is always red.

It cost a real event. On 2026-09-13 the fleet recorded its worst day in the
table's history and the only ≥100ms frame since July, and the embed posted
green — indistinguishable from the 3-frame day that followed it. The board
re-derivation that found that frame queried the table on a scheduled card
trigger; it did not come from the digest.

The day shapes below are transcribed from `ktp_spike_daily` and from the posted
embeds in /var/log/ktp-spike-digest.log. They are fixtures, not a distribution
to re-tune against: what each test asserts is that a branch CAN fire on a shape
this fleet has actually produced, and that an ordinary day still cannot.

`pymysql` is imported at module scope by the script and the unit lane installs
only pytest + jsonschema, so it is stubbed before the load. The stub is never
called: nothing here opens a connection.
"""

from __future__ import annotations

import datetime
import importlib.util
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPTS = os.path.join(_ROOT, "scripts")


def _stub_pymysql():
    if "pymysql" in sys.modules:
        return
    stub = types.ModuleType("pymysql")
    stub.connect = lambda **kw: None
    cursors = types.ModuleType("pymysql.cursors")
    cursors.DictCursor = object
    stub.cursors = cursors
    sys.modules["pymysql"] = stub
    sys.modules["pymysql.cursors"] = cursors


def _load(mod_name, filename):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(_SCRIPTS, filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_stub_pymysql()
sd = _load("ktp_spike_digest", "ktp-spike-digest.py")

GREEN, YELLOW, RED = sd.KTP_GREEN, sd.KTP_YELLOW, sd.KTP_RED

NYC = "74.91.123.64:27016"
ATL = "74.91.121.9:27015"


def rows(*triples):
    """(fingerprint, bucket, count) -> the shape fetch_day_rows returns."""
    return [
        {"fingerprint": fp, "phase": fp.split(":", 1)[0], "magnitude_bucket": bucket,
         "server_endpoint": NYC, "n": n}
        for fp, bucket, n in triples
    ]


def colour(day_rows, history_series=None, day=datetime.date(2026, 9, 13)):
    """Build the embed the cron would post and return its colour.

    history_series maps a fingerprint to its per-day totals over the trailing
    window, oldest first. Anything absent is 0 on every day, which is what the
    real fetch_history produces for a fingerprint that did not fire.
    """
    history_series = history_series or {}
    hdays = [day - datetime.timedelta(days=n) for n in range(sd.BASELINE_WINDOW_DAYS, 0, -1)]
    history = {}
    for fp, series in history_series.items():
        assert len(series) == len(hdays), "a series must cover the whole window"
        for d, n in zip(hdays, series):
            history[(fp, d)] = n
    embed = sd.build_embed(day, sd.summarize_day(day_rows), history, set(hdays), [], [])
    assert embed is not None
    return embed["color"]


# ── the branch that stayed dark ─────────────────────────────────────────────

def test_a_single_severe_frame_is_not_green():
    """2026-09-13 as recorded: one READ:100-250ms frame on nyc:27016.

    The whole point of the ≥100ms tier. Under the shipped floor this returned
    green, so the rarest event the digest measures looked like a quiet day.
    """
    day = rows(("READ:100-250ms", "100-250ms", 1),
               ("READ:25-50ms", "25-50ms", 3),
               ("READ:10-25ms", "10-25ms", 9),
               ("STEAM:10-25ms", "10-25ms", 1),
               ("READ:0-5ms", "0-5ms", 2837))
    assert colour(day, {"READ:10-25ms": [5, 6, 4, 7, 2, 9, 11]}) == YELLOW


def test_severe_promotion_is_reachable_at_one_occurrence():
    """Both ≥100ms events in the table's history are single frames.

    A floor above one makes the tier unreachable, which is the defect this
    file exists to prevent recurring — assert the property, not the number.
    """
    assert sd.SEVERE_YELLOW_FLOOR <= 1
    for bucket in sorted(sd.SEVERE_BUCKETS):
        assert colour(rows((f"READ:{bucket}", bucket, 1))) == YELLOW, bucket


def test_sigma_breach_can_fire_on_an_observed_day():
    """2026-09-06 as recorded: READ:10-25ms at 10 against a 7d mean near 2.

    A real excursion, five times its own baseline. The shipped floor of 20 sat
    above every fleet-day total the table has ever held, so this returned green.
    """
    day = rows(("READ:10-25ms", "10-25ms", 10), ("READ:0-5ms", "0-5ms", 2827))
    assert colour(day, {"READ:10-25ms": [2, 1, 3, 2, 0, 2, 4]}) == YELLOW


def test_breach_floor_sits_under_the_fleets_observed_range():
    """The worst single-fingerprint fleet-day in the table is 13 (2026-07-12).

    A floor at or above that is not a strict gate, it is an unreachable one.
    """
    assert sd.BREACH_FLOOR < 13


# ── and the branch that must stay dark ──────────────────────────────────────

def test_an_ordinary_day_stays_green():
    """2026-09-14 as recorded. Lowering a floor must not make every day fire."""
    day = rows(("STEAM:25-50ms", "25-50ms", 1),
               ("READ:10-25ms", "10-25ms", 1),
               ("STEAM:10-25ms", "10-25ms", 1),
               ("READ:0-5ms", "0-5ms", 1939))
    assert colour(day, {"READ:10-25ms": [9, 7, 4, 2, 6, 5, 6]}) == GREEN


def test_noise_alone_is_green_however_large():
    """Sub-10ms frames are the declared noise floor: volume there is not news."""
    day = rows(("READ:0-5ms", "0-5ms", 9999), ("STEAM:5-10ms", "5-10ms", 500))
    assert colour(day) == GREEN


def test_a_flat_baseline_does_not_breach_on_a_small_excursion():
    """σ=0 history must not let +1 fire — the sqrt(mean) floor in breaches()."""
    assert not sd.breaches(9, mean=8.0, sd=0.0)


def test_red_needs_magnitude_as_well_as_volume():
    """A breaching 10-25ms fingerprint is yellow; red is reserved for ≥25ms."""
    mild = rows(("READ:10-25ms", "10-25ms", 12))
    assert colour(mild, {"READ:10-25ms": [1, 0, 2, 1, 0, 1, 2]}) == YELLOW
    harsh = rows(("READ:25-50ms", "25-50ms", 12))
    assert colour(harsh, {"READ:25-50ms": [1, 0, 2, 1, 0, 1, 2]}) == RED


def test_warmup_suppresses_sigma_but_not_the_severe_tier():
    """Without a baseline there is nothing to breach; a ≥100ms frame still counts."""
    day = datetime.date(2026, 9, 13)
    short = {day - datetime.timedelta(days=1)}
    embed = sd.build_embed(day, sd.summarize_day(rows(("READ:100-250ms", "100-250ms", 1))),
                           {}, short, [], [])
    assert embed["color"] == YELLOW
    assert "warming up" in embed["footer"]["text"]


def test_severity_constants_are_ordered_coherently():
    """A promotion floor above the breach floor would shadow the severe tier."""
    assert sd.SEVERE_YELLOW_FLOOR <= sd.BREACH_FLOOR <= sd.WARMUP_RED_FLOOR
    assert sd.SEVERE_BUCKETS <= sd.RED_ELIGIBLE_BUCKETS
    assert not (sd.NOISE_BUCKETS & sd.RED_ELIGIBLE_BUCKETS)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
