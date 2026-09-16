"""Performance weighting: splitting one result between team-mates.

Win/loss is a single bit shared by all twelve players in a match, so it can
never separate two people who played the SAME match. KTPR v2 is the only
available signal that differs between them, and it is outcome-independent,
so combining it with an outcome-driven rating is not double-counting the
result.

Measured on the real nine-match S10 corpus: off, 100 players hold 6 distinct
ratings; on, they hold 77.

The share maths is pure and always runs. The redistribution test needs the
model and importorskips.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "mmr"))


def report(players, status="available"):
    return {"ratings": {"ktpr_v2": {"status": status, "players": players}}}


class ComponentsFromReport(unittest.TestCase):
    def setUp(self):
        import performance as PF
        self.PF = PF

    def test_reads_components_keyed_by_alias(self):
        payload = report([{"name": "Ecl1ps3", "components": {"swing": 1.5, "output": -0.5}}])
        self.assertEqual(self.PF.components_by_alias(payload),
                         {"ecl1ps3": {"swing": 1.5, "output": -0.5}})

    def test_an_unavailable_ktpr_block_yields_nothing(self):
        """Nothing, NOT zeros. Zeros would read as 'everybody played badly'
        and would quietly redistribute a result on fabricated evidence."""
        payload = report([{"name": "A", "components": {"swing": 1.0}}], status="unavailable")
        self.assertEqual(self.PF.components_by_alias(payload), {})

    def test_a_missing_ratings_block_is_survivable(self):
        self.assertEqual(self.PF.components_by_alias({}), {})
        self.assertEqual(self.PF.components_by_alias(None), {})

    def test_non_numeric_components_are_dropped(self):
        payload = report([{"name": "A", "components": {"swing": None, "output": 2.0}}])
        self.assertEqual(self.PF.components_by_alias(payload), {"a": {"output": 2.0}})


class Shares(unittest.TestCase):
    def setUp(self):
        import performance as PF
        self.PF = PF

    def test_shares_average_to_one(self):
        """Load-bearing. Shares redistribute the SAME total movement; they
        must not scale how much a result is worth, or performance becomes a
        second, unearned confidence knob on top of the outcome."""
        shares = self.PF.team_shares([1, 2, 3], {1: 2.0, 2: -1.0, 3: 0.0})
        self.assertAlmostEqual(sum(shares.values()) / len(shares), 1.0, places=9)

    def test_a_better_match_earns_a_bigger_share(self):
        shares = self.PF.team_shares([1, 2], {1: 1.5, 2: -1.5})
        self.assertGreater(shares[1], shares[2])

    def test_zero_strength_reproduces_an_even_split(self):
        shares = self.PF.team_shares([1, 2, 3], {1: 3.0, 2: -3.0}, strength=0.0)
        self.assertEqual(set(round(v, 9) for v in shares.values()), {1.0})

    def test_a_player_the_report_omits_gets_an_average_share(self):
        """Absent data must not be read as a bad performance."""
        shares = self.PF.team_shares([1, 2], {1: 0.0})
        self.assertAlmostEqual(shares[2], 1.0, places=9)

    def test_one_extreme_match_cannot_swallow_the_whole_update(self):
        shares = self.PF.team_shares([1, 2, 3, 4, 5, 6], {1: 99.0})
        self.assertLessEqual(max(shares.values()), self.PF.MAX_SHARE * 1.5)
        self.assertGreater(min(shares.values()), 0.0)

    def test_sides_are_normalised_independently(self):
        """Cross-team normalisation would let the losers' performance change
        how far the winners move, entangling 'played well' with 'won' -- the
        exact thing KTPR's outcome-independence exists to avoid."""
        home, away = self.PF.match_shares([1, 2], [11, 12], {1: 2.0, 2: -2.0, 11: 0.0, 12: 0.0})
        self.assertAlmostEqual(sum(home.values()) / 2, 1.0, places=9)
        self.assertAlmostEqual(sum(away.values()) / 2, 1.0, places=9)
        self.assertEqual(round(away[11], 9), round(away[12], 9))

    def test_no_players_is_not_a_crash(self):
        self.assertEqual(self.PF.team_shares([], {1: 1.0}), {})


class Redistribution(unittest.TestCase):
    def setUp(self):
        import importlib
        if importlib.util.find_spec("openskill") is None:
            self.skipTest("openskill not installed")
        from ladder import OpenSkill
        self.OpenSkill = OpenSkill
        self.t1, self.t2 = [1, 2, 3, 4, 5, 6], [11, 12, 13, 14, 15, 16]

    def _mu_after(self, shares):
        m = self.OpenSkill()
        m.update(self.t1, self.t2, 1.0, shares=shares)
        return m

    def test_without_shares_team_mates_stay_identical(self):
        m = self._mu_after(None)
        self.assertEqual(m.r[1].mu, m.r[2].mu)

    def test_the_sides_total_movement_is_preserved(self):
        even = self._mu_after(None)
        tilted = self._mu_after({1: 1.6, 2: 0.4, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0})
        start = 25.0
        self.assertAlmostEqual(sum(tilted.r[p].mu - start for p in self.t1),
                               sum(even.r[p].mu - start for p in self.t1), places=9)

    def test_the_split_is_proportional_not_a_step(self):
        """Regression guard against openskill's own `weights` argument.

        Measured on openskill 6.2.0: its weights are a binary step at w > 1.0
        -- 0.0, 0.25, 0.5 and 1.0 all give byte-identical output, and 1.1
        through 2.5 give one identical larger result. Redistribution is done
        in ladder._apply for that reason. If someone 'simplifies' it back to
        the library argument, this test fails, because a step function cannot
        produce three distinct gaps.
        """
        base = self._mu_after(None).r[1].mu
        gains = [self._mu_after({1: w, 2: 2.0 - w}).r[1].mu - base for w in (1.2, 1.5, 1.8)]
        self.assertEqual(len(set(round(g, 9) for g in gains)), 3, gains)
        self.assertEqual(gains, sorted(gains))

    def test_a_bigger_share_gains_more_from_a_win(self):
        m = self._mu_after({1: 1.5, 2: 0.5, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0})
        self.assertGreater(m.r[1].mu, m.r[2].mu)

    def test_a_bigger_share_loses_more_from_a_defeat(self):
        """Symmetry: carrying a loss should not be punished like causing one."""
        m = self.OpenSkill()
        m.update(self.t1, self.t2, 0.0, shares={1: 1.5, 2: 0.5})
        self.assertLess(m.r[1].mu, m.r[2].mu)

    def test_uncertainty_still_tracks_playing_not_playing_well(self):
        """Sigma is evidence, not quality -- a strong match and a weak one are
        equally informative about who you are."""
        m = self._mu_after({1: 1.9, 2: 0.1, 3: 1.0, 4: 1.0, 5: 1.0, 6: 1.0})
        self.assertAlmostEqual(m.r[1].sigma, m.r[2].sigma, places=9)


if __name__ == "__main__":
    unittest.main()
