"""Weekly MMR update: predict, score the predictions, update ratings, report.

Runs the whole loop the user described: take each newly-completed league
match, compute each side's strength from its players' current ratings,
predict the winner BEFORE looking at the result, then compare against what
actually happened and update every player's rating accordingly. Over weeks
this is what makes the rating converge.

Everything it needs is public website data (anon key): official results from
ktp.match, rosters from season_team_member, divisions from division. No
game-server credentials -- which is what makes running this in CI practical.
Richer performance-weighted updates (KTPR v2 components) need game-server
data and stay a separate, later step.

Outputs, all written next to this script:
  ratings_current.json   -- ratings after this run (the operative set)
  weekly_digest.md       -- human-readable report for the week
  weekly_summary.json    -- machine-readable, for the CI job to open an issue

Deliberately recomputes the whole ladder from scratch every run rather than
incrementally updating a stored state: the corpus is small, ratings are
path-dependent, and a full deterministic replay means a bad week can never
silently corrupt the running state -- rerun and you get the same answer.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import ladder as L
import match_binding as MB

HERE = Path(__file__).parent
DATA = HERE / "data"
PROJECT_URL = "https://yxpjfenpnwksvvquqlde.supabase.co"
STEAM64_BASE = 76561197960265728
CONFIDENT_MISS = 0.70  # same threshold as phase3_lite.py


def fetch(key: str, table: str, select: str, extra: str = "") -> list[dict]:
    url = f"{PROJECT_URL}/rest/v1/{table}?select={select}&limit=1000{extra}"
    req = urllib.request.Request(url, headers={
        "apikey": key, "Authorization": f"Bearer {key}", "Accept-Profile": "ktp",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{table}: HTTP {e.code} -- {e.read().decode('utf-8','replace')[:300]}") from e


def steam64_to_hlstats(steam_id64: str) -> str:
    n = int(steam_id64) - STEAM64_BASE
    return f"{n % 2}:{n // 2}"


def load_bridge() -> dict[str, int] | None:
    """steam "authserver:accountid" -> hlstatsx player_id, if the local file
    from the game server is present. Optional on purpose: in CI it is absent,
    and the ladder keys on the website's own player_id instead. That keeps
    Steam IDs out of the repo entirely and keeps the scheduled job dependent
    on nothing but public website data. Locally the bridge is used so ratings
    line up with the S9 research corpus, which is keyed on hlstatsx ids."""
    path = DATA / "hlstats_player_unique_ids.tsv"
    if not path.exists():
        return None
    bridge = {}
    for line in open(path, encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) == 2 and parts[1] != "uniqueId":
            bridge[parts[1]] = int(parts[0])
    return bridge


def actual_participants(key: str, fixtures, rosters):
    """{fixture_id: binding} for fixtures whose game match can be identified.

    The ladder needs who PLAYED, not who is registered. See match_binding for
    why that distinction is the difference between a player rating and a team
    rating. Only website player ids are usable here (game_match_player carries
    those), so this runs on the CI identity path.
    """
    completed = [f for f in fixtures if f.get("home_score") is not None]
    if not completed:
        return {}
    earliest = min(f["scheduled_at"] for f in completed if f.get("scheduled_at"))
    window_start = str(earliest)[:10]
    games = fetch(key, "game_match", "id,game_match_id,started_at,map_name,player_count",
                  f"&started_at=gte.{window_start}&order=started_at")
    if not games:
        return {}
    ids = ",".join(str(g["id"]) for g in games)
    people = fetch(key, "game_match_player",
                   "game_match_id,player_id,game_team,player_name",
                   f"&game_match_id=in.({ids})")
    by_game = defaultdict(list)
    for row in people:
        by_game[row["game_match_id"]].append(row)
    bound, unbound = MB.bind(completed, games, by_game, rosters)
    for miss in unbound:
        print(f"  fixture {miss['fixture_id']}: {miss['reason']} "
              f"-- falling back to the registered roster")
    return bound


def build_league_matches(key: str) -> tuple[list[dict], dict]:
    """Completed league matches with both scores, newest last."""
    bridge = load_bridge()
    # identity merges are keyed on hlstatsx ids, so they only apply on the
    # local path; in CI (website ids) there is nothing to merge against.
    merges = L.load_identity_merges() if bridge is not None else {}
    matches_raw = fetch(key, "match", "id,season_id,week_id,division_id,home_season_team_id,"
                                       "away_season_team_id,scheduled_at,status,home_score,away_score,"
                                       "winner_season_team_id", "&order=scheduled_at")
    season_teams = {t["id"]: t for t in fetch(key, "season_team", "id,season_id,team_id,division_id,status")}
    teams = {t["id"]: t["name"] for t in fetch(key, "team", "id,name")}
    divisions = {d["id"]: d["name"] for d in fetch(key, "division", "id,name,season_id")}
    steam_by_pid = {p["player_id"]: p["steam_id64"]
                    for p in fetch(key, "player_steam", "player_id,steam_id64,status")
                    if p["status"] == "current"}

    rosters = defaultdict(set)
    for row in fetch(key, "season_team_member", "season_team_id,player_id,left_at"):
        if row.get("left_at"):
            continue
        if bridge is None:
            # CI path: key on the website's own player_id, no Steam ID needed.
            rosters[row["season_team_id"]].add(row["player_id"])
            continue
        steam64 = steam_by_pid.get(row["player_id"])
        if not steam64:
            continue
        pid = bridge.get(steam64_to_hlstats(steam64))
        if pid is not None:
            rosters[row["season_team_id"]].add(merges.get(pid, pid))

    # Who ACTUALLY played, where we can establish it. Falls back to the
    # registered roster per fixture, which is recorded so the digest can say
    # how much of the week was rated on real participation.
    played = actual_participants(key, matches_raw, rosters) if bridge is None else {}

    out, pending, used_actual, ringer_appearances = [], 0, 0, 0
    for m in matches_raw:
        if m["home_score"] is None or m["away_score"] is None or m["home_score"] == m["away_score"]:
            pending += 1
            continue
        binding = played.get(m["id"])
        if binding:
            home, away = set(binding["home_players"]), set(binding["away_players"])
            used_actual += 1
            ringer_appearances += len(binding.get("ringers", []))
        else:
            home = rosters.get(m["home_season_team_id"], set())
            away = rosters.get(m["away_season_team_id"], set())
        if len(home) < 4 or len(away) < 4:
            continue
        st_home = season_teams.get(m["home_season_team_id"], {})
        st_away = season_teams.get(m["away_season_team_id"], {})
        out.append(dict(
            match_id=f"league-{m['id']}", when=m["scheduled_at"] or "",
            map=divisions.get(m["division_id"], "?"),
            home_team=teams.get(st_home.get("team_id"), "?"),
            away_team=teams.get(st_away.get("team_id"), "?"),
            division=divisions.get(m["division_id"], "?"),
            t1=sorted(home), t2=sorted(away),
            y=1.0 if m["home_score"] > m["away_score"] else 0.0,
            margin=abs(m["home_score"] - m["away_score"]),
            home_score=m["home_score"], away_score=m["away_score"],
            actual_roster=bool(binding),
            # Recorded but excluded from t1/t2 -- see match_binding._ringers.
            # Kept on the row, not just the aggregate count, so a specific
            # match's ringer(s) can be traced back from the digest/summary.
            ringers=sorted(binding.get("ringers", [])) if binding else [],
        ))
    out.sort(key=lambda m: m["when"])
    return out, dict(pending=pending, total_scheduled=len(matches_raw),
                     rated_on_actual_participants=used_actual,
                     ringer_appearances=ringer_appearances)


def season_rosters(key: str, season_number: int):
    """(rosters, current_division, division_sizes) for one season.

    Shared with seeding_report.py so both read rosters the same way -- a
    second loader would be a second set of filter decisions to keep in step.
    Player keys match whatever run_weekly rated on (website player ids in CI,
    hlstatsx ids locally when the Steam bridge is present).
    """
    bridge = load_bridge()
    merges = L.load_identity_merges() if bridge is not None else {}
    seasons = {s["id"]: s["number"] for s in fetch(key, "season", "id,number")}
    season_ids = [sid for sid, num in seasons.items() if num == season_number]
    if not season_ids:
        return {}, {}, []
    sid = season_ids[0]

    divisions = {d["id"]: d["name"] for d in fetch(key, "division", "id,name,season_id")
                 if d["season_id"] == sid}
    teams = {t["id"]: t["name"] for t in fetch(key, "team", "id,name")}
    steam_by_pid = {p["player_id"]: p["steam_id64"]
                    for p in fetch(key, "player_steam", "player_id,steam_id64,status")
                    if p["status"] == "current"}
    season_teams = {t["id"]: t for t in fetch(key, "season_team", "id,season_id,team_id,division_id,status")
                    if t["season_id"] == sid}

    rosters, current = defaultdict(list), {}
    for row in fetch(key, "season_team_member", "season_team_id,player_id,left_at"):
        st = season_teams.get(row["season_team_id"])
        if not st or row.get("left_at"):
            continue
        name = teams.get(st["team_id"], f"team-{st['team_id']}")
        current[name] = divisions.get(st["division_id"])
        if bridge is None:
            rosters[name].append(row["player_id"])
            continue
        steam64 = steam_by_pid.get(row["player_id"])
        if not steam64:
            continue
        pid = bridge.get(steam64_to_hlstats(steam64))
        if pid is not None:
            rosters[name].append(merges.get(pid, pid))

    counts = Counter(d for d in current.values() if d)
    # Order divisions strongest-first; the league's own tier order is not in
    # this payload, so fall back to the conventional Gold/Silver/Bronze.
    rank = {"Gold": 0, "Silver": 1, "Bronze": 2}
    sizes = sorted(counts.items(), key=lambda kv: rank.get(kv[0], 99))
    return dict(rosters), current, sizes


def run(matches, model_factory):
    """Predict each match before applying it, then update. Returns per-match
    rows and the metrics over all predictions made."""
    model = model_factory()
    rows, preds, ys = [], [], []
    for m in matches:
        p = model.predict(m["t1"], m["t2"])
        # The lean survives damping; the confidence does not. Scoring uses the
        # damped value (it is what we actually claim), but "did it pick the
        # right side" reads the lean, so a fully-damped 0.500 is not silently
        # counted as a pick for the away team.
        raw = model.predict_raw(m["t1"], m["t2"]) if hasattr(model, "predict_raw") else p
        evidence = model.evidence(m["t1"], m["t2"]) if hasattr(model, "evidence") else None
        rows.append(dict(match_id=m["match_id"], when=m["when"], home=m["home_team"], away=m["away_team"],
                          division=m["division"], p_home=round(p, 3), p_raw=round(raw, 3),
                          evidence=round(evidence, 2) if evidence is not None else None,
                          y=m["y"], home_score=m["home_score"], away_score=m["away_score"],
                          correct=bool((raw > 0.5) == (m["y"] == 1.0)), margin=m["margin"]))
        preds.append(p)
        ys.append(m["y"])
        model.update(m["t1"], m["t2"], m["y"])
    return model, rows, (L.metrics(preds, ys) if preds else None)


def challengers(matches, holdout_n):
    """Champion vs challengers on the most recent holdout_n matches, none of
    which any variant saw while fitting. Only reports; never auto-adopts."""
    if len(matches) < holdout_n + 10:
        return []
    train, holdout = matches[:-holdout_n], matches[-holdout_n:]
    variants = {
        "champion (openskill default)": L.OpenSkill,
        "openskill beta x1.5": lambda: L.OpenSkill(beta=25 / 6 * 1.5),
        "openskill beta x2": lambda: L.OpenSkill(beta=25 / 6 * 2),
        "elo chess-anchored": L.Elo,
        "elo K=30/provisional 15": lambda: L.Elo(30, 50, 15),
    }
    results = []
    for name, factory in variants.items():
        model = factory()
        for m in train:
            model.update(m["t1"], m["t2"], m["y"])
        preds, ys = [], []
        for m in holdout:
            preds.append(model.predict(m["t1"], m["t2"]))
            ys.append(m["y"])
            model.update(m["t1"], m["t2"], m["y"])
        results.append(dict(name=name, **L.metrics(preds, ys)))
    return sorted(results, key=lambda r: r["log_loss"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"))
    ap.add_argument("--holdout", type=int, default=10, help="matches held out for champion/challenger")
    args = ap.parse_args()
    if not args.key:
        raise SystemExit("No key. Pass --key or set NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY.")

    matches, counts = build_league_matches(args.key)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"{len(matches)} completed league matches with rosters "
          f"({counts['pending']} scheduled/unplayed of {counts['total_scheduled']} total)")

    if not matches:
        digest = [f"# MMR weekly digest -- {now}\n",
                  "No completed league matches with reportable results yet.",
                  f"{counts['pending']} fixtures are scheduled and awaiting results.\n",
                  "The pipeline ran clean; there is simply nothing to rate yet. "
                  "This is the expected state before the season's first results are in."]
        (HERE / "weekly_digest.md").write_text("\n".join(digest) + "\n", encoding="utf-8")
        (HERE / "weekly_summary.json").write_text(json.dumps(
            dict(generated_at=now, completed_matches=0, pending=counts["pending"],
                 has_findings=False, headline="No completed matches yet"), indent=1), encoding="utf-8")
        print("wrote weekly_digest.md (no matches yet)")
        return

    model, rows, metrics = run(matches, L.OpenSkill)
    ratings = model.ratings()
    (HERE / "ratings_current.json").write_text(json.dumps(
        {str(p): r for p, r in sorted(ratings.items(), key=lambda kv: -kv[1]["ordinal"])}, indent=1), encoding="utf-8")

    upsets = [r for r in rows if abs(r["p_home"] - r["y"]) > CONFIDENT_MISS]
    cand = challengers(matches, args.holdout)
    beat_champion = [c for c in cand if cand and c["name"] != "champion (openskill default)"
                     and c["log_loss"] < next(x["log_loss"] for x in cand
                                              if x["name"] == "champion (openskill default)") - 0.01]

    digest = [f"# MMR weekly digest -- {now}\n",
              f"**{len(matches)} completed league matches** rated so far; "
              f"{counts['pending']} fixtures still scheduled.\n"]
    if counts.get("ringer_appearances"):
        digest.append(
            f"{counts['ringer_appearances']} ringer appearance(s) recorded this week -- a "
            "player who played for a team other than the one they're registered to. Recorded, "
            "but excluded from every rating those matches would otherwise have moved.\n")
    digest += ["## Prediction accuracy to date\n",
              f"| Metric | Value |", "|---|---|",
              f"| Matches predicted | {metrics['n']} |",
              f"| Accuracy | {metrics['acc']:.1%} |",
              f"| Log-loss | {metrics['log_loss']:.4f} |",
              f"| Brier | {metrics['brier']:.4f} |",
              f"| Calibration error (ECE) | {metrics['ece']:.4f} |",
              "",
              "Baseline for comparison: a coin flip scores 0.693 log-loss, 0.25 Brier, 50% accuracy.",
              "",
              "**Lean** is the side the ladder favours. **Confidence** is how much of that lean it",
              "has earned: it is pulled toward 50% by how few matches back the thinner of the two",
              "rosters, so early in a season most calls read near 50% on purpose. A confident wrong",
              "call costs far more than an uncertain one, so the ladder does not claim certainty it",
              "cannot support.",
              "",
              "## Most recent results vs predictions\n",
              "| Match | Division | Lean | Confidence | Result | Side called |",
              "|---|---|---|---|---|---|"]
    for r in rows[-10:]:
        lean = r["home"] if r["p_raw"] > 0.5 else r["away"]
        conf = max(r["p_home"], 1 - r["p_home"])
        ev = "" if r["evidence"] is None else f" · {r['evidence']:.0f} match{'' if r['evidence'] == 1 else 'es'} of evidence"
        winner = r["home"] if r["y"] == 1.0 else r["away"]
        digest.append(f"| {r['home']} vs {r['away']} | {r['division']} | {lean} | {conf:.0%}{ev} | "
                      f"{winner} {r['home_score']}-{r['away_score']} | {'yes' if r['correct'] else 'NO'} |")
    if upsets:
        digest += ["", f"## Upsets worth a look ({len(upsets)})\n",
                   "Matches the model called confidently and got wrong -- confident meaning after "
                   "damping, so these are misses it actually had the evidence to avoid. Each is "
                   "either a real signal the rating is missing or a genuine surprise.\n",
                   "| Match | Lean | Confidence | Actually won | Margin |", "|---|---|---|---|---|"]
        for r in upsets[-8:]:
            lean = r["home"] if r["p_raw"] > 0.5 else r["away"]
            winner = r["home"] if r["y"] == 1.0 else r["away"]
            digest.append(f"| {r['home']} vs {r['away']} | {lean} | "
                          f"{max(r['p_home'], 1-r['p_home']):.0%} | {winner} | {r['margin']} |")
    if cand:
        digest += ["", "## Tuning check (champion vs challengers)\n",
                   f"Each variant trained on all but the last {args.holdout} matches, then scored "
                   "on those held-out matches it never saw. Nothing is adopted automatically.\n",
                   "| Variant | Log-loss | Brier | Accuracy |", "|---|---|---|---|"]
        for c in cand:
            digest.append(f"| {c['name']} | {c['log_loss']:.4f} | {c['brier']:.4f} | {c['acc']:.1%} |")
        if beat_champion:
            digest += ["", f"**A challenger beat the champion**: {beat_champion[0]['name']} "
                           f"({beat_champion[0]['log_loss']:.4f} vs champion). Worth a human look "
                           "before adopting -- one week is a small sample."]
    (HERE / "weekly_digest.md").write_text("\n".join(digest) + "\n", encoding="utf-8")
    (HERE / "weekly_summary.json").write_text(json.dumps(dict(
        generated_at=now, completed_matches=len(matches), pending=counts["pending"],
        ringer_appearances=counts.get("ringer_appearances", 0),
        accuracy=metrics["acc"], log_loss=metrics["log_loss"], brier=metrics["brier"], ece=metrics["ece"],
        upsets=len(upsets), challenger_beat_champion=bool(beat_champion),
        challenger_name=beat_champion[0]["name"] if beat_champion else None,
        has_findings=bool(upsets or beat_champion),
        headline=(f"{len(matches)} matches rated, {metrics['acc']:.0%} accuracy"
                  + (f", {len(upsets)} upsets" if upsets else "")
                  + (", challenger beat champion" if beat_champion else "")),
    ), indent=1), encoding="utf-8")
    print(f"accuracy={metrics['acc']:.1%} log_loss={metrics['log_loss']:.4f} upsets={len(upsets)}")
    print("wrote weekly_digest.md, weekly_summary.json, ratings_current.json")


if __name__ == "__main__":
    main()
