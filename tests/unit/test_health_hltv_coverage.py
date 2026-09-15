"""ktp-data-server-health.sh: the HLTV coverage leg — no counts in keys, and a
crash-looping proxy that never reaches `failed`.

The crash-loop block is extracted from the real script between its
`# >>> ktp-hltv-crashloop` markers, so renaming or deleting it fails this file
rather than silently testing a copy that no longer ships. `systemctl` is the only
stubbed piece: it is the systemd boundary, and `hltv_unit_restarts` runs for real
so the `show … -p NRestarts --value` spelling is under test too.
"""
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ktp-data-server-health.sh"
BEGIN, END = "# >>> ktp-hltv-crashloop", "# <<< ktp-hltv-crashloop"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def block_source():
    text = SCRIPT.read_text(encoding="utf-8")
    assert BEGIN in text and END in text, "the hltv-crashloop markers are gone from the shipped script"
    # Drop the remainder of the marker's own line: it is prose, not shell.
    return text.split(BEGIN, 1)[1].split("\n", 1)[1].split(END, 1)[0]


def run_crashloop(tmp_path, up_ports, restarts, name, warn=None):
    """Drive the crash-loop leg with a scripted `systemctl show … NRestarts`.

    `restarts` maps port -> what systemctl prints; anything unlisted prints 0.
    Returns (down, detail) as the block left them.
    """
    fake = tmp_path / ("bin_" + name)
    fake.mkdir()
    table = "\n".join('    %s) echo "%s" ;;' % (k, v) for k, v in restarts.items())
    (fake / "systemctl").write_text(
        "#!/bin/bash\n"
        "# $1=show $2=hltv@<port> $3=-p $4=NRestarts $5=--value\n"
        'case "${2#hltv@}" in\n%s\n    *) echo 0 ;;\nesac\n' % table,
        encoding="utf-8", newline="\n")
    (fake / "systemctl").chmod(0o755)

    body = ("export PATH=%s:$PATH\n" % fake.as_posix())
    body += "set -euo pipefail\n"
    if warn is not None:
        body += "export HLTV_RESTART_WARN=%d\n" % warn
    body += "down=()\ndeclare -A detail=()\n"
    body += "up_ports=(%s)\n" % " ".join(up_ports)
    body += block_source() + "\n"
    body += 'for k in ${down[@]+"${down[@]}"}; do echo "DOWN|$k|${detail[$k]:-}"; done\n'
    p = tmp_path / (name + ".sh")
    p.write_text(body, encoding="utf-8", newline="\n")
    r = subprocess.run(["bash", p.as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, "exit %d; stderr=%r" % (r.returncode, r.stderr)
    rows = [ln.split("|", 2) for ln in r.stdout.splitlines() if ln.startswith("DOWN|")]
    return [k for _, k, _ in rows], {k: d for _, k, d in rows}


# ------------------------------------------------------ the leg nobody else has

def test_a_proxy_reading_active_while_crash_looping_is_reported(tmp_path):
    """`Restart=always` + `RestartSec=10` outruns systemd's start-rate limit, so
    the unit never reaches `failed` and is-active reads `active`. NRestarts is
    the only leg that sees it — the hltv-demo-renamer wedge shape."""
    down, detail = run_crashloop(tmp_path, ["27031"], {"27031": "7"}, "looper")
    assert down == ["hltv@27031=crash-looping"]
    assert detail["hltv@27031=crash-looping"] == "7 automatic restarts since its last clean start"


def test_a_healthy_proxy_is_silent(tmp_path):
    down, _ = run_crashloop(tmp_path, ["27020", "27021"], {}, "healthy")
    assert down == []


def test_the_threshold_is_a_floor_not_an_equality(tmp_path):
    below, _ = run_crashloop(tmp_path, ["27022"], {"27022": "2"}, "below")
    at, _ = run_crashloop(tmp_path, ["27022"], {"27022": "3"}, "at")
    assert below == []
    assert at == ["hltv@27022=crash-looping"]


@pytest.mark.parametrize("idx,value", list(enumerate(["", "n/a", "[not set]", "-1x"])))
def test_an_unparseable_nrestarts_is_not_an_alert(tmp_path, idx, value):
    """systemctl answers `[not set]` for a unit it does not know, and nothing at
    all when the call fails. Neither is a crash loop, and a bare `-ge` on either
    is a bash syntax error that would kill the run under `set -e`."""
    down, _ = run_crashloop(tmp_path, ["27023"], {"27023": value}, "bad%d" % idx)
    assert down == []


def test_the_threshold_is_overridable(tmp_path):
    down, _ = run_crashloop(tmp_path, ["27024"], {"27024": "2"}, "override", warn=2)
    assert down == ["hltv@27024=crash-looping"]


def test_the_default_threshold_ships_as_three():
    assert 'HLTV_RESTART_WARN="${HLTV_RESTART_WARN:-3}"' in block_source()


def test_only_proxies_that_are_up_are_checked():
    """A port already named by the coverage leg is one fault. A second token for
    it would double-count it in the `comm` set diff."""
    src = block_source()
    assert 'for p in ${up_ports[@]+"${up_ports[@]}"}; do' in src
    code = "\n".join(ln for ln in SCRIPT.read_text(encoding="utf-8").splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "up_ports+=(\"$p\")" in code


# ------------------------------------------------------- keys carry no numbers

def test_no_hltv_key_carries_a_measured_count():
    """#388 deadbanded this out of the disk keys; the HLTV key is the one place
    its diff did not reach. `hltv-instance-count=23/24` put the count inside the
    key, so 23/24 -> 22/24 read to the set diff as a recovery plus a new failure."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "hltv-instance-count" not in text
    assert 'key="hltv-instance-coverage"' in text
    assert 'detail[$key]="${active_hltv}/${expected_hltv} proxies active"' in text
    # Control: the pre-fix spelling is what this test has to be able to catch.
    assert "hltv-instance-count" in 'down+=("hltv-instance-count=${active_hltv}/${expected_hltv}")'


def test_the_crash_loop_key_does_not_carry_the_restart_count():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'key="hltv@$p=crash-looping"' in text
    assert "${nrestarts} automatic restarts" in text


def test_nrestarts_is_read_with_show_not_is_active():
    """is-active cannot see a crash loop; that is the whole reason this leg exists."""
    assert 'systemctl show "hltv@$1" -p NRestarts --value' in block_source()


def test_the_script_still_parses():
    r = subprocess.run(["bash", "-n", SCRIPT.as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
