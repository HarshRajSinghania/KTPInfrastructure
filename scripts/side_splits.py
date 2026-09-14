"""Per-side and per-class splits for the match report.

Side is the side the PLAYER held in that half (life ledger), never the
catalog side of the weapon: a picked-up enemy weapon stays under the player's
side. A player's side is constant within a half, so every split here is a
per-half split carrying a side label.

- weapon_sides: sql/analytics/weapon_half_fact.sql rows, checked against the
  match-level weapons[] they split.
- duels_by_side: the duel matrix's frags split by the killer's side, checked
  against the duel matrix.
- player_classes: lives, kills, deaths and headshot kills per class id read at
  spawn (ktp_life_events.player_class), labelled from
  config/analytics/dod_classes.toml. Accuracy per class is not reconstructible:
  hlstats_Events_Statsme has a half but no class or time.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from scripts.life_ledger import (
    as_int, boundaries_by_player_half, match_frags, place_frags)

DEFAULT_CLASS_MAP = Path(__file__).resolve().parents[1] / "config" / "analytics" / "dod_classes.toml"
WEAPON_COLUMNS = ("kills", "headshot_kills", "shots", "hits", "damage_dealt")


def _unavailable(*flags: str, rows_key: str = "rows") -> dict[str, Any]:
    return {"status": "unavailable", "flags": sorted(set(flags)), rows_key: []}


def annotate_player_halves(
    player_halves: dict[str, Any],
    sides: Mapping[tuple[int, int], str | None],
    best_streaks: Mapping[tuple[int, int], int | None],
) -> None:
    for row in player_halves.get("rows") or ():
        key = (as_int(row.get("half")), as_int(row.get("player_id")))
        row["side"] = sides.get(key)
        row["best_streak"] = best_streaks.get(key)


def build_weapon_sides(
    rows: Iterable[Mapping[str, Any]] | None,
    weapons: Sequence[Mapping[str, Any]],
    sides: Mapping[tuple[int, int], str | None],
    *,
    per_hit_damage: bool,
) -> dict[str, Any]:
    if rows is None:
        return _unavailable("source-not-captured")
    if not sides:
        return _unavailable("no-life-boundaries")
    checked = tuple(c for c in WEAPON_COLUMNS if per_hit_damage or c != "damage_dealt")
    split: dict[tuple[int, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    out, unsided_kills = [], 0
    for r in rows:
        pid, half, weapon = as_int(r.get("player_id")), as_int(r.get("half")), r.get("weapon")
        values = {c: as_int(r.get(c)) or 0 for c in WEAPON_COLUMNS}
        for c in WEAPON_COLUMNS:
            split[(pid, weapon)][c] += values[c]
        if not any(values.values()):
            continue
        side = sides.get((half, pid))
        if side is None:
            unsided_kills += values["kills"]
        out.append({
            "player_id": pid,
            "player_name_at_match": r.get("player_name_at_match"),
            "team": as_int(r.get("team")), "half": half, "side": side, "weapon": weapon,
            **{c: (values[c] if per_hit_damage or c != "damage_dealt" else None)
               for c in WEAPON_COLUMNS},
        })
    mismatched = sorted({
        c for w in weapons for c in checked
        if (as_int(w.get(c)) or 0) != split.get((as_int(w.get("player_id")), w.get("weapon")), {}).get(c, 0)
    } | {
        c for (pid, weapon), sums in split.items() for c in checked
        if sums.get(c) and not any(as_int(w.get("player_id")) == pid and w.get("weapon") == weapon
                                   for w in weapons)
    })
    return {"status": "available", "flags": [], "reconciled": not mismatched,
            "mismatched_columns": mismatched, "unsided_kills": unsided_kills, "rows": out}


def build_duels_by_side(
    frag_timeline: Iterable[Mapping[str, Any]],
    players: Sequence[Mapping[str, Any]],
    sides: Mapping[tuple[int, int], str | None],
    duel_matrix: Mapping[str, Any],
) -> dict[str, Any]:
    if not sides:
        return _unavailable("no-life-boundaries", rows_key="cells")
    names = {as_int(p.get("player_id")): p.get("player_name_at_match") for p in players}
    teams = {as_int(p.get("player_id")): p.get("team") for p in players}
    counts: dict[tuple[int, int, str | None], int] = defaultdict(int)
    unsided = 0
    for frag in frag_timeline:
        killer, victim = as_int(frag.get("killer_id")), as_int(frag.get("victim_id"))
        if killer not in names or victim not in names:
            continue
        side = sides.get((as_int(frag.get("half")), killer))
        unsided += side is None
        counts[(killer, victim, side)] += 1
    cells = [
        {"killer_id": k, "killer_name": names[k], "victim_id": v, "victim_name": names[v],
         "killer_side": side, "kills": n, "cross_team": teams[k] != teams[v]}
        for (k, v, side), n in sorted(counts.items(), key=lambda i: (i[0][0], i[0][1], str(i[0][2])))
    ]
    totals: dict[tuple[int, int], int] = defaultdict(int)
    for c in cells:
        totals[(c["killer_id"], c["victim_id"])] += c["kills"]
    expected = {(c["killer_id"], c["victim_id"]): c["kills"] for c in duel_matrix.get("cells") or ()}
    return {"status": "available", "flags": [], "reconciled": dict(totals) == expected,
            "unsided_kills": unsided, "cells": cells}


def load_class_map(path: Path = DEFAULT_CLASS_MAP) -> dict[int, dict[str, Any]]:
    import tomllib  # 3.11+; imported here so the builders load on older runners

    with Path(path).open("rb") as source:
        classes = tomllib.load(source).get("classes", {})
    return {int(class_id): dict(entry) for class_id, entry in classes.items()}


def _class_at(lives: Sequence[Mapping[str, Any]], at: float) -> int | None:
    current = None
    for b in lives:
        if b["at"] > at:
            break
        if b["kind"] == "start":
            current = b["player_class"]
    return current


def build_player_classes(
    frag_context: Iterable[Mapping[str, Any]] | None,
    life_boundaries: Iterable[Mapping[str, Any]] | None,
    players: Sequence[Mapping[str, Any]],
    sides: Mapping[tuple[int, int], str | None],
    class_map: Mapping[int, Mapping[str, Any]],
    *,
    match_id: str | None,
    source_available: bool,
) -> dict[str, Any]:
    """One row per (player, half, class id).

    lives: consecutive starts with no end between are one life, and the later
    start's class owns it (the go-live baseline followed by the real spawn, or
    a class-change respawn). kills and deaths take the class of the player's
    latest start at or before the frag, so a grenade landing after its
    thrower died counts to the class that threw it.
    """
    if not source_available or frag_context is None or life_boundaries is None:
        return _unavailable("source-not-captured")
    boundaries = list(life_boundaries)
    if not boundaries:
        return _unavailable("no-life-boundaries")
    ledger = boundaries_by_player_half(boundaries)
    roster = {as_int(p.get("player_id")): p for p in players}
    cells: dict[tuple[int, int, int], dict[str, int]] = defaultdict(
        lambda: {"lives": 0, "kills": 0, "deaths": 0, "headshot_kills": 0})
    coverage = {"lives": 0, "lives_mapped": 0, "kills_classed": 0, "kills_unclassed": 0,
                "deaths_classed": 0, "deaths_unclassed": 0}
    unmapped: set[int] = set()

    for (half, pid), rows in ledger.items():
        if pid not in roster:
            continue
        alive_class: int | None = None
        alive = False
        for b in rows:
            if b["kind"] == "end":
                alive = False
                continue
            cls = b["player_class"]
            if alive and alive_class is not None:
                cells[(half, pid, alive_class)]["lives"] -= 1
                coverage["lives"] -= 1
                coverage["lives_mapped"] -= alive_class in class_map
            alive, alive_class = True, cls
            if cls is None:
                continue
            cells[(half, pid, cls)]["lives"] += 1
            coverage["lives"] += 1
            coverage["lives_mapped"] += cls in class_map
            if cls not in class_map:
                unmapped.add(cls)

    for frag in place_frags(match_frags(frag_context, match_id), boundaries, match_id=match_id):
        for role, pid in (("kills", frag["killer_id"]), ("deaths", frag["victim_id"])):
            if pid not in roster:
                continue
            key = (frag["half"], pid)
            cls = _class_at(ledger.get(key, ()), frag["at"]) if frag["at"] is not None else None
            if cls is None:
                coverage[f"{role}_unclassed"] += 1
                continue
            coverage[f"{role}_classed"] += 1
            cells[(frag["half"], pid, cls)][role] += 1
            if role == "kills" and frag["headshot"]:
                cells[(frag["half"], pid, cls)]["headshot_kills"] += 1
            if cls not in class_map:
                unmapped.add(cls)

    out = []
    for (half, pid, cls), v in cells.items():
        if not any(v.values()):
            continue
        entry = class_map.get(cls) or {}
        player = roster[pid]
        out.append({
            "player_id": pid, "player_name_at_match": player.get("player_name_at_match"),
            "team": as_int(player.get("team")), "half": half, "side": sides.get((half, pid)),
            "class_id": cls, "class_code": entry.get("code"), "class_name": entry.get("name"),
            **v,
        })
    out.sort(key=lambda r: (r["half"], r["team"] or 0, str(r["player_name_at_match"]), r["class_id"]))
    return {"status": "available", "flags": ["unmapped-class-ids"] if unmapped else [],
            "coverage": coverage | {"unmapped_class_ids": sorted(unmapped)}, "rows": out}
