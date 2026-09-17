"""Key moments: highlight windows ranked on the flag_swing timeline."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import analytics_report_dto as dto
from scripts.highlight_windows import HighlightConfig, build_highlight_windows

ROSTER = [
    {"player_id": 1, "player_name_at_match": "alpha", "team": 1},
    {"player_id": 2, "player_name_at_match": "bravo", "team": 1},
    {"player_id": 3, "player_name_at_match": "charlie", "team": 2},
    {"player_id": 4, "player_name_at_match": "delta, jr", "team": 2},
]


def frag(t, killer, victim, delta, half=1):
    return {"half": half, "game_time": t, "kind": "frag", "killer_id": killer, "victim_id": victim,
            "killer_man_advantage": 0, "p_allies_after": 0.5, "delta": delta}


def flag(t, delta, half=1):
    return {"half": half, "game_time": t, "kind": "flag", "killer_id": None, "victim_id": None,
            "killer_man_advantage": 0, "p_allies_after": 0.5, "delta": delta}


def test_unavailable_when_source_is_not():
    out = build_highlight_windows([frag(10, 1, 3, 0.1)], ROSTER, source_status="unavailable")
    assert out["status"] == "unavailable" and out["windows"] == []
    assert any("unavailable" in c for c in out["caveats"])


def test_unavailable_on_empty_timeline():
    assert build_highlight_windows([], ROSTER)["status"] == "unavailable"
    assert build_highlight_windows(None, ROSTER)["status"] == "unavailable"


def test_events_within_gap_share_a_window_and_are_padded():
    tl = [frag(100, 1, 3, 0.05), frag(104, 1, 4, 0.05), frag(300, 2, 3, 0.02)]
    out = build_highlight_windows(tl, ROSTER, HighlightConfig(top_n=10))
    assert out["status"] == "available" and out["windows_total"] == 2
    first = out["windows"][0]
    assert first["events"] == 2 and first["start"] == 96.0 and first["end"] == 107.0
    assert first["summary"] == "2k by alpha"


def test_multikill_and_objective_bonuses_and_ranking():
    two_kills = [frag(10, 1, 3, 0.05), frag(12, 1, 4, 0.05)]
    one_big = [frag(200, 2, 3, 0.15)]
    with_flag = [frag(400, 3, 1, 0.05), flag(402, 0.05)]
    out = build_highlight_windows(two_kills + one_big + with_flag, ROSTER, HighlightConfig(top_n=10))
    by_start = {w["start"]: w for w in out["windows"]}
    assert by_start[6.0]["score"] == pytest.approx(0.10 + 0.15)   # swing + repeat-killer bonus
    assert by_start[196.0]["score"] == pytest.approx(0.15)
    assert by_start[396.0]["score"] == pytest.approx(0.10 + 0.10)  # swing + objective bonus
    assert [w["rank"] for w in out["windows"]] == [1, 2, 3]
    assert out["windows"][0]["start"] == 6.0, "the 2k with bonus outranks the single big swing"
    assert by_start[396.0]["kinds"] == ["flag", "frag"]
    assert by_start[396.0]["summary"] == "kill by charlie, 1 flag event"


def test_long_window_is_centred_on_the_peak_not_truncated():
    # 60 s of continuous action with the peak at the end; a start-truncated window would miss it.
    tl = [frag(100 + i * 5, 1 if i % 2 else 2, 3, 0.01) for i in range(12)] + [frag(160, 1, 4, 0.30)]
    out = build_highlight_windows(tl, ROSTER, HighlightConfig(max_len=30.0, top_n=5))
    w = out["windows"][0]
    assert w["duration"] == 30.0
    assert w["start"] <= 160 <= w["end"], "peak-swing event is inside the window"
    assert w["peak_at"] == 160.0


def test_involved_is_capped_and_led_by_the_dominant_killer():
    # alpha kills 2, bravo kills 1, charlie dies 3 times, delta dies once
    tl = [frag(10, 1, 3, 0.05), frag(12, 1, 3, 0.05), frag(14, 2, 3, 0.05), frag(16, 3, 4, 0.05)]
    out = build_highlight_windows(tl, ROSTER, HighlightConfig(max_cameras=2, top_n=1))
    inv = out["windows"][0]["involved"]
    assert len(inv) == 2
    assert inv[0]["player_id"] == 1 and inv[0]["player_name_at_match"] == "alpha"
    assert out["windows"][0]["summary"].startswith("2k by alpha")
    assert all("involvement" in p and "team" in p for p in inv)


def test_halves_never_merge_across_the_break():
    tl = [frag(1190, 1, 3, 0.05, half=1), frag(5, 2, 4, 0.05, half=2)]
    out = build_highlight_windows(tl, ROSTER, HighlightConfig(top_n=10))
    assert out["windows_total"] == 2 and {w["half"] for w in out["windows"]} == {1, 2}


def test_top_n_and_render_budget_fields():
    tl = [frag(100 * i, 1, 3, 0.01 * (i + 1)) for i in range(30)]
    out = build_highlight_windows(tl, ROSTER, HighlightConfig(top_n=5, max_cameras=4))
    assert out["windows_total"] == 30 and len(out["windows"]) == 5
    assert out["windows"][0]["swing"] > out["windows"][-1]["swing"]
    assert out["render_seconds"] == pytest.approx(sum(w["duration"] for w in out["windows"]))
    assert out["render_seconds_all_involved"] == pytest.approx(
        sum(w["duration"] * len(w["involved"]) for w in out["windows"]))


def test_config_validation():
    with pytest.raises(ValueError):
        HighlightConfig(max_len=1.0, min_len=8.0).validate()
    with pytest.raises(ValueError):
        HighlightConfig(top_n=0).validate()


def test_parameters_are_published_for_reproducibility():
    out = build_highlight_windows([frag(10, 1, 3, 0.1)], ROSTER)
    p = out["parameters"]
    assert p["ranker"] == "flag_swing_v1" and p["clock"] == "producer_game_time"
    assert p["top_n"] == 20 and p["max_cameras"] == 4 and p["max_len"] == 30.0


# ---------------------------------------------------------------- public DTO

def _minimal_report(highlight_windows: dict) -> dict:
    """Just enough report for sanitize_report to run; every other block empty."""
    return {
        "schema_version": 16, "match_id": "1.3-1-TST1", "generated_at": "2026-09-17 00:00:00",
        "match": {"match_id": "1.3-1-TST1", "map_name": "dod_anzio", "started_at": "2026-09-17 00:00:00",
                  "ended_at": "2026-09-17 00:40:00", "halves_played": 2, "duration_seconds": 2400},
        "players": ROSTER, "teams": [], "weapons": [], "duel_matrix": [], "assists": [],
        "capture_events": [], "capture_credits": [], "cap_participation": [],
        "shadow_explorations": {"highlight_windows": highlight_windows},
        "positional": {}, "spatial_layers": {}, "quality": {}, "source_mode": "database",
    }


def test_public_key_moments_block_is_names_only_and_sanitized():
    hw = build_highlight_windows(
        [frag(10, 1, 3, 0.05), frag(12, 1, 4, 0.05), flag(14, 0.05)], ROSTER, HighlightConfig(top_n=3))
    public = dto.sanitize_report(_minimal_report(hw))
    dto.assert_sanitized(public)
    km = public["key_moments"]
    assert km["status"] == "available" and km["definition"] == "highlight_windows_v1"
    assert km["windows_total"] == 1 and len(km["windows"]) == 1
    w = km["windows"][0]
    assert w["rank"] == 1 and w["half"] == 1 and w["summary"] == "2k by alpha, 1 flag event"
    assert [p["name"] for p in w["involved"]][0] == "alpha"
    assert "player_id" not in json.dumps(km)
    assert "ratings" in public and "key_moments" not in public["ratings"], "not a rating; top-level"


def test_public_key_moments_block_when_unavailable():
    public = dto.sanitize_report(_minimal_report(build_highlight_windows([], ROSTER)))
    assert public["key_moments"]["status"] == "unavailable" and public["key_moments"]["windows"] == []


def test_public_key_moments_block_when_absent_from_report():
    public = dto.sanitize_report(_minimal_report({}))
    assert public["key_moments"]["status"] == "unavailable"


SPECIMENS = os.environ.get("KTP_REPORT_SPECIMENS")


@pytest.mark.skipif(not SPECIMENS or not Path(SPECIMENS).is_dir(), reason="KTP_REPORT_SPECIMENS not set")
def test_real_corpus_every_report_ranks_and_sanitizes():
    specimens = Path(SPECIMENS)
    n = 0
    for path in sorted(specimens.glob("report-*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        fs = report["shadow_explorations"]["flag_swing"]
        hw = build_highlight_windows(fs.get("timeline"), report["players"], source_status=fs.get("status"))
        report["shadow_explorations"]["highlight_windows"] = hw
        public = dto.sanitize_report(report)
        dto.assert_sanitized(public)
        if hw["status"] == "available":
            assert 1 <= len(public["key_moments"]["windows"]) <= 20
            for w in public["key_moments"]["windows"]:
                if "frag" in w["kinds"]:
                    assert w["involved"] and w["involved"][0]["name"], "a window with a kill has a lead camera"
                else:
                    assert w["involved"] == [], "flag-only windows carry no player ids in the timeline"
        n += 1
    assert n > 0
