"""The routing canon, and the drift it exists to stop.

Three things are asserted here that no other test could:

  1. The Python module and its bash twin agree. They must, because half the
     producers are shell and shelling out to Python on the alert path is worse
     than a second copy — so the second copy needs a test, not a comment.
  2. No producer that posts a Discord embed carries a colour outside the canon.
     This is the check that would have caught `hltv-restart-all.sh` rendering
     pure `0x00FF00` for a year. Scope comes from the files themselves (anything
     that builds a `channelId` payload), so a producer added tomorrow is covered
     without anyone remembering to list it.
  3. A green run is silent, and a green that follows a bad run is not.
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ktp_alert_routing as ar  # noqa: E402

SHELL_TWIN = SCRIPTS / "ktp-alert-routing.sh"
CANON = {ar.KTP_GREEN, ar.KTP_YELLOW, ar.KTP_RED, ar.KTP_GREY}

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


# ──────────────────────────────────────────────────────────────────────────
# 1. The two copies of the canon
# ──────────────────────────────────────────────────────────────────────────

def shell_canon() -> dict[str, str]:
    """Read the marked block out of the shell twin, so deleting or renaming the
    markers fails here rather than quietly testing nothing."""
    text = SHELL_TWIN.read_text(encoding="utf-8")
    begin, end = "# >>> ktp-alert-canon", "# <<< ktp-alert-canon"
    assert begin in text and end in text, "canon markers are gone from the shell twin"
    block = text.split(begin, 1)[1].split(end, 1)[0]
    out = {}
    for line in block.splitlines():
        m = re.match(r"^([A-Z_][A-Z_0-9]*)=('?)([^'#]*)\2\s*(#.*)?$", line.strip())
        if m:
            out[m.group(1)] = m.group(3).strip()
    return out


def test_shell_twin_carries_the_same_colours():
    shell = shell_canon()
    assert int(shell["KTP_GREEN"]) == ar.KTP_GREEN
    assert int(shell["KTP_YELLOW"]) == ar.KTP_YELLOW
    assert int(shell["KTP_RED"]) == ar.KTP_RED
    assert int(shell["KTP_GREY"]) == ar.KTP_GREY


def test_shell_twin_carries_the_same_glyphs_and_lanes():
    shell = shell_canon()
    for severity, glyph in ar.GLYPHS.items():
        assert shell[f"KTP_GLYPH_{severity.name}"] == glyph
    for lane in ar.Lane:
        assert shell[f"KTP_LANE_{lane.name}"] == lane.value


@needs_bash
@pytest.mark.parametrize("severity", [s.value for s in ar.Severity])
def test_shell_route_matches_python_route(tmp_path, severity):
    probe = tmp_path / "probe.sh"
    probe.write_text(
        f'. "{SHELL_TWIN.as_posix()}"\n'
        f'ktp_alert_route {severity} || exit 9\n'
        'printf "%s\\t%s\\t%s\\n" "$KTP_ALERT_GLYPH" "$KTP_ALERT_COLOR" "$KTP_ALERT_LANE"\n',
        encoding="utf-8", newline="\n")
    got = subprocess.run(["bash", probe.as_posix()], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout.strip().split("\t")
    want = ar.route(ar.Severity(severity))
    assert got == [want.glyph, str(want.color), want.lane.value]


@needs_bash
def test_shell_route_rejects_an_unknown_severity(tmp_path):
    """A typo must not render as INFO — that is a page that never arrives."""
    probe = tmp_path / "probe.sh"
    probe.write_text(f'. "{SHELL_TWIN.as_posix()}"\nktp_alert_route critcal\n',
                     encoding="utf-8", newline="\n")
    r = subprocess.run(["bash", probe.as_posix()], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 2


# ──────────────────────────────────────────────────────────────────────────
# 2. Nothing in the estate posts an off-canon colour
# ──────────────────────────────────────────────────────────────────────────

# The one file that is allowed to disagree, and for a reason that is not taste:
# the live canonical it mirrors is host-only and gitignored, and
# scripts/deploy-restart-script.py refuses a deploy when the two differ by
# anything at all — a comment included. It moves when the host copy moves.
COLOUR_SCAN_EXEMPT = {"scripts/ktp-scheduled-restart.sh.example"}

COLOUR_LINE = re.compile(r"colou?r", re.IGNORECASE)
INTEGER = re.compile(r"(?<![\w.#])(\d{4,8})(?![\w.])")


def embed_producers() -> list[pathlib.Path]:
    """Every tracked file that builds a Discord embed payload.

    Derived from content (`channelId`), not from a list someone maintains: an
    allow-list would be blind to the producer added next week, which is the
    exact way this drift got in.
    """
    found = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in (".py", ".sh", ".example"):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith((".git/", "tests/", "sites/", "services/")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        if "channelId" in text:
            found.append(path)
    return found


def test_the_scan_finds_the_producers_it_is_meant_to():
    """A scope bug here reads as a clean pass, so pin the scope itself."""
    rels = {p.relative_to(ROOT).as_posix() for p in embed_producers()}
    for expected in ("scripts/hltv-restart-all.sh",
                     "scripts/ktp-perf-rollup.py",
                     "scripts/ktp-data-server-health.sh",
                     "scripts/ktp-systemd-alert.py",
                     "monitoring/crashreporter/report_core.py"):
        assert expected in rels, f"colour scan does not reach {expected}"


def test_no_producer_carries_an_off_canon_embed_colour():
    offenders = []
    for path in embed_producers():
        rel = path.relative_to(ROOT).as_posix()
        if rel in COLOUR_SCAN_EXEMPT:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not COLOUR_LINE.search(line):
                continue
            for value in INTEGER.findall(line):
                if int(value) not in CANON:
                    offenders.append(f"{rel}:{n}: {value} — {line.strip()}")
    assert not offenders, (
        "off-canon embed colours (use ktp_alert_routing / ktp-alert-routing.sh):\n"
        + "\n".join(offenders))


def test_the_colour_scan_can_fail(tmp_path, monkeypatch):
    """A scan that cannot fail is not a check. Prove it flags a planted value."""
    planted = tmp_path / "fake-producer.sh"
    planted.write_text('payload="{\\"channelId\\": \\"1\\"}"\ncolor=65280\n',
                       encoding="utf-8", newline="\n")
    lines = [l for l in planted.read_text(encoding="utf-8").splitlines()
             if COLOUR_LINE.search(l)]
    assert lines, "the planted line does not even reach the colour matcher"
    assert any(int(v) not in CANON for l in lines for v in INTEGER.findall(l))


# ──────────────────────────────────────────────────────────────────────────
# 3. Routing and channel resolution
# ──────────────────────────────────────────────────────────────────────────

def test_severity_decides_glyph_and_colour():
    assert ar.route(ar.Severity.PAGE) == (ar.Severity.PAGE, ar.Lane.PAGE, "🔴", ar.KTP_RED)
    assert ar.route(ar.Severity.WARN).lane is ar.Lane.OPS_DAILY
    assert ar.route(ar.Severity.INFO).glyph == "⚪"
    assert ar.route(ar.Severity.RECOVERY).color == ar.KTP_GREEN


def test_a_recovery_lands_in_its_producers_lane_not_always_the_page_lane():
    """A weekly producer's all-clear belongs next to the thing it clears."""
    assert ar.route_for("ktp-soak-verify", ar.Severity.RECOVERY).lane is ar.Lane.OPS_WEEKLY
    assert ar.route_for("crashreporter", ar.Severity.RECOVERY).lane is ar.Lane.PAGE


def test_an_unknown_producer_falls_back_to_the_severity_default():
    assert ar.route_for("something-new", ar.Severity.PAGE).lane is ar.Lane.PAGE


def test_every_lane_has_an_env_key_and_every_producer_a_known_lane():
    assert set(ar.LANE_ENV_KEY) == set(ar.Lane)
    assert set(ar.DEFAULT_LANE) == set(ar.Severity)
    assert set(ar.GLYPHS) == set(ar.Severity) == set(ar.COLORS)
    for producer, lane in ar.PRODUCER_LANE.items():
        assert isinstance(lane, ar.Lane), producer


def test_channel_resolution_prefers_env_then_conf_then_legacy(monkeypatch):
    conf = {"KTP_CHANNEL_PAGE": "222222222222222222"}
    monkeypatch.delenv("KTP_CHANNEL_PAGE", raising=False)
    assert ar.resolve_channel(ar.Lane.PAGE, conf, "333") == ("222222222222222222", "conf")
    monkeypatch.setenv("KTP_CHANNEL_PAGE", "111111111111111111")
    assert ar.resolve_channel(ar.Lane.PAGE, conf, "333") == ("111111111111111111", "env")


def test_channel_resolution_falls_back_to_the_callers_existing_channel(monkeypatch):
    """This is what makes the module safe to merge before the config change:
    an unmapped lane keeps posting exactly where that producer posts today."""
    monkeypatch.delenv("KTP_CHANNEL_OPS_DAILY", raising=False)
    got = ar.resolve_channel(ar.Lane.OPS_DAILY, {}, "1497957091107668070")
    assert got == ("1497957091107668070", "legacy")


def test_channel_resolution_never_invents_an_id(monkeypatch):
    monkeypatch.delenv("KTP_CHANNEL_OPS_WEEKLY", raising=False)
    with pytest.raises(LookupError):
        ar.resolve_channel(ar.Lane.OPS_WEEKLY, {}, None)


def test_a_non_id_in_the_config_is_an_error_not_a_channel(monkeypatch):
    """`KTP_CHANNEL_PAGE=#ktp-crashes` is the plausible config mistake; posting
    to a channel named by that string would silently go nowhere."""
    monkeypatch.setenv("KTP_CHANNEL_PAGE", "#ktp-crashes")
    with pytest.raises(ValueError):
        ar.resolve_channel(ar.Lane.PAGE, {}, "1497957091107668070")


def test_load_conf_reads_the_relay_shape_and_tolerates_a_missing_file(tmp_path):
    p = tmp_path / "discord-relay.conf"
    p.write_text('# comment\nRELAY_URL="https://x/"\nKTP_CHANNEL_PAGE=123\n\nJUNK\n',
                 encoding="utf-8")
    cfg = ar.load_conf(p)
    assert cfg["RELAY_URL"] == "https://x/" and cfg["KTP_CHANNEL_PAGE"] == "123"
    assert ar.load_conf(tmp_path / "nope.conf") == {}


# ──────────────────────────────────────────────────────────────────────────
# 4. Silence means healthy
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("severity,previous,expected", [
    (ar.Severity.PAGE, None, True),
    (ar.Severity.PAGE, ar.Severity.PAGE, True),
    (ar.Severity.WARN, ar.Severity.INFO, True),
    (ar.Severity.INFO, ar.Severity.INFO, False),
    (ar.Severity.INFO, None, False),
    (ar.Severity.INFO, ar.Severity.PAGE, True),
    (ar.Severity.INFO, ar.Severity.WARN, True),
    (ar.Severity.RECOVERY, ar.Severity.PAGE, True),
])
def test_should_post(severity, previous, expected):
    assert ar.should_post(severity, previous) is expected


def test_a_lost_state_file_does_not_manufacture_an_all_clear():
    """No history must read as routine, not as recovery: an all-clear for a page
    nobody saw is worse than one more quiet night."""
    assert ar.should_post(ar.Severity.INFO, None) is False
    assert ar.recovery_of(ar.Severity.INFO, None) is ar.Severity.INFO


def test_recovery_of_promotes_only_a_green_after_a_bad_run():
    assert ar.recovery_of(ar.Severity.INFO, ar.Severity.PAGE) is ar.Severity.RECOVERY
    assert ar.recovery_of(ar.Severity.INFO, ar.Severity.INFO) is ar.Severity.INFO
    assert ar.recovery_of(ar.Severity.PAGE, ar.Severity.PAGE) is ar.Severity.PAGE


# ──────────────────────────────────────────────────────────────────────────
# 5. The digest spool
# ──────────────────────────────────────────────────────────────────────────

def test_spool_round_trip(tmp_path):
    ar.record_digest_line("hltv-restart-all", "HLTV: 24/24 connected",
                          spool_dir=str(tmp_path))
    ar.record_digest_line("ktp-scheduled-restart", "Game servers: 24/24",
                          spool_dir=str(tmp_path))
    lines = ar.drain_digest_lines(spool_dir=str(tmp_path))
    assert [l["producer"] for l in lines] == ["hltv-restart-all", "ktp-scheduled-restart"]
    assert lines[0]["glyph"] == "⚪"
    assert ar.drain_digest_lines(spool_dir=str(tmp_path)) == [], "drain must clear"


def test_a_malformed_spool_line_does_not_sink_the_digest(tmp_path):
    path = ar.spool_path(ar.Lane.OPS_DAILY, str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"producer":"good","text":"a"}\nnot json\n\n', encoding="utf-8")
    assert [l["producer"] for l in ar.drain_digest_lines(spool_dir=str(tmp_path))] == ["good"]


@needs_bash
def test_shell_spool_writes_what_python_reads(tmp_path):
    if shutil.which("jq") is None:
        pytest.skip("needs jq")
    probe = tmp_path / "probe.sh"
    probe.write_text(
        f'export KTP_ALERT_SPOOL_DIR="{tmp_path.as_posix()}"\n'
        f'. "{SHELL_TWIN.as_posix()}"\n'
        'ktp_alert_spool_line hltv-restart-all info "HLTV: 24/24 connected"\n',
        encoding="utf-8", newline="\n")
    subprocess.run(["bash", probe.as_posix()], capture_output=True, text=True, check=True)
    lines = ar.drain_digest_lines(spool_dir=str(tmp_path))
    assert len(lines) == 1
    assert lines[0]["producer"] == "hltv-restart-all"
    assert lines[0]["glyph"] == ar.GLYPHS[ar.Severity.INFO]


# ──────────────────────────────────────────────────────────────────────────
# 6. hltv-restart-all.sh, the first producer wired to the canon
# ──────────────────────────────────────────────────────────────────────────

RESTART = SCRIPTS / "hltv-restart-all.sh"
R_BEGIN, R_END = "# >>> ktp-hltv-restart-severity", "# <<< ktp-hltv-restart-severity"


def restart_functions() -> str:
    text = RESTART.read_text(encoding="utf-8")
    assert R_BEGIN in text and R_END in text, "severity markers are gone from hltv-restart-all.sh"
    return text.split(R_BEGIN, 1)[1].split("\n", 1)[1].split(R_END, 1)[0]


@needs_bash
@pytest.mark.parametrize("failed,succeeded,expected", [
    (0, 24, "info"),
    (1, 23, "warn"),
    (24, 0, "page"),
])
def test_restart_severity(tmp_path, failed, succeeded, expected):
    probe = tmp_path / "probe.sh"
    probe.write_text(restart_functions() + f'\nhltv_restart_severity {failed} {succeeded}\n',
                     encoding="utf-8", newline="\n")
    out = subprocess.run(["bash", probe.as_posix()], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == expected


@needs_bash
@pytest.mark.parametrize("severity,previous,posts", [
    ("info", "info", False),
    ("info", "", False),
    ("info", "page", True),
    ("info", "warn", True),
    ("warn", "info", True),
    ("page", "page", True),
])
def test_restart_should_post(tmp_path, severity, previous, posts):
    probe = tmp_path / "probe.sh"
    probe.write_text(
        restart_functions()
        + f'\nif hltv_restart_should_post {severity} "{previous}"; then echo POST; else echo QUIET; fi\n',
        encoding="utf-8", newline="\n")
    out = subprocess.run(["bash", probe.as_posix()], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == ("POST" if posts else "QUIET")


def test_restart_script_no_longer_carries_its_own_colour_constants():
    text = RESTART.read_text(encoding="utf-8")
    assert "COLOR_GREEN=65280" not in text
    assert "ktp-alert-routing.sh" in text


def test_restart_script_still_posts_to_both_hltv_channels():
    """The KTP/EXTERNAL pair is an operator decision, not something this change
    gets to collapse. Halving someone's feed without asking is worse than noise."""
    text = RESTART.read_text(encoding="utf-8")
    assert 'send_discord_embed "$CHANNEL_HLTV_STATUS"' in text
    assert 'send_discord_embed "$CHANNEL_HLTV_STATUS_EXTERNAL"' in text


# ──────────────────────────────────────────────────────────────────────────
# 7. The CLI a shell caller could use
# ──────────────────────────────────────────────────────────────────────────

def test_cli_emits_json(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("KTP_CHANNEL_PAGE", "999999999999999999")
    rc = ar._cli(["page", "--producer", "crashreporter", "--json",
                  "--conf", str(tmp_path / "absent.conf")])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"severity": "page", "lane": "page", "glyph": "🔴",
                   "color": ar.KTP_RED, "channel": "999999999999999999",
                   "channel_source": "env"}


def test_cli_refuses_an_unmapped_lane_rather_than_guessing(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("KTP_CHANNEL_OPS_WEEKLY", raising=False)
    rc = ar._cli(["info", "--lane", "ops-weekly", "--conf", str(tmp_path / "absent.conf")])
    assert rc == 2
    assert "never guesses" in capsys.readouterr().err
