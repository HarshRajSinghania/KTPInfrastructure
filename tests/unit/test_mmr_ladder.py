"""Unit tests for the MMR rating ladder's pure logic.

Deliberately split by dependency: the damping maths and the seeding
aggregation are plain functions and always run, including on the Tier 1
config gate which installs pytest and nothing else. Anything that needs the
`openskill` package importorskips, so a thin runner still reports the rest
rather than erroring at collection.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "mmr"))


class DampedProbability(unittest.TestCase):
    """Confidence damping — see ladder.damped_probability.

    Live failure this exists for: through the first nine S10 league matches
    the ladder issued 92/93/98% calls off one or two matches of evidence and
    lost several, scoring 0.985 log-loss against a 0.693 coin flip, with a
    0.40 calibration error. Damping took calibration error to 0.056.
    """

    def setUp(self):
        from ladder import damped_probability
        self.f = damped_probability

    def test_no_evidence_is_a_coin_flip(self):
        # The whole point: an unbacked 98% claim becomes an honest 50%.
        self.assertEqual(self.f(0.98, 0), 0.5)
        self.assertEqual(self.f(0.02, 0), 0.5)

    def test_damping_never_flips_the_favourite(self):
        # Seeding and the digest both read the ordering; damping must only
        # move confidence, never which side is ahead.
        for p in (0.51, 0.6, 0.75, 0.99):
            for ev in (0, 1, 3, 10, 50):
                self.assertGreaterEqual(self.f(p, ev), 0.5, (p, ev))
        for p in (0.49, 0.4, 0.25, 0.01):
            for ev in (0, 1, 3, 10, 50):
                self.assertLessEqual(self.f(p, ev), 0.5, (p, ev))

    def test_more_evidence_means_more_confidence(self):
        seq = [self.f(0.9, ev) for ev in (0, 1, 2, 5, 10, 40)]
        self.assertEqual(seq, sorted(seq), "confidence must rise with evidence")
        self.assertLess(seq[-1], 0.9, "must never exceed the undamped value")

    def test_converges_to_the_raw_value(self):
        self.assertAlmostEqual(self.f(0.9, 100000), 0.9, places=3)

    def test_a_true_coin_flip_stays_put(self):
        for ev in (0, 5, 1000):
            self.assertEqual(self.f(0.5, ev), 0.5)

    def test_k_controls_how_fast_confidence_is_earned(self):
        lenient, strict = self.f(0.9, 6, k=2.0), self.f(0.9, 6, k=20.0)
        self.assertGreater(lenient, strict)

    def test_negative_evidence_is_treated_as_none(self):
        self.assertEqual(self.f(0.9, -5), 0.5)


class EvidenceIsTheThinnerSide(unittest.TestCase):
    """A confident call needs BOTH sides known, so evidence is the minimum.

    With 23 teams and 9 matches played, nearly every fixture had a debut
    team; taking the mean instead would let one well-known side lend
    borrowed confidence to a matchup nobody can actually call yet.
    """

    def setUp(self):
        import importlib
        self.openskill = importlib.util.find_spec("openskill")
        if self.openskill is None:
            self.skipTest("openskill not installed")
        from ladder import OpenSkill
        self.OpenSkill = OpenSkill

    def test_unknown_opponent_zeroes_the_evidence(self):
        m = self.OpenSkill()
        for p in (1, 2, 3):
            m.games[p] = 25          # one very well-known side
        self.assertEqual(m.evidence([1, 2, 3], [7, 8, 9]), 0.0)

    def test_evidence_is_per_player_not_per_team(self):
        m = self.OpenSkill()
        for p in (1, 2, 3, 4, 5, 6):
            m.games[p] = 4
        self.assertEqual(m.evidence([1, 2, 3], [4, 5, 6]), 4.0)

    def test_damping_off_returns_the_raw_model(self):
        m = self.OpenSkill(damping=None)
        self.assertEqual(m.predict([1, 2], [3, 4]),
                         m.predict_raw([1, 2], [3, 4]))

    def test_damped_prediction_sits_between_raw_and_a_coin_flip(self):
        m = self.OpenSkill()
        m.update([1, 2], [3, 4], 1.0)      # gives everyone one match
        raw = m.predict_raw([1, 2], [3, 4])
        damped = m.predict([1, 2], [3, 4])
        self.assertLessEqual(abs(damped - 0.5), abs(raw - 0.5))


if __name__ == "__main__":
    unittest.main()
