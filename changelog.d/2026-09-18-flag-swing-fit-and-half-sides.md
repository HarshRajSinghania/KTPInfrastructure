### `scripts`: flag swing reads each half's sides from life boundaries; `fit_team_score_labels` fits it on ledger-labeled halves (2026-09-18)

`flag_swing` priced flag ownership in the engine's frame (`owner_team` is the side of that
half) but players in the roster's frame (`ktp_match_players.team` is the side of the LAST half
played, overwritten each half). Sides swap at the half, so in every half 1 the flag term
pointed the opposite way from the alive term, and a cap in half 1 credited its capper with a
negative swing. Measured on `1789326428-NY1`: player 6 is team 1 in the roster and Axis in
half 1 per `ktp_life_events`. `sides_by_half` now maps `(half, player_id) -> side` from the
life boundaries the model already requires, with the roster as fallback. `attributed_swing`
(a KTPR v2 component), the timeline and `key_moments` all inherit the fix; report numbering
of `players[].team` is unchanged.

`scripts/fit_team_score_labels.py` is the driver the fitter never had: half winners come from
`ktp_team_score_observations` finals (cumulative closes differenced per roster slot, any
producer), inputs come through the report's own analytics queries, samples through
`extract_half_samples` (now seeded with the map's spawn ownership like the model), and out
comes a `FlagSwingConfig` JSON plus a per-half calibration table. First run, 9 official S10
matches, 18 halves, 11,047 samples: `flag_coefficient 3.06`, `alive_coefficient 0.97`, log
loss 0.613 against 0.693 for a coin, end-of-half state agreeing with the label on 16 of 18.
The fitted vector is not wired into the report yet; that is a separate decision.

`tests/e2e_stats/test_demo_team_score_import_integration.py` executes the demo importer's
SQL as the `hltv-demo` producer against a real ledger (migrations 023-034), twice, and once
over a match the HUD producer already owns. Nothing had done that before the 2026-09-18
backfill, which is why it took #441-#448.
