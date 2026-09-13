"""in_game_result on the stream shape real S10 matches produce: the carry into
half 2, the 0/0 scoreboard reset just after it opens, a repeated final, and
ktp_match_end stated in half-1 side terms."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scripts.analytics_report_dto import assert_sanitized
from scripts.in_game_result import load_in_game_result, unavailable

MATCH = "1789000000-TST1"
MAP = "dod_thunder2"
NOW = datetime.fromisoformat("2026-09-13T21:00:00+00:00").timestamp()
CLOSED = [(1, 0), (2, 0)]


def score(half, seq, tick, kind, allies, axis, allies_slot, axis_slot):
    return {"tick": tick, "match_id": MATCH, "map": MAP, "match_type": 0,
            "half": half, "event": "team_score", "allies_score": allies,
            "axis_score": axis, "allies_team_slot": allies_slot,
            "axis_team_slot": axis_slot, "event_sequence": seq,
            "source": "engine-team-score-v1", "sample_kind": kind}


def stream(*, h1_open=(0, 0), h2_open=(12, 142), h2_close=(25, 273),
           match_end=(273, 25)):
    """Half 1: slot 1 plays Allies and closes 142-12. Half 2: sides swap."""
    rows = [
        score(1, 1, 600.0, "baseline", *h1_open, 1, 2),
        score(1, 2, 620.0, "change", 40, 5, 1, 2),
        score(1, 3, 1800.0, "final", 142, 12, 1, 2),
        score(1, 4, 1800.0, "final", 142, 12, 1, 2),
        score(2, 1, 130.0, "baseline", *h2_open, 2, 1),
        score(2, 2, 140.0, "change", 0, 0, 2, 1),
        score(2, 3, 141.0, "change", 12, 142, 2, 1),
        score(2, 4, 900.0, "change", 20, 200, 2, 1),
        score(2, 5, 1350.0, "final", *h2_close, 2, 1),
        {"tick": 1350.0, "match_id": MATCH, "map": MAP, "match_type": 0, "half": 2,
         "event": "player_stats_summary", "reason": "match_end", "players": []},
        {"tick": 1350.0, "match_id": MATCH, "map": MAP, "match_type": 0, "half": 2,
         "event": "ktp_match_end"}
        | ({"allies_score": match_end[0], "axis_score": match_end[1]} if match_end else {}),
    ]
    return rows


class ObserverDir:
    def __init__(self, events, *, match_id=MATCH, event_count=None):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        d = self.root / match_id
        d.mkdir()
        (d / "events.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        (d / "metadata.json").write_text(json.dumps({
            "matchId": match_id, "map": MAP, "matchType": 0, "half": 1,
            "startedAt": "2026-09-13T19:07:00.000Z",
            "endedAt": "2026-09-13T19:50:00.000Z",
            "eventCount": len(events) if event_count is None else event_count,
            "sourceServer": "KTP - Test 1"}), encoding="utf-8")

    def load(self, match_id=MATCH, map_name=MAP, closed=CLOSED):
        return load_in_game_result(self.root, match_id, map_name=map_name,
                                   closed_halves=closed, now=NOW)

    def close(self):
        self._tmp.cleanup()


def result(events=None, **kwargs):
    d = ObserverDir(stream() if events is None else events)
    try:
        return d.load(**kwargs)
    finally:
        d.close()


class RealShape(unittest.TestCase):
    def test_totals_and_teams_follow_the_report_numbering(self):
        r = result()
        self.assertEqual(r["status"], "complete")
        self.assertEqual(r["flags"], [])
        # Report team 1 played Allies in the terminal half: stream slot 2.
        self.assertEqual((r["team1_score"], r["team2_score"]), (25, 273))
        self.assertEqual(r["winner"], 2)
        self.assertEqual(r["authority"], "in_game_team_score")
        self.assertIn("not the league result", r["notice"])

    def test_halves_split_the_cumulative_score(self):
        h1, h2 = result()["halves"]
        self.assertEqual(h1, {"half": 1, "team1_points": 12, "team2_points": 142,
                              "team1_cumulative": 12, "team2_cumulative": 142,
                              "team1_side": "Axis", "team2_side": "Allies"})
        self.assertEqual(h2, {"half": 2, "team1_points": 13, "team2_points": 131,
                              "team1_cumulative": 25, "team2_cumulative": 273,
                              "team1_side": "Allies", "team2_side": "Axis"})
        self.assertEqual(h1["team1_points"] + h2["team1_points"], 25)

    def test_the_half_two_scoreboard_reset_is_not_a_regression(self):
        """Control: the same dip in a CLOSE is a regression."""
        self.assertEqual(result()["status"], "complete")
        r = result(stream(h2_close=(25, 100), match_end=(100, 25)))
        self.assertEqual((r["status"], r["flags"]), ("unavailable", ["score-regression"]))
        self.assertIsNone(r["winner"])

    def test_match_end_is_read_in_half_one_side_terms(self):
        """Read through the terminal half's sides, the correct pair looks swapped."""
        r = result(stream(match_end=(25, 273)))
        self.assertEqual((r["status"], r["flags"]), ("unavailable", ["match-end-disagreement"]))
        self.assertIsNone(r["team1_score"])

    def test_a_warmup_baseline_before_the_scoreboard_clear_is_not_a_late_start(self):
        """Real S10 streams open half 1 on warmup points and clear to 0/0 seconds later."""
        rows = stream(h1_open=(19, 1))
        rows.insert(1, score(1, 5, 610.0, "change", 0, 0, 1, 2))
        r = result(rows)
        self.assertEqual((r["status"], r["team1_score"], r["team2_score"]), ("complete", 25, 273))
        self.assertEqual(r["halves"][0]["team2_points"], 142)

    def test_missing_match_end_scores_publish_partial(self):
        r = result(stream(match_end=None))
        self.assertEqual((r["status"], r["flags"]), ("partial", ["match-end-missing"]))
        self.assertEqual(r["winner"], 2)

    def test_draw(self):
        r = result(stream(h2_close=(142, 142), match_end=(142, 142)))
        self.assertEqual((r["team1_score"], r["team2_score"], r["winner"]), (142, 142, "draw"))


class FailClosed(unittest.TestCase):
    def assertUnavailable(self, r, flag):
        self.assertEqual((r["status"], r["flags"]), ("unavailable", [flag]))
        self.assertEqual(r["halves"], [])
        self.assertIsNone(r["team1_score"])

    def test_late_stream_start(self):
        self.assertUnavailable(result(stream(h1_open=(5, 3))), "late-stream-start")

    def test_carryover_mismatch(self):
        self.assertUnavailable(result(stream(h2_open=(0, 0))), "half-carryover-mismatch")

    def test_half_set_must_match_ktp_matches(self):
        self.assertUnavailable(result(closed=[(1, 0)]), "half-set-mismatch")

    def test_bogus_match_id_finds_nothing(self):
        self.assertUnavailable(result(match_id="1789999999-NOPE9"), "observer-stream-missing")

    def test_map_must_match(self):
        self.assertUnavailable(result(map_name="dod_anzio"), "observer-context-mismatch")

    def test_match_type_must_match(self):
        self.assertUnavailable(result(closed=[(1, 1), (2, 1)]), "observer-context-mismatch")

    def test_a_malformed_stream_is_refused_by_the_strict_reader(self):
        d = ObserverDir(stream(), event_count=3)
        try:
            self.assertUnavailable(d.load(), "observer-stream-invalid")
        finally:
            d.close()

    def test_no_root(self):
        self.assertUnavailable(
            load_in_game_result(None, MATCH, map_name=MAP, closed_halves=CLOSED),
            "observer-root-not-configured")

    def test_available_and_unavailable_share_keys(self):
        self.assertEqual(set(result()), set(unavailable("x")))
        assert_sanitized(result())


if __name__ == "__main__":
    unittest.main()
