"""In-game match result from the engine team-score stream.

The score is the game engine's own TeamScore (`engine-team-score-v1`, relayed
by KTPHudObserver). It is NOT the league result: that is the captain-reported
`ktp.match.home_score` / `away_score` on the website, which can differ
(forfeits, rulings, replays) and which this pipeline cannot see.

Reading rule (KTPHLStatsX CLAUDE.md, the half-2 `team_score` trap):
  - Scores carry across halves: a later half opens at the previous close with
    sides swapped, so a half's close (its last `final` row) is cumulative and
    the match total is the terminal half's close.
  - Points scored in a half = close(half) - close(previous half), per team.
  - The scoreboard dips to 0/0 just after a half opens, before the plugin
    restores the carry. That dip is not a close and is ignored; only closes
    are compared for regression.
  - `ktp_match_end` states the total in HALF-1 side terms, so it is checked
    through half 1's side slots, never the terminal half's.

Team numbering follows the report: ktp_match_players.team is the side a player
held in the last half they played, so report team 1 is whichever stream slot
played Allies in the terminal half.

The stream is read from the observer's settled files through
team_score_telemetry.read_event_files (the importer's own strict reader), and
bound to the match by id, map, match type and closed half set from ktp_matches.
The observer's `sourceServer` label is not the hlstats server name, so it is
not used as the binding.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from scripts.team_score_telemetry import (
    OFFICIAL_SOURCE, PRODUCER, TeamScoreError, TeamScoreObservation,
    _type_allowed, read_event_files)

DEFAULT_OBSERVER_ROOT = Path("/opt/hud-observer/matches")
AUTHORITY = "in_game_team_score"
NOTICE = (
    "In-game team score read from the game engine, not the league result. "
    "The league result is the captain-reported score and can differ."
)


def unavailable(*flags: str) -> dict[str, Any]:
    return {
        "status": "unavailable", "flags": sorted(set(flags)),
        "authority": AUTHORITY, "source": OFFICIAL_SOURCE, "producer": PRODUCER,
        "notice": NOTICE, "team1_score": None, "team2_score": None,
        "winner": None, "halves": [],
    }


def _slots(row: TeamScoreObservation, allies: int, axis: int) -> dict[int, int]:
    return {row.allies_team_id: allies, row.axis_team_id: axis}


def build_in_game_result(
    observations: Sequence[TeamScoreObservation],
    *,
    closed_halves: Sequence[int],
    match_end: dict[str, int | None] | None,
) -> dict[str, Any]:
    if not observations:
        return unavailable("no-score-rows")
    by_half: dict[int, list[TeamScoreObservation]] = defaultdict(list)
    for row in observations:
        by_half[row.half].append(row)
    halves = sorted(by_half)
    if halves != sorted(closed_halves):
        return unavailable("half-set-mismatch")

    opens: dict[int, dict[int, int]] = {}
    closes: dict[int, dict[int, int]] = {}
    sides: dict[int, dict[int, str]] = {}
    for half in halves:
        rows = sorted(by_half[half], key=lambda r: (r.tick_seconds, r.event_sequence))
        mapping = {(r.allies_team_id, r.axis_team_id) for r in rows}
        if len(mapping) != 1 or set(next(iter(mapping))) != {1, 2}:
            return unavailable("side-mapping-unknown")
        baselines = [r for r in rows if r.observation_kind == "baseline"]
        finals = [r for r in rows if r.observation_kind == "final"]
        if not baselines:
            return unavailable("missing-half-start")
        if not finals or any(r.observation_kind != "final"
                             for r in rows[rows.index(finals[0]):]):
            return unavailable("missing-half-final")
        opens[half] = _slots(baselines[0], baselines[0].allies_score, baselines[0].axis_score)
        close = finals[-1]
        closes[half] = _slots(close, close.allies_score, close.axis_score)
        sides[half] = {close.allies_team_id: "Allies", close.axis_team_id: "Axis"}

    # The half-1 baseline is taken at match start, before the scoreboard clears,
    # so it can still hold warmup points. A stream that saw the clear (a 0/0
    # reading) caught the go-live; one that never did joined mid-climb.
    if not any(r.allies_score == 0 and r.axis_score == 0 for r in by_half[halves[0]]):
        return unavailable("late-stream-start")
    for previous, half in zip(halves, halves[1:]):
        if opens[half] != closes[previous]:
            return unavailable("half-carryover-mismatch")
        if any(closes[half][s] < closes[previous][s] for s in (1, 2)):
            return unavailable("score-regression")

    flags: set[str] = set()
    terminal = halves[-1]
    if not match_end or match_end.get("allies_score") is None:
        flags.add("match-end-missing")
    else:
        first = by_half[halves[0]][0]
        stated = _slots(first, int(match_end["allies_score"]), int(match_end["axis_score"]))
        if stated != closes[terminal]:
            return unavailable("match-end-disagreement")

    slot = {1: next(s for s, side in sides[terminal].items() if side == "Allies"),
            2: next(s for s, side in sides[terminal].items() if side == "Axis")}
    rows_out = []
    carried = {1: 0, 2: 0}
    for half in halves:
        rows_out.append({
            "half": half,
            "team1_points": closes[half][slot[1]] - carried[slot[1]],
            "team2_points": closes[half][slot[2]] - carried[slot[2]],
            "team1_cumulative": closes[half][slot[1]],
            "team2_cumulative": closes[half][slot[2]],
            "team1_side": sides[half][slot[1]],
            "team2_side": sides[half][slot[2]],
        })
        carried = closes[half]
    team1, team2 = closes[terminal][slot[1]], closes[terminal][slot[2]]
    return {
        "status": "partial" if flags else "complete", "flags": sorted(flags),
        "authority": AUTHORITY, "source": OFFICIAL_SOURCE, "producer": PRODUCER,
        "notice": NOTICE, "team1_score": team1, "team2_score": team2,
        "winner": 1 if team1 > team2 else 2 if team2 > team1 else "draw",
        "halves": rows_out,
    }


def load_in_game_result(
    observer_root: Path | None,
    match_id: str,
    *,
    map_name: str | None,
    closed_halves: Sequence[tuple[int, int | None]],
    now: float | None = None,
) -> dict[str, Any]:
    """`closed_halves` is (half, ktp_matches.match_type) for every closed half."""
    if observer_root is None:
        return unavailable("observer-root-not-configured")
    if not closed_halves:
        return unavailable("no-closed-halves")
    events = Path(observer_root) / match_id / "events.jsonl"
    metadata = events.with_name("metadata.json")
    if not events.is_file() or not metadata.is_file():
        return unavailable("observer-stream-missing")
    try:
        source_server = json.loads(metadata.read_text(encoding="utf-8"))["sourceServer"]
        parsed = read_event_files(
            [events], source_server_roots={str(source_server): Path(observer_root)}, now=now)
    except (OSError, ValueError, KeyError, TypeError, TeamScoreError):
        return unavailable("observer-stream-invalid")
    if len(parsed.manifests) != 1:
        return unavailable("observer-stream-invalid")
    manifest = parsed.manifests[0]
    if (manifest.match_id != match_id or manifest.map_name != map_name
            or any(match_type is None or not _type_allowed(manifest.match_type, match_type, half)
                   for half, match_type in closed_halves)):
        return unavailable("observer-context-mismatch")
    return build_in_game_result(
        parsed.observations,
        closed_halves=[half for half, _ in closed_halves],
        match_end={"allies_score": manifest.match_end_allies_score,
                   "axis_score": manifest.match_end_axis_score},
    )
