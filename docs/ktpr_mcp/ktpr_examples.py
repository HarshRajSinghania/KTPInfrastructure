"""
Print the worked examples in KTPR_CALCULATION.md section 3 from live data.

    KTPR_SSH_HOST=user@host python ktpr_examples.py                # hildebrand, bR0M, p12
    KTPR_SSH_HOST=user@host python ktpr_examples.py "TillJim" --compare new_v4

Needs the untracked roster.csv beside this file: without it every team label is
unresolved, no placement boost applies, and every KTPR comes out lower.
"""

from __future__ import annotations

import argparse
import sys

import ktpr_engine as E
import ktpr_mysql as Q

EXAMPLES = ("hildebrand", "bR0M", "p12")


def _breakdown(pl: E.Player, mb: dict, p: E.KtprParams) -> dict:
    # Mirrors compute_team term by term; main() asserts the result matches it.
    def R(x: float, m: float, k: float = 0.0) -> float:
        return min(max((x + k) / (m + k), p.ratio_floor), p.ratio_cap)

    rk, rkd, ra = R(pl.kills_half, mb["K"]), R(pl.kd_ratio, mb["KD"]), R(pl.assists_half, mb["A"])
    rd, rf = R(pl.damage_half, mb["D"]), R(pl.flags_half, mb["F"])
    rb = R(pl.breaks_half, mb["B"], p.break_smooth_k)
    rx = pl.deaths_half / mb["X"]
    kill_term = rk ** p.kill_exp
    amp_d = min(max(1 + p.dmg_interaction * (1 - rk), p.dmg_scale_min), p.dmg_scale_max)
    amp_a = min(max(1 + p.assist_interaction * (1 - rk), p.dmg_scale_min), p.dmg_scale_max)
    terms = [p.tw_kill * kill_term, p.tw_kd * rkd, p.tw_assist * ra * amp_a,
             p.tw_damage * rd * amp_d, p.tw_flag * rf, p.tw_break * rb]
    w = (p.tw_kill + p.tw_kd + p.tw_assist + p.tw_damage + p.tw_flag + p.tw_break) or 1.0
    score = sum(terms) / w
    adj_rx = rx / (1 + max(0.0, p.death_kill_relief * (rk - 1)))
    death_adj = 1 - min(max(p.death_w * (adj_rx - 1), -p.team_death_up_cap), p.death_cap)
    n_teams = len(p.team_placements)
    place = p.team_placements.get(pl.team)
    boost = 1.0
    if p.team_placement_weight and n_teams > 1 and place:
        boost = 1 + p.team_placement_weight * (n_teams - place) / (n_teams - 1)
    role_w = p.role_weights.get(pl.role, 1.0) if p.role_weights else 1.0
    return dict(rk=rk, rkd=rkd, ra=ra, rd=rd, rf=rf, rb=rb, rx=rx, kill_term=kill_term,
                amp_d=amp_d, amp_a=amp_a, terms=terms, w=w, score=score, adj_rx=adj_rx,
                death_adj=death_adj, place=place, boost=boost,
                ktpr=p.scale * score * death_adj * role_w * boost)


def _ranks(players, vals) -> dict[int, int]:
    order = sorted((i for i, v in enumerate(vals) if v is not None), key=lambda i: -vals[i])
    return {i: pos for pos, i in enumerate(order, 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Print KTPR worked examples from live data.")
    ap.add_argument("names", nargs="*", default=list(EXAMPLES), help="player-name substrings")
    ap.add_argument("--profile", default="new")
    ap.add_argument("--compare", default="new_v1", help="profile shown as 'was' (the old weights)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    ids = Q._tournament_match_ids()
    players = Q.load_players_from_mysql()
    p, pc = E.load_params(args.profile), E.load_params(args.compare)
    vals, cvals = E.compute_ktpr(players, p), E.compute_ktpr(players, pc)
    rank, crank = _ranks(players, vals), _ranks(players, cvals)
    baseline_for = E._team_baselines(players, p)
    threshold = E._participation_threshold(players, p)
    named = [pl for pl in players if pl.name]

    print(f"database {Q.DB} · {len(ids)} tournament matches · match_type "
          f"{', '.join(Q.TOURNAMENT_MATCH_TYPES)} on {', '.join(Q.TOURNAMENT_DAYS)}")
    print(f"players {len(named)} · regulars {sum(pl.matches >= threshold for pl in named)} "
          f"(matches >= {threshold:.2f})")
    unplaced = sorted({pl.team for pl in named if pl.team not in p.team_placements})
    if p.team_placement_weight and unplaced:
        print(f"WARNING: no placement for teams {unplaced} -- roster.csv missing or stale?")

    for i, pl in enumerate(players):
        if not pl.name or not any(n in pl.name for n in args.names):
            continue
        mb, b = baseline_for(pl), _breakdown(pl, baseline_for(pl), p)
        if abs(b["ktpr"] - vals[i]) > 1e-9:
            raise SystemExit(f"breakdown drifted from compute_team for {pl.name}: "
                             f"{b['ktpr']} != {vals[i]}")
        t = b["terms"]
        print(f"\n{pl.name} ({pl.role}, {pl.team}, place {b['place']}) · "
              f"rank {rank[i]} under {args.profile}, {crank.get(i)} under {args.compare}")
        print(f"raw/half   K/D {pl.kd_ratio:.2f} · kills {pl.kills_half:.2f} · assists {pl.assists_half:.2f}"
              f" · damage {pl.damage_half:.0f} · flags {pl.flags_half:.2f} · breaks {pl.breaks_half:.3f}"
              f" · deaths {pl.deaths_half:.2f}")
        print(f"{pl.role:<6} med K {mb['K']:.2f} · KD {mb['KD']:.2f} · A {mb['A']:.2f} · D {mb['D']:.0f}"
              f" · F {mb['F']:.2f} · B {mb['B']:.3f} · X {mb['X']:.2f}")
        print(f"ratios     rk {b['rk']:.2f} · rkd {b['rkd']:.2f} · ra {b['ra']:.2f} · rd {b['rd']:.2f}"
              f" · rf {b['rf']:.2f} · rb {b['rb']:.2f} · rx {b['rx']:.2f}")
        print(f"shape      kill_term {b['rk']:.2f}^{p.kill_exp} = {b['kill_term']:.3f} ; amp_d {b['amp_d']:.2f},"
              f" amp_a {b['amp_a']:.2f} → dmg_term {t[3] / p.tw_damage:.2f}, assist_term {t[2] / p.tw_assist:.2f}")
        print(f"terms      kill {t[0]:.3f} | kd {t[1]:.3f} | assist {t[2]:.3f} | dmg {t[3]:.3f}"
              f" | flag {t[4]:.3f} | break {t[5]:.3f}")
        print(f"score      {sum(t):.3f} / {b['w']:.2f} = {b['score']:.3f}")
        print(f"death_adj  rx {b['rx']:.2f} → adj_rx {b['adj_rx']:.2f} → {b['death_adj']:.3f}")
        print(f"boost      {b['boost']:.3f}")
        was = f"{cvals[i]:.3f}" if cvals[i] is not None else "-"
        print(f"KTPR       {p.scale} × {b['score']:.3f} × {b['death_adj']:.3f} × {b['boost']:.3f}"
              f" = {vals[i]:.3f}      (was {was} under {args.compare})")


if __name__ == "__main__":
    main()
