"""Per-match performance signal, for splitting a result between team-mates.

The problem this exists to solve
--------------------------------
Win/loss is one bit of information shared by all twelve players in a match.
It can separate a player from their team-mates over time (play a different
number of matches and your rating diverges -- see match_binding), but it can
never separate two players who were in the SAME match: they won or lost
together, so they move together.

KTPR v2 is the only signal available that differs between them. It scores
each player's contribution within the match, and it is deliberately
outcome-independent, which is what makes it safe to combine with an
outcome-driven rating: it is not a second helping of the same information.

This is the TrueSkill-2-shaped idea from the original research -- feed
individual performance into the update so the credit for a win is not split
evenly between the player who carried it and the player who was carried.

Wiring only. Nothing here is switched on by default: run_weekly takes
--use-performance, and the weights are not tuned. Tuning against nine
matches would fit noise, so the honest sequence is to land the plumbing
now, measure later, and turn it on only if held-out calibration improves.

How the join works
------------------
Three hops, all on public tables:

  fixture --(match_binding)--> game match --(match_report)--> KTPR components

The report's player names are website ALIASES (the sync job resolves them
through player_steam), not in-game names. `game_match_player.player_name`
carries the in-game name with its clan tag -- "gskiLL  rolyat" -- so joining
on that would miss nearly everyone. Verified on real data: 0 of 12 join by
in-game name, 12 of 12 join by alias. So alias -> ktp.player.id is the key.
"""
from __future__ import annotations

# A player's share of the team's update, before normalisation, is
# 1 + STRENGTH * (their z-score). At STRENGTH = 0 every share is 1 and the
# update is split evenly, which is exactly today's behaviour.
DEFAULT_STRENGTH = 0.35
# However good a match was, one player never absorbs the whole update: a
# single blowout performance should tilt the split, not own it.
MIN_SHARE = 0.25
MAX_SHARE = 2.5


def normalize_name(value):
    return (value or "").strip().lower() or None


def components_by_alias(report_payload):
    """{alias: {component: z-score}} from one match report payload.

    Returns {} for a report whose KTPR block is unavailable -- a match with
    no performance signal must fall back to an even split, never to zeros,
    which would read as "everybody played badly".
    """
    ktpr = ((report_payload or {}).get("ratings") or {}).get("ktpr_v2") or {}
    if ktpr.get("status") != "available":
        return {}
    out = {}
    for player in ktpr.get("players") or []:
        alias = normalize_name(player.get("name"))
        if alias is None:
            continue
        comps = {k: v for k, v in (player.get("components") or {}).items()
                 if isinstance(v, (int, float))}
        if comps:
            out[alias] = comps
    return out


def player_scores(components, weights=None):
    """{alias: blended z-score}. `weights` selects and weights components.

    Defaults to an equal blend of whatever the report carried, so a report
    that gains or loses a component does not silently change the meaning of
    the score.
    """
    out = {}
    for alias, comps in components.items():
        names = [c for c in (weights or comps) if c in comps]
        if not names:
            continue
        if weights:
            total = sum(abs(weights[c]) for c in names) or 1.0
            out[alias] = sum(weights[c] * comps[c] for c in names) / total
        else:
            out[alias] = sum(comps[c] for c in names) / len(names)
    return out


def team_shares(team_player_ids, scores_by_id, strength=DEFAULT_STRENGTH):
    """Per-player shares of one team's rating update, mean-normalised to 1.

    Mean 1 is the load-bearing property: OpenSkill reads these as partial-play
    weights, and scaling a whole team uniformly changes nothing (measured --
    a uniform weight cancels out entirely). Only the RELATIVE spread within a
    team does any work, so the shares must redistribute the same total rather
    than inflate it, or performance weighting would quietly become a second,
    unearned confidence knob.

    A player the report says nothing about scores 0, i.e. an average share,
    rather than being penalised for missing data.
    """
    if not team_player_ids:
        return {}
    raw = {}
    for pid in team_player_ids:
        z = scores_by_id.get(pid, 0.0)
        raw[pid] = min(MAX_SHARE, max(MIN_SHARE, 1.0 + strength * z))
    mean = sum(raw.values()) / len(raw)
    if mean <= 0:
        return {pid: 1.0 for pid in team_player_ids}
    return {pid: value / mean for pid, value in raw.items()}


def match_shares(home_ids, away_ids, scores_by_id, strength=DEFAULT_STRENGTH):
    """(home_shares, away_shares) -- each side normalised independently.

    Independently on purpose. Cross-team normalisation would let the losing
    side's performance change how much the winning side's rating moves, which
    conflates "who played well" with "who won" -- the exact entanglement
    KTPR's outcome-independence is there to avoid.
    """
    return (team_shares(home_ids, scores_by_id, strength),
            team_shares(away_ids, scores_by_id, strength))
