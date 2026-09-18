"""Progression: cumulative per-player series over each half, computed once.

The website's match page draws a kills-over-time chart (Leetify's per-player
timeline, translated to DoD). It must not derive that from the raw event
stream itself: a second consumer (the HUD, a post-match message, the clip
pipeline) would derive it separately and the two would disagree, which is the
divergence the HUD momentum work already measured. So the running totals are
built here, in the producer, and the DTO ships the points.

Three per-player metrics and one per-team metric, each a series per half:

  kills            frags with a producer clock where the killer is on the
                   other team (team kills and suicides do not count, matching
                   the box score's `kills`)
  deaths           every frag with a clock where the player is the victim
  damage           `damage_capped` dealt to the other team, from the per-hit
                   damage rows -- only when that source was captured
  flag_differential (team)  flags held by team 1 minus flags held by team 2,
                   from the flag_swing timeline's flag events seeded with the
                   same spawn ownership flag_swing used, so the two agree on
                   what a half started as

The x-axis is the producer's game_time WITHIN the half -- each half is its own
map load and the clock restarts -- so every series begins at [0, 0] and halves
are separate panels downstream. No round index is invented; DoD has none.

Two DoD-native metrics the research named are deliberately absent for now:
cap participation (its per-event rows are keyed on wall clock, not game time)
and cap breaks (no fact query loads per-event break rows at all). Both are
documented follow-ups, not silently zero series.

Player ids stay in this (private) block; analytics_report_dto re-keys to names.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Sequence

DEFINITION = "progression_v1"
DEFINITION_VERSION = 1

PLAYER_METRICS = ("kills", "deaths", "damage")
TEAM_METRICS = ("flag_differential",)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _t(value: float) -> float | int:
    rounded = round(value, 2)
    return int(rounded) if rounded.is_integer() else rounded


class _Series:
    """Running total with one point per change, starting at [0, 0]."""

    def __init__(self) -> None:
        self.points: list[list[float | int]] = [[0, 0]]
        self.total: float = 0.0

    def add(self, at: float, amount: float) -> None:
        self.total += amount
        total = _t(self.total)
        t = _t(at)
        # Several events on the same tick collapse to the last value at that t.
        if self.points[-1][0] == t:
            self.points[-1][1] = total
        else:
            self.points.append([t, total])


def _sorted_events(rows: Iterable[dict[str, Any]], order_key: str = "game_time"):
    keyed = []
    for order, row in enumerate(rows):
        half, at = _int(row.get("half")), _num(row.get(order_key))
        if half and at is not None:
            keyed.append((half, at, order, row))
    keyed.sort(key=lambda item: (item[0], item[1], item[2]))
    return keyed


def build_progression(
    frag_context: Sequence[dict[str, Any]] | None,
    damage_rows: Sequence[dict[str, Any]] | None,
    flag_swing_timeline: Sequence[dict[str, Any]] | None,
    roster: Sequence[dict[str, Any]] | None,
    *,
    spawn_ownership: dict[int, int] | None = None,
    frags_available: bool = True,
    damage_available: bool = True,
    flags_available: bool = True,
    temporal_valid: bool = True,
) -> dict[str, Any]:
    """Build the progression block.

    ``frag_context`` rows: half, game_time, killer_id, victim_id, killer_team,
    victim_team. ``damage_rows``: half, game_time, attacker_id, victim_id,
    attacker_team, victim_team, damage_capped. ``flag_swing_timeline``:
    flag_swing_v1's timeline (flag events carry half, game_time, flag_index,
    owner). ``roster`` rows: player_id, team.
    """
    envelope: dict[str, Any] = {
        "definition": DEFINITION,
        "definition_version": DEFINITION_VERSION,
        "parameters": {
            "clock": "producer_game_time",
            "x_axis": "game_time within the half; halves are separate panels",
            "kills": "cross-team frags with a clock",
            "deaths": "all frags with a clock, as victim",
            "damage": "damage_capped dealt cross-team",
            "flag_differential": "team 1 flags minus team 2 flags",
        },
        "status": "available",
        "visibility": "private_shadow_only",
        "writes": False,
        "rating_effect": False,
        "metrics": list(PLAYER_METRICS),
        "team_metrics": list(TEAM_METRICS),
        "available": {"kills": False, "deaths": False, "damage": False,
                      "flag_differential": False},
        "coverage": {"frags_with_clock": 0, "frags_total": 0,
                     "damage_with_clock": 0, "damage_total": 0},
        "caveats": [
            "Series count only events carrying a producer clock; a final point "
            "can trail the box score when some events have none -- coverage "
            "says how many.",
        ],
        "players": [],
        "teams": [],
    }
    if not temporal_valid:
        envelope["status"] = "timed_metrics_suppressed"
        envelope["caveats"].append("Replay-sourced report: timing is not trustworthy.")
        return envelope

    teams = {int(p["player_id"]): _int(p.get("team"))
             for p in roster or [] if p.get("player_id") is not None}
    if not teams:
        envelope["status"] = "unavailable"
        envelope["caveats"].append("No roster.")
        return envelope

    per_player: dict[tuple[int, int, str], _Series] = defaultdict(_Series)
    halves: set[int] = set()

    if frags_available and frag_context:
        rows = list(frag_context)
        envelope["coverage"]["frags_total"] = len(rows)
        with_clock = 0
        for half, at, _order, row in _sorted_events(rows):
            with_clock += 1
            halves.add(half)
            killer, victim = _int(row.get("killer_id")), _int(row.get("victim_id"))
            killer_team, victim_team = _int(row.get("killer_team")), _int(row.get("victim_team"))
            if victim in teams:
                per_player[(victim, half, "deaths")].add(at, 1)
            if (killer in teams and killer != victim
                    and killer_team in (1, 2) and victim_team in (1, 2)
                    and killer_team != victim_team):
                per_player[(killer, half, "kills")].add(at, 1)
        envelope["coverage"]["frags_with_clock"] = with_clock
        if with_clock:
            envelope["available"]["kills"] = True
            envelope["available"]["deaths"] = True

    if damage_available and damage_rows:
        rows = list(damage_rows)
        envelope["coverage"]["damage_total"] = len(rows)
        with_clock = 0
        for half, at, _order, row in _sorted_events(rows):
            with_clock += 1
            halves.add(half)
            attacker, victim = _int(row.get("attacker_id")), _int(row.get("victim_id"))
            attacker_team, victim_team = _int(row.get("attacker_team")), _int(row.get("victim_team"))
            amount = _num(row.get("damage_capped"))
            if (attacker in teams and amount and attacker != victim
                    and attacker_team in (1, 2) and victim_team in (1, 2)
                    and attacker_team != victim_team):
                per_player[(attacker, half, "damage")].add(at, amount)
        envelope["coverage"]["damage_with_clock"] = with_clock
        if with_clock:
            envelope["available"]["damage"] = True

    # Every rostered player gets a series for every available metric in every
    # half seen, even if it is just [[0, 0]] -- a missing series would read as
    # "no data" downstream, and "zero kills" is data.
    for pid in teams:
        for half in sorted(halves):
            for metric in PLAYER_METRICS:
                if envelope["available"][metric]:
                    per_player[(pid, half, metric)]
    envelope["players"] = [
        {"player_id": pid, "team": teams[pid], "half": half, "metric": metric,
         "points": series.points}
        for (pid, half, metric), series in sorted(
            per_player.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2]))
    ]

    if flags_available and flag_swing_timeline:
        flag_events = [e for e in flag_swing_timeline if e.get("kind") == "flag"]
        if flag_events:
            envelope["available"]["flag_differential"] = True
            seed = {int(k): int(v) for k, v in (spawn_ownership or {}).items()}
            by_half: dict[int, _Series] = {}
            owners: dict[int, int] = {}
            current_half: int | None = None
            for half, at, _order, row in _sorted_events(flag_events):
                if half != current_half:
                    current_half = half
                    owners = dict(seed)
                    series = by_half[half] = _Series()
                    series.points[0][1] = _differential(owners)
                    series.total = float(series.points[0][1])
                flag, owner = _int(row.get("flag_index")), _int(row.get("owner"))
                if flag is None:
                    continue
                owners[flag] = owner if owner in (1, 2) else 0
                new_total = _differential(owners)
                by_half[half].add(at, new_total - by_half[half].total)
            envelope["teams"] = [
                {"team": 1, "half": half, "metric": "flag_differential",
                 "points": series.points}
                for half, series in sorted(by_half.items())
            ] + [
                {"team": 2, "half": half, "metric": "flag_differential",
                 "points": [[t, -v] for t, v in series.points]}
                for half, series in sorted(by_half.items())
            ]

    if not any(envelope["available"].values()):
        envelope["status"] = "unavailable"
        envelope["caveats"].append("No timed source was captured for this match.")
    return envelope


def _differential(owners: dict[int, int]) -> int:
    return sum(1 for o in owners.values() if o == 1) - sum(1 for o in owners.values() if o == 2)
