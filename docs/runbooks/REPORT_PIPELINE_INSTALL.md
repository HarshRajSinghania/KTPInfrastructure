# Report pipeline: first install on the data server

Match reports are built on `neindataatl` and pushed to the website. Nothing in
this pipeline existed on the box before Season 10; this runbook is the install.

Three commands run in order on a timer: `generate` turns finished matches in
`hlstatsx` into report rows, `aggregate` rolls those into season totals, and
`report_sync` pushes both to Supabase for ktpleague.gg to read.

Everything below needs root. It is a one-time setup.

## 1. Service account

Not `krodssh`. That is an interactive login for analysis and has never run
automation. The pipeline gets its own identity: SELECT on the tables it reads,
and INSERT on the two it owns.

Confirm the auth plugin first, because the `CREATE USER` below depends on it:

```bash
sudo mysql -N -e "SELECT user, host, plugin FROM mysql.user WHERE user='krodssh';"
```

Expect `auth_socket`. Anything else and the `CREATE USER` needs a different
clause — stop and ask rather than improvising one.

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin ktpreports
sudo mysql -e "CREATE USER 'ktpreports'@'localhost' IDENTIFIED WITH auth_socket AS 'ktpreports';"
```

The Linux user and the MySQL user must have the same name. `auth_socket`
authenticates by peer credential, so a mismatch fails at connect time with a
confusing access-denied rather than anything that names the cause.

Grant the tables the pipeline names, and nothing else. Don't copy `krodssh`'s
grants: they also cover `hlstatsx_lan` and `ktp_lan`, which the pipeline never
reads, and `SHOW GRANTS` prints lines with no trailing `;`, so piping them back
into `mysql` fails at line 2.

The list was derived on 2026-09-13 from `f498463`. It is every table named in
the import closure of `scripts/report_service.py` and `scripts/report_sync.py`,
plus the `sql/analytics/*.sql` files `match_analytics` loads from disk. A grep of
the Python alone misses most of the `hlstats_Events_*` tables. The pipeline only
appends — a new report or aggregate is a new revision row — so there is no
UPDATE or DELETE.

```bash
sudo mysql <<'SQL'
GRANT SELECT ON hlstatsx.hlstats_Actions                    TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.hlstats_Events_Frags               TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.hlstats_Events_PlayerActions       TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.hlstats_Events_PlayerPlayerActions TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.hlstats_Events_Statsme             TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.hlstats_Events_Statsme2            TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.hlstats_Events_Suicides            TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.hlstats_Events_Teamkills           TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_assist_events                  TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_capture_health                 TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_capture_manifests              TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_damage_events                  TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_flag_captures                  TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_flag_positions                 TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_flag_state_events              TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_grenade_entity_events          TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_life_events                    TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_match_players                  TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_match_stats                    TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_matches                        TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_objective_attempt_events       TO 'ktpreports'@'localhost';
GRANT SELECT ON hlstatsx.ktp_position_samples               TO 'ktpreports'@'localhost';
GRANT SELECT, INSERT ON hlstatsx.ktp_match_reports          TO 'ktpreports'@'localhost';
GRANT SELECT, INSERT ON hlstatsx.ktp_web_season_aggregates  TO 'ktpreports'@'localhost';
SQL
```

When the code starts reading a new table, add it here and grant it. Nothing
else will tell you, which is the point of the next check.

### Check what the account can see

`source_capabilities()` decides which optional sources to use by asking
`information_schema` whether each table exists, and MySQL hides tables the
account holds no privilege on. A missing grant therefore looks like a missing
table: that source is skipped, the report comes out WARN, and it still
publishes. No error anywhere.

Check as the account, not as root:

```bash
sudo -u ktpreports mysql --user=ktpreports hlstatsx -N -e \
  "SELECT CURRENT_USER(), COUNT(*) FROM information_schema.tables WHERE table_schema='hlstatsx'"
```

Expect `ktpreports@localhost` and one table per line of the grant block (24 at
`f498463`). Pass `--user` every time: without it `sudo -u` sends `root` and gets
`ERROR 1698`, and the account has no home, so there is no `.my.cnf` to fall back
on. If `CURRENT_USER()` names anyone else, you measured the wrong account.

### Smoke-test the writes without leaving a row

The account cannot DELETE, so an insert-then-delete smoke test fails halfway
and strands a permanent `smoke-test` row in an append-only table. Both tables
are InnoDB, so roll it back instead:

```sql
-- sudo -u ktpreports mysql --user=ktpreports hlstatsx
SELECT COUNT(*) FROM ktp_match_reports;
START TRANSACTION;
INSERT INTO ktp_match_reports
  (match_id, schema_version, revision, generated_at, quality_status, publishable, report_sha256, report)
  VALUES ('smoke-test', 0, 1, NOW(), 'SMOKE', 0, REPEAT('0', 64), '{}');
SELECT ROW_COUNT();   -- 1
ROLLBACK;
SELECT COUNT(*) FROM ktp_match_reports;   -- same as before

SELECT COUNT(*) FROM ktp_web_season_aggregates;
START TRANSACTION;
INSERT INTO ktp_web_season_aggregates
  (kind, revision, generated_at, source_report_count, report_schema_version, payload_sha256, payload)
  VALUES ('smoke-test', 1, NOW(), 0, 0, REPEAT('0', 64), '{}');
SELECT ROW_COUNT();   -- 1
ROLLBACK;
SELECT COUNT(*) FROM ktp_web_season_aggregates;   -- same as before
```

If either count moved, a row was committed: stop and have root remove it.

## 2. Deployment checkout

A real checkout owned by `ktpreports`. Not the Actions runner's work directory
under `/opt/ktp-tier2-runner`, which is CI scratch and gets wiped. Not
`/opt/ktp-infra` either — that is a separate, deliberately stale copy that the
weekly fleet audit runs from and that never pulls.

```bash
sudo install -d -o ktpreports -g ktpreports /opt/ktp-reports
sudo -u ktpreports git clone https://github.com/afraznein/KTPInfrastructure.git \
  /opt/ktp-reports/KTPInfrastructure
```

## 3. The Supabase secret

```bash
sudo install -d -m 0750 -o root -g ktpreports /etc/ktp
sudo install -m 0640 -o root -g ktpreports /dev/null /etc/ktp/reports.env
```

Then write into `/etc/ktp/reports.env`:

```
KTP_SUPABASE_URL=https://yxpjfenpnwksvvquqlde.supabase.co
KTP_SUPABASE_SECRET_KEY=<service-role secret>
```

This is the **service-role** secret, not the publishable key. The publishable
key is read-only and public by design; this job writes. It must never reach a
game server or the website repo.

## 4. Install the units

```bash
sudo install -m 0644 /opt/ktp-reports/KTPInfrastructure/systemd/ktp-reports.service /etc/systemd/system/
sudo install -m 0644 /opt/ktp-reports/KTPInfrastructure/systemd/ktp-reports.timer   /etc/systemd/system/
sudo systemctl daemon-reload
```

Dry-run the sync before arming anything. This is the step that catches a
misplaced or unreadable secret, and it writes nothing:

```bash
sudo -u ktpreports env $(sudo cat /etc/ktp/reports.env | xargs) \
  python3 -m scripts.report_sync --since 2026-09-13 --dry-run
```

Run it from `/opt/ktp-reports/KTPInfrastructure`. It should report what it
would push and exit 0. `missing env KTP_SUPABASE_URL` means the file is not
readable by the account — check group ownership and the `0640` mode.

These work because `report_service.py` passes `--user` explicitly. It did not
always: plain `mysql` sends `root` even when the process really is uid
`ktpreports` and `$USER`/`$LOGNAME` are already `ktpreports` — measured,
including with `--no-defaults` and with the environment unset. The server then
refuses with `ERROR 1698 (28000): Access denied for user 'root'@'localhost'`,
which reads exactly like a broken pipeline rather than a wrong invocation.

An earlier version of this page blamed `sudo -u` for leaking `$USER` and told
you to unset it. **That was wrong** — those variables are already correct
under `sudo`, and unsetting them changes nothing. The fix belongs in the client
invocation, not the shell.

Then arm it:

```bash
sudo systemctl enable --now ktp-reports.timer
systemctl list-timers ktp-reports.timer
```

## 5. Confirm after the first real match

Three counts, walking the chain in order. The first that reads zero says where
it stopped.

```sql
SELECT COUNT(*) FROM ktp_match_reports;          -- generate ran
SELECT COUNT(*) FROM ktp_web_season_aggregates;  -- aggregate ran
```

Then open <https://ktpleague.gg/stats/matches>. The empty-state text should be
gone and the match listed.

| Symptom | Cause |
|---|---|
| 0 reports | Timer never fired, or the match is before the `--since` floor |
| Reports but 0 aggregates | `aggregate` failed; read the second ExecStart in the journal |
| Both built, site empty | `report_sync` did not push. Almost always the secret |
| A rating renders negative | Checkout is behind `main`; the display floor is 50 |

```bash
journalctl -u ktp-reports.service -n 50 --no-pager
```

### Use the verifier, not a row count

`SELECT COUNT(*) FROM ktp_match_reports` returns 0 when the timer never ran,
when it ran and correctly found nothing, and when it ran and failed — and the
site answers 200 in all three. The verifier tells those apart and exits
non-zero when the pipeline is broken:

```bash
cd /opt/ktp-reports/KTPInfrastructure
sudo -u ktpreports python3 -m scripts.verify_report_pipeline --since 2026-09-13 --site https://ktpleague.gg
```

It reads `/var/log/ktp-report-service.log`, which the unit writes via
`StandardOutput=append:`. If that file is absent the `RAN` check fails, which
is the correct answer to "did it run" rather than a fault in the verifier.

## The `--since` floor

`ktp-reports.service` pins `--since 2026-09-13`, and every step also requires an
official match type: `.ktp` (0) or `.ktpOT` (4), the set KTPMatchHandler's
`is_official_match_type()` uses, defined once in `scripts/report_scope.py`.
Scrims, 12mans, drafts and untyped (NULL) matches are never reported, whatever
their date. Widening either publishes practice matches as league results.

All three steps apply the same scope: an official-type half that started on or
after the floor. `aggregate` and `report_sync` refuse to start without a floor.
`generate` only uses it to discover matches; a report persisted any other way (an
explicit match id, a manual test run by someone with INSERT on
`ktp_match_reports`) is still held back by `aggregate`, which will not pool it
into the season, and by `report_sync`, which will not push it. They print
`held back by --since …: N` and `held back by match_type …: N` when they skip
one, and `generate <match_id>` warns when an explicit id is out of scope. A
report whose match has no `ktp_matches` row is held back too, since neither its
date nor its type can be proved.

When you move the floor for a new season, move it on all three `ExecStart` lines.

Nothing is lost by a late install. `generate` reads `hlstatsx` retroactively, so
matches played before the timer existed still publish on the first run.
