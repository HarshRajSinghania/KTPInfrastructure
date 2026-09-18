-- ENGINE: mysql (hlstatsx on the data server)
-- KTP Infrastructure Migration 032: name KTPHudObserver as the producer of the team-score ledger.
-- Apply after migration 023. Forward-only and idempotent. Any existing row is backfilled
-- with KTPHudObserver by the column default, and the default is then dropped so every
-- writer has to name the producer itself.

-- Refuse anything other than the migration-023 shape, with or without a partial 032.
SET @m32_obs_named := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_observations'
 AND COLUMN_NAME IN ('id','match_id','map_name','match_type','half','tick_seconds',
 'event_sequence','observed_at','allies_score','axis_score','allies_team_id',
 'axis_team_id','source_server','source','source_version','observation_kind',
 'retention_class','manifest_content_sha256','raw_event_json','raw_event_sha256',
 'source_file_sha256','source_path_sha256','source_line_number','ingested_at'));
SET @m32_obs_total := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_observations');
SET @m32_obs_producer := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_observations'
 AND COLUMN_NAME='producer');
SET @m32_obs_producer_bad := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_observations'
 AND COLUMN_NAME='producer' AND NOT(DATA_TYPE='varchar' AND CHARACTER_MAXIMUM_LENGTH=32
 AND COLLATION_NAME='ascii_bin' AND IS_NULLABLE='NO'
 AND (COLUMN_DEFAULT IS NULL OR BINARY COLUMN_DEFAULT='KTPHudObserver')));

SET @m32_man_named := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_ingest_manifests'
 AND COLUMN_NAME IN ('match_id','map_name','match_type','source_server','observer_started_at',
 'observer_ended_at','terminal_half','event_count','official_row_count',
 'retained_row_count','lifecycle_complete','settlement_seconds','events_file_sha256',
 'metadata_file_sha256','events_path_sha256','metadata_path_sha256',
 'manifest_content_sha256','match_end_allies_score','match_end_axis_score',
 'retention_class','ingested_at'));
SET @m32_man_total := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_ingest_manifests');
SET @m32_man_producer := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_ingest_manifests'
 AND COLUMN_NAME='producer');
SET @m32_man_producer_bad := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_ingest_manifests'
 AND COLUMN_NAME='producer' AND NOT(DATA_TYPE='varchar' AND CHARACTER_MAXIMUM_LENGTH=32
 AND COLLATION_NAME='ascii_bin' AND IS_NULLABLE='NO'
 AND (COLUMN_DEFAULT IS NULL OR BINARY COLUMN_DEFAULT='KTPHudObserver')));

SET @m32_misplaced_checks := (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_TYPE='CHECK' AND (
  (CONSTRAINT_NAME='chk_team_score_producer' AND TABLE_NAME<>'ktp_team_score_observations') OR
  (CONSTRAINT_NAME='chk_team_score_manifest_producer' AND TABLE_NAME<>'ktp_team_score_ingest_manifests')));

SET @m32_ok := (@m32_obs_named=24 AND @m32_obs_total=24+@m32_obs_producer
 AND @m32_obs_producer_bad=0 AND @m32_man_named=21 AND @m32_man_total=21+@m32_man_producer
 AND @m32_man_producer_bad=0 AND @m32_misplaced_checks=0);
SET @m32_ddl := IF(@m32_ok,'DO 0','SELECT * FROM ERROR_032_team_score_producer_needs_the_migration_023_shape');
PREPARE m32_stmt FROM @m32_ddl; EXECUTE m32_stmt; DEALLOCATE PREPARE m32_stmt;

SET @m32_ddl := IF(@m32_man_producer,'DO 0',
 'ALTER TABLE ktp_team_score_ingest_manifests ADD COLUMN producer VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT ''KTPHudObserver'' AFTER source_server');
PREPARE m32_stmt FROM @m32_ddl; EXECUTE m32_stmt; DEALLOCATE PREPARE m32_stmt;
SET @m32_ddl := IF(@m32_obs_producer,'DO 0',
 'ALTER TABLE ktp_team_score_observations ADD COLUMN producer VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT ''KTPHudObserver'' AFTER source_version');
PREPARE m32_stmt FROM @m32_ddl; EXECUTE m32_stmt; DEALLOCATE PREPARE m32_stmt;

SET @m32_exists := (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_TYPE='CHECK'
 AND TABLE_NAME='ktp_team_score_ingest_manifests' AND CONSTRAINT_NAME='chk_team_score_manifest_producer');
SET @m32_ddl := IF(@m32_exists,'DO 0',
 'ALTER TABLE ktp_team_score_ingest_manifests ADD CONSTRAINT chk_team_score_manifest_producer CHECK (producer = ''KTPHudObserver'')');
PREPARE m32_stmt FROM @m32_ddl; EXECUTE m32_stmt; DEALLOCATE PREPARE m32_stmt;
SET @m32_exists := (SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
 WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_TYPE='CHECK'
 AND TABLE_NAME='ktp_team_score_observations' AND CONSTRAINT_NAME='chk_team_score_producer');
SET @m32_ddl := IF(@m32_exists,'DO 0',
 'ALTER TABLE ktp_team_score_observations ADD CONSTRAINT chk_team_score_producer CHECK (producer = ''KTPHudObserver'')');
PREPARE m32_stmt FROM @m32_ddl; EXECUTE m32_stmt; DEALLOCATE PREPARE m32_stmt;

SET @m32_exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_ingest_manifests'
 AND COLUMN_NAME='producer' AND COLUMN_DEFAULT IS NOT NULL);
SET @m32_ddl := IF(@m32_exists,'ALTER TABLE ktp_team_score_ingest_manifests ALTER COLUMN producer DROP DEFAULT','DO 0');
PREPARE m32_stmt FROM @m32_ddl; EXECUTE m32_stmt; DEALLOCATE PREPARE m32_stmt;
SET @m32_exists := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ktp_team_score_observations'
 AND COLUMN_NAME='producer' AND COLUMN_DEFAULT IS NOT NULL);
SET @m32_ddl := IF(@m32_exists,'ALTER TABLE ktp_team_score_observations ALTER COLUMN producer DROP DEFAULT','DO 0');
PREPARE m32_stmt FROM @m32_ddl; EXECUTE m32_stmt; DEALLOCATE PREPARE m32_stmt;

ALTER TABLE ktp_team_score_ingest_manifests
 COMMENT='Settled KTPHudObserver file identity and finality for relayed engine team scores, not the captain-reported league score';
ALTER TABLE ktp_team_score_observations
 COMMENT='Game-engine team scores relayed by the HUD observer (KTPHudObserver), not the captain-reported league score';

-- MySQL stores the clause with a charset introducer and backslash-escaped quotes, so both are stripped before comparing.
SET @m32_final_columns := (SELECT COUNT(*) FROM information_schema.COLUMNS
 WHERE TABLE_SCHEMA=DATABASE() AND COLUMN_NAME='producer'
 AND TABLE_NAME IN ('ktp_team_score_observations','ktp_team_score_ingest_manifests')
 AND DATA_TYPE='varchar' AND CHARACTER_MAXIMUM_LENGTH=32 AND COLLATION_NAME='ascii_bin'
 AND IS_NULLABLE='NO' AND COLUMN_DEFAULT IS NULL);
-- Exactly the clause set this lane's migrations produce: 032's own equality and
-- 033's widening. A later widening adds its normalised clause here (mirror 023).
SET @m32_final_checks := (SELECT COUNT(*) FROM (
 SELECT BINARY REGEXP_REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(c.CHECK_CLAUSE,
    '`',''),' ',''),'(',''),')',''),CHAR(9),''),CHAR(10),''),CHAR(13),''),CHAR(92),''),
    '_[A-Za-z0-9]+''','''') AS exact_clause
 FROM information_schema.TABLE_CONSTRAINTS t
 JOIN information_schema.CHECK_CONSTRAINTS c
   ON c.CONSTRAINT_SCHEMA=t.CONSTRAINT_SCHEMA AND c.CONSTRAINT_NAME=t.CONSTRAINT_NAME
 WHERE t.CONSTRAINT_SCHEMA=DATABASE() AND t.CONSTRAINT_TYPE='CHECK' AND (
  (t.TABLE_NAME='ktp_team_score_observations' AND t.CONSTRAINT_NAME='chk_team_score_producer') OR
  (t.TABLE_NAME='ktp_team_score_ingest_manifests' AND t.CONSTRAINT_NAME='chk_team_score_manifest_producer'))
 ) p WHERE p.exact_clause IN (
   BINARY 'producer=''KTPHudObserver''',
   BINARY 'producerin''KTPHudObserver'',''hltv-demo'''));
SET @m32_ok := (@m32_final_columns=2 AND @m32_final_checks=2);
SET @m32_ddl := IF(@m32_ok,'DO 0','SELECT * FROM ERROR_032_team_score_producer_column_or_check_incompatible');
PREPARE m32_stmt FROM @m32_ddl; EXECUTE m32_stmt; DEALLOCATE PREPARE m32_stmt;
