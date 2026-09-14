# Analytics report DTO contract

`scripts/analytics_report_dto.py::sanitize_report` turns one internal match
report (`scripts/match_analytics.py::build_report`) into the payload stored in
the website's `ktp.match_report.payload`. This file describes the blocks whose
meaning is not obvious from their names.

## Versions

| `contract_version` | report `schema_version` | Change |
|---|---|---|
| `analytics-report-dto-v1.0.0` | 7-9 | Box score, trades, multikills, recap speed, ratings, lane analytics, spatial layers |
| `analytics-report-dto-v1.1.0` | 10 | Adds `in_game_result`, `player_halves`, `lane_analytics.depth_profiles.units`; always carries `ratings.ktpr_v2.display_scale` |

Minor versions only add keys. A consumer that matches the
`analytics-report-dto-v1.` prefix keeps working; one that needs the new blocks
checks for them, because a v1.0.0 row never has them. A breaking change is a
new major version.

`ratings.ktpr_v2.display_scale` is the exception to that rule: it was added
while the contract still read v1.0.0, so a v1.0.0 row may or may not carry it.
Every v1.1.0 row does.

The report `schema_version` changes whenever `build_report` output changes, and
`report_service generate` regenerates every in-season match that has no report
at the current schema. `report_sync` then inserts those as new rows.

## `in_game_result`

The game engine's own team score (`source: engine-team-score-v1`, relayed by
KTPHudObserver). **It is not the league result.** The league result is the
captain-reported `ktp.match.home_score` / `away_score`, which this pipeline
cannot see and which can differ (forfeits, rulings, replays). `authority` is
always `in_game_team_score`, and `notice` says the same in words.

| Key | Meaning |
|---|---|
| `status` | `complete`, `partial` (published, with a flag), or `unavailable` (no score) |
| `flags` | Why a result is partial or unavailable |
| `team1_score`, `team2_score` | Total at the whistle, in report team numbers |
| `winner` | `1`, `2`, `"draw"`, or `null` when unavailable |
| `halves[]` | `half`, `team1_points` / `team2_points` scored in that half, `team1_cumulative` / `team2_cumulative` at its close, `team1_side` / `team2_side` (`Allies` / `Axis`) played that half |

Team numbers match `players[].team`: the side the team held in the terminal
half, since `ktp_match_players.team` is overwritten each half. Use
`halves[].team1_side` for the side in an earlier half.

Reading rule:

- Scores carry across halves. A half's close is its last `final` row, and the
  match total is the terminal half's close. Points in a half are its close
  minus the previous close.
- The scoreboard resets to 0/0 just after a half opens, before the carry is
  restored. That dip is not a close and is ignored.
- Half 1's baseline is recorded at match start and can still hold warmup
  points. The stream counts as complete only if it contains the 0/0 clear;
  without it the observer joined mid-half and the result is `late-stream-start`.
- `ktp_match_end` states the total in half-1 side terms and is checked that
  way.

Flags:

| Flag | Status |
|---|---|
| `match-end-missing` | partial |
| `observer-root-not-configured`, `observer-stream-missing`, `observer-stream-invalid`, `observer-context-mismatch`, `no-closed-halves`, `replay-source`, `not-in-report` | unavailable |
| `no-score-rows`, `half-set-mismatch`, `side-mapping-unknown`, `missing-half-start`, `missing-half-final`, `late-stream-start`, `half-carryover-mismatch`, `score-regression`, `match-end-disagreement` | unavailable |

The stream is bound to the match by match id, map, match type and the closed
half set in `ktp_matches`. It is read from the observer's settled
`<observer-root>/<match_id>/events.jsonl`, never from a live file.

## `player_halves`

| Key | Meaning |
|---|---|
| `status` | `available` or `unavailable` |
| `reconciled` | `true` when every player's halves add up to `players[]` for every additive column |
| `mismatched_columns` | Columns that did not add up |
| `rows[]` | `name`, `team` (match team number, as above), `half`, `duration_seconds`, and the box-score columns for that half |

A player with no events and no position samples in a half has no row for it.
Assists and cap breaks have no half column at the source and are placed by
event time. Damage columns are `null` for legacy matches without per-hit damage.

## `ratings.ktpr_v2.display_scale`

`parameters` describes the model, including `normalization:
per_match_z_scores`. That is true of `components` but not of the published
`rating`, which is already rescaled. `display_scale` says what each published
field actually is:

| Key | `kind` | Meaning |
|---|---|---|
| `rating` | `floored_index` | `max(floor, center + per_z * z)` with the block's `center` (100), `per_z` (15) and `floor` (50). Render as published; a second transform saturates it. |
| `components` | `raw_z_score` | Per-match z-scores, mean 0, negative below average. A consumer that must not show negatives maps these itself. |

The season aggregate (`report_service aggregate`) carries its own
`display_scale`: `rating` and `sos_rating` are `floored_index` with the same
numbers, and `se` is `raw_z_score` (a spread in z units, not rescaled).

## `lane_analytics.depth_profiles`

`units` names the unit of each player field:

- `mean_depth`, `depth_sd`: a fraction of the lane, the polyline through the
  map's flag origins in flag order. 0 is the player's own end and 1 the enemy
  end for that half. Every sample is clamped to [0, 1], so a value is never
  outside that range, and a player behind their own last flag reads 0. Showing
  it as a percentage of the lane is correct. It is not a fraction of the map.
- `lateral_mean`: mean perpendicular distance from the lane, in world units.
