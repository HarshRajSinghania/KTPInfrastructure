-- ENGINE: mysql (hlstatsx on the data server)
-- KTP Infrastructure Migration 034: settlement is a HUD-observer invariant, so scope it.
-- Apply after migration 033. Forward-only and idempotent. Writes no rows.
--
-- chk_team_score_settlement demands settlement_seconds >= 30. That is the window the
-- HUD observer waits after a match before finalising, and it is a real guarantee for
-- that producer. A demo is parsed after the fact, so there is no window to wait: the
-- demo importer stages 0, which is the honest value rather than a missing one.
--
-- The constraint becomes producer-conditional instead of being dropped, so the
-- >= 30 guarantee still holds for every KTPHudObserver row.
--
-- Measured on the data server 2026-09-17 running item 4 against main at 7ccb629,
-- with migration 033 applied and both producer constraints widened:
--   ERROR 3819 (HY000) at line 60: Check constraint
--   'chk_team_score_settlement' is violated.
-- All eight check constraints on the two tables were then evaluated against the
-- staged values, and this is the only one the demo importer violates.

-- Refuse unless 033 is applied: the producer constraint must already admit hltv-demo.
SET @m34_ready := (SELECT COUNT(*) FROM information_schema.CHECK_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_team_score_manifest_producer'
 AND CHECK_CLAUSE LIKE '%hltv-demo%');
SET @m34_ddl := IF(@m34_ready=1,'DO 0',
 'SELECT * FROM ERROR_034_team_score_demo_settlement_needs_migration_033_applied');
PREPARE m34_stmt FROM @m34_ddl; EXECUTE m34_stmt; DEALLOCATE PREPARE m34_stmt;

-- Widen only if it is not already producer-conditional, so a re-run is a no-op. Drop
-- and add in ONE statement: a separate DROP leaves a window with no settlement floor.
SET @m34_scoped := (SELECT COUNT(*) FROM information_schema.CHECK_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_team_score_settlement'
 AND CHECK_CLAUSE LIKE '%hltv-demo%');
SET @m34_ddl := IF(@m34_scoped=0,
 'ALTER TABLE ktp_team_score_ingest_manifests
   DROP CHECK chk_team_score_settlement,
   ADD CONSTRAINT chk_team_score_settlement
   CHECK (producer = _ascii''hltv-demo'' OR settlement_seconds >= 30)','DO 0');
PREPARE m34_stmt FROM @m34_ddl; EXECUTE m34_stmt; DEALLOCATE PREPARE m34_stmt;

-- The HUD floor must survive: the clause still has to mention 30.
SET @m34_done := (SELECT COUNT(*) FROM information_schema.CHECK_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_team_score_settlement'
 AND CHECK_CLAUSE LIKE '%hltv-demo%' AND CHECK_CLAUSE LIKE '%30%');
SET @m34_ddl := IF(@m34_done=1,'DO 0',
 'SELECT * FROM ERROR_034_team_score_demo_settlement_lost_the_thirty_second_floor');
PREPARE m34_stmt FROM @m34_ddl; EXECUTE m34_stmt; DEALLOCATE PREPARE m34_stmt;
