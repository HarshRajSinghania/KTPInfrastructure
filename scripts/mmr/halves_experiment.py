"""Does rating on halves predict matches better than rating on matches?

The demo labels carry a winner per HALF; the ladder rates per MATCH. A third
of opening-week matches split their halves, and in those a match-level label
throws away the fact that the loser took a half. But halves of one match
share a roster and a night, so treating them as two independent results
would double-count evidence and re-create the overconfidence damping just
fixed.

So two things are measured, not one:

  A. Does half-level updating change WHICH side is favoured for the next
     match, and does it predict better?  (information)
  B. Does it do so while counting a match as ONE unit of evidence, not two?
     (honesty -- each half carries evidence 0.5 toward damping/sigma)

Both variants predict each MATCH before seeing it, walk-forward, so they are
scored on the same thing the ladder is actually for. Half-level trains on
halves of earlier matches only; within a match, half 1 precedes half 2.

Nine matches is thin. The output says so, and the conclusion is drawn from
direction and mechanism, not from a third decimal place.
"""
from __future__ import annotations

import argparse
import os
from collections import Counter

import demo_labels as D
import ladder as L
import run_weekly as W


def home_slot(key, binding, label):
    people = W.fetch(key, "game_match_player", "game_match_id,player_id,game_team",
                     f"&game_match_id=eq.{binding['game_match_key']}")
    side_of = {p["player_id"]: p["game_team"] for p in people}
    votes = Counter(side_of[pid] for pid in binding["home_players"] if pid in side_of)
    if not votes:
        return None
    end_side = votes.most_common(1)[0][0]
    last = max(label["sides"])
    return label["sides"][last]["allies" if end_side == 1 else "axis"]


class HalfAware(L.OpenSkill):
    """OpenSkill that can count a result as a fraction of a match of evidence.

    The rating update itself is unchanged; only the games counter that feeds
    damped_probability() moves by `evidence` instead of 1. Two halves at 0.5
    each leave a player exactly one match better evidenced -- the same as
    rating the match once -- so any gain here is from information, not from
    the ladder having quietly become more sure of itself.
    """

    def _apply(self, players, rated, shares, evidence=1.0):
        for pid, new in zip(players, rated):
            old = self.r[pid]
            share = 1.0 if not shares else float(shares.get(pid, 1.0))
            self.r[pid] = self.m.rating(mu=old.mu + (new.mu - old.mu) * share, sigma=new.sigma)
            self.games[pid] += evidence

    def update(self, t1, t2, y, shares=None, evidence=1.0):
        a, b = [self.r[p] for p in t1], [self.r[p] for p in t2]
        na, nb = self.m.rate([a, b], ranks=[1, 2] if y == 1.0 else [2, 1])
        self._apply(t1, na, shares, evidence)
        self._apply(t2, nb, shares, evidence)


def walk_forward(matches, halves_by_match, mode):
    """Predict each match, then train on it (as a match, or as its halves)."""
    model = HalfAware()
    preds, ys, raws = [], [], []
    for m in matches:
        preds.append(model.predict(m["t1"], m["t2"]))
        raws.append(model.predict_raw(m["t1"], m["t2"]))
        ys.append(m["y"])
        if mode == "match":
            model.update(m["t1"], m["t2"], m["y"])
        else:
            for y_half in halves_by_match[m["match_id"]]:
                model.update(m["t1"], m["t2"], y_half, evidence=0.5)
    return preds, raws, ys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"))
    ap.add_argument("--backfill", required=True)
    args = ap.parse_args()

    demo = D.labels(D.read_backfill_sql(args.backfill))
    matches, counts = W.build_league_matches(args.key)
    bindings = counts.get("bindings") or {}

    halves_by_match, kept = {}, []
    for m in matches:
        b = bindings.get(int(m["match_id"].rsplit("-", 1)[-1]))
        label = demo.get(b["game_match_id"]) if b else None
        if not label:
            continue
        slot = home_slot(args.key, b, label)
        outcomes = []
        for half in (1, 2):
            w = D.winner(label["halves"][half])
            if w is None:
                continue                       # a tied half is no evidence either way
            outcomes.append(1.0 if w == slot else 0.0)
        if outcomes:
            halves_by_match[m["match_id"]] = outcomes
            kept.append(m)

    splits = sum(1 for o in halves_by_match.values() if len(set(o)) > 1)
    print(f"{len(kept)} matches with demo half labels; {splits} split their halves\n")

    results = {}
    for mode in ("match", "halves"):
        preds, raws, ys = walk_forward(kept, halves_by_match, mode)
        results[mode] = (L.metrics(preds, ys), raws)
        met = results[mode][0]
        print(f"{mode:7s} logloss={met['log_loss']:.4f} brier={met['brier']:.4f} "
              f"acc={met['acc']:.3f} ece={met['ece']:.4f}")

    print("\nwhere the two variants LEAN differently on a match (raw, pre-damping):")
    raw_m, raw_h = results["match"][1], results["halves"][1]
    changed = 0
    for m, pm, ph in zip(kept, raw_m, raw_h):
        if (pm > 0.5) != (ph > 0.5):
            changed += 1
            won = m["home_team"] if m["y"] == 1.0 else m["away_team"]
            print(f"  {m['home_team']} v {m['away_team']}: match-lean {pm:.2f}  halves-lean {ph:.2f}  "
                  f"actual winner {won}")
    if not changed:
        print("  none -- both variants favour the same side on every match this week")

    print("\nreading this: nine matches is thin. What matters is whether the halves")
    print("variant moves confidence in the right DIRECTION on split matches (toward")
    print("50%) without having counted more evidence -- the games counter is held to")
    print("one match either way. A tiny log-loss gap in either direction is noise.")


if __name__ == "__main__":
    main()
