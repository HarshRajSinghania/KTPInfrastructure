"""ktp-data-server-health.sh: the check must be able to say it stopped running.

The 15-day silence of 2026-08-31..09-15 was not caused by a check that was
wrong. It was caused by a check that was *absent* — it exited before its own
report on every one-sided transition, posted nothing, saved nothing, and that
is byte-identical to a healthy estate. `#397` removed the line it died on. It
did not give the check a way to say it had died, and the next abort will be a
different line.

These tests pin the producer that says so, and every one of them is paired with
a control that makes the check stay silent, because a self-report that cannot
stay quiet is worth nothing.

`ledger_items` and `write_ledger` are extracted from the shipped script between
their `# >>>`/`# <<<` markers, so renaming or deleting either fails here rather
than silently testing a copy that no longer ships. The end-to-end tests run the
real script.
"""
import os
import pathlib
import shutil
import subprocess
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ktp-data-server-health.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

STRICT = "set -Eeuo pipefail\n"
HOUR = 3600


def block(name):
    text = SCRIPT.read_text(encoding="utf-8")
    begin, end = "# >>> %s" % name, "# <<< %s" % name
    assert begin in text and end in text, "%s markers are gone from the shipped script" % name
    return text.split(begin, 1)[1].split("\n", 1)[1].split(end, 1)[0]


def bash(tmp_path, body, name="probe.sh", expect_ok=True, env=None):
    """Run a script from a FILE.

    Not `bash -c`: msys2 re-parses a multi-line -c argument on Windows and eats
    the positional parameters, which reads as a bug in the script under test.
    """
    p = tmp_path / name
    p.write_text(body, encoding="utf-8", newline="\n")
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(["bash", p.as_posix()], capture_output=True, text=True, env=e)
    if expect_ok:
        assert r.returncode == 0, "exit %d; stderr=%r" % (r.returncode, r.stderr)
    return r


def code_lines():
    """Shipped script without comment-only lines — the new block's comments quote
    the very tokens some of these scans look for."""
    return [ln for ln in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not ln.lstrip().startswith("#")]


# ------------------------------------------------------------- ledger_items

def items(tmp_path, ledger, now=1_000_000, gap=3 * HOUR, name="items"):
    """Run `ledger_items` under the script's own flags. Returns its lines."""
    led = tmp_path / ("%s.run" % name)
    led.write_text(ledger, encoding="utf-8", newline="\n")
    body = STRICT + block("ktp-run-ledger") + "\n"
    body += "LEDGER=$(cat %s)\n" % led.as_posix()
    body += 'out=$(ledger_items "$LEDGER" %d %d)\n' % (now, gap)
    body += 'echo BEGIN_ITEMS\nprintf "%s" "$out"\necho\necho END_ITEMS\n'
    body += "echo REACHED_THE_REST_OF_THE_RUN\n"
    r = bash(tmp_path, body, name + ".sh")
    assert "REACHED_THE_REST_OF_THE_RUN" in r.stdout, r.stderr
    fenced = r.stdout.split("BEGIN_ITEMS\n", 1)[1].split("\nEND_ITEMS", 1)[0]
    return [ln for ln in fenced.splitlines() if ln]


def test_a_run_that_died_is_reported_by_the_next_one(tmp_path):
    """The whole point. Yesterday's abort is today's alert."""
    out = items(tmp_path, "status=died\nrc=1\nline=487\nstarted=999000\ncompleted=996400",
                name="died")
    assert len(out) == 1, out
    key, detail = out[0].split("\t", 1)
    assert key == "health-check-aborted"
    assert "487" in detail
    assert "posted no alert" in detail


def test_a_signalled_run_does_not_claim_a_line_it_never_had(tmp_path):
    """The OOM killer and `systemctl stop` trip no ERR trap, so there is no line
    to report. `line 0` would send the reader to the shebang."""
    out = items(tmp_path, "status=died\nrc=137\nline=0\ncompleted=996400", name="signal")
    assert len(out) == 1, out
    key, detail = out[0].split("\t", 1)
    assert key == "health-check-aborted"
    assert "line 0" not in detail
    assert "signalled" in detail


def test_a_run_that_completed_on_time_says_nothing(tmp_path):
    """Control. Without this the test above proves only that it can talk."""
    assert items(tmp_path, "status=ok\nrc=0\nline=0\nstarted=996000\ncompleted=996410",
                 name="clean") == []


def test_a_first_run_with_no_ledger_at_all_says_nothing(tmp_path):
    """An absent ledger is a fresh install, not a missed run. One false alert on
    every new box is how a reader learns to skip the line."""
    assert items(tmp_path, "", name="absent") == []


def test_a_gap_longer_than_the_slack_is_reported(tmp_path):
    """Cron stopped firing, or the box was down. The runs that never happened
    cannot report themselves; the first one that comes back does it for them."""
    out = items(tmp_path, "status=ok\nrc=0\nline=0\ncompleted=%d" % (1_000_000 - 40 * HOUR),
                name="gap")
    assert len(out) == 1, out
    key, detail = out[0].split("\t", 1)
    assert key == "health-check-missed-runs"
    assert "40h" in detail


def test_a_gap_inside_the_slack_is_not_reported(tmp_path):
    """Control for the test above: a late run, a clock nudge, an NTP step."""
    assert items(tmp_path, "status=ok\nrc=0\ncompleted=%d" % (1_000_000 - 2 * HOUR),
                 name="late") == []


def test_the_boundary_is_strictly_greater_than_the_gap(tmp_path):
    """Exactly at the slack is still fine; one second past it is not. Named
    because an off-by-one here is an alert every hour or never."""
    assert items(tmp_path, "status=ok\ncompleted=%d" % (1_000_000 - 3 * HOUR),
                 name="edge_at") == []
    out = items(tmp_path, "status=ok\ncompleted=%d" % (1_000_000 - 3 * HOUR - 1),
                name="edge_past")
    assert [x.split("\t")[0] for x in out] == ["health-check-missed-runs"]


def test_one_fault_is_one_item(tmp_path):
    """A died run is also a run that did not complete. Two keys for one fault
    double-count it in the set diff and make the recovery ambiguous — the same
    mistake the renamer and HLTV legs are each written to avoid."""
    out = items(tmp_path, "status=died\nrc=1\nline=12\ncompleted=%d" % (1_000_000 - 50 * HOUR),
                name="both")
    assert len(out) == 1, out
    assert out[0].startswith("health-check-aborted\t")
    assert "50h" in out[0]


def test_a_ledger_with_no_completion_is_not_silence(tmp_path):
    """A truncated or half-written ledger must not read as a clean run. This is
    the direction that costs: `unknown` rendered as `fine` is the original bug."""
    out = items(tmp_path, "status=ok\nrc=0", name="trunc")
    assert len(out) == 1, out
    assert out[0].startswith("health-check-aborted\t")


@pytest.mark.parametrize("junk", [
    "", "\n\n", "garbage", "=", "status=", "completed=", "completed=notanumber",
    "status=died", "status=ok\ncompleted=-5", "completed=1000000\nstatus=ok",
    "status=ok\ncompleted=1000000\nstray line with spaces",
    "status=$(touch /tmp/ktp-ledger-pwned)\ncompleted=1000000",
])
def test_no_ledger_content_can_stop_the_run(tmp_path, junk):
    """The ledger is read before any probe runs. If a malformed one could kill
    the script, this change would have built the very failure it reports."""
    items(tmp_path, junk, name="junk%d" % abs(hash(junk)))
    assert not pathlib.Path("/tmp/ktp-ledger-pwned").exists()


def test_the_keys_carry_no_number(tmp_path):
    """#388's lesson, in the one place it would be easiest to repeat: a measured
    value inside the alert KEY makes every change of that value read to the
    `comm` set comparison as one recovery plus one new failure. The hours belong
    in `detail`, which is body text only."""
    for ledger in ["status=died\nline=40\ncompleted=900000",
                   "status=ok\ncompleted=800000"]:
        for line in items(tmp_path, ledger, name="key%d" % abs(hash(ledger))):
            key = line.split("\t")[0]
            assert key in ("health-check-aborted", "health-check-missed-runs")
            assert not any(c.isdigit() for c in key), key


# ------------------------------------------------------------- write_ledger

def write(tmp_path, rc, line, prev_completed="", name="w"):
    led = tmp_path / ("%s.run" % name)
    body = STRICT + block("ktp-run-ledger") + "\n"
    body += 'RUN_LEDGER=%s\n' % led.as_posix()
    body += 'PREV_COMPLETED=%s\n' % (prev_completed or '""')
    body += "RUN_STARTED=5\n"
    body += 'write_ledger %d %d\n' % (rc, line)
    body += "echo DONE\n"
    bash(tmp_path, body, name + ".sh", env={"RUN_LEDGER": led.as_posix()})
    return dict(l.split("=", 1) for l in led.read_text().splitlines() if "=" in l), led


def test_a_clean_exit_stamps_a_fresh_completion(tmp_path):
    rec, _ = write(tmp_path, 0, 0, prev_completed="1", name="ok")
    assert rec["status"] == "ok"
    assert abs(int(rec["completed"]) - int(time.time())) < 120


def test_a_died_run_carries_the_previous_completion_forward(tmp_path):
    """So the silence accumulates. If a died run stamped `completed` with now,
    the gap would reset every hour and a check that has been dead for a week
    would never look older than one run."""
    rec, _ = write(tmp_path, 1, 487, prev_completed="990000", name="died")
    assert rec["status"] == "died"
    assert rec["completed"] == "990000"
    assert rec["line"] == "487"
    assert rec["rc"] == "1"


def test_a_died_first_run_leaves_no_completion_rather_than_inventing_one(tmp_path):
    rec, _ = write(tmp_path, 2, 9, prev_completed="", name="firstdied")
    assert rec["status"] == "died"
    assert rec["completed"] == ""


def test_the_ledger_is_swapped_into_place_not_written_in_place(tmp_path):
    """A run killed mid-write must not leave a half-ledger that the next run
    reads as a clean completion."""
    _, led = write(tmp_path, 0, 0, name="atomic")
    assert not pathlib.Path(str(led) + ".tmp").exists()


def test_write_ledger_never_uses_jq():
    """It has to survive the failures that kill the rest of the script, and a
    broken jq is one of them — it is what `save_state` and the payload both
    depend on."""
    src = [ln for ln in block("ktp-run-ledger").splitlines()
           if not ln.lstrip().startswith("#")]
    assert not any("jq" in ln for ln in src), src


# ------------------------------------------------------------ script wiring

def test_the_ledger_is_read_into_the_down_set():
    code = "\n".join(code_lines())
    assert 'ledger_items "$PREV_LEDGER"' in code
    assert 'down+=("$_key")' in code


def test_exactly_one_exit_trap_and_it_writes_the_ledger():
    """Two `trap ... EXIT` lines means the second silently replaces the first —
    which is how the cleanup and the ledger would quietly stop coexisting."""
    code = code_lines()
    exits = [ln for ln in code if "trap " in ln and ln.rstrip().endswith("EXIT")]
    assert len(exits) == 1, exits
    assert "on_exit" in exits[0]
    assert any("write_ledger " in ln for ln in code)


def test_errtrace_is_on_so_an_abort_inside_a_function_reports_its_line():
    """Without -E the ERR trap is not inherited, and a death inside `save_state`
    — where the original bug lived — would be recorded as line 0."""
    code = "\n".join(code_lines())
    assert "set -Eeuo pipefail" in code
    assert "trap '_run_fail_line=$LINENO' ERR" in code


def test_the_state_file_is_written_through_a_temp_and_moved():
    """`> "$STATE_FILE"` truncates BEFORE the jq that fills it runs. A jq that
    failed there left a zero-byte state file, which the next run read back as an
    empty `--argjson prev` and died on — and so did every run after it, for good.
    Found by running the script against a broken jq, not by reading it."""
    code = "\n".join(code_lines())
    assert 'health_state_document "$down_json" "$prev_json" "$detail_json" "$(ts)" > "$STATE_FILE.tmp"' in code
    assert 'mv -f "$STATE_FILE.tmp" "$STATE_FILE"' in code
    assert '"$(ts)" > "$STATE_FILE"\n' not in code + "\n"


def test_an_empty_state_file_reads_as_an_empty_object():
    """jq on a zero-byte file prints nothing and exits 0, so the `||` fallback
    that was there never fired. The guard has to be on the VALUE."""
    code = "\n".join(code_lines())
    assert """[ -n "$prev_json" ] || prev_json='{}'""" in code


def test_the_state_file_is_overridable():
    """So the end-to-end tests below can exercise the real script without
    touching /var/lib. A hardcoded path is what forces a test to edit a copy."""
    code = "\n".join(code_lines())
    assert 'STATE_FILE="${STATE_FILE:-' in code
    assert 'RUN_LEDGER="${RUN_LEDGER:-' in code


# -------------------------------------------------------------- end to end

needs_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")


def stubs(tmp_path, jq_ok=True):
    """A data server that answers 'everything is fine', so the only thing that
    can move the down set is the check's own ledger.

    Nothing here reaches the network: `curl` appends its payload to a file and
    reports 200, so the Discord relay is never called.
    """
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)

    def put(name, body):
        p = d / name
        p.write_text("#!/bin/bash\n" + body, encoding="utf-8", newline="\n")
        p.chmod(0o755)

    put("systemctl", """
case "$1 $2" in
  "is-active "*)  echo active ;;
  "is-enabled "*) echo enabled ;;
  "--failed "*|"--failed") ;;
  "show "*)
      case "$*" in
        *LoadState*)  echo loaded ;;
        *Result*)     echo success ;;
        *ExecMainExitTimestamp*) date ;;
        *NRestarts*)  echo 0 ;;
        *) echo "" ;;
      esac ;;
  *) ;;
esac
exit 0
""")
    put("df", 'echo "Filesystem 1024-blocks Used Available Capacity Mounted"\n'
              'echo "/dev/sda1 100000000 50000000 50000000 50% /"\n')
    put("du", "")
    put("mysql", "")
    put("curl", 'printf "%%s\\n" "$@" >> %s\necho -n 200\n'
        % tmp_path.joinpath("posted.txt").as_posix())
    # Removed, not merely skipped: a stub left behind from an earlier run in the
    # same tmp_path would break every run after it and read as a real failure.
    d.joinpath("jq").unlink(missing_ok=True)
    if not jq_ok:
        put("jq", 'echo "jq: broken" >&2\nexit 2\n')
    return d


def run_script(tmp_path, jq_ok=True, script=None, expect_ok=True):
    d = stubs(tmp_path, jq_ok=jq_ok)
    env = dict(os.environ)
    env["PATH"] = d.as_posix() + os.pathsep + env["PATH"]
    env.update({
        "STATE_FILE": tmp_path.joinpath("state.json").as_posix(),
        "RUN_LEDGER": tmp_path.joinpath("health.run").as_posix(),
        "DISK_HISTORY": tmp_path.joinpath("disk.log").as_posix(),
        "SETTLE_SECONDS": "0",
        "RELAY_URL": "http://127.0.0.1:1/never",
        "AUTH_SECRET": "x",
    })
    r = subprocess.run(["bash", (script or SCRIPT).as_posix()],
                       capture_output=True, text=True, env=env)
    if expect_ok:
        assert r.returncode == 0, "exit %d\nstdout=%s\nstderr=%s" % (
            r.returncode, r.stdout, r.stderr)
    return r


def ledger_of(tmp_path):
    p = tmp_path / "health.run"
    if not p.exists():
        return None
    return dict(l.split("=", 1) for l in p.read_text().splitlines() if "=" in l)


@needs_jq
def test_a_real_abort_is_recorded_and_the_next_completed_run_alerts_on_it(tmp_path):
    """The whole change, end to end, against the shipped script.

    Run 1 settles a baseline. Run 2 dies on a broken jq — a real failure mode,
    and one the ledger has to survive because it is what `save_state` and the
    Discord payload are both built with. Run 3 is the next healthy hour, and it
    has to say what happened.
    """
    run_script(tmp_path)
    assert ledger_of(tmp_path)["status"] == "ok"
    baseline_completed = ledger_of(tmp_path)["completed"]

    broken = run_script(tmp_path, jq_ok=False, expect_ok=False)
    assert broken.returncode != 0
    assert "alert posted" not in broken.stdout
    rec = ledger_of(tmp_path)
    assert rec["status"] == "died"
    assert int(rec["line"]) > 0, "the ERR trap did not name the line"
    assert rec["completed"] == baseline_completed, "a died run reset the silence clock"

    healthy = run_script(tmp_path)
    assert "health-check-aborted" in healthy.stdout
    assert "TRANSITIONS: new_down=1 [health-check-aborted]" in healthy.stdout
    assert "recovered=0" in healthy.stdout
    posted = tmp_path.joinpath("posted.txt").read_text()
    assert "health-check-aborted" in posted, "it logged the abort but did not send it"
    assert rec["line"] in posted, "the alert does not name the line it died on"


@needs_jq
def test_the_run_after_that_reports_the_recovery_and_then_goes_quiet(tmp_path):
    """Control: the item must clear itself. A self-report that latches on is a
    permanent red mark, which is the same as no mark at all."""
    run_script(tmp_path)
    run_script(tmp_path, jq_ok=False, expect_ok=False)
    run_script(tmp_path)                       # alerts
    recovered = run_script(tmp_path)
    assert "recovered=1 [health-check-aborted]" in recovered.stdout
    quiet = run_script(tmp_path)
    assert "no transitions" in quiet.stdout
    assert "health-check-aborted" not in quiet.stdout


@needs_jq
def test_a_healthy_sequence_never_mentions_the_check_itself(tmp_path):
    """The control that matters most. If this producer could fire on a clean
    box, every one of the assertions above would pass for the wrong reason."""
    run_script(tmp_path)
    for _ in range(3):
        r = run_script(tmp_path)
        assert "health-check-aborted" not in r.stdout
        assert "health-check-missed-runs" not in r.stdout


@needs_jq
def test_a_zero_byte_state_file_does_not_end_the_check_for_good(tmp_path):
    """The latch found while proving the ledger works: one bad hour truncated
    the state file, and from then on every run died reading it back. The check
    stayed dead until someone deleted a file nobody knew to look at."""
    tmp_path.joinpath("state.json").write_text("", encoding="utf-8")
    first = run_script(tmp_path)
    assert "no transitions" in first.stdout or "TRANSITIONS" in first.stdout
    run_script(tmp_path)
    assert tmp_path.joinpath("state.json").read_text().strip().startswith("{")


@needs_jq
def test_without_the_exit_trap_the_abort_leaves_no_trace(tmp_path):
    """The control on the mechanism, not on the harness: strip the one line that
    installs the EXIT trap and the same injected failure becomes invisible again
    — which is precisely the estate the 15-day silence happened in.
    """
    maimed = tmp_path / "no-trap.sh"
    maimed.write_text(
        SCRIPT.read_text(encoding="utf-8").replace("trap on_exit EXIT", ": # trap removed"),
        encoding="utf-8", newline="\n")
    run_script(tmp_path, script=maimed)
    assert ledger_of(tmp_path) is None, "something other than the trap wrote the ledger"
    run_script(tmp_path, script=maimed, jq_ok=False, expect_ok=False)
    assert ledger_of(tmp_path) is None
    after = run_script(tmp_path, script=maimed)
    assert "health-check-aborted" not in after.stdout
