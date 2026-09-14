"""Per-side weapon and duel splits, per-class rows, and the class mapping."""
import unittest
from collections import defaultdict

import pytest

from scripts.side_splits import (
    DEFAULT_CLASS_MAP, build_duels_by_side, build_player_classes, build_weapon_sides)
from tests.unit.test_kill_streaks import FRAGS, LIVES, MATCH, PLAYERS
from scripts.life_ledger import resolve_sides

SIDES = resolve_sides(LIVES)

# killerRole on frags against the killer's class read at spawn, summed over the
# first S10 official matches (aggregate counts only). Empty roles are excluded.
MEASURED_ROLE_COUNTS = [
    (1, "#class_allied_garand", 1003), (2, "#class_allied_carbine", 20),
    (3, "#class_allied_garand", 4), (3, "#class_allied_heavy", 2),
    (3, "#class_allied_thompson", 1), (5, "#class_allied_sniper", 343),
    (6, "#class_allied_heavy", 621), (10, "#class_axis_kar98", 818),
    (11, "#class_axis_k43", 256), (11, "#class_axis_kar98", 6), (11, "#class_axis_mp40", 5),
    (12, "#class_axis_mp40", 185), (13, "#class_axis_mp44", 455),
    (14, "#class_axis_sniper", 431),
]
MIN_KILLS_TO_PIN = 20
DODCONST_CLASS_IDS = set(range(1, 9)) | set(range(10, 20)) | set(range(21, 26))


def class_map():
    tomllib = pytest.importorskip("tomllib")
    with DEFAULT_CLASS_MAP.open("rb") as source:
        return {int(k): v for k, v in tomllib.load(source)["classes"].items()}


def weapon_half_rows():
    rows = defaultdict(lambda: {"kills": 0, "headshot_kills": 0, "shots": 0, "hits": 0,
                                "damage_dealt": 0})
    for f in FRAGS:
        cell = rows[(f["killer_id"], f["stored_half"], f["weapon"])]
        cell["kills"] += 1
        cell["headshot_kills"] += f["headshot"]
        cell["damage_dealt"] += 100
    rows[(1, 2, "kar")]["shots"] += 5  # shots on a weapon with no kills in the half
    return [{"player_id": pid, "player_name_at_match": f"p{pid}", "team": 1 if pid < 3 else 2,
             "half": half, "weapon": weapon, **v} for (pid, half, weapon), v in rows.items()]


def match_weapons(rows):
    totals = defaultdict(lambda: defaultdict(int))
    for r in rows:
        for c in ("kills", "headshot_kills", "shots", "hits", "damage_dealt"):
            totals[(r["player_id"], r["weapon"])][c] += r[c]
    return [{"player_id": pid, "weapon": w, **v} for (pid, w), v in totals.items()]


class WeaponSides(unittest.TestCase):
    def test_split_reconciles_and_keeps_pickups_on_the_players_side(self):
        rows = weapon_half_rows()
        out = build_weapon_sides(rows, match_weapons(rows), SIDES, per_hit_damage=True)
        self.assertEqual((out["status"], out["reconciled"], out["unsided_kills"]),
                         ("available", True, 0))
        garand = [r for r in out["rows"] if r["player_id"] == 1 and r["weapon"] == "garand"]
        self.assertEqual([(r["half"], r["side"], r["kills"]) for r in garand], [(1, "Axis", 1)])

    def test_mismatch_is_named(self):
        rows = weapon_half_rows()
        weapons = match_weapons(rows)
        weapons[0]["kills"] += 1
        out = build_weapon_sides(rows, weapons, SIDES, per_hit_damage=True)
        self.assertEqual((out["reconciled"], out["mismatched_columns"]), (False, ["kills"]))

    def test_legacy_damage_is_null_and_unchecked(self):
        rows = weapon_half_rows()
        weapons = match_weapons(rows)
        for w in weapons:
            w["damage_dealt"] = 0
        out = build_weapon_sides(rows, weapons, SIDES, per_hit_damage=False)
        self.assertTrue(out["reconciled"])
        self.assertTrue(all(r["damage_dealt"] is None for r in out["rows"]))

    def test_unavailable_without_rows_or_sides(self):
        self.assertEqual(build_weapon_sides(None, [], SIDES, per_hit_damage=True)["status"],
                         "unavailable")
        self.assertEqual(build_weapon_sides(weapon_half_rows(), [], {}, per_hit_damage=True)["flags"],
                         ["no-life-boundaries"])


class DuelsBySide(unittest.TestCase):
    def matrix(self):
        counts = defaultdict(int)
        for f in FRAGS:
            counts[(f["killer_id"], f["victim_id"])] += 1
        return {"cells": [{"killer_id": k, "victim_id": v, "kills": n} for (k, v), n in counts.items()]}

    def timeline(self):
        return [{"half": f["stored_half"], "killer_id": f["killer_id"], "victim_id": f["victim_id"]}
                for f in FRAGS]

    def test_split_sums_back_to_the_matrix(self):
        out = build_duels_by_side(self.timeline(), PLAYERS, SIDES, self.matrix())
        self.assertTrue(out["reconciled"])
        self.assertEqual(out["unsided_kills"], 0)
        p1_on_p3 = {c["killer_side"]: c["kills"] for c in out["cells"]
                    if (c["killer_id"], c["victim_id"]) == (1, 3)}
        self.assertEqual(p1_on_p3, {"Axis": 4, "Allies": 2})

    def test_unsided_frags_still_reconcile(self):
        sides = {k: v for k, v in SIDES.items() if k != (2, 1)}
        out = build_duels_by_side(self.timeline(), PLAYERS, sides, self.matrix())
        self.assertTrue(out["reconciled"])
        self.assertEqual(out["unsided_kills"], 2)


class PlayerClasses(unittest.TestCase):
    CLASSES = {1: {"code": "garand", "name": "Rifleman"}, 2: {"code": "carbine"},
               6: {"code": "bar"}, 10: {"code": "kar98"}, 12: {"code": "mp40"},
               13: {"code": "mp44"}}

    def build(self):
        return build_player_classes(FRAGS, LIVES, PLAYERS, SIDES, self.CLASSES,
                                    match_id=MATCH, source_available=True)

    def rows(self):
        return {(r["half"], r["player_id"], r["class_id"]): r for r in self.build()["rows"]}

    def test_baseline_then_spawn_is_one_life(self):
        rows = self.rows()
        self.assertEqual(rows[(1, 1, 10)]["lives"], 1)
        self.assertEqual(rows[(1, 1, 13)]["lives"], 2)
        self.assertEqual(rows[(2, 4, 14)]["lives"], 1)

    def test_posthumous_grenade_counts_to_the_throwing_class(self):
        rows = self.rows()
        self.assertEqual((rows[(1, 1, 10)]["kills"], rows[(1, 1, 10)]["headshot_kills"]), (5, 1))
        self.assertEqual(rows[(1, 1, 13)]["kills"], 3)
        self.assertEqual(rows[(1, 1, 10)]["side"], "Axis")
        self.assertEqual(rows[(1, 1, 10)]["class_name"], None)
        self.assertEqual(rows[(1, 3, 1)]["class_name"], "Rifleman")

    def test_deaths_take_the_victims_class_and_unmapped_ids_are_flagged(self):
        out = self.build()
        rows = {(r["half"], r["player_id"], r["class_id"]): r for r in out["rows"]}
        self.assertEqual(rows[(1, 3, 1)]["deaths"], 5)
        self.assertEqual(out["flags"], ["unmapped-class-ids"])
        self.assertEqual(out["coverage"]["unmapped_class_ids"], [14])
        self.assertEqual(out["coverage"]["kills_unclassed"], 1)
        self.assertEqual(out["coverage"]["kills_classed"] + out["coverage"]["kills_unclassed"],
                         len(FRAGS))

    def test_unavailable_without_ledger(self):
        out = build_player_classes(FRAGS, [], PLAYERS, SIDES, self.CLASSES,
                                   match_id=MATCH, source_available=True)
        self.assertEqual((out["status"], out["flags"]), ("unavailable", ["no-life-boundaries"]))


class ClassMapping(unittest.TestCase):
    def test_every_dodconst_class_is_mapped_once(self):
        mapping = class_map()
        self.assertEqual(set(mapping), DODCONST_CLASS_IDS)
        codes = [v["code"] for v in mapping.values()]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(v["side"] in ("Allies", "Axis") and v["name"] for v in mapping.values()))

    def test_mapping_agrees_with_killer_role_where_measured(self):
        mapping = class_map()
        by_class = defaultdict(dict)
        for class_id, role, n in MEASURED_ROLE_COUNTS:
            by_class[class_id][role] = n
        pinned = 0
        for class_id, roles in by_class.items():
            if sum(roles.values()) < MIN_KILLS_TO_PIN:
                continue
            self.assertEqual(mapping[class_id].get("role"), max(roles, key=roles.get), class_id)
            pinned += 1
        self.assertGreater(pinned, 0)

    def test_role_side_prefix_matches_side(self):
        for class_id, entry in class_map().items():
            role = entry.get("role")
            if role is None:
                continue
            axis = role.startswith("#class_axis")
            self.assertEqual(entry["side"] == "Axis", axis, class_id)


class HalfTokens(unittest.TestCase):
    def test_every_query_resolves_its_half_tokens_both_ways(self):
        pytest.importorskip("tomllib")
        from scripts import match_analytics as ma
        on = {"frag_event_clock": True, "damage_event_clock": True, "break_producer_half": True}
        for path in ma.SQL_DIR.glob("*.sql"):
            for sources in (None, on):
                query = ma.read_query(path.name, "phase-a-TEST", sources)
                self.assertNotIn("{{", query, path.name)
        produced = ma.read_query("weapon_half_fact.sql", "x-TEST", on)
        self.assertIn("f.producer_half", produced)
        self.assertNotIn("producer_half", ma.read_query("weapon_half_fact.sql", "x-TEST"))
        self.assertIn("e.producer_half", ma.read_query("player_half_fact.sql", "x-TEST", on))


if __name__ == "__main__":
    unittest.main()
