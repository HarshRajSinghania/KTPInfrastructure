### `monitoring`: the data-server health check can now say it stopped running (2026-09-17)

`#397` removed the line the check died on. It did not give the check a way to say it had
died — and the reason the 2026-08-31 → 09-15 silence lasted fifteen days is not that the
abort was subtle, it is that **an aborted run and a healthy estate produce the same
output: nothing.** `ALERT_COVERAGE.md` hole 4 names this exactly: *"nothing watches the
hourly health check … a watcher that stops looks exactly like a quiet estate."*
`ktp-data-server-health` is a cron, so it has no `OnFailure=`, and `MAILTO=''` discards
cron's own notification. Nothing on the box observes its exit status at all.

- **A run ledger.** Every run records how it ended in `/var/lib/ktp-data-server-health.run`
  (`RUN_LEDGER`) from a single `EXIT` trap, so the clean `exit 0`, an errexit death and a
  signal are all covered. `set -E` and an `ERR` trap carry the line number, so the record
  names *where* it died rather than only that it did.
- **The next completed run reports it**, as an ordinary `down` item through the existing
  transition machinery: `health-check-aborted` (with the line and the length of the
  silence) or `health-check-missed-runs` when no run completed for `RUN_GAP_SEC` — three
  hourly intervals of slack. At most one item: a run that died is also a run that did not
  complete, and two keys for one fault double-count it in the `comm` set diff.
- **No number in either key**, #388's lesson in the place it would be easiest to repeat.
  The hours ride in `detail`, which is alert body text only.
- **The ledger is plain `key=value` written with `printf`, never `jq`** — it has to survive
  the failures that kill the rest of the script, and `jq` is what `save_state` and the
  Discord payload are both built on.
- **An absent ledger is silent.** A fresh install is not a missed run, and one false alert
  on every new box is how a reader learns to skip the line.

🔴 **And a latch this found, which had nothing to do with the ledger.** `save_state` ended
with `health_state_document … > "$STATE_FILE"`. The redirect **truncates before jq runs**,
so a jq that failed there left a zero-byte state file — and `jq -c . < empty` prints
nothing and exits **0**, so the `|| echo '{}'` fallback on the read never fired and the
next run aborted on `--argjson prev ""`. And the next. **One bad hour disabled the check
permanently**, until someone deleted a file nobody knew to look at. It is now written to
`$STATE_FILE.tmp` and moved into place, and an empty read falls back to `{}`. Reproduced
against `origin/main` and against the fix, on the data server with real jq: `origin/main`
exits 2 on both runs, the fix exits 0 on both.

⚠️ **What this does NOT cover:** a check that stops for good — cron removed, box down —
cannot report itself, and nothing here changes that. That needs the external detector in
`afraznein/KTPAdminBot`#21, which is **merged and not deployed**; its stale-state field is
the complement to this, not a duplicate of it. A failed Discord POST is also deliberately
not an abort: the run completed, the delivery did not, and `save_state` is already skipped
on that path so the transition re-alerts next hour.

⚠️ **`STATE_FILE` is now overridable** (`${STATE_FILE:-…}`), which is what lets the tests
exercise the real script instead of a sed'd copy. The cron sets only `SHELL`, `PATH` and
`MAILTO`, so nothing on the box changes.
