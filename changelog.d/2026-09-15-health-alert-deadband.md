### `monitoring`: stop the disk alerts announcing recoveries that never happened (2026-09-15)

`ktp-data-server-health.sh` bucketed the measured value **into** the alert key —
`disk-growth:/=3GiB/day+`. The set comparison that decides what to post treats a changed
key as one recovery plus one new failure, so a rate drifting from 4 to 6 GiB/day produced
"✅ recovered `disk-growth:/=3GiB/day+`" alongside "⚠️ `disk-growth:/=5GiB/day+`" while the
condition had not stopped being true for a moment.

Measured on the data server over 2026-04-20..2026-09-15: **14 of the 32 disk-growth alerts
were that flip**, against 8 genuine fires and 10 genuine clears. `/` is 262G of 493G, 56%,
206G free — never a capacity problem, only a channel teaching its readers to ignore it.

- The key is now constant while the condition holds (`disk-growth:/`, `disk-usage:/`,
  `disk-inodes:/`). The magnitude moved to the alert body, where changing it costs nothing.
- Each threshold gained a clear level strictly below its warn level (`DISK_PCT_CLEAR`,
  `DISK_GROWTH_CLEAR_GIB`). Inside the band the previous verdict is held, so a value parked
  on a threshold cannot oscillate. Firing from cold and clearing on a real drop are
  unchanged, and an item that never fired is never latched.
- The previous state file is read at the top of the run rather than after the checks,
  because the latch needs it.
- `bucket5` and `bucket_gib` are gone; nothing else used them.

⚠️ **One migration alert on the first run after install.** The state file still holds the
old bucketed keys, which read as recovered while the new bare keys read as new_down. It is
one post, on one run.
