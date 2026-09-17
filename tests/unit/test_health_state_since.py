"""ktp-data-server-health.sh: the state file remembers when each item was first seen.

The health check is the only thing that observes the transition, and its log is
root-only. Without `since` in the state file, "what is broken right now" could
be answered but "since when" could not -- which is how ktp-identity-reconcile
sat failed for over a week after its one alert. The document builder is
extracted from the shipped script by marker.
"""
import json
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ktp-data-server-health.sh"
BEGIN, END = "# >>> ktp-health-state", "# <<< ktp-health-state"
BASH = os.environ.get("KTP_TEST_BASH", "bash")

pytestmark = pytest.mark.skipif(
    shutil.which(BASH) is None or shutil.which("jq") is None, reason="needs bash and jq"
)


def source():
    text = SCRIPT.read_text(encoding="utf-8")
    assert BEGIN in text and END in text, "state markers are gone from the shipped script"
    return text.split(BEGIN, 1)[1].split("\n", 1)[1].split(END, 1)[0]


def build(tmp_path, down, prev, detail, ts):
    body = "%s\nhealth_state_document '%s' '%s' '%s' '%s'\n" % (
        source(), json.dumps(down), json.dumps(prev), json.dumps(detail), ts)
    p = tmp_path / "probe.sh"
    p.write_text(body, encoding="utf-8", newline="\n")
    r = subprocess.run([BASH, p.as_posix()], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_new_item_is_stamped_now_and_carried_forward_unchanged(tmp_path):
    t1 = build(tmp_path, ["mysql.service=failed"], {}, {}, "2026-09-08 09:00:00")
    assert t1["since"] == {"mysql.service=failed": "2026-09-08 09:00:00"}
    t2 = build(tmp_path, ["mysql.service=failed"], t1, {}, "2026-09-16 11:00:00")
    assert t2["since"] == {"mysql.service=failed": "2026-09-08 09:00:00"}
    assert t2["updated_at"] == "2026-09-16 11:00:00"


def test_a_cleared_item_drops_out_of_since(tmp_path):
    prev = {"since": {"a": "2026-09-01 00:00:00", "b": "2026-09-02 00:00:00"}}
    doc = build(tmp_path, ["b"], prev, {}, "2026-09-03 00:00:00")
    assert doc["down"] == ["b"] and doc["since"] == {"b": "2026-09-02 00:00:00"}


def test_an_old_state_file_without_since_reads_as_since_now_once(tmp_path):
    """The shape written before this change: every current item stamps now.
    Wrong by up to one week for anything already down, right from then on."""
    doc = build(tmp_path, ["disk-growth:/"], {"updated_at": "x", "down": ["disk-growth:/"]}, {}, "2026-09-16 11:00:00")
    assert doc["since"] == {"disk-growth:/": "2026-09-16 11:00:00"}


def test_detail_rides_along_only_for_items_that_have_one(tmp_path):
    doc = build(tmp_path, ["disk-growth:/", "failed-unit:x.service"], {},
                {"disk-growth:/": "4 GiB/day over the last 12h+", "stale-key": "ignored"}, "t")
    assert doc["detail"] == {"disk-growth:/": "4 GiB/day over the last 12h+"}


def test_empty_down_set_is_a_valid_document(tmp_path):
    doc = build(tmp_path, [], {"since": {"gone": "t0"}}, {}, "t1")
    assert doc == {"updated_at": "t1", "down": [], "since": {}, "detail": {}}
