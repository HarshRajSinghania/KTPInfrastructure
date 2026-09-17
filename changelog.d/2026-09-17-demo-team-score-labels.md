### `scripts`: winner labels from HLTV demos, as a second producer of the team-score ledger (2026-09-17)

`ktp_team_score_observations` had 0 rows after nine Season 10 matches. The only producer was
the HUD observer's `events.jsonl` path, whose home-flag fix (DoD-hud-observer #24) is merged but
not deployed, and whose owner is outside this repo. Every match played meanwhile was a winner
label lost — the input `fit_flag_swing.py` and the MMR ladder are both waiting on.

The engine's own scoreboard `TeamScore` is in every HLTV demo, and dod-tools decodes it.
`scripts/demo_team_score.py` turns that into ledger rows; `scripts/import_demo_team_score.py`
runs it over a demo directory and imports under producer `hltv-demo`, source
`hltv-demo-team-score-v1`. `ktp-demo-publish.sh` now calls it after each hourly publish for
official (`ktp/`) demos filed in the last three days, guarded so a missing parser, checkout or
credentials file logs and skips rather than failing the publish. `build/dod-tools/fetch.sh`
pins dod-tools v0.10.0 by sha256 for Linux and Windows in the same shape as `build/curl`.

Validated against ktpleague.gg's admin-entered scores: all nine official S10 matches match on
totals, and the one match the site shows per-half (`1789326428-NY1`, 142-12 / 131-13) matches
per-half. Two facts the derivation rests on, both measured rather than assumed: the engine
resets `TeamScore` at match-live but **not** at the half swap, so a half-2 demo reports the
match total and half 2 is derived by differencing per stable roster slot; and that is already
the ledger's own convention (`half-carryover-mismatch` requires half 2 to open at half 1's
final), so rows carry the demo's raw cumulative values with one `baseline` and one `final` per
half and the carryover holds by construction. Captures were tested as a label proxy and
rejected — 8/10 on the test set, every miss a near-even cap count against a decisive score.

Provenance stays honest: the rows never carry the HUD's producer, the per-match ingest
manifest the observations FK requires is written with the demo files as its evidence
(`events_*` = half-1 demo, `metadata_*` = last-half demo — documented in every row's
`raw_event_json`), and a match whose manifest belongs to a different producer is skipped in
both inserts. `tick_seconds` is 0 on this source; it carries the outcome, not a timeline.

Unit tests run on dod-tools JSON fixtures captured from the 28 real demos (86 KB), so the suite
needs neither a demo nor the parser. No migration: the `source` and `producer` columns exist;
`--migrate` applies 023/032 idempotently the way the HUD importer does.
