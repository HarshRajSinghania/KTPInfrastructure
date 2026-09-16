"""ktp-fleet-health.sh: the monitor-cron gate must not alert on the nightly restart.

`ktp-scheduled-restart.sh` strips the per-instance monitor cron lines for the
length of the restart, so a bare count fires a warn and a clear on every host
every night. The gate reads that script's own `monitor-cron.bak` sentinel to
tell the maintenance window apart from a restart that died and left the cron
off.

The gate is extracted from the real script between the `# >>> ktp-monitor-cron-gate`
markers, so deleting or renaming it fails this file rather than silently testing
a copy that no longer ships.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "monitoring"
    / "fleet-health"
    / "ktp-fleet-health.sh"
)
BEGIN, END = "# >>> ktp-monitor-cron-gate", "# <<< ktp-monitor-cron-gate"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

NOW = 1_800_000_000
GRACE_S = 600


def gate_source():
    text = SCRIPT.read_text(encoding="utf-8")
    assert BEGIN in text and END in text, "gate markers are gone from the shipped script"
    # Drop the remainder of the marker's own line: it is prose, not shell.
    return text.split(BEGIN, 1)[1].split("\n", 1)[1].split(END, 1)[0]


def bash(tmp_path, body, name="probe.sh"):
    """Run a script from a FILE.

    Not `bash -c`: msys2 re-parses a multi-line -c argument on Windows, which
    silently eats the positional parameters. The failure looks like a bug in the
    script under test.
    """
    p = tmp_path / name
    p.write_text(body, encoding="utf-8", newline="\n")
    r = subprocess.run(["bash", p.as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def sentinel(tmp_path, age_s, name="monitor-cron.bak"):
    """A restart cron backup last written `age_s` seconds before NOW."""
    p = tmp_path / name
    p.write_text("* * * * * dodserver monitor\n", encoding="utf-8")
    os.utime(p, (NOW - age_s, NOW - age_s))
    return p


def verdict(tmp_path, count, expected, state, sentinel_path, grace_s=GRACE_S, now=NOW):
    body = (
        "set -euo pipefail\n"
        + gate_source()
        + "\nmonitor_cron_gate %d %d %s '%s' %d %d\n"
        % (count, expected, state, sentinel_path, grace_s, now)
        + 'printf "%s %s\\n" "$CRON_VERDICT" "$CRON_STATE"\n'
    )
    out = bash(tmp_path, body).split()
    return out[0], out[1]


# --- the nightly restart, which is what the operator actually sees ------------


def test_stripped_cron_during_a_live_restart_is_silent(tmp_path):
    """0/5 with a fresh sentinel is the maintenance window, not a fault."""
    s = sentinel(tmp_path, age_s=65)
    assert verdict(tmp_path, 0, 5, "armed", s) == ("suppressed", "armed")


def test_the_whole_nightly_sequence_posts_nothing(tmp_path):
    """Replay a real night: armed -> stripped -> restored. No alert, no latch."""
    s = tmp_path / "monitor-cron.bak"
    seen = []

    # 03:00:00 cron tick, before the strip.
    seen.append(verdict(tmp_path, 5, 5, "armed", s))
    # 03:01:00 tick, mid-restart: lines gone, sentinel written a minute ago.
    sentinel(tmp_path, age_s=60)
    seen.append(verdict(tmp_path, 0, 5, "armed", s))
    # 03:02:00 tick, restart finished: lines back, sentinel removed by its trap.
    s.unlink()
    seen.append(verdict(tmp_path, 5, 5, "armed", s))

    assert seen == [("quiet", "armed"), ("suppressed", "armed"), ("quiet", "armed")]


# --- the case the alert exists for -------------------------------------------


def test_restart_that_died_mid_flight_alerts_once_grace_expires(tmp_path):
    """A stale sentinel means nothing is going to put the cron back."""
    s = sentinel(tmp_path, age_s=GRACE_S + 1)
    assert verdict(tmp_path, 0, 5, "armed", s) == ("warn", "incomplete")


def test_missing_lines_with_no_restart_in_flight_alerts_immediately(tmp_path):
    """No sentinel at all: an edit or a failed restore, not maintenance."""
    assert verdict(tmp_path, 0, 5, "armed", tmp_path / "absent.bak") == (
        "warn",
        "incomplete",
    )


def test_one_missing_instance_line_still_alerts(tmp_path):
    """4/5 is a real hole: that instance has nothing to restart it."""
    assert verdict(tmp_path, 4, 5, "armed", tmp_path / "absent.bak") == (
        "warn",
        "incomplete",
    )


def test_recovery_clears_the_latch(tmp_path):
    assert verdict(tmp_path, 5, 5, "incomplete", tmp_path / "absent.bak") == (
        "clear",
        "armed",
    )


def test_warn_does_not_repeat_while_incomplete(tmp_path):
    """One post per transition — the latch already held; do not re-post."""
    assert verdict(tmp_path, 0, 5, "incomplete", tmp_path / "absent.bak") == (
        "quiet",
        "incomplete",
    )


# --- the guard must fail towards alerting ------------------------------------


def test_unreadable_sentinel_alerts_rather_than_suppressing(tmp_path):
    """A directory where the backup should be: stat gives no mtime we can trust."""
    d = tmp_path / "monitor-cron.bak"
    d.mkdir()
    # -f is false for a directory, so this lands on the no-sentinel path.
    assert verdict(tmp_path, 0, 5, "armed", d) == ("warn", "incomplete")


def test_sentinel_from_the_future_alerts_rather_than_suppressing(tmp_path):
    """A backwards clock jump must not open an unbounded suppression window."""
    s = sentinel(tmp_path, age_s=-3600)
    assert verdict(tmp_path, 0, 5, "armed", s) == ("warn", "incomplete")


def test_grace_boundary_is_exclusive(tmp_path):
    """Exactly at the grace it is already stale — a value parked on the edge
    must resolve one way, not oscillate with the second hand."""
    assert verdict(tmp_path, 0, 5, "armed", sentinel(tmp_path, GRACE_S))[0] == "warn"
    assert (
        verdict(tmp_path, 0, 5, "armed", sentinel(tmp_path, GRACE_S - 1))[0]
        == "suppressed"
    )


# --- Chicago: 4 instances by design ------------------------------------------


def test_chicago_four_of_four_is_healthy(tmp_path):
    """EXPECTED=4 per-host. Counting against NUM_INSTANCES would false-alarm."""
    assert verdict(tmp_path, 4, 4, "armed", tmp_path / "absent.bak") == (
        "quiet",
        "armed",
    )


def test_chicago_stripped_cron_is_suppressed_too(tmp_path):
    """Chicago escapes today only because its restart finishes inside the minute.
    The gate must not depend on that margin."""
    s = sentinel(tmp_path, age_s=50)
    assert verdict(tmp_path, 0, 4, "armed", s) == ("suppressed", "armed")
