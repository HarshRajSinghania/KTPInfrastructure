"""ktp-data-server-health.sh: hit registration is a number, and it is watched.

The 2026-09 hitreg investigation ended on one figure: of the shot rows where
the server's own trace hit a live enemy cleanly, 99.9% have a damage row for
the same attacker/victim within 300 ms (12,204 real hits, every half at
99.2-100%). Nothing read that figure after the investigation closed. The block
under test is the producer that turns ktp_hitreg_quality rows into a latched
health item, in misses per thousand so the latch's integer compare can see
the 99.0-99.9% band.

The reducer and the latch are extracted from the shipped script by marker, so
renaming or deleting either fails this file rather than testing a copy that
no longer ships. No database is involved: the reducer takes the row mysql
would have printed, on stdin.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ktp-data-server-health.sh"
BASH = os.environ.get("KTP_TEST_BASH", "bash")

pytestmark = pytest.mark.skipif(shutil.which(BASH) is None, reason="needs bash")


def block(name):
    begin, end = "# >>> %s" % name, "# <<< %s" % name
    text = SCRIPT.read_text(encoding="utf-8")
    assert begin in text and end in text, "%s markers are gone from the shipped script" % name
    return text.split(begin, 1)[1].split("\n", 1)[1].split(end, 1)[0]


def bash(tmp_path, body, name="probe.sh"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8", newline="\n")
    r = subprocess.run([BASH, p.as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def reduce(tmp_path, row, **env):
    """Feed the mysql-shaped window row through hitreg_reg_rows; return parsed output."""
    exports = "".join("%s=%s\n" % kv for kv in env.items())
    body = "%s\n%s\nhitreg_reg_rows <<'ROWS'\n%s\nROWS\n" % (block("ktp-hitreg-reg"), exports, row)
    out = []
    for line in bash(tmp_path, body).splitlines():
        missed, clean, registered, halves = line.split("\t")
        out.append((int(missed), int(clean), int(registered), int(halves)))
    return out


def test_misses_are_reported_per_thousand_rounded(tmp_path):
    """The phase-2 fleet figure: 12,190 of 12,204 registered -> 1.15 per
    thousand missed -> 1. A half at the 99.2% floor -> 8."""
    assert reduce(tmp_path, "12204\t12190\t34") == [(1, 12204, 12190, 34)]
    assert reduce(tmp_path, "1000\t992\t1") == [(8, 1000, 992, 1)]
    assert reduce(tmp_path, "400\t400\t2") == [(0, 400, 400, 2)]


def test_a_window_under_the_floor_is_not_scored(tmp_path):
    """One lost row in a 40-hit warmup half is 25 per thousand. That must not page."""
    assert reduce(tmp_path, "40\t39\t1") == []
    assert reduce(tmp_path, "299\t290\t1") == []
    assert reduce(tmp_path, "300\t290\t1") == [(33, 300, 290, 1)]


def test_an_empty_window_produces_nothing(tmp_path):
    """SUM() over no rows prints NULL; a quiet week must read as nothing to
    say, not as zero misses out of zero hits."""
    assert reduce(tmp_path, "NULL\tNULL\t0") == []
    assert reduce(tmp_path, "") == []
    assert reduce(tmp_path, "not a row") == []


def test_floor_is_tunable(tmp_path):
    assert reduce(tmp_path, "150\t147\t1", HITREG_MIN_CLEAN=100) == [(20, 150, 147, 1)]


def replay(tmp_path, series):
    """Run a per-run misses-per-thousand series through the real latch,
    carrying the previous-down set between runs the way the hourly cron does."""
    prev = tmp_path / "prev.list"
    prev.write_text("", encoding="utf-8")
    src = block("ktp-alert-latch") + block("ktp-hitreg-reg")
    verdicts = []
    for i, permille in enumerate(series):
        body = ("%s\nPREV_LIST=%s\nif latched hitreg-reg %d \"$HITREG_WARN_PERMILLE\" "
                "\"$HITREG_CLEAR_PERMILLE\"; then echo YES; else echo NO; fi\n"
                % (src, prev.as_posix(), permille))
        fired = bash(tmp_path, body, "probe%d.sh" % i).strip() == "YES"
        verdicts.append(fired)
        prev.write_text("hitreg-reg\n" if fired else "", encoding="utf-8")
    return verdicts


def test_a_regression_pages_once_and_clears_once(tmp_path):
    """Measured normal is 0-2 per thousand. A build that starts losing 1.5% of
    clean hits pages on the first window that shows it, holds while it eases
    through the 0.5-1.0% band, and releases only once it is back under 0.5%."""
    series = [1, 1, 15, 22, 9, 6, 4, 1]
    assert replay(tmp_path, series) == [False, False, True, True, True, True, False, False]


def test_a_value_parked_between_clear_and_warn_does_not_flap(tmp_path):
    assert replay(tmp_path, [6, 8, 7, 9]) == [False] * 4
    assert replay(tmp_path, [12, 7, 9, 6]) == [True] * 4
