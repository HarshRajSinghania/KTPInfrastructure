"""Key moments: the match's highlight windows, ranked by momentum swing.

A report stage over ``shadow_explorations.flag_swing.timeline``. Every kill
and flag event there already carries a momentum delta, so "what were the big
moments" is a clustering-and-ranking pass, not a new model. Nothing here reads
a demo or a position.

Consumers: the match report's key-moments section, the HLTV viewer's deep
links (a window's ``start`` in game time is what the anchor converts to demo
time), the post-match message's "best moment", and the clip pipeline. All of
them should read this block rather than re-derive it, or they drift.

Design, carried over from the ktp_highlights prototype (2026-09-08..17):

- Events within MERGE_GAP seconds join one window; a window's score is the sum
  of |delta| plus small bonuses for repeated killers and objective events.
- A window longer than MAX_LEN is centred on its peak-swing event rather than
  truncated from the start, which was cutting away the moment it was picked for.
- ``involved`` is capped at MAX_CAMERAS players ranked by how much of the
  window they drove (kills weighted above deaths). A busy sequence touches all
  twelve; rendering twelve angles for it is what turned a 20-minute render
  budget into two hours.
- Flag events in the timeline carry no player ids (capper attribution lives in
  capture_credits), so a window made only of flag events has an empty
  ``involved``. Consumers fall back to a director view for those.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

DEFINITION = "highlight_windows_v1"
DEFINITION_VERSION = 1


@dataclass(frozen=True)
class HighlightConfig:
    merge_gap: float = 8.0      # seconds between events that still share a window
    pad_before: float = 4.0     # handles on each side: approach, aftermath, anchor slack
    pad_after: float = 3.0
    min_len: float = 8.0
    max_len: float = 30.0
    max_cameras: int = 4        # dominant killer, their victims, a second contributor
    multikill_bonus: float = 0.15
    objective_bonus: float = 0.10
    top_n: int = 20

    def validate(self) -> None:
        if self.merge_gap <= 0 or self.min_len <= 0 or self.max_len < self.min_len:
            raise ValueError("highlight window lengths must be positive and max_len >= min_len")
        if self.max_cameras < 1 or self.top_n < 1:
            raise ValueError("max_cameras and top_n must be >= 1")


def _delta(e: dict[str, Any]) -> float:
    try:
        return abs(float(e.get("delta") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _cluster(events: Sequence[dict[str, Any]], gap: float) -> list[list[dict[str, Any]]]:
    windows: list[list[dict[str, Any]]] = []
    for e in sorted(events, key=lambda x: float(x["game_time"])):
        if windows and float(e["game_time"]) - float(windows[-1][-1]["game_time"]) <= gap:
            windows[-1].append(e)
        else:
            windows.append([e])
    return windows


def _score(window: Sequence[dict[str, Any]], cfg: HighlightConfig) -> float:
    swing = sum(_delta(e) for e in window)
    killers = [e["killer_id"] for e in window if e.get("killer_id") is not None]
    repeats = len(killers) - len(set(killers))
    objectives = sum(1 for e in window if e.get("kind") != "frag")
    return swing + repeats * cfg.multikill_bonus + objectives * cfg.objective_bonus


def _involved(window: Sequence[dict[str, Any]], limit: int) -> list[tuple[int, float]]:
    """Players ranked by how much of the window they drove. Kills decide the
    order first, then total involvement, so the first entry -- the reel's
    camera -- is always the window's dominant killer, never someone who is in
    it only because they died a lot."""
    kills: dict[int, int] = {}
    weight: dict[int, float] = {}
    for e in window:
        if (k := e.get("killer_id")) is not None:
            kills[k] = kills.get(k, 0) + 1
            weight[k] = weight.get(k, 0.0) + 1.0
        if (v := e.get("victim_id")) is not None:
            weight[v] = weight.get(v, 0.0) + 0.4
    ranked = sorted(weight.items(), key=lambda kv: (-kills.get(kv[0], 0), -kv[1], kv[0]))
    return ranked[:limit]


def _summary(window: Sequence[dict[str, Any]], names: dict[int, str],
             involved: Sequence[tuple[int, float]]) -> str:
    """Names the same player ``involved`` leads with, so the summary and the
    first camera never disagree."""
    frags = [e for e in window if e.get("kind") == "frag" and e.get("killer_id") is not None]
    objectives = sum(1 for e in window if e.get("kind") != "frag")
    parts = []
    if frags:
        killers = [e["killer_id"] for e in frags]
        top = next((pid for pid, _ in involved if pid in killers), killers[0])
        n = killers.count(top)
        who = names.get(top) or f"player {top}"
        parts.append(f"{n}k by {who}" if n > 1 else f"kill by {who}")
    if objectives:
        parts.append(f"{objectives} flag event{'s' if objectives > 1 else ''}")
    return ", ".join(parts) or "activity"


def build_highlight_windows(
    timeline: Sequence[dict[str, Any]] | None,
    roster: Sequence[dict[str, Any]] | None,
    config: HighlightConfig | None = None,
    *,
    source_status: str | None = "available",
) -> dict[str, Any]:
    """Rank the flag-swing timeline into highlight windows.

    ``timeline`` rows are flag_swing_v1's: half, game_time, kind, killer_id,
    victim_id, delta. ``roster`` rows carry player_id, player_name_at_match,
    team. Player ids stay in this (private) block; the public DTO re-keys to
    names in analytics_report_dto.
    """
    cfg = config or HighlightConfig()
    cfg.validate()
    envelope: dict[str, Any] = {
        "definition": DEFINITION,
        "definition_version": DEFINITION_VERSION,
        "parameters": {**asdict(cfg), "ranker": "flag_swing_v1", "clock": "producer_game_time"},
        "status": "available",
        "visibility": "private_shadow_only",
        "writes": False,
        "rating_effect": False,
        "caveats": [
            "Windows are ranked on flag_swing_v1 deltas, which are uncalibrated; "
            "ordering is comparative, not absolute.",
        ],
        "windows": [],
        "windows_total": 0,
    }
    if source_status != "available":
        envelope["status"] = "unavailable"
        envelope["caveats"].append(f"flag_swing status is {source_status!r}; nothing to rank.")
        return envelope
    if not timeline:
        envelope["status"] = "unavailable"
        envelope["caveats"].append("flag_swing timeline is empty.")
        return envelope

    names = {int(p["player_id"]): str(p.get("player_name_at_match") or "")
             for p in roster or [] if p.get("player_id") is not None}
    teams = {int(p["player_id"]): p.get("team") for p in roster or [] if p.get("player_id") is not None}

    entries: list[dict[str, Any]] = []
    for half in sorted({int(e["half"]) for e in timeline if e.get("half") is not None}):
        rows = [e for e in timeline if e.get("half") == half and e.get("game_time") is not None]
        for window in _cluster(rows, cfg.merge_gap):
            first = float(window[0]["game_time"])
            last = float(window[-1]["game_time"])
            peak = float(max(window, key=_delta)["game_time"])
            start = first - cfg.pad_before
            end = max(last + cfg.pad_after, start + cfg.min_len)
            if end - start > cfg.max_len:
                start = max(first - cfg.pad_before, peak - cfg.max_len / 2)
                end = min(start + cfg.max_len, last + cfg.pad_after)
                start = end - cfg.max_len
            involved = _involved(window, cfg.max_cameras)
            entries.append({
                "half": half,
                "start": round(start, 2),
                "end": round(end, 2),
                "duration": round(end - start, 2),
                "peak_at": round(peak, 2),
                "kinds": sorted({str(e.get("kind")) for e in window}),
                "events": len(window),
                "swing": round(sum(_delta(e) for e in window), 4),
                "peak_delta": round(max(_delta(e) for e in window), 4),
                "score": round(_score(window, cfg), 4),
                "summary": _summary(window, names, involved),
                "involved": [
                    {"player_id": pid, "player_name_at_match": names.get(pid),
                     "team": teams.get(pid), "involvement": round(w, 1)}
                    for pid, w in involved
                ],
            })

    entries.sort(key=lambda w: (-w["score"], w["half"], w["start"]))
    selected = entries[: cfg.top_n]
    for rank, entry in enumerate(selected, start=1):
        entry["rank"] = rank
    envelope["windows"] = selected
    envelope["windows_total"] = len(entries)
    envelope["render_seconds"] = round(sum(w["duration"] for w in selected), 1)
    envelope["render_seconds_all_involved"] = round(
        sum(w["duration"] * len(w["involved"]) for w in selected), 1)
    return envelope
