"""The mmr_openskill season-aggregate payload.

This is a contract with a consumer in another repository (keep-the-prac
#760's profile card), so the tests pin the shape that consumer actually
reads, not merely that the function returns something.

Pure logic: no network, no openskill, no database.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "mmr"))

RATINGS = {"7": {"mu": 26.4, "sigma": 8.22, "ordinal": 1.74},
           "8": {"mu": 30.0, "sigma": 2.0, "ordinal": 24.0}}
PLAYED = {7: 2, 8: 9}
ALIASES = {7: "Ecl1ps3", 8: "seanality"}
WHEN = "2026-09-16T12:00:00+00:00"


def build(**kw):
    import mmr_payload as M
    return M.build(RATINGS, PLAYED, ALIASES, generated_at=WHEN, **kw)


class Contract(unittest.TestCase):
    """Field names and meanings the website reads. Changing any of these
    silently blanks the profile card rather than erroring."""

    def test_carries_the_kind_the_consumer_queries(self):
        self.assertEqual(build()["kind"], "mmr_openskill")

    def test_each_row_has_exactly_the_fields_the_card_reads(self):
        row = build()["players"][0]
        self.assertEqual(set(row), {"name", "matches", "rating", "uncertainty", "conservative"})

    def test_conservative_is_mu_minus_three_sigma(self):
        """Verified against the consumer's own fixture: rating 26.4,
        uncertainty 8.22, conservative 1.74."""
        row = next(r for r in build()["players"] if r["name"] == "Ecl1ps3")
        self.assertEqual(row["rating"], 26.4)
        self.assertEqual(row["uncertainty"], 8.22)
        self.assertEqual(row["conservative"], 1.74)

    def test_min_matches_travels_with_the_payload(self):
        """So the display threshold can move without a website deploy."""
        self.assertEqual(build(min_matches=5)["min_matches"], 5)

    def test_is_labelled_provisional(self):
        payload = build()
        self.assertTrue(payload["provisional"])
        self.assertIn("retroactively", payload["notice"])

    def test_carries_the_columns_the_aggregate_insert_needs(self):
        """So the existing insert path writes this row with no special case."""
        payload = build(source_report_count=9)
        self.assertEqual(payload["source_report_count"], 9)
        self.assertIsInstance(payload["report_schema_version"], int)


class Privacy(unittest.TestCase):
    def test_never_publishes_an_identifier(self):
        body = str(build())
        for banned in ("player_id", "steam_id", "steam_id64"):
            self.assertNotIn(banned, body)

    def test_a_player_without_an_alias_is_dropped_not_published_under_an_id(self):
        import mmr_payload as M
        payload = M.build(RATINGS, PLAYED, {7: "Ecl1ps3", 8: None}, generated_at=WHEN)
        self.assertEqual([r["name"] for r in payload["players"]], ["Ecl1ps3"])

    def test_a_blank_alias_counts_as_absent(self):
        import mmr_payload as M
        payload = M.build(RATINGS, PLAYED, {7: "  ", 8: "seanality"}, generated_at=WHEN)
        self.assertEqual([r["name"] for r in payload["players"]], ["seanality"])


class Stability(unittest.TestCase):
    def test_rows_are_ordered_best_first(self):
        names = [r["name"] for r in build()["players"]]
        self.assertEqual(names, ["seanality", "Ecl1ps3"])

    def test_the_same_input_produces_the_same_payload(self):
        """An unordered payload would hash differently every run and publish
        a new revision weekly for no change."""
        self.assertEqual(build(), build())

    def test_matches_played_comes_from_participation_not_the_rating(self):
        row = next(r for r in build()["players"] if r["name"] == "seanality")
        self.assertEqual(row["matches"], 9)

    def test_a_player_with_no_participation_record_reports_zero(self):
        import mmr_payload as M
        payload = M.build(RATINGS, {}, ALIASES, generated_at=WHEN)
        self.assertTrue(all(r["matches"] == 0 for r in payload["players"]))

    def test_a_rating_missing_its_numbers_is_skipped(self):
        import mmr_payload as M
        payload = M.build({"7": {"mu": None, "sigma": 1.0}}, PLAYED, ALIASES, generated_at=WHEN)
        self.assertEqual(payload["players"], [])


class ImportGuards(unittest.TestCase):
    """What report_service.py import-mmr refuses before writing to production.

    Tested here because report_service imports the Unix-only `pwd` module and
    cannot be exercised on a dev machine at all -- the guards on a production
    write are exactly the code that must not ship untested.
    """

    def setUp(self):
        import mmr_payload as M
        self.M = M
        self.good = build()

    def test_a_well_formed_payload_passes(self):
        self.assertEqual(self.M.validate_for_import(self.good), [])

    def test_the_wrong_kind_is_refused(self):
        bad = {**self.good, "kind": "leaderboard_ktpr_v22"}
        self.assertIn("expected 'mmr_openskill'", " ".join(self.M.validate_for_import(bad)))

    def test_an_empty_payload_is_refused(self):
        """Publishing zero players would blank every profile card at once."""
        self.assertIn("no players", " ".join(self.M.validate_for_import({**self.good, "players": []})))

    def test_rows_missing_a_field_the_card_reads_are_refused(self):
        bad = {**self.good, "players": [{"name": "x", "matches": 1, "rating": 1.0}]}
        problems = " ".join(self.M.validate_for_import(bad))
        self.assertIn("missing", problems)
        self.assertIn("conservative", problems)

    def test_a_payload_carrying_identifiers_is_refused(self):
        bad = {**self.good,
               "players": [{**self.good["players"][0], "steam_id64": "76561197960287930"}]}
        self.assertIn("identifiers", " ".join(self.M.validate_for_import(bad)))

    def test_a_non_object_is_refused_rather_than_crashing(self):
        self.assertTrue(self.M.validate_for_import([1, 2, 3]))
        self.assertTrue(self.M.validate_for_import(None))


if __name__ == "__main__":
    unittest.main()
