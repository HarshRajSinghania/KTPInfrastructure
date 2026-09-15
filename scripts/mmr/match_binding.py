"""Bind a league fixture to the game match that was actually played on it.

Why this exists
---------------
The ladder needs two things per match: who won, and who played. They live in
different places. `ktp.match` carries the reported result but no roster;
`ktp.game_match_player` carries the twelve people who were actually on the
server but no result.

Without the join the ladder has to fall back on `season_team_member`, the
REGISTERED season roster, which credits every rostered player for every match
whether or not they were in it. That is what made 152 players collapse onto
17 distinct ratings -- one per team -- because whole rosters moved together
every week and nothing could ever separate two team-mates. It is the same
bench-padding that made the S1-S8 legacy corpus unusable, reintroduced by
accident.

With the join, a player's rating moves only on matches they actually played,
which is what makes it a player rating rather than a team rating wearing
one's clothes.

How the binding is decided
--------------------------
For each fixture, look for game matches near its kickoff whose participants
fall inside the two registered rosters. A binding is accepted only when it is
unambiguous:

  * exactly one candidate survives the filters -- two candidates means a
    guess, and a wrong guess credits the wrong people;
  * enough of the twelve are recognised (MIN_OVERLAP);
  * both sides are represented, so a match cannot bind on one roster alone;
  * the two game teams map cleanly onto the two fixture teams, each side's
    players predominantly matching one roster and not the other.

Everything else is reported unbound and left to the caller, which falls back
to the registered roster and says so. Fail loud and partial rather than
quietly rating the wrong players.

Measured on the first nine S10 fixtures: all nine bound, each to exactly one
candidate, 11-12 of 12 participants recognised, every start within 20 minutes
of kickoff. The 11s are real -- a player on the server who is not on the
registered roster, which is exactly the rotation the registered roster cannot
see.

Ringers
-------
Some of those "not on the registered roster" players are a different case
from a walk-on filling a gap: they are registered to ANOTHER team this
season, guesting on this side for one match. Their rating must not move on
it -- a ringer's MMR should reflect the team they actually play for, not a
one-off appearance elsewhere, and crediting the borrowed team with a match
that isn't a real reflection of their own roster would be the same
bench-padding problem this module exists to avoid, just one hop removed.

A player who is registered nowhere at all this season is NOT a ringer; they
are a legitimate substitute and are rated normally, same as today -- `bind`
is given every team's roster, not just the fixture's two, specifically so it
can tell "registered to a different team" apart from "registered to no
team." Ringers are still returned, separately, so they are recorded rather
than silently dropped -- just excluded from `home_players`/`away_players`,
the sets the ladder actually rates on.
"""
from __future__ import annotations

import datetime
from collections import defaultdict

# A fixture and its game match start close together; anything further away is
# a different match on the same evening.
MAX_HOURS_FROM_KICKOFF = 6.0
# Of twelve participants, how many must be recognised as belonging to either
# registered roster before the binding is trustworthy.
MIN_OVERLAP = 8
# A game team must match its assigned fixture roster at least this much more
# than the other one, or the sides are not cleanly separable.
MIN_SIDE_MARGIN = 2


def _when(value):
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours_apart(a, b):
    ta, tb = _when(a), _when(b)
    if ta is None or tb is None:
        return None
    return abs((ta - tb).total_seconds()) / 3600.0


def _orient(participants, home_roster, away_roster):
    """Map the two game teams onto home/away, or None if they don't separate.

    Returns (home_players, away_players). The game's own team numbers are
    arbitrary relative to the fixture, so they are decided by which roster
    each side's players actually belong to.
    """
    sides = defaultdict(set)
    for p in participants:
        if p.get("player_id") is not None:
            sides[p.get("game_team")].add(p["player_id"])
    if len(sides) != 2:
        return None
    (ta, pa), (tb, pb) = sorted(sides.items(), key=lambda kv: str(kv[0]))

    straight = len(pa & home_roster) + len(pb & away_roster)
    swapped = len(pb & home_roster) + len(pa & away_roster)
    if abs(straight - swapped) < MIN_SIDE_MARGIN:
        return None
    return (pa, pb) if straight > swapped else (pb, pa)


def _ringers(side_players, own_roster, own_team_id, rosters):
    """Of `side_players`, who is registered to a DIFFERENT team this season.

    Checked against every roster, not just this fixture's two, since a
    ringer's own team may not even be playing tonight. A player in neither
    `own_roster` nor any other team's roster is registered nowhere -- a
    legitimate substitute, not a ringer -- and is never returned here.
    """
    unrostered = side_players - own_roster
    if not unrostered:
        return set()
    ringers = set()
    for team_id, roster in rosters.items():
        if team_id != own_team_id:
            ringers |= unrostered & roster
    return ringers


def bind(fixtures, game_matches, participants_by_game, rosters):
    """Bind fixtures to game matches.

    fixtures: rows with id, scheduled_at, home_season_team_id,
              away_season_team_id.
    game_matches: rows with id, game_match_id, started_at.
    participants_by_game: {game_match.id: [game_match_player rows]}.
    rosters: {season_team_id: set(player_id)} -- EVERY registered season
             roster, not just a fixture's own two -- used to recognise and
             orient participants against the fixture at hand, and to
             recognise a ringer (registered to a different team than the one
             they played this match for) against every other one. Never used
             as the roster itself.

    Returns (bound, unbound). Each bound entry carries the participants whose
    rating this match should move, the ringers it found (recorded, not
    rated), and why the fixture was accepted.
    """
    bound, unbound = {}, []
    for f in fixtures:
        home_id, away_id = f["home_season_team_id"], f["away_season_team_id"]
        home = rosters.get(home_id, set())
        away = rosters.get(away_id, set())
        candidates = []
        for g in game_matches:
            gap = _hours_apart(g.get("started_at"), f.get("scheduled_at"))
            if gap is None or gap > MAX_HOURS_FROM_KICKOFF:
                continue
            people = participants_by_game.get(g["id"], [])
            pids = {p["player_id"] for p in people if p.get("player_id") is not None}
            overlap_home, overlap_away = len(pids & home), len(pids & away)
            if overlap_home + overlap_away < MIN_OVERLAP:
                continue
            # Both sides must show up; one roster alone is not this fixture.
            if not overlap_home or not overlap_away:
                continue
            oriented = _orient(people, home, away)
            if oriented is None:
                continue
            home_all, away_all = oriented
            home_ringers = _ringers(home_all, home, home_id, rosters)
            away_ringers = _ringers(away_all, away, away_id, rosters)
            candidates.append({
                "game_match_id": g.get("game_match_id"),
                "game_match_key": g["id"],
                "home_players": sorted(home_all - home_ringers),
                "away_players": sorted(away_all - away_ringers),
                "ringers": sorted(home_ringers | away_ringers),
                "overlap": overlap_home + overlap_away,
                "hours_from_kickoff": round(gap, 2),
            })

        if len(candidates) == 1:
            bound[f["id"]] = candidates[0]
        else:
            unbound.append({
                "fixture_id": f["id"],
                "reason": "no candidate" if not candidates else f"{len(candidates)} candidates, ambiguous",
            })
    return bound, unbound
