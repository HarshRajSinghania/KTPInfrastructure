### `analytics`: objective score, grenade kill/damage stats, and per-map rate stats (2026-09-15)

Requested feedback: per-player/team grenade kill and damage stats, and overall
map stats (kills per minute, points per minute).

- `players[]` and `teams[]` gain `score` (DoD's own objective score, not a
  capture count — see `dod-objective-score-semantics`), `points_per_minute`,
  `grenade_kills`, `grenade_damage`, `grenade_damage_taken`.
  `player_halves.rows[]` carries the same five fields, unreconciled (the
  half=0 total row is the daemon's own pre-summed value, not a sum of the
  half rows, so it doesn't fit the existing reconciliation check).
- `teams[]` also gains `kills_per_minute`, dividing the team's own kills by
  the match's duration.
- `map_profiles` (season aggregate) gains `kills_per_minute` and
  `points_per_minute`, summed across every match played on the map and
  divided by their combined duration.
- Grenade *explosion* location was requested too but is not shippable: exact
  explosion events aren't persisted (see `MATCH_METRIC_CONTRACT_V1.md`'s
  "Explicitly unavailable in v1"). Grenade *kill* location is already public
  via `spatial_layers.frag_vectors`, filtered to the three grenade weapons.
- Bumps report `schema_version` to 12 and the DTO contract to
  `analytics-report-dto-v1.3.0` (additive only).
