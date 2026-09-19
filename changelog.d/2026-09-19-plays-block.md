### `analytics`: `plays` block — each player's best plays, the match's top three, and the dunce (2026-09-19)

- New report stage `scripts/plays.py` (`shadow_explorations.plays`, public
  `plays`): a player's kills, deaths and credited caps within 12 s form a play,
  valued on `flag_swing_v1` from that player's side. Per player the best
  three and for the match the top three. The dunce — the worst play, by
  preference a wasted run behind the lines — is computed in the private
  block only, kept for an end-of-season reel, never published per match.
- New report stage `scripts/excursions.py` (`shadow_explorations.excursions`,
  private): solo runs past the enemy's rear line from position samples, with
  no per-map config — spawn side from where players appear, flag depth by
  projection. Excursions become plays and are charged an exposure cost for
  the time the team played a man down.
- `flag_swing` flag events now carry `credited`, `allies_flags`,
  `axis_flags` and `capout_denied` (the cap took a flag from a side holding
  all but one).
- Fixes two silent defects in `flag_swing` cap credit: `build_report` passed
  the aggregated capture-event rows (no `player_id`), so no cap was ever
  credited to a player in production; and the join was an exact string
  match on `event_time` while the flag-state and capture tables disagree by
  a second on real matches. New `capture_credit_timeline_fact.sql` feeds
  per-credit rows and the join is nearest within 3 s.
- Schema 17 → 18, DTO contract v1.5.0 → v1.6.0 (additive). Existing reports
  read `plays` as unavailable until regenerated; a regeneration also corrects
  their flag-swing cap credit.
