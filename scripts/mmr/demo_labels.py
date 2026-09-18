"""Winner labels from HLTV demos, read for cross-checking the ladder.

The ladder takes match outcomes from ktp.match -- captain-reported scores on
the website. The demo pipeline (KTPInfrastructure#434) derives the same
outcomes from the engine's own TeamScore messages in the HLTV demo, an
independent source validated 8/8 against the admin-entered totals. Two
independent sources for the same fact is exactly the situation where a
disagreement is worth more than either source alone: it is a data-quality
finding, and it should surface BEFORE the ladder trusts either one further.

This module reads the demo labels and reduces them to per-half and
per-match winners. It does not rate anything.

Slot convention, from the #434 contract: rows carry cumulative allies/axis
scores plus which team slot is Allies in that half (sides swap at the
break). Slot 1 is Allies in half 1, which is hlstatsx's team 1 -- so slot
numbers line up with game_match_player.game_team, which is how they join to
the ladder's home/away orientation.

Two readers: the backfill SQL file (what exists today), and the live table
once the operator applies it. Same output shape from either.
"""
from __future__ import annotations

import re
from collections import defaultdict

# Leading scalar columns of an observation row, before the JSON blobs that
# make naive comma-splitting unsafe.
_ROW = re.compile(
    r"\('(?P<match_id>[^']+)',\s*'(?P<map>[^']*)',\s*(?P<match_type>\d+),\s*(?P<half>\d+),\s*"
    r"'(?P<tick>[^']*)',\s*(?P<seq>\d+),\s*'(?P<observed>[^']*)',\s*"
    r"(?P<allies>\d+),\s*(?P<axis>\d+),\s*(?P<allies_slot>\d+),\s*(?P<axis_slot>\d+),\s*"
    r"'(?P<server>[^']*)',\s*'(?P<source>[^']*)',\s*(?P<source_version>\d+),\s*'(?P<kind>[^']+)'"
)


def read_backfill_sql(path):
    """Yield observation dicts from the #434 backfill SQL."""
    text = open(path, encoding="utf-8").read()
    for m in _ROW.finditer(text):
        yield {
            "match_id": m["match_id"], "half": int(m["half"]),
            "allies": int(m["allies"]), "axis": int(m["axis"]),
            "allies_slot": int(m["allies_slot"]), "axis_slot": int(m["axis_slot"]),
            "kind": m["kind"], "source": m["source"],
        }


def rows_from_table(rows):
    """Adapt SELECT rows (dicts with the table's column names) to the same shape."""
    for r in rows:
        yield {
            "match_id": r["match_id"], "half": int(r["half"]),
            "allies": int(r["allies_score"]), "axis": int(r["axis_score"]),
            "allies_slot": int(r["allies_team_id"]), "axis_slot": int(r["axis_team_id"]),
            "kind": r["observation_kind"], "source": r["source"],
        }


def labels(observations):
    """{match_id: {"halves": {1: {slot: pts}, 2: {slot: pts}}, "match": {slot: pts}}}

    Scores are cumulative through the match, so half 1's contribution is
    the half-1 final and half 2's is (half-2 final − half-1 final), each
    keyed by the stable team SLOT rather than by side, because sides swap.
    The match total is the half-2 final.
    """
    finals, sides = defaultdict(dict), defaultdict(dict)
    for o in observations:
        if o["kind"] != "final":
            continue
        finals[o["match_id"]][o["half"]] = {o["allies_slot"]: o["allies"], o["axis_slot"]: o["axis"]}
        sides[o["match_id"]][o["half"]] = {"allies": o["allies_slot"], "axis": o["axis_slot"]}

    out = {}
    for match_id, by_half in finals.items():
        h1, h2 = by_half.get(1), by_half.get(2)
        if not h1 or not h2:
            continue          # a partial match cannot be labelled
        halves = {1: dict(h1), 2: {slot: h2[slot] - h1.get(slot, 0) for slot in h2}}
        # `sides` records which slot held Allies/Axis in each half. Needed
        # because game_match_player.game_team is the side at match END,
        # whereas the demo slot is stable across the swap -- measured: every
        # one of the first nine S10 matches inverted when this was assumed
        # equal. Deriving it from the row means no swap assumption at all.
        out[match_id] = {"halves": halves, "match": dict(h2), "sides": dict(sides[match_id])}
    return out


def winner(points):
    """Slot with more points, or None on a tie."""
    (a, pa), (b, pb) = sorted(points.items())
    if pa == pb:
        return None
    return a if pa > pb else b
