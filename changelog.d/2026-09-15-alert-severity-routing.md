### `scripts`: one place decides an alert's channel, glyph and colour (2026-09-15)

Sixteen producers post Discord embeds and each one carried its own colour constants and its
own channel default. The estate drifted in both. Measured 2026-09-15 across all five game
hosts and the data server, read-only:

- **Five distinct palettes.** `hltv-restart-all.sh` and the host-side restart script render
  raw `65280` / `16711680` / `16750848` — pure green, pure red, pure orange — while
  `ktp-perf-rollup.py`, `ktp-data-server-health.sh`, `crashreporter` and `ktp-systemd-alert`
  render the Discord palette. Two producers disagreed on yellow alone (`16763904` vs
  `15844367`), and `ktp-hltv-liveness.sh` used a third (`15158332`).
- **`#ktp-crashes` (`1497957091107668070`) is seven producers deep** — crashreporter,
  perf-rollup, spike-digest, data-server-health, post-reboot-verify, kernel-reboot, and the
  admin bot's `ops_alerts` cog, which posts through the gateway rather than the relay. Over
  2026-09-09..09-15 the channel's traffic was 22 health posts and 7 spike digests. It is not
  a crash channel; the crashreporter posted **nothing** all week, which is the correct
  outcome and is indistinguishable from the channel being broken.

`scripts/ktp_alert_routing.py` and its shell twin `scripts/ktp-alert-routing.sh` own the two
decisions every producer was making privately: severity → glyph + colour, and severity →
**lane**. Lanes (`page`, `ops-daily`, `ops-weekly`, `community`) are named roles, not ids —
`resolve_channel` reads a lane's id from the environment or `/etc/ktp/discord-relay.conf`
and falls back to whatever the producer already used, so **merging this changes no routing
until an operator maps lanes to ids**. A lane with no mapping and no legacy channel raises
rather than returning a plausible id.

This is not a sixth alerting implementation — it owns no state file, no fail-streak, no
cooldown, and never posts. `OBSERVABILITY_PLAN.md` §2.1 still stands.

**`hltv-restart-all.sh` is silent when it succeeds.** It posted twice a day into two
channels whether or not anything was wrong: 4 posts a day that only ever said "all 24
connected". Now a clean run writes a digest line to `/var/lib/ktp-alerts/ops-daily.jsonl`
and posts nothing; a partial run is 🟠 and a total failure is 🔴, both unchanged in content.
The **first** green after a bad run still posts, as 🟢 — a page needs an end, and a lost
state file is treated as routine rather than manufacturing an all-clear for a page nobody
saw. The `CHANNEL_HLTV_STATUS` / `_EXTERNAL` pair is deliberately left intact: the two ids
resolve to different guilds, so collapsing them would remove one audience's feed entirely.

Colour fixes carried in the same change, each verified against the canon by a test:
`ktp-demo-retention.sh`, `ktp-backup-watchdog.sh`, `ktp-hltv-liveness.sh`,
`ktp-scheduled-kernel-reboot.sh`, `ktp-soak-verify.py`, `precache_audit.py`,
`audit-fleet-drift.py`.

`tests/unit/test_alert_routing.py` holds the line three ways: the Python module and the
shell twin must agree; no file that builds a `channelId` payload may carry an off-canon
colour, with the scan's scope derived from the files themselves so a producer added next
week is covered without anyone listing it; and the silence rules are exercised in both
directions.

⚠️ **Deploy order matters.** `ktp-alert-routing.sh` must land beside the shell producers
before they do — they abort with `exit 3` and a message rather than posting the wrong
colour. See `docs/runbooks/ALERT_ROUTING.md`.

⛔ **Not touched here, on purpose.** `ktp-data-server-health.sh` belongs to PR #397;
`ktp-spike-digest.py` to #393; `monitoring/fleet-health/` to #391;
`ktp-scheduled-restart.sh.example` is the mirror of a host-only canonical that
`scripts/deploy-restart-script.py` refuses to deploy when the two differ by so much as a
comment. Each is described in the runbook for a follow-up.
