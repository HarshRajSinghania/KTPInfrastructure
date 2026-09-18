-- ENGINE: mysql (hlstatsx on the data server)
-- KTP Infrastructure Migration 033: admit hltv-demo as a team-score producer.
-- Apply after migration 032. Forward-only and idempotent. Writes no rows.
--
-- Migration 032 named KTPHudObserver as the producer and pinned both CHECK
-- constraints to that single value, while its own header says the column default is
-- dropped "so every writer has to name the producer itself". The demo importer added
-- in #434 is that second writer: scripts/demo_team_score.py sets PRODUCER =
-- "hltv-demo", its generated SQL skips a match whose manifest belongs to another
-- producer, test_import_sql_shape asserts it never writes under the HUD producer, and
-- NEIN-DEPLOY.md item 4 verifies with producer='hltv-demo'.
--
-- Measured on the data server 2026-09-17 running item 4 against main at c8c4af2:
--   ERROR 3819 (HY000) at line 60: Check constraint
--   'chk_team_score_manifest_producer' is violated.
-- The parse half was healthy -- 18 demos, 9 matches, 9 manifests, 36 observations --
-- and nothing was written, because the insert is inside the GET_LOCK transaction.

-- Refuse unless 032 is fully applied: both producer columns present as ascii(32).
SET @m33_obs_producer := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_observations'
 AND COLUMN_NAME='producer' AND DATA_TYPE='varchar' AND CHARACTER_MAXIMUM_LENGTH=32);
SET @m33_man_producer := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_ingest_manifests'
 AND COLUMN_NAME='producer' AND DATA_TYPE='varchar' AND CHARACTER_MAXIMUM_LENGTH=32);
SET @m33_ddl := IF(@m33_obs_producer=1 AND @m33_man_producer=1,'DO 0',
 'SELECT * FROM ERROR_033_team_score_demo_producer_needs_migration_032_applied');
PREPARE m33_stmt FROM @m33_ddl; EXECUTE m33_stmt; DEALLOCATE PREPARE m33_stmt;

-- Widen only if hltv-demo is not already admitted, so a re-run is a no-op. Drop and
-- add in ONE statement: a separate DROP leaves a window accepting any producer.
SET @m33_wide := (SELECT COUNT(*) FROM information_schema.CHECK_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_team_score_producer'
 AND CHECK_CLAUSE LIKE '%hltv-demo%');
SET @m33_ddl := IF(@m33_wide=0,
 'ALTER TABLE ktp_team_score_observations
   DROP CHECK chk_team_score_producer,
   ADD CONSTRAINT chk_team_score_producer
   CHECK (producer IN (_ascii''KTPHudObserver'', _ascii''hltv-demo''))','DO 0');
PREPARE m33_stmt FROM @m33_ddl; EXECUTE m33_stmt; DEALLOCATE PREPARE m33_stmt;

SET @m33_wide := (SELECT COUNT(*) FROM information_schema.CHECK_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_team_score_manifest_producer'
 AND CHECK_CLAUSE LIKE '%hltv-demo%');
SET @m33_ddl := IF(@m33_wide=0,
 'ALTER TABLE ktp_team_score_ingest_manifests
   DROP CHECK chk_team_score_manifest_producer,
   ADD CONSTRAINT chk_team_score_manifest_producer
   CHECK (producer IN (_ascii''KTPHudObserver'', _ascii''hltv-demo''))','DO 0');
PREPARE m33_stmt FROM @m33_ddl; EXECUTE m33_stmt; DEALLOCATE PREPARE m33_stmt;

-- Both constraints must now name both producers, or this migration did nothing.
SET @m33_done := (SELECT COUNT(*) FROM information_schema.CHECK_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE()
 AND CONSTRAINT_NAME IN ('chk_team_score_producer','chk_team_score_manifest_producer')
 AND CHECK_CLAUSE LIKE '%KTPHudObserver%' AND CHECK_CLAUSE LIKE '%hltv-demo%');
SET @m33_ddl := IF(@m33_done=2,'DO 0',
 'SELECT * FROM ERROR_033_team_score_demo_producer_did_not_widen_both_constraints');
PREPARE m33_stmt FROM @m33_ddl; EXECUTE m33_stmt; DEALLOCATE PREPARE m33_stmt;
