# Official team-score telemetry v1

This slice retains and projects the authoritative in-game team score emitted by
the HUD observer as `source: "engine-team-score-v1"`. It is deliberately
separate from player points, capture credits, KTPR, and the experimental
accumulation models.

## Provenance

These tables hold the game engine's own team score, relayed by the HUD observer
(KTPHudObserver). They are **not** the captain-reported league score. That one
is `ktp.match.home_score` / `away_score` in the website's database, and nothing
copies between the two.

Migration 032 records this on the rows themselves. `ktp_team_score_observations`
and `ktp_team_score_ingest_manifests` carry a `producer` column that is always
`KTPHudObserver`, pinned by a CHECK constraint, and both table comments say the
same. The column has no default, so the importer writes it explicitly and a
writer that leaves it out fails.

## Authority and ordering

- Only official-v1 `team_score` rows are eligible.
- `tick` is fractional `get_gametime()` seconds since the current map started.
  It is stored as `DECIMAL(20,9)` without a tick-rate conversion; no
  `engine_tick` is invented.
- Retained order is `(match_id, half, tick_seconds, event_sequence)`. JSONL is
  HTTP arrival order and may be out of order during the observer's bounded
  settlement window.
- Every row contains Allies and Axis scores plus their opaque stable match-team
  slots. Regulation side swaps and explicit OT mappings are producer facts.
- `ktp_match_end` is comparison-only quality evidence. It never overwrites the
  last valid final `team_score` row.

## Local migration and import

Apply `sql/migrate_023_team_score_observations.sql`, then
`sql/migrate_032_team_score_producer.sql`, with the normal local MySQL/MariaDB
migration account. Both are forward-only and idempotent. 023 creates a
closed-file ingestion-manifest ledger, an append-only observation ledger, and a
separate conflict-audit ledger. 032 adds the `producer` column and its CHECK to
the observation and manifest ledgers and rewrites their table comments.

Reapplying migration 023 verifies the exact table/column/collation/unique-index
contract, repairs only compatible missing named indexes, and fails on partial or
incompatible pre-existing schema. It accepts the schema both before and after
032, so it stays safe to re-run in either state. 032 refuses to run unless the
tables have exactly the migration-023 shape (optionally with a partial 032 it
can finish), and it verifies its own result before returning.

The order is always 023 then 032:

| Database | What to apply |
|---|---|
| Fresh (LAN, test) | 023, then 032. `--migrate` does both, in that order. |
| Production `hlstatsx` (023 already applied) | 032 only, through the migration queue. |
| Re-run, once 023 is in place | Either file, any number of times. |

If a table already holds rows when 032 runs, the column default backfills them
with `KTPHudObserver` before the default is dropped.

### HUD observer import: retired (2026-09-18)

`scripts/import_team_score_events.py` (the `events.jsonl` + `metadata.json` path
written by KTPHudObserver) is removed. It produced 0 production rows across the
first 9 S10 official matches; HLTV demos cover every official match instead.
Rows now come from `scripts/import_demo_team_score.py` (`producer = hltv-demo`,
migrations 033/034), run hourly by the `ktp-demo-publish.sh` labels hook on the
data server. The `read_event_files` validator in `team_score_telemetry.py` stays:
the Lane B e2e fixture and `in_game_result.py` still read observer-format files.

`ktp_team_score_observations` rows with `producer = KTPHudObserver` remain valid
ledger rows; nothing here rewrites or purges them.

## Post-match projection

After ingestion settlement and match finality:

```bash
python3 scripts/project_team_score.py \
  --defaults-extra-file /etc/ktp/team-score-client.cnf \
  --database hlstatsx_lan \
  --match-id MATCH_ID \
  --output-dir build/objective-score/MATCH_ID
```

The output directory contains:

- `objective-score-timeline.json`: canonical key-sorted JSON containing only
  neutral `team-1` / `team-2` labels, half-relative seconds, both scores,
  observation kinds, and quality metadata.
- `objective-score-release.json`: deterministic release id, SHA-256, byte
  length, immutable marker, and draft publication state. A correction produces
  a new digest/release; prior published bytes are not mutated.
- `objective-score-private-release.json`: internal match selector, file and
  manifest digests, lifecycle/finality context, and objective digest used for
  the later analytics join. This file is private and is never a Pages/report
  artifact.

Missing boundaries, score regression, unknown mapping, carryover mismatch,
source-time regression, sequence ties, and duplicate-order conflicts produce an
explicit unavailable projection with no points. A sequence gap, late recovery,
or match-end disagreement produces a partial projection with a quality flag.
Multi-point jumps are retained as the single observed change.

**Do not build on `project_official_score` as it stands: every real observer
stream comes back unavailable.** It reads the 0/0 dip just after a half opens as
a score movement rather than as the not-yet-restored carry, and it compares
`ktp_match_end` through the terminal half's side slots when that row states the
total in half 1's. The match report therefore does not use it; it reads the same
stream through `scripts/in_game_result.py`, whose docstring carries both rules.

The automated Lane B report join validates the private selected match, map,
objective digest, and exact normalized-analytics facts digest, then strips the
entire private binding. Only the strict neutral DTO and its SHA-256 reach JSON,
Markdown, HTML, verification, manifests, or Pages outputs. The supported secondary
`match_report_bundle.py` CLI applies the same rule: `--objective-score-json`
must be paired with `--objective-score-private-release`; it never accepts a
bare public DTO as sufficient join authority. A score-enabled Lane B run
uses the repository-owned paired observer fixture and requires an available
projection; any lane without explicit score collection publishes unavailable
with `incomplete-stream`. The Denver fixtures predate this stream and remain
explicitly unavailable--no score is inferred from them.

## Retention and rollout boundary

The scheduled match retention allowlist includes all four score ledgers. Scrim,
12man, and `-TEST` match rows therefore follow the existing 14-day purge;
competitive, draft, and explicit OT classifications remain retained under the
existing policy.

This change supplies migration, one-shot import, settlement/finality validation,
projection, and test/report artifacts. It does not install a service, deploy a
production UI, tail a live file, or alter existing authorization, health, and
diagnostic gates.
