"""Unit tests for the team-score-labeled flag-swing fit driver."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fit_team_score_labels import fit_labeled_halves, half_winners


def final(match_id, half, allies_slot, allies, axis, tick="100"):
    return {"match_id": match_id, "half": half, "allies_team_id": allies_slot,
            "axis_team_id": 3 - allies_slot, "allies_score": allies,
            "axis_score": axis, "tick_seconds": tick, "event_sequence": 1}


def test_half_winners_difference_cumulative_closes_per_slot():
    # 1789326428-NY1 as the ledger holds it: h2 is cumulative, sides swapped.
    finals = [final("NY1", 1, 1, 142, 12), final("NY1", 2, 2, 25, 273)]
    # slot 1: 142 -> 273 (+131 as Axis in h2); slot 2: 12 -> 25 (+13 as Allies)
    assert half_winners(finals) == {"NY1": {1: 1, 2: 2}}


def test_half_winners_take_the_last_final_and_skip_draws_and_gaps():
    finals = [final("A", 1, 1, 10, 5, tick="50"), final("A", 1, 1, 5, 10, tick="60"),
              final("A", 2, 2, 30, 20),            # h2 points: slot 2 +20 as Allies, slot 1 +15
              final("B", 1, 1, 7, 7),              # draw: no label
              final("C", 2, 2, 9, 1)]              # no half 1: no label
    assert half_winners(finals) == {"A": {1: 2, 2: 1}}


def test_fit_labeled_halves_reports_per_half_calibration():
    roster = [{"player_id": pid, "team": 1 if pid <= 2 else 2} for pid in (1, 2, 3, 4)]
    lives = [{"half": 1, "player_id": pid, "team": 1 if pid <= 2 else 2,
              "game_time": 0.0, "boundary_kind": "start"} for pid in (1, 2, 3, 4)]
    inputs = {
        "flag_states": [{"half": 1, "flag_index": 0, "owner_team": 1, "game_time": 10.0},
                        {"half": 1, "flag_index": 1, "owner_team": 1, "game_time": 20.0}],
        "frags": [{"half": 1, "game_time": 30.0, "killer_id": 1, "victim_id": 3}],
        "life_boundaries": lives, "roster": roster,
    }
    result = fit_labeled_halves({"M": {1: 1}, "Z": {1: 2}},
                                lambda mid: inputs if mid == "M" else None,
                                lambda _map: {}, {"M": "dod_x"})
    rows = {r["match_id"]: r for r in result["halves"]}
    assert rows["Z"]["status"] == "no-inputs"
    assert rows["M"]["samples"] == 7 and rows["M"]["end_agrees_with_label"] is True
    assert result["halves_labeled"] == 1 and result["halves_end_state_agrees"] == 1
    assert result["config"]["calibration"] == "fitted_team_score_1_halves_7_samples"
