# ktp-fleet-health

Per-host alerter that fires a Discord embed when `pgrep -c hlds_linux` falls
below the expected instance count for N consecutive minutes. Single post per
state transition (one DEGRADED on decline, one RECOVERED on return), silent
when healthy.

Designed for the case where the LinuxGSM monitor cron either fails to
restart a crashed instance or restarts it but the process exits again
immediately — without this alerter, the host can run degraded for hours
before anyone notices.

Since 2026-09-18 it also watches the host's disks — the filesystem holding
`/home/dodserver` and `/` by default — for usage or inode percent crossing
75%, clearing at 72%, one post per transition. These are the hosts that write
demos, HLTV recordings and logs *during* a match, and until then nothing
checked them; the data server has watched its own disks since May.

## Files

| File | Purpose |
|------|---------|
| `ktp-fleet-health.sh` | The alerter itself. Runs every minute via cron under `dodserver`. |
| `fleet-health.conf.example` | Template for `/etc/ktp/fleet-health.conf`. Webhook + topology overrides. |

## Install

The provisioning flow (`provision-gameserver.sh`) copies the script to
`/home/dodserver/ktp-fleet-health.sh` and seeds the cron entry. For an
existing host or manual install:

```bash
sudo cp ktp-fleet-health.sh /home/dodserver/
sudo chown dodserver:dodserver /home/dodserver/ktp-fleet-health.sh
sudo chmod 755 /home/dodserver/ktp-fleet-health.sh

sudo cp fleet-health.conf.example /etc/ktp/fleet-health.conf
sudo chown root:dodserver /etc/ktp/fleet-health.conf
sudo chmod 640 /etc/ktp/fleet-health.conf
sudo $EDITOR /etc/ktp/fleet-health.conf   # set WEBHOOK_URL, MENTION_USER_ID

# crontab as dodserver
(crontab -u dodserver -l 2>/dev/null; echo '* * * * * /home/dodserver/ktp-fleet-health.sh >/dev/null 2>&1') \
    | crontab -u dodserver -
```

## Config layering

Three sources, later overrides earlier:

1. **Script defaults** — safe-by-default (`WEBHOOK_URL=""` ⇒ no Discord posts).
2. **`/etc/ktp/fleet-health.conf`** — system-wide config. Operator-managed.
3. **`~dodserver/.ktp-fleet-health/config.sh`** — per-host fine-tuning.

If `WEBHOOK_URL` is empty after all sources load, the alerter still tracks
state in `~/.ktp-fleet-health/state` but skips the network call. Useful for
LAN deployments where Discord may not be reachable or wanted.

## Topology config

`BASE_PORT` + `NUM_INSTANCES` drive the per-port enumeration in the DEGRADED
embed body. Defaults assume the standard KTP 5-instance host (`27015–27019`);
LAN events on a different port range only need to set those two keys.

## The monitor-cron gate

A second, independent check asks whether LinuxGSM's per-instance monitor cron is
still armed — the thing that would bring a crashed instance back. Counting those
lines on its own is useless, because `ktp-scheduled-restart.sh` strips exactly
those lines for the length of the nightly restart and puts them back at the end.
A per-minute sampler therefore posts a warn and a clear on every host every
night, and the one case that matters — a restart that died mid-flight and left
the cron off until someone notices — arrives as an identical warn.

So the gate reads the restart's own sentinel instead. `ktp-scheduled-restart.sh`
writes `$RESTART_STATE_DIR/monitor-cron.bak` before it strips and removes it
from its `EXIT` trap, so the file exists for precisely the stripped window:

| monitor cron | sentinel | verdict |
|---|---|---|
| complete | — | quiet, or a `re-armed` clear if the previous run warned |
| short | fresh (< `RESTART_GRACE_MINUTES`) | suppressed — restart in flight |
| short | stale or absent | **warn** — nothing is going to re-arm it |

⚠️ An unreadable sentinel is treated as ancient, so the gate alerts rather than
suppresses. A guard that cannot read its own evidence must fire.

⚠️ `RESTART_STATE_DIR` must match `ktp-scheduled-restart.sh`'s `CRON_STATE_DIR`.
Both default to `${KTP_STATE_DIR:-$HOME/.ktp}`; overriding one and not the other
restores the nightly alert with nothing to say why.

## State

`~dodserver/.ktp-fleet-health/state` is a tiny shell-source file containing:

- `CONSECUTIVE_BAD` — consecutive minutes below `EXPECTED`
- `ALERT_STATE` — `healthy` | `unhealthy`
- `LAST_RUN` — epoch seconds
- `LAST_RUNNING` — last observed `pgrep -c hlds_linux`
- `CRON_STATE` — `armed` | `incomplete`, the monitor-cron gate's latch
- `LAST_MONITOR_CRONS` — last counted monitor cron lines

Delete the state file to reset (the next run will recreate it).
