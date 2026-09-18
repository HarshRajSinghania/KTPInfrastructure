"""ktp-fleet-health.sh: the per-host disk leg posts once per transition and never flaps.

The data server has watched its own disks since May; the five game hosts had
nothing, and they are the hosts that write demos, HLTV recordings and logs
during a match. The gate is extracted from the real script between the
`# >>> ktp-disk-gate` markers, so renaming or deleting it fails this file rather
than silently testing a copy that no longer ships. The loop around it is
exercised end to end with `df` and `send_alert` stubbed, because the state
round-trip through DISK_WARN_MOUNTS is where a latch bug would hide.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "monitoring" / "fleet-health" / "ktp-fleet-health.sh"
BEGIN, END = "# >>> ktp-disk-gate", "# <<< ktp-disk-gate"
BASH = os.environ.get("KTP_TEST_BASH", "bash")

pytestmark = pytest.mark.skipif(shutil.which(BASH) is None, reason="needs bash")


def gate_source():
    text = SCRIPT.read_text(encoding="utf-8")
    assert BEGIN in text and END in text, "disk gate markers are gone from the shipped script"
    return text.split(BEGIN, 1)[1].split("\n", 1)[1].split(END, 1)[0]


def bash(tmp_path, body, name="probe.sh"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8", newline="\n")
    r = subprocess.run([BASH, p.as_posix()], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


def verdict(tmp_path, pct, ipct, was, warn=75, clear=72):
    body = "set -euo pipefail\n%s\ndisk_gate / '%s' '%s' %d %d %d\necho \"$DISK_VERDICT\"\n" % (
        gate_source(), pct, ipct, warn, clear, was)
    return bash(tmp_path, body).strip()


def test_crossing_warn_posts_once_and_only_from_the_clean_state(tmp_path):
    assert verdict(tmp_path, 75, 10, was=0) == "warn"
    assert verdict(tmp_path, 90, 10, was=1) == "quiet"     # still bad, already said so


def test_deadband_holds_between_clear_and_warn(tmp_path):
    for pct in (72, 73, 74):
        assert verdict(tmp_path, pct, 0, was=1) == "quiet"  # not yet recovered
        assert verdict(tmp_path, pct, 0, was=0) == "quiet"  # not yet a problem


def test_recovery_clears_only_below_the_clear_line(tmp_path):
    assert verdict(tmp_path, 71, 0, was=1) == "clear"
    assert verdict(tmp_path, 71, 0, was=0) == "quiet"


def test_inodes_count_as_much_as_bytes(tmp_path):
    """A log directory with a million tiny files fills inodes long before bytes."""
    assert verdict(tmp_path, 20, 80, was=0) == "warn"
    assert verdict(tmp_path, 20, 50, was=1) == "clear"


def test_an_unreadable_reading_is_quiet_never_a_recovery(tmp_path):
    """df failed or the mount is gone: neither warn nor clear. A guard that
    cannot read its evidence must not report a recovery it did not observe."""
    assert verdict(tmp_path, "", "", was=1) == "quiet"
    assert verdict(tmp_path, "n/a", 0, was=1) == "quiet"
    assert verdict(tmp_path, 80, "-", was=0) == "warn"     # df prints - for inode% on some fs


def _loop_probe(tmp_path, readings, prior_warned):
    """Run the shipped script's disk loop with df and send_alert stubbed.

    `readings` maps mount -> (pct, ipct). Prints each alert title and the
    persisted DISK_WARN_MOUNTS so the round-trip is visible.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    start = text.index("# >>> ktp-disk-gate")
    end = text.index("# State transitions")
    loop = text[start:end]
    # A header line then a data line, the way real df prints; %%%% survives the
    # Python formatting as %% so bash printf emits a literal percent sign.
    df_cases = "".join(
        '    *"%s") if [ "$1" = -P ] && [ "$2" = -i ]; then printf "h\\nfs 1 1 1 %s%%%% %s\\n"; else printf "h\\nfs 1 1 1 %s%%%% %s\\n"; fi ;;\n'
        % (m, ip, m, p, m) for m, (p, ip) in readings.items())
    body = """set -euo pipefail
HOME_DIR=/home/dodserver
LOCATION=TEST
DISK_MOUNTS="%s"
DISK_PCT_WARN=75
DISK_PCT_CLEAR=72
DISK_WARN_MOUNTS="%s"
df() { case "$*" in
%s    *) return 1 ;;
esac; }
send_alert() { echo "ALERT: $1"; }
%s
echo "STATE: $DISK_WARN_MOUNTS"
""" % (" ".join(readings), prior_warned, df_cases, loop)
    return bash(tmp_path, body, "loop.sh")


def test_loop_round_trips_the_warned_set_through_state(tmp_path):
    out = _loop_probe(tmp_path, {"/": (80, 5), "/home": (40, 5)}, prior_warned="")
    assert "ALERT: 💽 TEST disk / at 80%" in out and "/home" not in out.split("STATE:")[0]
    assert out.strip().endswith("STATE: /")
    # Next minute, same readings: nothing posts, state holds.
    out = _loop_probe(tmp_path, {"/": (80, 5), "/home": (40, 5)}, prior_warned="/")
    assert "ALERT" not in out and out.strip().endswith("STATE: /")
    # Recovery on one mount, a fresh warn on the other, in one run.
    out = _loop_probe(tmp_path, {"/": (60, 5), "/home": (76, 5)}, prior_warned="/")
    assert "ALERT: ✅ TEST disk / back to 60%" in out
    assert "ALERT: 💽 TEST disk /home at 76%" in out
    assert out.strip().endswith("STATE: /home")


def test_a_mount_removed_from_config_is_forgotten_not_alerted(tmp_path):
    """It is not re-evaluated, so neither warn nor clear fires for it."""
    out = _loop_probe(tmp_path, {"/": (80, 5)}, prior_warned="/ /mnt/gone")
    assert "ALERT" not in out
    # /mnt/gone is not in DISK_MOUNTS this run so it is not re-evaluated; the
    # warned set is rebuilt from the mounts actually checked, which drops it.
    # That is the one intended forgetting: a mount removed from config stops
    # being tracked, silently, the same as removing a unit from a checklist.
    assert out.strip().endswith("STATE: /")
