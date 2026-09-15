### `lane-b`: the two harness tests that were red on `main` itself (2026-09-15)

Every Lane B run reported `2 failed, 537 passed`, and both failures came from
plain `main` rather than from the branch under test. A permanently-red check
teaches everyone to skim past the channel, so the next real failure reads as
the usual two.

- `test_full_and_corpus_lanes_apply_context_migrations_in_order` counted each
  migration path twice in `lane-b-stats-e2e.yml`. `27ab494` moved the list into
  `DEFAULT_SCHEMA_FILES` and made both `--schema` blocks expand the builder's
  `schema-migrations.txt`, so the count went to zero. Replaced with an assertion
  on the property that commit established — the applied set is derived from the
  daemon ref under test, keeps registered order, and skips only what the ref
  does not carry — so it survives the next legitimate change to the list.
- `analytics-phase-a-contract.sql` carried `half` on `hlstats_Events_Frags` and
  `hlstats_Events_Statsme` but not on `hlstats_Events_Teamkills` or
  `hlstats_Events_Suicides`. `source_coverage.player_halves` probes all four (as
  `migrate_002` writes all four), so it resolved false and the report withheld
  every per-half box score, against a test that expects them. Added the missing
  column and gave the two fixture rows a half.
- Restoring that coverage ran `player_half_fact.sql` against the fixture for the
  first time and it failed on `Unknown column 'e.eventTime'`: the fixture's
  `hlstats_Events_PlayerActions` had no `eventTime` either, and the stored form
  of `{{BREAK_HALF}}` places a cap break by comparing it to the half windows.
  Added the column and put the two cap breaks in different halves, so the
  query's half placement is exercised rather than assumed.
