"""ktp-data-server-health.sh: a value parked on a threshold must not oscillate.

The latch functions and the threshold defaults are extracted from the real
script, between the `# >>> ktp-alert-latch` markers and by name, so renaming or
deleting them fails this file rather than silently testing a copy that no longer
ships.
"""
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ktp-data-server-health.sh"
BEGIN, END = "# >>> ktp-alert-latch", "# <<< ktp-alert-latch"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def latch_source():
    text = SCRIPT.read_text(encoding="utf-8")
    assert BEGIN in text and END in text, "latch markers are gone from the shipped script"
    # Drop the remainder of the marker's own line: it is prose, not shell.
    return text.split(BEGIN, 1)[1].split("\n", 1)[1].split(END, 1)[0]


def bash(tmp_path, body, name="probe.sh"):
    """Run a script from a FILE.

    Not `bash -c`: msys2 re-parses a multi-line -c argument on Windows, which
    silently eats the positional parameters and executes backticks inside
    comments. The failure looks like a bug in the script under test.
    """
    p = tmp_path / name
    p.write_text(body, encoding="utf-8", newline="\n")
    r = subprocess.run(["bash", p.as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def run_series(tmp_path, values, warn, clear):
    """Replay a value series through the latch, carrying state between runs.

    Returns the per-step verdicts (True = the item would be reported).
    """
    prev = tmp_path / "prev.list"
    prev.write_text("", encoding="utf-8")
    src = latch_source()
    out = []
    for i, v in enumerate(values):
        body = "%s\nPREV_LIST=%s\nif latched k '%s' %s %s; then echo YES; else echo NO; fi\n" % (
            src, prev.as_posix(), v, warn, clear)
        reported = bash(tmp_path, body, "probe%d.sh" % i).strip() == "YES"
        out.append(reported)
        prev.write_text("k\n" if reported else "", encoding="utf-8")
    return out


def transitions(verdicts):
    """(fires, clears) — how many alerts the channel would carry."""
    fires = clears = 0
    for a, b in zip([False] + verdicts, verdicts):
        if b and not a:
            fires += 1
        elif a and not b:
            clears += 1
    return fires, clears


# The real hourly rates for `/` from ktp-data-server-health.log, 2026-09-13 20:00
# through 2026-09-14 11:00. Bucketing the value into the key turned this into
# four alerts: fire at 21:00, "recovered 3GiB/day+ / new 5GiB/day+" at 23:00, the
# reverse at 05:00, clear at 11:00. The two middle ones announced a recovery that
# never happened — the rate never fell below the 3 GiB/day warn level in between.
MEASURED_20260913 = [2, 3, 4, 6, 6, 5, 6, 6, 7, 4, 3, 3, 3, 2, 2, 0]


def test_the_measured_flap_becomes_one_fire_and_one_clear(tmp_path):
    v = run_series(tmp_path, MEASURED_20260913, warn=3, clear=2)
    assert transitions(v) == (1, 1), v
    assert v[0] is False and v[1] is True and v[-1] is False


def test_a_value_parked_on_the_threshold_cannot_oscillate(tmp_path):
    v = run_series(tmp_path, [3, 2, 3, 2, 3, 2, 3], warn=3, clear=2)
    assert transitions(v) == (1, 0)
    assert all(v)


def test_it_still_clears_when_the_value_really_drops(tmp_path):
    v = run_series(tmp_path, [9, 9, 1, 1], warn=3, clear=2)
    assert v == [True, True, False, False]
    assert transitions(v) == (1, 1)


def test_it_still_fires_from_a_cold_state(tmp_path):
    assert run_series(tmp_path, [0, 1, 2, 3], warn=3, clear=2) == [False, False, False, True]


def test_the_deadband_does_not_latch_an_item_that_never_fired(tmp_path):
    """Inside the band with no history is NOT reported — otherwise the latch
    would invent an alert nobody ever raised."""
    assert run_series(tmp_path, [2, 2, 2], warn=3, clear=2) == [False, False, False]


@pytest.mark.parametrize("val", ["", "n/a", "-", "1-2", "abc"])
def test_a_non_numeric_reading_is_not_an_alert(tmp_path, val):
    assert run_series(tmp_path, [val], warn=3, clear=2) == [False]


def test_a_negative_rate_is_numeric_and_below_the_band(tmp_path):
    assert run_series(tmp_path, [-3, -1], warn=3, clear=2) == [False, False]


def test_no_disk_key_carries_a_measured_number():
    """A number inside a key is what made the set comparison spam: the key must
    stay constant while the condition holds, with the magnitude in `detail`."""
    text = SCRIPT.read_text(encoding="utf-8")
    keys = [ln.strip() for ln in text.splitlines()
            if ln.strip().startswith('key="disk-')]
    assert len(keys) == 3, keys  # usage, inodes, growth
    for ln in keys:
        assert "%" not in ln and "GiB" not in ln, ln
    # Control: the pre-fix spelling would have failed the check above.
    assert "%" in 'key="disk-usage:${mount}=$(bucket5 "$pct")%+"'


def test_clear_levels_sit_strictly_below_their_warn_levels(tmp_path):
    names = ["DISK_PCT_WARN", "DISK_GROWTH_WARN_GIB", "DISK_PCT_CLEAR", "DISK_GROWTH_CLEAR_GIB"]
    text = SCRIPT.read_text(encoding="utf-8")
    defs = {n: next((ln for ln in text.splitlines() if ln.startswith(n + "=")), None)
            for n in names}
    assert all(defs.values()), defs
    body = "\n".join(defs[n] for n in names) + '\necho "$%s"\n' % '" "$'.join(names)
    pw, gw, pc, gc = (int(x) for x in bash(tmp_path, body).split())
    assert pc < pw, (pc, pw)
    assert gc < gw, (gc, gw)
