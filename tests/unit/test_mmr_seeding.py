"""Unit tests for the division seeding recommendations.

Pure logic only -- no network, no openskill -- so this runs on the Tier 1
config gate, which installs pytest and little else.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "mmr"))


def rating(mu, sigma, matches=20):
    return {"mu": mu, "sigma": sigma, "matches": matches}


class ConservativeEstimate(unittest.TestCase):
    def setUp(self):
        import seeding_report as S
        self.S = S

    def test_uncertainty_is_penalised(self):
        settled = self.S.conservative(rating(25.0, 1.0))
        unproven = self.S.conservative(rating(25.0, 8.0))
        self.assertGreater(settled, unproven)

    def test_a_settled_lower_mu_can_outrank_a_loud_higher_one(self):
        """The reason seeding does not rank on mu.

        A player two matches in can post a flattering mu; seeding on it would
        place a team on noise.
        """
        loud = self.S.conservative(rating(30.0, 8.0))
        settled = self.S.conservative(rating(26.0, 1.0))
        self.assertGreater(settled, loud)


class TeamStrength(unittest.TestCase):
    def setUp(self):
        import seeding_report as S
        self.S = S

    def test_spread_reports_a_top_heavy_roster(self):
        # Same mean (25), very different teams -- the spread is what says so.
        even = self.S.team_strength([rating(25, 2), rating(25, 2), rating(25, 2)])
        lopsided = self.S.team_strength([rating(40, 2), rating(25, 2), rating(10, 2)])
        self.assertEqual(even["spread"], 0.0)
        self.assertGreater(lopsided["spread"], 20)
        self.assertAlmostEqual(even["score"], lopsided["score"], places=6)

    def test_empty_roster_has_no_strength(self):
        self.assertIsNone(self.S.team_strength([]))


class DivisionSplit(unittest.TestCase):
    def setUp(self):
        import seeding_report as S
        self.S = S

    def test_teams_fill_the_leagues_real_division_sizes(self):
        ranked = [{"team": f"T{i}", "score": 100 - i} for i in range(6)]
        placement, _ = self.S.split_into_divisions(ranked, [("Gold", 2), ("Silver", 2), ("Bronze", 2)])
        self.assertEqual(placement["T0"], "Gold")
        self.assertEqual(placement["T1"], "Gold")
        self.assertEqual(placement["T2"], "Silver")
        self.assertEqual(placement["T5"], "Bronze")

    def test_boundary_gap_exposes_a_marginal_split(self):
        # T1 and T2 are effectively tied across the Gold/Silver line.
        ranked = [{"team": "T0", "score": 50}, {"team": "T1", "score": 40},
                  {"team": "T2", "score": 39.9}, {"team": "T3", "score": 20}]
        _, boundaries = self.S.split_into_divisions(ranked, [("Gold", 2), ("Silver", 2)])
        self.assertAlmostEqual(boundaries["Gold"], 0.1, places=6)


class Confidence(unittest.TestCase):
    def setUp(self):
        import seeding_report as S
        self.S = S

    def test_mostly_provisional_roster_is_low_confidence(self):
        row = {"provisional_players": 5, "rated_players": 8, "roster_size": 8}
        self.assertTrue(self.S.confidence_for(row, 10.0).startswith("low"))

    def test_a_knife_edge_boundary_is_low_confidence_even_when_well_rated(self):
        """A solid rating can still make a marginal RECOMMENDATION."""
        row = {"provisional_players": 0, "rated_players": 8, "roster_size": 8}
        self.assertIn("borderline", self.S.confidence_for(row, 0.3))

    def test_well_evidenced_and_clearly_separated_is_high(self):
        row = {"provisional_players": 0, "rated_players": 8, "roster_size": 8}
        self.assertEqual(self.S.confidence_for(row, 9.0), "high")


class Report(unittest.TestCase):
    def setUp(self):
        import seeding_report as S
        self.S = S

    def _build(self, matches):
        # Players 6-11 rate higher, so Bravo should take Gold and Alpha drop
        # to Silver -- the opposite of where they sit today, so both move.
        ratings = {str(i): rating(25 + i, 1.0, matches) for i in range(12)}
        rosters = {"Alpha": list(range(6)), "Bravo": list(range(6, 12))}
        return self.S.build(ratings, rosters, [("Gold", 1), ("Silver", 1)],
                            current={"Alpha": "Gold", "Bravo": "Silver"})

    def test_a_team_below_the_rated_minimum_is_not_seeded(self):
        ratings = {"1": rating(25, 1)}
        seedable, unseedable, _ = self.S.build(
            ratings, {"Thin": [1, 2, 3, 4, 5, 6]}, [("Gold", 1)])
        self.assertEqual(seedable, [])
        self.assertEqual(unseedable[0]["team"], "Thin")
        self.assertEqual(unseedable[0]["confidence"], "insufficient data")

    def test_moves_are_flagged_against_todays_division(self):
        seedable, _, _ = self._build(matches=20)
        by_team = {r["team"]: r for r in seedable}
        # Bravo rates higher, so it takes Gold and Alpha drops -- both move.
        self.assertEqual(by_team["Bravo"]["recommended_division"], "Gold")
        self.assertTrue(by_team["Alpha"]["moves"])

    def test_unproven_ratings_trigger_the_do_not_act_banner(self):
        seedable, unseedable, boundaries = self._build(matches=1)
        text = self.S.render(seedable, unseedable, boundaries, "Season 99")
        self.assertIn("Not ready to act on", text)

    def test_established_ratings_drop_the_banner(self):
        seedable, unseedable, boundaries = self._build(matches=40)
        text = self.S.render(seedable, unseedable, boundaries, "Season 99")
        self.assertNotIn("Not ready to act on", text)


if __name__ == "__main__":
    unittest.main()
