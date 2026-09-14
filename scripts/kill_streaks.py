"""kill_streak_v1: the longest run of enemy kills between a player's own life ends, per half.

Definition (docs/proposals/streaks-and-side-splits.md section 1):
- counts: hlstats_Events_Frags rows with the player as killer; teamkills live in
  hlstats_Events_Teamkills and never count;
- resets: every `end` boundary for the player in ktp_life_events (death to an
  enemy or a teammate, suicide, world, disconnect);
- a teamkill BY the player neither counts nor resets, and a start without an end
  never resets;
- a half always resets; the match value is the best half;
- a kill landing after the killer's own death (a grenade) counts toward the
  counter as it stands then, which the death has already reset;
- a kill and the killer's own death on the same tick: the kill is first;
- a frag with no producer clock and no recoverable victim death is left out of
  the run and marks its row `lower_bound`.

Never the stock hlstatsx `kill_streak_N` actions: capped, receipt-ordered, and
ended by handlers that are not match-scoped.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

from scripts.life_ledger import (
    as_int, boundaries_by_player_half, match_frags, place_frags, resolve_sides)

DEFINITION = "kill_streak_v1"
DEFINITION_VERSION = 1
LONG_RUN = 3


def unavailable(*flags: str) -> dict[str, Any]:
    return {
        "definition": DEFINITION, "definition_version": DEFINITION_VERSION,
        "status": "unavailable", "flags": sorted(set(flags)),
        "coverage": {"ordered_frags": 0, "recovered_frags": 0, "unordered_frags": 0,
                     "kills_after_own_death": 0},
        "rows": [], "players": [],
    }


def _runs(kills: Sequence[float], ends: Sequence[float]) -> tuple[int, int]:
    """(best run, runs of LONG_RUN or more). Ties put the kill first."""
    events = sorted([(t, 0) for t in kills] + [(t, 1) for t in ends])
    run = best = long_runs = 0
    for _, kind in events:
        if kind == 0:
            run += 1
            best = max(best, run)
            continue
        long_runs += run >= LONG_RUN
        run = 0
    long_runs += run >= LONG_RUN
    return best, long_runs


def build_kill_streaks(
    frag_context: Iterable[Mapping[str, Any]] | None,
    life_boundaries: Iterable[Mapping[str, Any]] | None,
    players: Sequence[Mapping[str, Any]],
    *,
    match_id: str | None,
    source_available: bool,
) -> dict[str, Any]:
    if not source_available or frag_context is None or life_boundaries is None:
        return unavailable("source-not-captured")
    boundaries = list(life_boundaries)
    if not boundaries:
        return unavailable("no-life-boundaries")
    frags = match_frags(frag_context, match_id)
    if frags and all(f.get("game_time") is None for f in frags):
        return unavailable("no-frag-clock")

    placed = place_frags(frags, boundaries, match_id=match_id)
    ledger = boundaries_by_player_half(boundaries)
    sides = resolve_sides(boundaries)
    roster = {as_int(p.get("player_id")): p for p in players}

    kills_at: dict[tuple[int, int], list[float]] = defaultdict(list)
    kill_counts: dict[tuple[int, int], int] = defaultdict(int)
    unordered: set[tuple[int, int]] = set()
    after_death = 0
    for frag in placed:
        if frag["killer_id"] is None or frag["half"] is None:
            continue
        key = (frag["half"], frag["killer_id"])
        kill_counts[key] += 1
        if frag["at"] is None:
            unordered.add(key)
            continue
        kills_at[key].append(frag["at"])
        own = ledger.get(key) or []
        last = next((b for b in reversed(own) if b["at"] <= frag["at"]), None)
        if last is not None and last["kind"] == "end" and last["at"] < frag["at"]:
            after_death += 1

    flags: set[str] = set()
    rows = []
    for key in sorted(set(ledger) | set(kill_counts)):
        half, pid = key
        player = roster.get(pid)
        if player is None:
            continue
        ends = [b["at"] for b in ledger.get(key, ()) if b["kind"] == "end"]
        if key in ledger:
            best, long_runs = _runs(kills_at.get(key, ()), ends)
        else:
            # Kills with no boundary at all cannot be split into lives.
            best, long_runs = None, None
            flags.add("kills-without-life-boundaries")
        rows.append({
            "player_id": pid,
            "player_name_at_match": player.get("player_name_at_match"),
            "team": as_int(player.get("team")),
            "half": half,
            "side": sides.get(key),
            "kills": kill_counts.get(key, 0),
            "best_streak": best,
            "streaks_3_plus": long_runs,
            "lower_bound": key in unordered,
        })
    if unordered:
        flags.add("unordered-frags")
    rows.sort(key=lambda r: (r["half"], r["team"] or 0, -(r["best_streak"] or 0),
                             str(r["player_name_at_match"])))

    coverage = {
        "ordered_frags": sum(f["placement"] == "clocked" for f in placed),
        "recovered_frags": sum(f["placement"] == "recovered" for f in placed),
        "unordered_frags": sum(f["placement"] == "unordered" for f in placed),
        "kills_after_own_death": after_death,
    }
    return {
        "definition": DEFINITION, "definition_version": DEFINITION_VERSION,
        "status": "available", "flags": sorted(flags), "coverage": coverage,
        "rows": rows, "players": _match_rows(rows),
    }


def _match_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_player: dict[int, dict[str, Any]] = {}
    unknown: dict[int, set[str | None]] = {}
    for r in rows:
        p = by_player.setdefault(r["player_id"], {
            "player_id": r["player_id"],
            "player_name_at_match": r["player_name_at_match"],
            "team": r["team"], "best_streak": None,
            "by_side": {"Allies": None, "Axis": None}, "lower_bound": False,
        })
        p["lower_bound"] = p["lower_bound"] or bool(r["lower_bound"])
        if r["best_streak"] is None:
            # An unknown half makes the match value unknown, not the other half's.
            unknown.setdefault(r["player_id"], set()).update({"match", r["side"]})
            continue
        p["best_streak"] = max(p["best_streak"] or 0, r["best_streak"])
        if r["side"] in p["by_side"]:
            p["by_side"][r["side"]] = max(p["by_side"][r["side"]] or 0, r["best_streak"])
    for pid, scopes in unknown.items():
        p = by_player[pid]
        if "match" in scopes:
            p["best_streak"] = None
        for side in p["by_side"]:
            if side in scopes:
                p["by_side"][side] = None
    return sorted(by_player.values(),
                  key=lambda p: (p["team"] or 0, -(p["best_streak"] or 0),
                                 str(p["player_name_at_match"])))


def best_by_player_half(result: Mapping[str, Any]) -> dict[tuple[int, int], int | None]:
    return {(r["half"], r["player_id"]): r["best_streak"] for r in result.get("rows") or ()}


def best_by_player(result: Mapping[str, Any]) -> dict[int, int | None]:
    return {p["player_id"]: p["best_streak"] for p in result.get("players") or ()}
