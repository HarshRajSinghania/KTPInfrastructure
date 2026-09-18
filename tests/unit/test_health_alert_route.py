"""ktp-data-server-health.sh: severity, colour and channel come from the routing helper.

The health script is the most-fired producer on the box. Two properties make
it safe to change its post path during a live season, and both are pinned here:

  - With the helper sourced and no lane mapped, it posts to exactly the channel
    it posts to today (ALERT_CHANNEL, as the legacy fallback). Setting
    KTP_CHANNEL_PAGE is what moves it, and only that.
  - Without the helper present at all -- a deploy-order slip -- it still runs,
    with today's colours and channel. A dead watcher is the one outcome this
    estate cannot afford, so the script must never FATAL on a missing helper.

The route function is extracted from the shipped script by marker; the helper
is the real scripts/ktp-alert-routing.sh.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ktp-data-server-health.sh"
HELPER = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ktp-alert-routing.sh"
BEGIN, END = "# >>> ktp-health-route", "# <<< ktp-health-route"
BASH = os.environ.get("KTP_TEST_BASH", "bash")

pytestmark = pytest.mark.skipif(shutil.which(BASH) is None, reason="needs bash")

LEGACY = "1497957091107668070"


def route_source():
    text = SCRIPT.read_text(encoding="utf-8")
    assert BEGIN in text and END in text, "route markers are gone from the shipped script"
    return text.split(BEGIN, 1)[1].split("\n", 1)[1].split(END, 1)[0]


def run(tmp_path, n_new, with_helper=True, env=None):
    body = "set -euo pipefail\n"
    if with_helper:
        body += ". '%s'\n" % HELPER.as_posix()
    body += "ALERT_CHANNEL=%s\n%s\nhealth_alert_route %d\n" % (LEGACY, route_source(), n_new)
    body += ("printf '%s\\n' \"$KTP_ALERT_SEVERITY\" \"$KTP_ALERT_GLYPH\" \"$KTP_ALERT_COLOR\" "
             "\"$KTP_ALERT_LANE\" \"$KTP_ALERT_CHANNEL\" \"$KTP_ALERT_CHANNEL_SOURCE\"\n")
    p = tmp_path / "probe.sh"
    p.write_text(body, encoding="utf-8", newline="\n")
    r = subprocess.run([BASH, p.as_posix()], capture_output=True, text=True,
                       env={**os.environ, **(env or {})}, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    sev, glyph, color, lane, chan, src = r.stdout.rstrip("\n").split("\n")
    return dict(severity=sev, glyph=glyph, color=int(color), lane=lane, channel=chan, source=src)


def test_new_down_is_a_page_and_lands_where_it_lands_today(tmp_path):
    out = run(tmp_path, 2, env={"KTP_CHANNEL_PAGE": ""})
    assert out["severity"] == "page" and out["lane"] == "page"
    assert out["glyph"] == "\U0001F534" and out["color"] == 15548997
    assert out["channel"] == LEGACY and out["source"] == "legacy"


def test_recovery_only_is_green_on_the_same_lane(tmp_path):
    out = run(tmp_path, 0, env={"KTP_CHANNEL_PAGE": ""})
    assert out["severity"] == "recovery" and out["lane"] == "page"
    assert out["glyph"] == "\U0001F7E2" and out["color"] == 5763719
    assert out["channel"] == LEGACY


def test_mapping_the_page_lane_is_the_only_thing_that_moves_it(tmp_path):
    out = run(tmp_path, 1, env={"KTP_CHANNEL_PAGE": "1498813261263405097"})
    assert out["channel"] == "1498813261263405097" and out["source"] == "env"
    # A mapping for a lane this producer never uses changes nothing.
    out = run(tmp_path, 1, env={"KTP_CHANNEL_PAGE": "", "KTP_CHANNEL_OPS_DAILY": "42"})
    assert out["channel"] == LEGACY


def test_without_the_helper_it_still_runs_with_todays_colours_and_channel(tmp_path):
    down = run(tmp_path, 1, with_helper=False)
    up = run(tmp_path, 0, with_helper=False)
    assert (down["color"], up["color"]) == (15548997, 5763719)
    assert down["channel"] == up["channel"] == LEGACY
    assert down["source"] == "fallback" and down["glyph"] == ""


def test_colours_agree_with_the_helper_so_the_fallback_is_not_a_second_canon(tmp_path):
    """If the canon ever changes, this fails and the fallback gets updated -- or
    deleted, once the helper is deployed beside the script."""
    assert run(tmp_path, 1)["color"] == run(tmp_path, 1, with_helper=False)["color"]
    assert run(tmp_path, 0)["color"] == run(tmp_path, 0, with_helper=False)["color"]
