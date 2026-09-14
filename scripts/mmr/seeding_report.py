"""Division seeding recommendations for admins.

The point of the whole rating workstream. Admins place teams into Gold /
Silver / Bronze by discussion and feel; this gives that conversation a
second opinion with its reasoning attached. It recommends, it never places:
every row carries the evidence behind it and a confidence, and a thin roster
is labelled thin rather than quietly ranked as though it were known.

Two things it deliberately does NOT do:

  * It does not rank by mu. A player two matches in can post a flattering mu;
    ranking on it would seed a team on noise. Team strength uses the
    conservative estimate (mu - k*sigma), so a rating has to be both good and
    established to lift a team.
  * It does not invent divisions. Group sizes come from the season's real
    divisions, so the output is a reseeding of the actual league, not an
    abstract ladder.

Usage:
    python seeding_report.py --key sb_publishable_... --season 11
    python seeding_report.py --ratings ratings_current.json --season 10
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path

import run_weekly as W

HERE = Path(__file__).parent
# A roster with fewer rated players than this cannot be seeded on evidence.
MIN_RATED_PLAYERS = 4
# Matches below which a player's rating is provisional for seeding purposes.
PROVISIONAL_MATCHES = 5
# Conservative estimate: mu - CONSERVATISM * sigma.
CONSERVATISM = 2.0


def conservative(rating: dict, conservatism: float = CONSERVATISM) -> float:
    """Rate a team as if its players were at the low end of plausible.

    Protects against seeding a team up on a couple of loud matches: a high
    mu with a high sigma scores below a slightly lower mu that is settled.
    """
    return float(rating["mu"]) - conservatism * float(rating["sigma"])


def team_strength(member_ratings: list[dict], conservatism: float = CONSERVATISM):
    """Team score, plus the spread that says how uneven the roster is."""
    if not member_ratings:
        return None
    scores = [conservative(r, conservatism) for r in member_ratings]
    return {
        "score": round(statistics.mean(scores), 3),
        "spread": round(max(scores) - min(scores), 3) if len(scores) > 1 else 0.0,
        "best": round(max(scores), 3),
        "worst": round(min(scores), 3),
        "rated_players": len(scores),
    }


def split_into_divisions(ranked, division_sizes):
    """Assign ranked teams into divisions of the league's real sizes.

    `ranked` is best-first. Returns {team: division} plus, for each team, the
    gap to the next team down -- a large gap at a boundary means the split is
    clean, a small one means the two teams either side are barely separable
    and the admin should know the recommendation is marginal.
    """
    order = [name for name, _ in division_sizes]
    out, boundaries, cursor = {}, {}, 0
    for div in order:
        size = dict(division_sizes)[div]
        block = ranked[cursor:cursor + size]
        for team in block:
            out[team["team"]] = div
        if block and cursor + size < len(ranked):
            below = ranked[cursor + size]
            boundaries[div] = round(block[-1]["score"] - below["score"], 3)
        cursor += size
    return out, boundaries


def build(ratings: dict, rosters: dict, division_sizes, current: dict | None = None,
          conservatism: float = CONSERVATISM):
    """rosters: {team_name: [player_key, ...]}; ratings keyed by the same."""
    rows = []
    for team, members in rosters.items():
        rated = [ratings[str(p)] for p in members if str(p) in ratings]
        strength = team_strength(rated, conservatism)
        thin = [str(p) for p in members
                if str(p) in ratings and ratings[str(p)].get("matches", 0) < PROVISIONAL_MATCHES]
        rows.append({
            "team": team,
            "score": strength["score"] if strength else None,
            "spread": strength["spread"] if strength else None,
            "rated_players": strength["rated_players"] if strength else 0,
            "roster_size": len(members),
            "provisional_players": len(thin),
            "current_division": (current or {}).get(team),
        })

    seedable = sorted([r for r in rows if r["rated_players"] >= MIN_RATED_PLAYERS],
                      key=lambda r: -r["score"])
    unseedable = [r for r in rows if r["rated_players"] < MIN_RATED_PLAYERS]

    # Unseedable teams cannot take a slot; size the divisions to what we can
    # actually rank, and say plainly that the rest need a human.
    sizes, remaining = [], len(seedable)
    for div, size in division_sizes:
        take = min(size, remaining)
        sizes.append((div, take))
        remaining -= take
    placement, boundaries = split_into_divisions(seedable, sizes)

    for r in seedable:
        r["recommended_division"] = placement.get(r["team"])
        r["confidence"] = confidence_for(r, boundaries.get(r["recommended_division"]))
        r["moves"] = (r["current_division"] is not None
                      and r["current_division"] != r["recommended_division"])
    for r in unseedable:
        r["recommended_division"] = None
        r["confidence"] = "insufficient data"
        r["moves"] = False
    return seedable, unseedable, boundaries


def confidence_for(row, gap_below):
    """Confidence is about the recommendation, not the team.

    A team can be well understood and still sit on a knife-edge between two
    divisions; that is a low-confidence RECOMMENDATION even though the rating
    is solid. Both causes are reported because they call for different
    responses from an admin.
    """
    if row["provisional_players"] >= max(2, row["rated_players"] // 2):
        return "low - roster largely unproven"
    if gap_below is not None and gap_below < 1.0:
        return "low - borderline between divisions"
    if row["rated_players"] < row["roster_size"] - 2:
        return "medium - several players unrated"
    return "high"


def render(seedable, unseedable, boundaries, season_label: str) -> str:
    out = [f"# Division seeding recommendations -- {season_label}\n",
           "A second opinion for the seeding conversation, not a placement. Every row",
           "shows the evidence behind it; anything thin is labelled thin rather than",
           "quietly ranked as if it were known.",
           "",
           f"Team score is the mean of its players' conservative ratings (mu - {CONSERVATISM:g} x sigma),",
           "so a rating has to be both good and settled to lift a team. Spread is the gap",
           "between the strongest and weakest rated player, which is how top-heavy the",
           "roster is.",
           ""]
    # A report that recommends moves while every row is unproven is the most
    # dangerous thing this script can emit: it is skimmable, it looks
    # authoritative, and it is not. Say so at the top, before the table.
    low = [r for r in seedable if r["confidence"].startswith("low")]
    if seedable and len(low) >= 0.6 * len(seedable):
        out += ["> **Not ready to act on.** "
                f"{len(low)} of {len(seedable)} recommendations are low-confidence, which means the "
                "ratings behind them are not yet established. Early in a season every roster is "
                "unproven and teammates share near-identical ratings, so the ordering below is "
                "closer to a coin toss than a finding. Read it as a structure check -- does the "
                "shape look sane -- not as a seeding proposal. It becomes useful after several "
                "weeks of results.", ""]
    moves = [r for r in seedable if r["moves"]]
    if moves:
        out += [f"**{len(moves)} team{'s' if len(moves) != 1 else ''} would move** from where they sit today. "
                "Those rows are marked.", ""]
    out += ["| Team | Recommended | Today | Score | Spread | Rated | Provisional | Confidence |",
            "|---|---|---|---|---|---|---|---|"]
    for r in seedable:
        mark = " **(move)**" if r["moves"] else ""
        today = r["current_division"] or "--"
        out.append(f"| {r['team']} | {r['recommended_division']}{mark} | {today} | {r['score']:.2f} | "
                   f"{r['spread']:.2f} | {r['rated_players']}/{r['roster_size']} | "
                   f"{r['provisional_players']} | {r['confidence']} |")
    if boundaries:
        out += ["", "## How clean are the division splits\n",
                "The gap between the last team in a division and the first team below it. A",
                "small gap means those two teams are barely separable and the boundary is a",
                "judgement call rather than a finding.\n",
                "| Boundary below | Gap |", "|---|---|"]
        for div, gap in boundaries.items():
            verdict = "clean" if gap >= 2.0 else ("narrow" if gap >= 1.0 else "effectively a tie")
            out.append(f"| {div} | {gap:.2f} -- {verdict} |")
    if unseedable:
        out += ["", f"## Needs a human ({len(unseedable)})\n",
                f"Fewer than {MIN_RATED_PLAYERS} players with any rating, so there is nothing to seed on.",
                "These are not weak teams; they are unmeasured ones.\n",
                "| Team | Rated players | Roster |", "|---|---|---|"]
        for r in unseedable:
            out.append(f"| {r['team']} | {r['rated_players']} | {r['roster_size']} |")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"))
    ap.add_argument("--ratings", type=Path, default=HERE / "ratings_current.json")
    ap.add_argument("--season", type=int, default=10, help="season number to seed")
    ap.add_argument("--conservatism", type=float, default=CONSERVATISM)
    ap.add_argument("--out", type=Path, default=HERE / "seeding_report.md")
    args = ap.parse_args()
    if not args.key:
        raise SystemExit("No key. Pass --key or set NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY.")
    if not args.ratings.exists():
        raise SystemExit(f"No ratings at {args.ratings}. Run run_weekly.py first.")

    ratings = json.loads(args.ratings.read_text(encoding="utf-8"))
    rosters, current, sizes = W.season_rosters(args.key, args.season)
    if not rosters:
        raise SystemExit(f"No rosters found for season {args.season}.")
    seedable, unseedable, boundaries = build(ratings, rosters, sizes, current, args.conservatism)
    args.out.write_text(render(seedable, unseedable, boundaries, f"Season {args.season}"),
                        encoding="utf-8")
    moved = sum(1 for r in seedable if r["moves"])
    print(f"{len(seedable)} teams seeded, {len(unseedable)} unseedable, "
          f"{moved} would move -> {args.out.name}")


if __name__ == "__main__":
    main()
