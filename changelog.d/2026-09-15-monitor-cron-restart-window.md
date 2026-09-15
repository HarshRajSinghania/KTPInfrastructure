### `monitoring`: stop the monitor-cron alert firing on the restart it is measuring (2026-09-15)

`ktp-fleet-health.sh` counted the LinuxGSM monitor cron lines once a minute and posted a
Discord embed the moment the count fell short. `ktp-scheduled-restart.sh` strips exactly
those lines on purpose and puts them back at the end of the nightly restart, so the
sampler was guaranteed to catch the window it created.

Measured on all five hosts from `~/log/scheduled-restart.log`, 2026-09-11..09-15: the cron
is stripped at 03:00:01–03:00:02 and restored at 03:01:06–03:01:13 on Atlanta, Dallas,
Denver and New York — straddling the 03:01:00 cron tick, so each posts a warn at 03:01 and
a clear at 03:02. Chicago strips at 03:00:01 and restores at 03:00:53–03:00:56, inside the
same minute, and is silent only because it runs one fewer instance and finishes first.
Nothing about Chicago's configuration differs: it is checked, its `EXPECTED=4` override is
correct, and a few seconds of extra work would put it in the same channel.

The alert itself is not wrong to exist — the restart holds the cron in a stripped state
with only a durable backup standing between the fleet and a host that reboots with nothing
to lift it. What was wrong is that the nightly firing and the real fault were the same
message, and the real one was distinguishable only by a clear that never arrived.

- The gate now reads `ktp-scheduled-restart.sh`'s own `monitor-cron.bak`, which that script
  writes before the strip and removes from its `EXIT` trap. A short count with a fresh
  sentinel is a restart in flight and is silent; a short count with a stale or absent one
  is a restart that never re-armed, and alerts. The question asked is whether the work got
  done, not what the crontab held at one arbitrary second.
- Two config keys, `RESTART_STATE_DIR` and `RESTART_GRACE_MINUTES`. The first must match
  that script's `CRON_STATE_DIR`; both default to `${KTP_STATE_DIR:-$HOME/.ktp}`.
- An unreadable sentinel, and a sentinel dated in the future, both alert. A guard that
  cannot read its own evidence must fire rather than suppress.
- `CRON_STATE` and `LAST_MONITOR_CRONS` were already written to the state file and are now
  documented in the header and README.
- `tests/unit/test_fleet_health_monitor_cron_gate.py` drives the gate extracted from the
  shipped script between `# >>> ktp-monitor-cron-gate` markers.

⚠️ Deploy is a file copy to `/home/dodserver/ktp-fleet-health.sh` on all five hosts; cron
re-executes the script every minute, so nothing needs restarting. Until it lands, the
nightly eight posts continue.
