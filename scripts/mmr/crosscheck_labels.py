"""Cross-check the ladder's outcomes against the demo-derived labels.

Two independent sources for the same fact:
  * ktp.match -- captain-reported scores on the website. What the ladder
    rates on today.
  * ktp_team_score_observations -- HLTV demo TeamScore, engine-derived
    (KTPInfrastructure#434). Validated 8/8 against admin totals.

If they agree on every match, the ladder's labels are corroborated by
something a captain cannot mistype. If they disagree anywhere, that is a
data-quality finding to resolve before either source is trusted further --
and it is far cheaper to find now, on nine matches, than in week eight.

Also reports the per-half picture, because the demo labels carry it and the
ladder does not: how often a match's two halves went to different teams. That
number is what decides whether rating on halves is worth doing (see the
per-half section of the output).

Usage:
    python crosscheck_labels.py --key sb_publishable_... \\
        --backfill G:/GIT/KTP/ktp_highlights/evidence/observations-s10-official-20260913.sql
"""
from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict

import demo_labels as D
import run_weekly as W


def home_slot(key, binding, label):
    """Which demo SLOT the fixture's HOME side is.

    game_match_player.game_team is the side (1 Allies, 2 Axis) at match END.
    The demo slot is the stable team number across the half-time swap. They
    are not the same thing, and assuming they were inverted all nine S10
    matches. So: read home's end-of-match side from the players, then ask
    the demo's own half-2 row which slot held that side. No swap assumption.
    """
    people = W.fetch(key, "game_match_player", "game_match_id,player_id,game_team",
                     f"&game_match_id=eq.{binding['game_match_key']}")
    side_of = {p["player_id"]: p["game_team"] for p in people}
    votes = Counter(side_of[pid] for pid in binding["home_players"] if pid in side_of)
    if not votes:
        return None
    home_side_at_end = votes.most_common(1)[0][0]
    last_half = max(label["sides"])
    return label["sides"][last_half]["allies" if home_side_at_end == 1 else "axis"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"))
    ap.add_argument("--backfill", required=True, help="the #434 backfill SQL")
    args = ap.parse_args()
    if not args.key:
        raise SystemExit("No key.")

    demo = D.labels(D.read_backfill_sql(args.backfill))
    matches, counts = W.build_league_matches(args.key)
    bindings = counts.get("bindings") or {}

    agree, disagree, unmatched, half_splits = 0, [], [], 0
    print(f"{'fixture':44s} {'website':>10s} {'demo':>10s}  verdict")
    for m in matches:
        fixture_id = int(m["match_id"].rsplit("-", 1)[-1])
        b = bindings.get(fixture_id)
        label = demo.get(b["game_match_id"]) if b else None
        if not b or not label:
            unmatched.append(m["match_id"])
            continue
        slot = home_slot(args.key, b, label)
        away_slot = 2 if slot == 1 else 1
        demo_home, demo_away = label["match"].get(slot, 0), label["match"].get(away_slot, 0)
        site_says_home = m["y"] == 1.0
        demo_says_home = demo_home > demo_away
        scores_match = (demo_home, demo_away) == (m["home_score"], m["away_score"])
        name = f"{m['home_team']} v {m['away_team']}"
        site = f"{m['home_score']}-{m['away_score']}"
        dem = f"{demo_home}-{demo_away}"
        if site_says_home == demo_says_home:
            agree += 1
            verdict = "agree" + ("" if scores_match else " (winner only; totals differ)")
        else:
            disagree.append(name)
            verdict = "DISAGREE"
        print(f"{name:44s} {site:>10s} {dem:>10s}  {verdict}")

        h1w, h2w = D.winner(label["halves"][1]), D.winner(label["halves"][2])
        if h1w and h2w and h1w != h2w:
            half_splits += 1

    print()
    print(f"agree on winner: {agree}/{agree + len(disagree)}   disagree: {len(disagree)}   "
          f"unmatched: {len(unmatched)}")
    for name in disagree:
        print(f"  DISAGREE: {name}")
    for mid in unmatched:
        print(f"  unmatched (no demo label or no binding): {mid}")

    print()
    print("per-half picture")
    print(f"  matches where the two halves went to DIFFERENT teams: {half_splits}/{agree + len(disagree)}")
    print("  (this is the only case where rating on halves carries information a")
    print("   match-level label does not; if it is rare, per-half rating mostly")
    print("   double-counts the same result)")


if __name__ == "__main__":
    main()
