"""Guards for `scripts/audit_redact.py`, the redaction the fleet audit publishes through.

`.github/workflows/fleet-audit.yml` posts the drift report to a GitHub issue and
uploads it as a run artifact on a PUBLIC repository, and the snapshot captures
root's crontab and `/etc/rc.local` verbatim. So these tests pull in two
directions at once, and a failure in either direction is serious:

  * a credential-shaped value must NOT survive -- every such test below carries
    a fixture that starts dirty, so it can fail;
  * the audit's correctness contract must survive BYTE-IDENTICAL. The
    `provision/expected-*.conf` comparisons are what make this audit worth
    running, and a redaction that ate them would turn the report green by
    blinding it. Those tests read the real conf files, so a pattern added later
    that the redaction eats fails here rather than on a Monday.

EVERY VALUE IN THIS FILE IS INVENTED. Nothing here is, or has ever been, a KTP
credential -- a self-test that embeds a live value leaks it the moment it is
committed.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REDACT = REPO / "scripts" / "audit_redact.py"
DRIFT = REPO / "scripts" / "audit-fleet-drift.py"
EXPECTED_RC_LOCAL = REPO / "provision" / "expected-rc-local.conf"
EXPECTED_CMDLINE = REPO / "provision" / "expected-cmdline.conf"

RC_LOCAL = "/etc/rc.local (non-comment, sorted)"
GRUB = "GRUB CMDLINE"
CRONTAB = "DODSERVER CRONTAB (non-comment, sorted)"

# Invented. Each one is shaped like the thing it stands in for and is not any
# real value: mixed case plus digits, at or over the 20-character threshold.
FAKE_RELAY_SECRET = "Fak3RelaySecretNotReal01"
FAKE_AUTH_HEADER = "Fak3AuthHeaderNotReal002"
FAKE_RCON = "Fak3RconValueNotReal0003"
FAKE_URL_USER = "fakeuser"
FAKE_URL_PASS = "Fak3UrlPassNotReal00004"
FAKE_UNHINTED = "Fak3UnhintedValNotReal05"

FAKES = (FAKE_RELAY_SECRET, FAKE_AUTH_HEADER, FAKE_RCON,
         FAKE_URL_PASS, FAKE_UNHINTED)


@pytest.fixture(scope="module")
def ar():
    spec = importlib.util.spec_from_file_location("_ktp_audit_redact", REDACT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_drift_module(tmp_path):
    """Import the audit script without paramiko and without /etc/ktp.

    Same shape as tests/unit/test_audit_fleet_drift.py: the script imports
    paramiko and reads a fleet config at import time, and neither is needed to
    exercise the pure functions.
    """
    cfg = tmp_path / "fleet.json"
    cfg.write_text(json.dumps({"hosts": [
        {"name": "Atlanta", "host": "10.0.0.1", "user": "u", "password": "p",
         "group": "baremetal"},
        {"name": "Dallas", "host": "10.0.0.2", "user": "u", "password": "p",
         "group": "baremetal"},
        {"name": "Chicago", "host": "10.0.0.3", "user": "u", "password": "p",
         "group": "vps"},
    ]}))

    sys.modules.setdefault("paramiko", types.ModuleType("paramiko"))
    saved_stdout = sys.stdout
    prev = os.environ.get("KTP_AUDIT_FLEET_CONFIG")
    os.environ["KTP_AUDIT_FLEET_CONFIG"] = str(cfg)
    try:
        spec = importlib.util.spec_from_file_location("_ktp_audit_drift_r", DRIFT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.stdout = saved_stdout
        if prev is None:
            os.environ.pop("KTP_AUDIT_FLEET_CONFIG", None)
        else:
            os.environ["KTP_AUDIT_FLEET_CONFIG"] = prev
    return mod


@pytest.fixture(scope="module")
def drift(tmp_path_factory):
    return _load_drift_module(tmp_path_factory.mktemp("auditcfg_redact"))


def _load_expected(path):
    """Parse a `provision/expected-*.conf` into {group: [line, ...]}."""
    groups, current = {"default": []}, "default"
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            groups.setdefault(current, [])
            continue
        groups[current].append(line)
    return groups


# A stand-in for one baremetal's /etc/rc.local, written to exercise every glob
# in provision/expected-rc-local.conf. Generic kernel and NIC tuning, nothing
# host-specific.
RC_LOCAL_FIXTURE = [
    "for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > $g; done",
    "for s in /sys/devices/system/cpu/cpu*/cpuidle/state*/disable; do echo 1 > $s; done",
    "echo never > /sys/kernel/mm/transparent_hugepage/enabled",
    "echo never > /sys/kernel/mm/transparent_hugepage/defrag",
    "echo 0 > /proc/sys/vm/compaction_proactiveness",
    "echo 0 > /sys/kernel/mm/ksm/run",
    "echo 0 > /sys/kernel/mm/lru_gen/min_ttl_ms",
    "ethtool -K enp1s0f0 gro off 2>/dev/null",
    "ethtool -K enp1s0f0 tso off 2>/dev/null",
    "ethtool -K enp1s0f0 lro off 2>/dev/null",
    "ethtool -C enp1s0f0 rx-usecs 0 rx-frames 0 2>/dev/null",
    "iptables -t raw -A PREROUTING -p udp --dport 27015:27019 -j NOTRACK",
    "iptables -t raw -A OUTPUT -p udp --sport 27015:27019 -j NOTRACK",
    "ethtool -G enp1s0f0 rx 4096 tx 4096 2>/dev/null",
    "tc qdisc replace dev enp1s0f0 root pfifo_fast",
]

CRONTAB_CLEAN = [
    "* * * * * /home/dodserver/dod-27015/dodserver monitor > /dev/null 2>&1",
    "0 3 * * * /home/dodserver/ktp-scheduled-restart.sh >> /home/dodserver/restart.log 2>&1",
    "*/5 * * * * /usr/local/bin/ktp-chrt.sh",
]

CRONTAB_DIRTY = [
    "0 5 * * 1 RELAY_SECRET=%s /usr/local/bin/ktp-fleet-audit.sh" % FAKE_RELAY_SECRET,
    '*/10 * * * * curl -H "X-Relay-Auth: %s" https://relay.invalid/post' % FAKE_AUTH_HEADER,
    "0 4 * * * rsync rsync://%s:%s@backup.invalid/vol /srv/x" % (FAKE_URL_USER, FAKE_URL_PASS),
    "30 2 * * * /usr/local/bin/ktp-rcon.sh --to 27015 rcon_password %s" % FAKE_RCON,
    "45 2 * * * KTP_UNHINTED=%s /usr/local/bin/ktp-thing.sh" % FAKE_UNHINTED,
]


# ------------------------------------------------------------- positive controls

def test_module_exposes_the_functions_under_test(ar):
    """If the loader produced a stub, every assertion below passes vacuously."""
    for name in ("redact_line", "redact_text", "redact_snapshot",
                 "redact_diagnostic"):
        assert callable(getattr(ar, name, None)), f"{name} missing from module"
    assert ar.PLACEHOLDER
    assert ar.CRONTAB_SECTION == CRONTAB


def test_the_dirty_fixture_is_actually_dirty(ar):
    """The control that lets the redaction tests fail. If a future edit made
    these fixtures clean, every 'the secret is gone' assertion below would pass
    without the redaction doing anything."""
    raw = "\n".join(CRONTAB_DIRTY)
    for fake in FAKES:
        assert fake in raw, f"{fake} vanished from the fixture"
    assert ar.redact_text(raw, CRONTAB) != raw


def test_the_expected_state_files_are_readable(ar):
    """The harness sees the real conf files. Without this, the two contract
    tests below would iterate an empty list and pass on any redaction at all."""
    rc = _load_expected(EXPECTED_RC_LOCAL)
    cmd = _load_expected(EXPECTED_CMDLINE)
    assert len(rc["default"]) >= 10, rc
    assert len(cmd.get("baremetal", [])) >= 5, cmd


# ------------------------------------------------- a credential must not survive

@pytest.mark.parametrize("line, fake", [
    (CRONTAB_DIRTY[0], FAKE_RELAY_SECRET),
    (CRONTAB_DIRTY[1], FAKE_AUTH_HEADER),
    (CRONTAB_DIRTY[2], FAKE_URL_PASS),
    (CRONTAB_DIRTY[3], FAKE_RCON),
    (CRONTAB_DIRTY[4], FAKE_UNHINTED),
])
def test_credential_shaped_values_do_not_survive_a_cron_line(ar, line, fake):
    assert fake not in ar.redact_line(line, CRONTAB)


def test_the_schedule_and_the_command_survive(ar):
    """Redacting the whole line would be safe and useless -- the audit exists to
    notice a cron entry that changed."""
    out = ar.redact_line(CRONTAB_DIRTY[0], CRONTAB)
    assert out.startswith("0 5 * * 1 ")
    assert "/usr/local/bin/ktp-fleet-audit.sh" in out
    assert "RELAY_SECRET=" in out


def test_a_url_keeps_its_host_and_loses_its_userinfo(ar):
    out = ar.redact_line(CRONTAB_DIRTY[2], CRONTAB)
    assert "backup.invalid" in out
    assert FAKE_URL_USER not in out
    assert FAKE_URL_PASS not in out


def test_a_credential_named_assignment_is_redacted_outside_the_crontab(ar):
    """The name-based rule is not scoped to one section: rc.local runs as root
    and can carry a curl too."""
    line = 'curl -H "Authorization: Bearer %s" https://x.invalid' % FAKE_AUTH_HEADER
    assert FAKE_AUTH_HEADER not in ar.redact_line(line, RC_LOCAL)


def test_a_shell_variable_reference_is_kept(ar):
    """`$SECRET` is not a value, and whether a host inlines a credential or
    reads one is the drift worth seeing."""
    line = 'curl -H "X-Relay-Auth: $KTP_RELAY_SECRET" "$KTP_RELAY_URL"'
    assert ar.redact_line(line, CRONTAB) == line


def test_known_limit_is_the_one_the_module_documents(ar):
    """Honest lock on the stated gap: outside the crontab, a short all-lower-case
    value under a name that does not name a credential survives. If someone
    closes that, this test should be deleted with the docstring -- not left to
    assert a weakness that no longer exists."""
    line = "FOO=tinyval /usr/local/bin/thing.sh"
    assert ar.redact_line(line, RC_LOCAL) == line
    assert "tinyval" not in ar.redact_line(line, CRONTAB)


# ------------------------------------------ the comparison contract must survive

def test_every_expected_cmdline_flag_survives_byte_identical(ar):
    """`compute_repo_list_drift` compares these literally. A redacted flag reads
    as every host having lost its CPU isolation."""
    groups = _load_expected(EXPECTED_CMDLINE)
    flags = [f for g in groups.values() for f in g]
    assert flags
    for flag in flags:
        assert ar.redact_line(flag, GRUB) == flag, flag


def test_every_expected_rc_local_glob_still_matches_after_redaction(ar):
    """`compute_repo_glob_drift` matches these patterns against the live lines.
    Redaction runs first, so it must not break a single one."""
    patterns = _load_expected(EXPECTED_RC_LOCAL)["default"]
    assert patterns
    redacted = [ar.redact_line(line, RC_LOCAL) for line in RC_LOCAL_FIXTURE]
    for pat in patterns:
        assert any(fnmatch.fnmatchcase(line, pat) for line in redacted), pat


def test_the_rc_local_fixture_matches_before_redaction_too(ar):
    """Partner control: if the fixture never matched, the test above would be
    asserting that a redaction preserved nothing."""
    patterns = _load_expected(EXPECTED_RC_LOCAL)["default"]
    for pat in patterns:
        assert any(fnmatch.fnmatchcase(line, pat) for line in RC_LOCAL_FIXTURE), pat


@pytest.mark.parametrize("line", [
    "net.core.rmem_max = 26214400",
    "kernel.core_pattern = /tmp/core.%e.%p.%t",
    "vm.swappiness = 10",
    "engine_i486.so = 2c57641b06d9ef69",
    "dod/addons/ktpamx/dlls/ktpamx_i386.so = 39af1c968e3e42a2",
    "stats_logging.amxx = d37816f7cb3232c4",
    "dod-27015: parse=OK oldtype=disabled samesocket=armed duppid=armed",
    "lgsm-versions: v26.2.0",
    "ktp-chrt.timer",
    "ktp-corpus-push-chicago.service = active",
    "kernel: 6.8.0-110-lowlatency",
    "cpu-cores: 8",
])
def test_ordinary_snapshot_facts_are_untouched(ar, line):
    """The gate step greps `oldtype=armed` out of the report, and every
    repo-vs-fleet section compares the value half. All of it has to come
    through unchanged."""
    assert ar.redact_line(line) == line


def test_section_headers_are_never_rewritten(ar):
    """The orchestrator keys every fact off these. Rewriting one reads as the
    whole section vanishing."""
    text = "=== %s ===\n%s\n" % (CRONTAB, CRONTAB_DIRTY[0])
    assert ("=== %s ===" % CRONTAB) in ar.redact_snapshot(text)


def test_the_broad_assignment_rule_is_scoped_to_the_crontab(ar):
    """rc.local is compared by glob against expected-rc-local.conf, so blanking
    every assignment there would cost real signal for no gain the shape rules
    do not already give."""
    line = "IFACE=$(ip -o -4 route show to default | awk '{print $5}')"
    assert ar.redact_line(line, RC_LOCAL) == line


# ------------------------------------------------------------------ diagnostics

def test_a_connection_error_loses_the_address(ar):
    err = "[Errno 111] Connection refused connecting to ('10.11.12.13', 22)"
    out = ar.redact_diagnostic(err)
    assert "10.11.12.13" not in out
    assert "Connection refused" in out


def test_a_version_string_is_not_mistaken_for_an_address(ar):
    assert ar.redact_diagnostic("paramiko 3.4.0 timed out") == \
        "paramiko 3.4.0 timed out"


# --------------------------------------------------------------------- end to end

def _snapshot_text():
    return "\n".join([
        "=== HOST ===",
        "hostname: neinatl-bm",
        "kernel: 6.8.0-110-lowlatency",
        "",
        "=== GRUB CMDLINE ===",
        "isolcpus=2,3,4,5,6,7",
        "mitigations=off",
        "",
        "=== %s ===" % RC_LOCAL,
        *RC_LOCAL_FIXTURE,
        "",
        "=== %s ===" % CRONTAB,
        *CRONTAB_CLEAN,
        *CRONTAB_DIRTY,
        "",
        "=== KTP BINARIES md5 ===",
        "engine_i486.so = 2c57641b06d9ef69",
        "",
    ])


def test_no_fixture_credential_reaches_the_rendered_report(drift, ar):
    """The whole point, end to end: snapshot -> redact -> parse -> report."""
    redacted = ar.redact_snapshot(_snapshot_text())
    snaps = {name: drift.parse_snapshot(redacted)
             for name in ("Atlanta", "Dallas", "Chicago")}
    report, _, _, _ = drift.render_report(snaps, {})
    for fake in FAKES:
        assert fake not in report, fake


def test_a_real_divergence_still_reports_through_the_redaction(drift, ar):
    """Partner control for the test above. A redaction that ate the section
    would also pass it."""
    keep = ar.redact_snapshot(_snapshot_text())
    lost = ar.redact_snapshot(
        _snapshot_text().replace("mitigations=off\n", ""))
    # Dallas, not Chicago: the high-signal section compares baremetals only.
    snaps = {"Atlanta": drift.parse_snapshot(keep),
             "Dallas": drift.parse_snapshot(lost),
             "Chicago": drift.parse_snapshot(keep)}
    report, bm_items, _, _ = drift.render_report(snaps, {})
    assert "mitigations=off" in report
    assert any(key == "mitigations=off" for _, key, _ in bm_items), bm_items


def test_snapshot_payload_redacts_both_halves(drift):
    """The wiring: this is what run_snapshot returns, and nothing else in the
    orchestrator sees the fleet's raw text."""
    out, err = drift.snapshot_payload(
        _snapshot_text(),
        "  ssh: connect to host 10.11.12.13 port 22: timed out\n")
    for fake in FAKES:
        assert fake not in out, fake
    assert "10.11.12.13" not in err
    assert err.startswith("ssh: connect")


def test_snapshot_payload_reports_no_error_when_stderr_is_blank(drift):
    """`err or None` is what makes a host count as reached; redaction must not
    turn whitespace into a truthy error and fail every host."""
    _, err = drift.snapshot_payload("=== HOST ===\nhostname: x\n", "  \n \n")
    assert err is None


def test_the_report_roster_carries_no_host_address(drift):
    report, _, _, _ = drift.render_report({}, {})
    assert "Atlanta" in report
    for addr in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
        assert addr not in report, addr


def test_redaction_is_idempotent(ar):
    """The report is built from already-redacted facts; a second pass anywhere
    downstream must not corrupt it."""
    once = ar.redact_snapshot(_snapshot_text())
    assert ar.redact_snapshot(once) == once


def test_line_count_is_preserved(ar):
    """The orchestrator groups facts by line. Dropping or splitting one would
    read as drift on every host at once."""
    raw = _snapshot_text()
    assert len(ar.redact_snapshot(raw).splitlines()) == len(raw.splitlines())
