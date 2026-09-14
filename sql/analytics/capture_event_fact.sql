-- One row per unique capture event. credited_players preserves multi-capper
-- size; do not sum ktp_flag_captures directly to count control changes.
--
-- Same warmup-bleed guard as capture_credit_fact.sql: a capture attempt
-- begun before KTP_MATCH_START can still complete a moment after the flip
-- and land tagged with the new half. Real captures cannot complete within
-- the same rounded second as the half's own start_time.
SELECT
    c.match_id,
    c.half,
    c.team AS team_name,
    c.flag_name,
    c.event_time,
    COUNT(*) AS credited_players
FROM ktp_flag_captures c
LEFT JOIN ktp_matches m
  ON m.match_id = c.match_id AND m.half = c.half
WHERE c.match_id = {{MATCH_ID}}
  AND (m.start_time IS NULL OR c.event_time > m.start_time)
GROUP BY c.match_id, c.half, c.team, c.flag_name, c.event_time
ORDER BY event_time, flag_name, team;
