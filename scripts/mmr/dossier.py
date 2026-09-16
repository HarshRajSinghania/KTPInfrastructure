"""Why a confident prediction missed, in terms of who played how.

Product #1 from the original brief: when the ladder rates team A well above
team B and B wins, say what actually happened rather than just recording that
the model was wrong. A weekly report that says "we called this one wrong"
teaches nobody anything; one that says "the side we favoured had its two best
players post their worst matches of the season" is a finding someone can act
on.

Uses the same per-match KTPR components the performance weighting consumes,
but for REPORTING only -- a dossier is produced whether or not weighting is
switched on, because explaining a miss is useful even when the rating is not
yet using that signal to update.

Deliberately not automated any further than description. It surfaces what
diverged; a human decides whether that means the rating is missing something
(worth a model change) or the match was simply a surprise (worth nothing).
Auto-adjusting a model from its own misses is how a rating chases noise.
"""
from __future__ import annotations

# A performance this far from the match average is worth naming. Below it,
# players are just having ordinary matches and listing them is noise.
NOTABLE_Z = 0.75
# Most names to show per side; a dossier listing everyone explains nothing.
MAX_NAMED = 3


def _named(scores, player_ids, names, best_first=True):
    rows = [(scores[pid], names.get(pid) or f"player {pid}")
            for pid in player_ids if pid in scores]
    rows.sort(reverse=best_first)
    return rows


def build(match, scores, names):
    """One dossier, or None when there is nothing worth saying.

    `match` is a per-match row from run_weekly.run(); `scores` is
    {player_id: blended z-score} for that match; `names` maps id -> alias.
    """
    if not scores:
        return None
    favoured_home = match["p_raw"] > 0.5
    favoured = match["t1"] if favoured_home else match["t2"]
    winner = match["t1"] if match["y"] == 1.0 else match["t2"]
    favoured_name = match["home"] if favoured_home else match["away"]
    winner_name = match["home"] if match["y"] == 1.0 else match["away"]

    # The two halves of an upset: who on the favoured side went missing, and
    # who on the winning side went beyond what their rating would predict.
    underperformed = [r for r in _named(scores, favoured, names, best_first=False)
                      if r[0] <= -NOTABLE_Z][:MAX_NAMED]
    overperformed = [r for r in _named(scores, winner, names) if r[0] >= NOTABLE_Z][:MAX_NAMED]
    if not underperformed and not overperformed:
        return None
    return {
        "match_id": match["match_id"],
        "favoured": favoured_name,
        "winner": winner_name,
        "underperformed": [{"name": n, "z": round(z, 2)} for z, n in underperformed],
        "overperformed": [{"name": n, "z": round(z, 2)} for z, n in overperformed],
    }


def render(dossiers):
    """Markdown lines for the weekly digest. Empty list when there is none."""
    if not dossiers:
        return []
    out = ["", "### What happened in those matches\n",
           "Per-player KTPR v2 for the match itself, as distance from that match's average. "
           "This describes the miss; it does not adjust anything. Whether a name here means the "
           "rating is missing something or the match was simply a surprise is a judgement call, "
           "and the weekly review is where it gets made.\n"]
    for d in dossiers:
        out.append(f"**{d['favoured']} were favoured; {d['winner']} won.**")
        if d["underperformed"]:
            who = ", ".join(f"{p['name']} ({p['z']:+.2f})" for p in d["underperformed"])
            out.append(f"- Below their match average on {d['favoured']}: {who}")
        if d["overperformed"]:
            who = ", ".join(f"{p['name']} ({p['z']:+.2f})" for p in d["overperformed"])
            out.append(f"- Above it on {d['winner']}: {who}")
        out.append("")
    return out
