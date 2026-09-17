"""Upset dossiers: explaining a confident miss in terms of who played how.

Product #1 from the original brief. A weekly report saying "we called this
wrong" teaches nobody anything; one saying "the side we favoured had its two
best players post their worst matches" is actionable.

Pure logic, no network, no openskill.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "mmr"))

NAMES = {1: "Ecl1ps3", 2: "berty", 3: "njr", 11: "seanality", 12: "vertex", 13: "sik"}


def match(p_raw=0.8, y=0.0):
    """Favours home by default (p_raw > 0.5) and home loses (y = 0) -- an upset."""
    return {"match_id": "league-900", "t1": [1, 2, 3], "t2": [11, 12, 13],
            "home": "-revo", "away": "IcyHot", "p_raw": p_raw, "y": y}


class Build(unittest.TestCase):
    def setUp(self):
        import dossier as D
        self.D = D

    def test_names_who_went_missing_on_the_favoured_side(self):
        d = self.D.build(match(), {1: -1.8, 2: -1.2, 3: 0.1}, NAMES)
        self.assertEqual(d["favoured"], "-revo")
        self.assertEqual(d["winner"], "IcyHot")
        self.assertEqual([p["name"] for p in d["underperformed"]], ["Ecl1ps3", "berty"])
        # njr was ordinary; naming ordinary matches is noise, not explanation.
        self.assertNotIn("njr", [p["name"] for p in d["underperformed"]])

    def test_names_who_went_beyond_expectation_on_the_winning_side(self):
        d = self.D.build(match(), {11: 2.0, 12: 1.1, 13: 0.0}, NAMES)
        self.assertEqual([p["name"] for p in d["overperformed"]], ["seanality", "vertex"])

    def test_worst_and_best_are_listed_first(self):
        d = self.D.build(match(), {1: -0.9, 2: -2.5, 11: 1.0, 12: 3.0}, NAMES)
        self.assertEqual([p["name"] for p in d["underperformed"]], ["berty", "Ecl1ps3"])
        self.assertEqual([p["name"] for p in d["overperformed"]], ["vertex", "seanality"])

    def test_an_ordinary_match_produces_no_dossier(self):
        """If nobody stood out, there is nothing to explain, and saying so
        anyway would train people to skim the section."""
        self.assertIsNone(self.D.build(match(), {1: 0.1, 2: -0.2, 11: 0.3}, NAMES))

    def test_no_performance_data_produces_no_dossier(self):
        self.assertIsNone(self.D.build(match(), {}, NAMES))

    def test_the_favoured_side_follows_the_lean_not_home_advantage(self):
        """When the model favoured AWAY, the underperformers to name are on
        away -- reading home as favoured would blame the wrong team."""
        d = self.D.build(match(p_raw=0.2, y=1.0), {11: -2.0, 1: 2.0}, NAMES)
        self.assertEqual(d["favoured"], "IcyHot")
        self.assertEqual(d["winner"], "-revo")
        self.assertEqual([p["name"] for p in d["underperformed"]], ["seanality"])
        self.assertEqual([p["name"] for p in d["overperformed"]], ["Ecl1ps3"])

    def test_a_long_list_is_capped(self):
        scores = {pid: -3.0 for pid in (1, 2, 3)}
        d = self.D.build(match(), scores, NAMES)
        self.assertLessEqual(len(d["underperformed"]), self.D.MAX_NAMED)

    def test_an_unknown_player_is_still_named_usefully(self):
        d = self.D.build(match(), {1: -2.0}, {})
        self.assertEqual(d["underperformed"][0]["name"], "player 1")


class Render(unittest.TestCase):
    def setUp(self):
        import dossier as D
        self.D = D

    def test_nothing_renders_when_there_are_no_dossiers(self):
        self.assertEqual(self.D.render([]), [])

    def test_renders_both_sides_with_signed_values(self):
        d = self.D.build(match(), {1: -1.8, 11: 2.0}, NAMES)
        text = "\n".join(self.D.render([d]))
        self.assertIn("-revo were favoured; IcyHot won.", text)
        self.assertIn("Ecl1ps3 (-1.80)", text)
        self.assertIn("seanality (+2.00)", text)

    def test_says_it_describes_rather_than_adjusts(self):
        """Guard on the framing: a reader must not take this as the model
        having already learned from the miss."""
        text = "\n".join(self.D.render([self.D.build(match(), {1: -2.0}, NAMES)]))
        self.assertIn("does not adjust", text)


if __name__ == "__main__":
    unittest.main()
