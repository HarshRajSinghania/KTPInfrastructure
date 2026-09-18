"""Translate the ladder's output into the `mmr_openskill` season aggregate.

The gap this closes
-------------------
run_weekly writes {player_id: {mu, sigma, ordinal}}. keep-the-prac's profile
card (PR #760) reads a `ktp.season_aggregate` row of kind `mmr_openskill`
holding rows keyed by ALIAS with `matches`, `rating`, `uncertainty` and
`conservative`. Nothing bridged the two, so the card renders nothing on every
profile -- by design, but permanently until this exists.

`conservative` is mu - 3*sigma, confirmed against the consumer's own test
fixture (rating 26.4, uncertainty 8.22, conservative 1.74). That is the same
ordinal the ladder already computes, so the two sides agree by construction
rather than by coincidence.

What this module does NOT do
---------------------------
Write anything. It builds a payload; `report_service.py import-mmr` is what
inserts it, and only that runs where the data server's write credential
lives. Keeping the translation pure means it is testable here, in CI, without
any credential at all -- and it means the operator step is a single command
over a file rather than a script that recomputes ratings on a box that has no
business recomputing them.

Privacy note: the payload carries aliases, never player ids or Steam ids,
matching every other public surface. A player with no alias is dropped rather
than published under an id.
"""
from __future__ import annotations

AGGREGATE_KIND = "mmr_openskill"
METHOD_VERSION = "openskill_pl_v1"
# Below this, a rating is too thinly evidenced to show a player as fact. The
# consumer reads this from the payload rather than hard-coding it, so the
# threshold can move without a website deploy.
MIN_MATCHES_FOR_DISPLAY = 3


def conservative(mu, sigma):
    """mu - 3*sigma: the value the consumer ranks and displays.

    Deliberately pessimistic. A player two matches in can post a flattering
    mu; publishing that as their skill would be a claim the evidence does not
    support, and it is the player's own profile reading it back at them.
    """
    return round(float(mu) - 3.0 * float(sigma), 2)


def build(ratings, matches_played, aliases, *, generated_at,
          min_matches=MIN_MATCHES_FOR_DISPLAY, source_report_count=0,
          report_schema_version=9):
    """Build the aggregate payload.

    ratings: {player_id: {"mu", "sigma", ...}} straight from run_weekly.
    matches_played: {player_id: int} -- how many matches actually rated them.
    aliases: {player_id: alias or None}.

    Rows are sorted by conservative descending so the payload is stable: an
    unordered payload would hash differently run to run and publish a new
    revision every week for no change.
    """
    rows = []
    for pid, rating in ratings.items():
        key = int(pid)
        alias = (aliases.get(key) or "").strip()
        if not alias:
            continue          # never publish someone under a raw id
        mu, sigma = rating.get("mu"), rating.get("sigma")
        if mu is None or sigma is None:
            continue
        rows.append({
            "name": alias,
            "matches": int(matches_played.get(key, 0)),
            "rating": round(float(mu), 2),
            "uncertainty": round(float(sigma), 2),
            "conservative": conservative(mu, sigma),
        })
    rows.sort(key=lambda r: (-r["conservative"], r["name"].lower()))
    return {
        "kind": AGGREGATE_KIND,
        "provisional": True,
        "notice": ("Provisional rating: recomputed from the whole season every "
                   "week, so published values change retroactively."),
        "method_version": METHOD_VERSION,
        "generated_at": generated_at,
        "min_matches": int(min_matches),
        "players": rows,
        # Carried so the existing aggregate insert path can write this row
        # without a special case.
        "source_report_count": int(source_report_count),
        "report_schema_version": int(report_schema_version),
    }


REQUIRED_ROW_FIELDS = ("name", "matches", "rating", "uncertainty", "conservative")
FORBIDDEN_ROW_FIELDS = ("player_id", "steam_id", "steam_id64", "steamid")


def validate_for_import(payload):
    """Problems that should stop this payload being written. [] means fine.

    Lives here rather than in report_service so it is importable, and
    therefore testable, without the data server's Unix-only dependencies --
    the guards on a production write are exactly the code that should not
    ship untested.
    """
    problems = []
    if not isinstance(payload, dict):
        return ["payload is not an object"]
    if payload.get("kind") != AGGREGATE_KIND:
        problems.append(f"kind is {payload.get('kind')!r}, expected {AGGREGATE_KIND!r}")
    rows = payload.get("players")
    if not isinstance(rows, list) or not rows:
        problems.append("payload carries no players")
        return problems
    missing = sorted({f for f in REQUIRED_ROW_FIELDS
                      for row in rows if f not in row})
    if missing:
        problems.append(f"player rows are missing {missing}")
    leaked = sorted({f for f in FORBIDDEN_ROW_FIELDS
                     for row in rows if f in row})
    if leaked:
        problems.append(f"payload carries identifiers {leaked}; aliases only")
    return problems
