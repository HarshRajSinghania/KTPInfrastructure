"""kill_streak_v1 against a synthetic match, cross-checked three ways.

Route A is the published definition over hlstatsx frags and the life ledger.
Route B replays an observer-style kill stream windowed from golive to half end.
Route C is the observer plugin's own best_streak counter. On real matches A and
B differ only where A marks `lower_bound` (an unordered frag), and C carries
its own miscounts; the fixture reproduces both so the routes stay honest.
"""
import unittest
from collections import defaultdict

from scripts.kill_streaks import _runs, build_kill_streaks
from scripts.life_ledger import place_frags, resolve_sides

E0 = 1_800_000_000
MATCH = "fixture-TEST"
PLAYERS = [
    {"player_id": 1, "player_name_at_match": "p1", "team": 1},
    {"player_id": 2, "player_name_at_match": "p2", "team": 1},
    {"player_id": 3, "player_name_at_match": "p3", "team": 2},
    {"player_id": 4, "player_name_at_match": "p4", "team": 2},
]


def life(half, pid, kind, reason, team, cls, t):
    return {"half": half, "player_id": pid, "boundary_kind": kind, "reason": reason,
            "team": team, "player_class": cls, "game_time": float(t),
            "event_epoch": E0 + half * 10_000 + int(t)}


def frag(half, killer, victim, t, weapon="kar", headshot=0, clocked=True):
    # An unclocked frag has no producer context at all: no event_epoch, only the
    # daemon's receipt time, a second behind the game server.
    epoch = E0 + half * 10_000 + int(t)
    return {"half": half if clocked else None, "stored_half": half,
            "producer_match_id": MATCH if clocked else None, "stored_match_id": MATCH,
            "killer_id": killer, "victim_id": victim, "weapon": weapon, "headshot": headshot,
            "game_time": float(t) if clocked else None,
            "event_epoch": epoch if clocked else None,
            "receipt_epoch": epoch + 1}


# Half 1: p1/p2 play Axis (ledger team 2), p3/p4 Allies. Half 2 swaps.
LIVES = [
    life(1, 1, "start", "context_live", 2, 10, 0), life(1, 1, "start", "spawn", 2, 10, 1),
    life(1, 1, "end", "death", 2, 10, 50), life(1, 1, "start", "spawn", 2, 13, 60),
    life(1, 1, "end", "death", 2, 13, 80), life(1, 1, "start", "spawn", 2, 13, 90),
    life(1, 2, "start", "spawn", 2, 12, 1), life(1, 2, "end", "death", 2, 12, 20),
    life(1, 2, "start", "spawn", 2, 12, 30), life(1, 2, "end", "death", 2, 12, 120),
    life(1, 3, "start", "spawn", 1, 1, 1), life(1, 3, "end", "death", 1, 1, 5),
    life(1, 3, "start", "spawn", 1, 1, 10), life(1, 3, "end", "death", 1, 1, 15),
    life(1, 3, "start", "spawn", 1, 1, 20), life(1, 3, "end", "death", 1, 1, 52),
    life(1, 3, "start", "spawn", 1, 1, 55), life(1, 3, "end", "death", 1, 1, 100),
    life(1, 3, "start", "spawn", 1, 1, 105), life(1, 3, "end", "death", 1, 1, 130),
    life(1, 3, "start", "spawn", 1, 1, 135),
    life(1, 4, "start", "spawn", 1, 6, 1), life(1, 4, "end", "death", 1, 6, 10),
    life(1, 4, "start", "spawn", 1, 6, 12), life(1, 4, "end", "death", 1, 6, 30),
    life(1, 4, "start", "spawn", 1, 6, 35), life(1, 4, "end", "death", 1, 6, 70),
    life(1, 4, "start", "spawn", 1, 6, 75), life(1, 4, "end", "death", 1, 6, 80),
    life(1, 4, "start", "spawn", 1, 6, 85), life(1, 4, "end", "disconnect", 1, 6, 111),
    life(2, 1, "start", "spawn", 1, 1, 1), life(2, 1, "end", "death", 1, 1, 50),
    life(2, 2, "start", "spawn", 1, 2, 1), life(2, 2, "end", "death", 1, 2, 30),
    life(2, 2, "start", "spawn", 1, 2, 35), life(2, 2, "end", "disconnect", 1, 2, 40),
    life(2, 3, "start", "spawn", 2, 10, 1), life(2, 3, "end", "death", 2, 10, 10),
    life(2, 3, "start", "spawn", 2, 10, 15), life(2, 3, "end", "death", 2, 10, 20),
    life(2, 3, "start", "spawn", 2, 10, 25),
    life(2, 4, "start", "spawn", 2, 14, 1), life(2, 4, "start", "spawn", 2, 14, 40),
]

FRAGS = [
    frag(1, 1, 3, 5, headshot=1), frag(1, 1, 4, 10), frag(1, 1, 3, 15),
    frag(1, 1, 4, 30, weapon="garand"),               # picked-up enemy weapon
    frag(1, 3, 1, 50, weapon="garand"),
    frag(1, 1, 3, 52, weapon="grenade"),               # lands after p1 died at 50
    frag(1, 1, 4, 70, weapon="mp44"),
    frag(1, 1, 4, 80, weapon="mp44"), frag(1, 4, 1, 80, weapon="bar"),  # same tick
    frag(1, 2, 3, 100, clocked=False),                 # recovered from p3's death
    frag(1, 2, 4, 110, clocked=False),                 # only a disconnect nearby
    frag(1, 3, 2, 120, weapon="garand"),
    frag(1, 1, 3, 130),                                # open run at half end
    frag(2, 1, 3, 10), frag(2, 1, 3, 20),
    frag(2, 4, 2, 30, weapon="spring"), frag(2, 4, 1, 50, weapon="spring"),
]


def observer_stream():
    """The same match as the observer sees it: SteamIDs are placeholders, a
    warmup kill precedes golive, teamkills and disconnects are events."""
    ev = []

    def add(half, tick, event, **kw):
        ev.append({"half": half, "tick": tick, "event": event, **kw})

    add(1, -5, "kill", killer_id="u3", victim_id="u1", kill_type="normal")
    add(1, 0, "match_phase", phase="golive")
    for f in FRAGS:
        t = f["game_time"] if f["game_time"] is not None else f["receipt_epoch"] - 1 - E0 - f["stored_half"] * 10_000
        add(f["stored_half"], t, "kill", killer_id=f"u{f['killer_id']}",
            victim_id=f"u{f['victim_id']}", kill_type="normal")
    add(1, 20, "kill", killer_id="u1", victim_id="u2", kill_type="teamkill")
    add(1, 111, "player_disconnect", user_id="u4")
    add(1, 200, "half_end")
    add(2, 0, "match_phase", phase="golive")
    add(2, 40, "player_disconnect", user_id="u2")
    add(2, 200, "ktp_match_end")
    order = {"match_phase": 0, "kill": 1, "player_disconnect": 2, "half_end": 3, "ktp_match_end": 3}
    return sorted(ev, key=lambda e: (e["half"], e["tick"], order[e["event"]]))


def route_b(events):
    run, best, live = defaultdict(int), defaultdict(int), {}
    for e in events:
        half = e["half"]
        if e["event"] == "match_phase" and e.get("phase") == "golive":
            live[half] = True
        elif e["event"] in ("half_end", "ktp_match_end"):
            live[half] = False
        elif not live.get(half):
            continue
        elif e["event"] == "kill":
            killer, victim = (half, e["killer_id"]), (half, e["victim_id"])
            if e["kill_type"] == "normal":
                run[killer] += 1
                best[killer] = max(best[killer], run[killer])
            run[victim] = 0
        elif e["event"] == "player_disconnect":
            run[(half, e["user_id"])] = 0
    return {(h, int(u[1:])): b for (h, u), b in best.items()}


def route_c(b_values):
    """The plugin's best_streak snapshots: route B's values plus the plugin's
    own undercount on one player-half."""
    c = dict(b_values)
    c[(1, 3)] = c.get((1, 3), 0) - 1
    return c


class KillStreakRuns(unittest.TestCase):
    def test_tie_puts_the_kill_first(self):
        self.assertEqual(_runs([10.0, 20.0, 30.0], [30.0]), (3, 1))

    def test_no_reset_without_an_end(self):
        self.assertEqual(_runs([1.0, 2.0, 3.0, 4.0], []), (4, 1))


class KillStreakDefinition(unittest.TestCase):
    def setUp(self):
        self.out = build_kill_streaks(FRAGS, LIVES, PLAYERS, match_id=MATCH, source_available=True)
        self.rows = {(r["half"], r["player_id"]): r for r in self.out["rows"]}

    def test_status_and_coverage(self):
        self.assertEqual(self.out["status"], "available")
        self.assertEqual(self.out["coverage"], {"ordered_frags": 15, "recovered_frags": 1,
                                                "unordered_frags": 1, "kills_after_own_death": 1})
        self.assertEqual(self.out["flags"], ["unordered-frags"])

    def test_resets_on_death_and_counts_posthumous_kill_fresh(self):
        p1 = self.rows[(1, 1)]
        self.assertEqual((p1["kills"], p1["best_streak"], p1["streaks_3_plus"]), (8, 4, 2))
        self.assertEqual(p1["side"], "Axis")
        self.assertFalse(p1["lower_bound"])

    def test_unordered_frag_marks_lower_bound(self):
        p2 = self.rows[(1, 2)]
        self.assertEqual((p2["kills"], p2["best_streak"], p2["lower_bound"]), (2, 1, True))

    def test_half_boundary_resets_and_spawn_after_spawn_does_not(self):
        self.assertEqual(self.rows[(2, 1)]["best_streak"], 2)
        self.assertEqual(self.rows[(2, 4)]["best_streak"], 2)
        self.assertEqual(self.rows[(2, 1)]["side"], "Allies")

    def test_players_without_kills_still_have_a_row(self):
        self.assertEqual((self.rows[(2, 3)]["kills"], self.rows[(2, 3)]["best_streak"]), (0, 0))

    def test_match_and_side_values(self):
        players = {p["player_id"]: p for p in self.out["players"]}
        self.assertEqual(players[1]["best_streak"], 4)
        self.assertEqual(players[1]["by_side"], {"Allies": 2, "Axis": 4})
        self.assertTrue(players[2]["lower_bound"])

    def test_kills_reconcile_with_the_frag_rows(self):
        per_player = defaultdict(int)
        for f in FRAGS:
            per_player[f["killer_id"]] += 1
        streak_kills = defaultdict(int)
        for r in self.out["rows"]:
            streak_kills[r["player_id"]] += r["kills"]
        self.assertEqual(dict(streak_kills), dict(per_player))

    def test_recovery_ignores_a_non_death_boundary_in_window(self):
        placed = {(f["half"], f["killer_id"], f["victim_id"]): f["placement"]
                  for f in place_frags(FRAGS, LIVES, match_id=MATCH)}
        self.assertEqual(placed[(1, 2, 3)], "recovered")
        self.assertEqual(placed[(1, 2, 4)], "unordered")

    def test_an_unclocked_frag_with_no_epoch_at_all_stays_unordered(self):
        bare = [dict(f, receipt_epoch=None) if f["game_time"] is None else f for f in FRAGS]
        placements = [f["placement"] for f in place_frags(bare, LIVES, match_id=MATCH)]
        self.assertEqual(placements.count("recovered"), 0)
        self.assertEqual(placements.count("unordered"), 2)

    def test_sides_resolve_per_half_and_refuse_two_sides(self):
        sides = resolve_sides(LIVES + [life(2, 4, "start", "spawn", 1, 1, 60)])
        self.assertEqual(sides[(1, 3)], "Allies")
        self.assertIsNone(sides[(2, 4)])


class ThreeRouteValidation(unittest.TestCase):
    def test_routes_disagree_only_where_expected(self):
        a = {(r["half"], r["player_id"]): r["best_streak"]
             for r in build_kill_streaks(FRAGS, LIVES, PLAYERS, match_id=MATCH,
                                         source_available=True)["rows"]}
        b = route_b(observer_stream())
        c = route_c(b)
        keys = set(a)
        diff = lambda x, y: {k for k in keys if x.get(k, 0) != y.get(k, 0)}
        self.assertEqual(diff(a, b), {(1, 2)})           # A understates: lower_bound row
        self.assertEqual(diff(b, c), {(1, 3)})           # the plugin's own miscount
        self.assertEqual(diff(a, c), {(1, 2), (1, 3)})
        self.assertEqual(sorted(b.values(), reverse=True)[:1], [4])  # warmup kill excluded


class KillStreakUnavailable(unittest.TestCase):
    def test_no_source_no_ledger_no_clock(self):
        self.assertEqual(build_kill_streaks(None, None, PLAYERS, match_id=MATCH,
                                            source_available=False)["flags"],
                         ["source-not-captured"])
        self.assertEqual(build_kill_streaks(FRAGS, [], PLAYERS, match_id=MATCH,
                                            source_available=True)["flags"],
                         ["no-life-boundaries"])
        unclocked = [dict(f, game_time=None) for f in FRAGS]
        out = build_kill_streaks(unclocked, LIVES, PLAYERS, match_id=MATCH, source_available=True)
        self.assertEqual((out["status"], out["flags"], out["rows"]),
                         ("unavailable", ["no-frag-clock"], []))

    def test_kills_in_a_half_without_boundaries_make_the_match_value_unknown(self):
        lives = [b for b in LIVES if (b["half"], b["player_id"]) != (2, 1)]
        out = build_kill_streaks(FRAGS, lives, PLAYERS, match_id=MATCH, source_available=True)
        row = next(r for r in out["rows"] if (r["half"], r["player_id"]) == (2, 1))
        p1 = next(p for p in out["players"] if p["player_id"] == 1)
        self.assertEqual((row["kills"], row["best_streak"]), (2, None))
        self.assertIn("kills-without-life-boundaries", out["flags"])
        self.assertIsNone(p1["best_streak"])
        self.assertEqual(p1["by_side"], {"Allies": None, "Axis": 4})

    def test_frags_booked_to_another_match_are_not_counted(self):
        other = dict(frag(1, 1, 3, 140), stored_match_id="other-TEST")
        out = build_kill_streaks(FRAGS + [other], LIVES, PLAYERS, match_id=MATCH,
                                 source_available=True)
        self.assertEqual(next(r for r in out["rows"] if (r["half"], r["player_id"]) == (1, 1))["kills"], 8)


if __name__ == "__main__":
    unittest.main()
