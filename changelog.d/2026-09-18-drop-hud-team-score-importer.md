### `scripts`: HUD observer team-score importer removed (2026-09-18)

`import_team_score_events.py` and its CLI tests are gone. The KTPHudObserver
`events.jsonl` path produced 0 production rows across the first 9 S10 official
matches while `import_demo_team_score.py` (HLTV demo TeamScore, `producer =
hltv-demo`) now labels every official half hourly from the data server, so the
second path was code to keep migrations honest against for no rows. The shared
`read_event_files` validator in `team_score_telemetry.py` stays; Lane B's score
fixture and `in_game_result.py` still read observer-format files through it.
Existing `producer = KTPHudObserver` ledger rows are untouched.
