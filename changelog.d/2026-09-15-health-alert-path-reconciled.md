### `monitoring`: the data-server health check could only speak when a failure and a recovery landed in the same hour (2026-09-15)

`ktp-data-server-health.sh` built its report with
`printf '%s\n' "${arr[@]}" | grep -v '^$' | paste -sd, -`. An **empty** array makes `printf`
emit one blank line, `grep` match nothing and exit 1, and under the script's own
`set -e -o pipefail` that assignment ends the run — before the `TRANSITIONS` log line,
before the Discord POST, and before the state file is written. Since #207 added those two
lines on 2026-08-31 the check has therefore alerted **only** on runs that carried at least
one new failure *and* at least one recovery.

Measured on the data server: `/var/log/ktp-data-server-health.log` (2026-09-01 → 09-15)
holds **0** one-sided transitions out of 42. `.log.1`, the month before the regression,
holds **257** out of 283. A first mysql failure, a wedged `hltv-demo-renamer`, a stopped
`ktp-render-banlist.timer` — each arriving alone, as they do — produced no alert anywhere.
The state file was left stale by the same exit, so the next alert's "All currently down"
footer described the past.

- `join_keys` replaces the pipeline at all three sites. An empty array yields an empty
  string and exit 0; an empty element now renders as a gap rather than being dropped,
  which the old spelling only did as a side effect.
- A control test asserts the shipped spelling **would** have died, and a source scan keeps
  the idiom from returning.

⚠️ **This had to land with the #388 deadband, not after it.** The oscillating `disk-growth`
key was supplying the non-empty recovery side that kept the script alive. Removing the
oscillation makes pure-failure runs the norm, and every one of them would have aborted.

### `monitoring`: stop reporting HLTV proxies that are mid-restart, and start seeing the ones that crash-loop (2026-09-15)

The hourly cron fired at `:00`, the same minute as `hltv-restart.timer` (`OnCalendar`
03:00 and 11:00 ET), so it sampled the 24 proxies while the restart it schedules was
walking through them and reported whichever one it caught as `deactivating`, plus
`hltv-instance-count=23/24`. **252 of the 325 alerts logged over 2026-04-20..09-15 fall in
hours 03, 04, 11 and 12**; in the last seven days, 13 of 20 name an `hltv@` port and none
was a real fault. The fleet reads 24/24 active on any sample taken between restarts, which
is why the admin bot's 09:08 ET digest and this check disagreed every day.

- `settled_state` re-reads a unit reporting a transitional systemd state
  (`activating`/`deactivating`/`reloading`/`refreshing`) after `SETTLE_SECONDS` (20,
  longer than `hltv@.service`'s `RestartSec=10`). It covers the general service sweep as
  well as the proxies. A unit still not active then is genuinely stuck and still alerts;
  a terminal state is never re-sampled, so nothing already down is given a grace period.
  One sleep per run, not one per unit — it answers in `$SETTLED` rather than on stdout,
  because a `$(...)` caller would discard the latch and 24 proxies would each pay the delay.
- The cron moved to `:17`, so a sample never coincides with a restart we schedule
  ourselves, and off the top-of-hour pile-up. The settle re-read is the real guard; the
  offset is defence in depth for the restarts we control.
- **New leg — `hltv@<port>=crash-looping`.** `Restart=always` with `RestartSec=10` outruns
  systemd's default start-rate limit, so a crash-looping proxy never reaches `failed`: it
  flaps active↔activating and `is-active` reads `active` most of the time. That is the same
  shape as the `hltv-demo-renamer` wedge of 2026-08-25, where unit state answered a question
  it could not see. `NRestarts` sees it. It counts automatic restarts only and an explicit
  restart resets it, so the 03:00/11:00 pass re-arms it twice a day.
- `hltv-instance-count=23/24` put the measured count inside the alert key, so 23/24 → 22/24
  read to the set comparison as one recovery plus one new failure — the defect #388
  deadbanded out of the disk keys, in the one place its diff did not reach. The key is now
  `hltv-instance-coverage` and the count rides in the alert body via #388's `detail` map.

⚠️ **Installing this is a cron change as well as a script change** — copy
`scripts/ktp-data-server-health.cron` to `/etc/cron.d/ktp-data-server-health`, or the
script fix lands while the sample stays on the restart minute.

⚠️ **Expect one migration alert on the first run**, if `/var/lib/ktp-data-server-health.json`
still holds old-format keys: `hltv-instance-count=…` and #388's bucketed disk keys read as
recovered while the new bare keys read as new. One post, one run.
