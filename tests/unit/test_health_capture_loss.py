"""ktp-data-server-health.sh: the daemon rejecting the fleet's events is a state.

Between 2026-09-02 and 09-08 the stats daemon rejected 22-34% of every frag the
fleet sent, and one match lost 87% of its events. ktp_capture_health recorded
all of it; nothing read that table on a schedule, so it was found six days later
by someone looking for a trends dataset. The block under test is the producer
that turns those rows into a latched health item.

The reducer and the latch are extracted from the shipped script by marker, so
renaming or deleting either fails this file rather than testing a copy that no
longer ships. No database is involved: the reducer takes the rows mysql would
have printed, on stdin.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ktp-data-server-health.sh"
# CI runs plain bash; on a Windows workstation "bash" resolves to WSL's, which
# cannot see these paths -- point KTP_TEST_BASH at Git Bash there.
BASH = os.environ.get("KTP_TEST_BASH", "bash")

pytestmark = pytest.mark.skipif(shutil.which(BASH) is None, reason="needs bash")


def block(name):
    begin, end = "# >>> %s" % name, "# <<< %s" % name
    text = SCRIPT.read_text(encoding="utf-8")
    assert begin in text and end in text, "%s markers are gone from the shipped script" % name
    return text.split(begin, 1)[1].split("\n", 1)[1].split(end, 1)[0]


def bash(tmp_path, body, name="probe.sh"):
    # From a file, not `bash -c`: msys2 re-parses a multi-line -c argument on
    # Windows and the failure looks like a bug in the script under test.
    p = tmp_path / name
    p.write_text(body, encoding="utf-8", newline="\n")
    r = subprocess.run([BASH, p.as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def reduce(tmp_path, rows, **env):
    """Feed mysql-shaped rows through capture_loss_rows; return parsed output."""
    exports = "".join("%s=%s\n" % kv for kv in env.items())
    body = "%s\n%s\ncapture_loss_rows <<'ROWS'\n%s\nROWS\n" % (block("ktp-capture-loss"), exports, rows)
    out = []
    for line in bash(tmp_path, body).splitlines():
        etype, pct, received, rejected = line.split("\t")
        out.append((etype, int(pct), int(received), int(rejected)))
    return out


def test_rounds_to_an_integer_the_latch_can_compare(tmp_path):
    rows = "frag\t63959\t11575\nposition\t1188005\t17093\ndamage\t119286\t0"
    assert reduce(tmp_path, rows) == [
        ("frag", 18, 63959, 11575),        # 18.10 -> 18
        ("position", 1, 1188005, 17093),   # 1.44 -> 1
        ("damage", 0, 119286, 0),
    ]


def test_rows_under_the_floor_are_dropped_not_scored(tmp_path):
    """One rejected frag in a ten-frag warmup is 10%. That must not page."""
    rows = "frag\t10\t1\nlife\t199\t50\nposition\t200\t0"
    assert reduce(tmp_path, rows) == [("position", 0, 200, 0)]


def test_empty_and_malformed_input_produce_nothing(tmp_path):
    assert reduce(tmp_path, "") == []
    assert reduce(tmp_path, "\nnot a row\nfrag\t5000") == []


def test_floor_is_tunable(tmp_path):
    assert reduce(tmp_path, "frag\t150\t30", CAPTURE_LOSS_MIN_RECEIVED=100) == [("frag", 20, 150, 30)]


def replay(tmp_path, series):
    """Run a per-run percentage series through the real latch, carrying the
    previous-down set between runs the way the hourly cron does."""
    prev = tmp_path / "prev.list"
    prev.write_text("", encoding="utf-8")
    src = block("ktp-alert-latch") + block("ktp-capture-loss")
    verdicts = []
    for i, pct in enumerate(series):
        body = ("%s\nPREV_LIST=%s\nif latched capture-loss:frag %d \"$CAPTURE_LOSS_WARN_PCT\" "
                "\"$CAPTURE_LOSS_CLEAR_PCT\"; then echo YES; else echo NO; fi\n"
                % (src, prev.as_posix(), pct))
        fired = bash(tmp_path, body, "probe%d.sh" % i).strip() == "YES"
        verdicts.append(fired)
        prev.write_text("capture-loss:frag\n" if fired else "", encoding="utf-8")
    return verdicts


def test_the_september_loss_would_have_paged_and_then_cleared(tmp_path):
    """Frag over the trailing 24h, in whole percent, as the daemon's week went:
    healthy, then the 09-02 loss, easing, the 09-08 restart, then transit floor.
    One alert on the way up, one recovery on the way down, no chatter between."""
    series = [0, 24, 27, 29, 34, 22, 10, 3, 1, 0]
    verdicts = replay(tmp_path, series)
    assert verdicts == [False, True, True, True, True, True, True, True, False, False]
    # 3% holds because it is above CLEAR and already reported; 1% releases.


def test_a_value_parked_between_clear_and_warn_does_not_flap(tmp_path):
    assert replay(tmp_path, [3, 4, 3, 4]) == [False] * 4
    assert replay(tmp_path, [6, 3, 4, 3]) == [True, True, True, True]
