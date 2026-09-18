### `monitoring`, `lane-b`: hit registration is measured on every 12-man and every full lane run (2026-09-18)

The hitreg investigation (coordination `infra-hitreg-diagnostics`, closed 2026-09-18) ended on
one number: of the shot rows where the server's own trace hit a live enemy cleanly, 99.9% have
a `ktp_damage_events` row for the same attacker/victim within 300 ms (12,204 real hits, every
half 99.2–100%). Nothing read that number once the investigation closed — a regression in the
plugin, the daemon or the engine would have looked exactly like today until someone re-ran the
analysis by hand, which is the shape of every incident in `ALERT_COVERAGE.md`.

- **`ktp-data-server-health.sh` gains `hitreg-reg`.** Once per run it scores every finished
  (match, half) that carries target state and has no row in `ktp_hitreg_quality` (KTPHLStatsX
  migration 036 — **apply it before deploying this script**, or the item reads
  `hitreg-reg=query-failed`), capped at 25 halves so a backlog is bounded; then latches on
  misses per thousand over a trailing 48 h — warn at 10 (1.0%), clear at 5, floor 300 clean
  hits. Per-thousand rather than percent because the latch compares integers and the band that
  matters is 99.0–99.9%. `hitreg-reg=stale` fires when 12-mans finished in the last 7 days and
  none produced a scorable half: shot detail off, a build that stopped emitting `tgt_*`, or the
  daemon dropping them must read as *unmeasured*, never as clean.
  `tests/unit/test_health_hitreg.py` extracts the reducer and the latch by marker.
- **Lane B gains `check_hit_registration`**, the same predicate on the synthetic match, floor
  98% with 50 clean hits (a bot half is ~100 hits, so the investigation's 0.1% attribution
  residual is a whole percent there; the check tolerates one such row and still fails on a
  step). `not_exercised` when the run carries no target state.
- **The full lane now runs with `shot_detail` = 1 by default.** Production runs shot detail on
  12-mans (bitmask 4, kept by the 2026-09-18 ruling) and the lane's match type is not a 12-man,
  so the lane opts in explicitly to exercise the same fields. With `--require-complete-coverage`
  the old default would have made every full run INCOMPLETE on the new check.
- Registers `sql/migrate_036_hitreg_quality.sql` at the tail of `DEFAULT_SCHEMA_FILES` — merge
  this before the KTPHLStatsX migration lands, as always.
