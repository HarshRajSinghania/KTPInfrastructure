"""Shared readers over the physical life ledger (ktp_life_events) and producer frags.

Used by kill_streaks.py and side_splits.py. Rows come from
sql/analytics/life_boundary_fact.sql and sql/analytics/frag_context_fact.sql.

- A player's side is constant within a half, so it is resolved once per
  (half, player) from the ledger's `team` (1 Allies, 2 Axis). Two teams in one
  half resolve to None rather than to a guess.
- A frag with no producer clock takes the game_time of the victim's own death
  boundary when exactly one unclaimed boundary lies within the recovery window
  of its epoch. Such frags usually carry no event_epoch either, only the
  daemon's receipt time, which runs about a second behind the game server.
  Otherwise the frag stays unordered.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

SIDE_NAMES = {1: "Allies", 2: "Axis"}
RECOVERY_WINDOW_SECONDS = 2


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            f = float(value)
        except (TypeError, ValueError):
            return None
        return int(f) if f.is_integer() else None


def as_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _time_key(value: float) -> float:
    return round(value, 2)


def resolve_sides(life_boundaries: Iterable[Mapping[str, Any]] | None) -> dict[tuple[int, int], str | None]:
    """(half, player_id) -> "Allies" / "Axis", or None when the half shows two sides."""
    teams: dict[tuple[int, int], set[int]] = defaultdict(set)
    for row in life_boundaries or ():
        half, pid, team = as_int(row.get("half")), as_int(row.get("player_id")), as_int(row.get("team"))
        if half is None or pid is None:
            continue
        key = (half, pid)
        teams.setdefault(key, set())
        if team in SIDE_NAMES:
            teams[key].add(team)
    return {key: (SIDE_NAMES[next(iter(v))] if len(v) == 1 else None) for key, v in teams.items()}


def frag_half(row: Mapping[str, Any], match_id: str | None) -> int | None:
    """Producer half when it names this match, else the stored half."""
    half = as_int(row.get("half"))
    if half is not None and half > 0:
        return half
    producer = row.get("producer_match_id")
    if producer not in (None, "", match_id):
        return None
    stored = as_int(row.get("stored_half"))
    return stored if stored is not None and stored > 0 else None


def match_frags(frag_context: Iterable[Mapping[str, Any]] | None, match_id: str | None) -> list[dict[str, Any]]:
    """Frags booked to this match, the same rows the box score counts.

    frag_context_fact also returns rows whose producer context names the match
    while the stored match_id does not; those are not box-score frags.
    """
    out = []
    for row in frag_context or ():
        stored = row.get("stored_match_id")
        if match_id is not None and stored is not None and stored != match_id:
            continue
        out.append(dict(row))
    return out


def place_frags(
    frags: Sequence[Mapping[str, Any]],
    life_boundaries: Iterable[Mapping[str, Any]] | None,
    *,
    match_id: str | None,
    window: int = RECOVERY_WINDOW_SECONDS,
) -> list[dict[str, Any]]:
    """Give every frag a half and, where knowable, a game_time.

    Each output row carries `at` (None when unordered) and `placement`:
    `clocked`, `recovered` or `unordered`.
    """
    deaths: dict[tuple[int, int], list[tuple[float, int | None]]] = defaultdict(list)
    for row in life_boundaries or ():
        if str(row.get("boundary_kind") or "") != "end" or str(row.get("reason") or "") != "death":
            continue
        half, pid, at = as_int(row.get("half")), as_int(row.get("player_id")), as_float(row.get("game_time"))
        if half is None or pid is None or at is None:
            continue
        deaths[(half, pid)].append((at, as_int(row.get("event_epoch"))))

    placed: list[dict[str, Any]] = []
    claimed: set[tuple[int, int, float]] = set()
    for row in frags:
        half = frag_half(row, match_id)
        at = as_float(row.get("game_time"))
        out = {
            "killer_id": as_int(row.get("killer_id")),
            "victim_id": as_int(row.get("victim_id")),
            "half": half,
            "at": at if half is not None else None,
            "placement": "clocked" if at is not None and half is not None else "unordered",
            "headshot": bool(as_int(row.get("headshot"))),
            "weapon": row.get("weapon"),
            "event_epoch": (as_int(row.get("event_epoch"))
                            if row.get("event_epoch") is not None
                            else as_int(row.get("receipt_epoch"))),
            "event_id": as_int(row.get("event_id")) or 0,
        }
        if out["placement"] == "clocked" and out["victim_id"] is not None:
            claimed.add((half, out["victim_id"], _time_key(at)))
        placed.append(out)

    untimed = sorted((p for p in placed if p["placement"] == "unordered" and p["half"] is not None),
                     key=lambda p: (p["event_epoch"] is None, p["event_epoch"] or 0, p["event_id"]))
    for frag in untimed:
        epoch, victim = frag["event_epoch"], frag["victim_id"]
        if epoch is None or victim is None:
            continue
        candidates = sorted(
            (abs(e - epoch), at) for at, e in deaths.get((frag["half"], victim), ())
            if e is not None and abs(e - epoch) <= window
            and (frag["half"], victim, _time_key(at)) not in claimed
        )
        if not candidates:
            continue
        nearest = [at for gap, at in candidates if gap == candidates[0][0]]
        if len({_time_key(at) for at in nearest}) != 1:
            continue
        frag["at"], frag["placement"] = nearest[0], "recovered"
        claimed.add((frag["half"], victim, _time_key(nearest[0])))
    return placed


def boundaries_by_player_half(
    life_boundaries: Iterable[Mapping[str, Any]] | None,
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """(half, player_id) -> boundaries ordered by game_time, never by sequence."""
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(life_boundaries or ()):
        half, pid, at = as_int(row.get("half")), as_int(row.get("player_id")), as_float(row.get("game_time"))
        kind = str(row.get("boundary_kind") or "")
        if half is None or pid is None or at is None or kind not in ("start", "end"):
            continue
        grouped[(half, pid)].append({
            "at": at, "kind": kind, "reason": str(row.get("reason") or ""),
            "player_class": as_int(row.get("player_class")),
            "order": (as_int(row.get("event_id")) or 0, index),
        })
    for rows in grouped.values():
        rows.sort(key=lambda r: (r["at"], r["order"]))
    return grouped
