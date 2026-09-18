"""Reading HLTV-demo winner labels for the ladder's cross-check.

Pure parsing and reduction; no network.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "mmr"))


def obs(match_id, half, allies, axis, allies_slot, kind="final"):
    axis_slot = 2 if allies_slot == 1 else 1
    return {"match_id": match_id, "half": half, "allies": allies, "axis": axis,
            "allies_slot": allies_slot, "axis_slot": axis_slot, "kind": kind, "source": "t"}


class Reduction(unittest.TestCase):
    def setUp(self):
        import demo_labels as D
        self.D = D

    def test_scores_are_cumulative_so_half_two_is_a_difference(self):
        # h1: slot 1 is Allies (80), slot 2 is Axis (26).
        # h2 (swapped): slot 2 is Allies, cumulative 44; slot 1 is Axis, cumulative 130.
        rows = [obs("M", 1, 80, 26, allies_slot=1), obs("M", 2, 44, 130, allies_slot=2)]
        L = self.D.labels(rows)["M"]
        self.assertEqual(L["halves"][1], {1: 80, 2: 26})
        self.assertEqual(L["halves"][2], {1: 50, 2: 18})     # 130-80, 44-26
        self.assertEqual(L["match"], {1: 130, 2: 44})        # the h2 cumulative

    def test_baseline_rows_are_ignored(self):
        rows = [obs("M", 1, 0, 0, 1, kind="baseline"), obs("M", 1, 80, 26, 1),
                obs("M", 2, 0, 0, 2, kind="baseline"), obs("M", 2, 44, 130, 2)]
        self.assertEqual(self.D.labels(rows)["M"]["match"], {1: 130, 2: 44})

    def test_a_match_with_one_half_is_not_labelled(self):
        self.assertEqual(self.D.labels([obs("M", 1, 80, 26, 1)]), {})

    def test_sides_record_the_swap(self):
        """The trap: game_match_player.game_team is the side at match END,
        the demo slot is stable across the swap. Consumers need the per-half
        side map to reconcile the two -- all nine S10 matches inverted when
        this was assumed equal."""
        rows = [obs("M", 1, 80, 26, allies_slot=1), obs("M", 2, 44, 130, allies_slot=2)]
        sides = self.D.labels(rows)["M"]["sides"]
        self.assertEqual(sides[1], {"allies": 1, "axis": 2})
        self.assertEqual(sides[2], {"allies": 2, "axis": 1})

    def test_winner_and_tie(self):
        self.assertEqual(self.D.winner({1: 130, 2: 70}), 1)
        self.assertEqual(self.D.winner({1: 12, 2: 70}), 2)
        self.assertIsNone(self.D.winner({1: 50, 2: 50}))


class BackfillParser(unittest.TestCase):
    def test_parses_the_real_row_shape(self):
        import demo_labels as D
        line = ("INSERT INTO ktp_team_score_observations (`match_id`) VALUES "
                "('1789326428-NY1', 'dod_thunder2', 0, 2, '0.000000000', 2, "
                "'2026-09-13 15:40:00.000', 25, 273, 2, 1, 'NY1', "
                "'hltv-demo-team-score-v1', 1, 'final', 'retained', UNHEX('ab'), "
                "'{\"json\": \"with, commas\"}', UNHEX('cd'), UNHEX('ef'), UNHEX('01'), 1);")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.sql"
            p.write_text(line, encoding="utf-8")
            rows = list(D.read_backfill_sql(p))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual((r["match_id"], r["half"], r["kind"]), ("1789326428-NY1", 2, "final"))
        self.assertEqual((r["allies"], r["axis"]), (25, 273))
        self.assertEqual((r["allies_slot"], r["axis_slot"]), (2, 1))

    def test_table_rows_reduce_to_the_same_shape(self):
        import demo_labels as D
        rows = list(D.rows_from_table([{
            "match_id": "M", "half": 1, "allies_score": 80, "axis_score": 26,
            "allies_team_id": 1, "axis_team_id": 2, "observation_kind": "final",
            "source": "hltv-demo-team-score-v1"}]))
        self.assertEqual(rows[0]["allies_slot"], 1)
        self.assertEqual(rows[0]["allies"], 80)


if __name__ == "__main__":
    unittest.main()
