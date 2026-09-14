-- Unique flag-control events used only for private shadow correlations.
-- Excludes warmup bleed-through -- see capture_credit_fact.sql.
SELECT
    c.half,
    UNIX_TIMESTAMP(c.event_time) AS event_unix,
    c.event_time,
    c.team AS team_name,
    CASE LOWER(c.team)
      WHEN 'allies' THEN 1
      WHEN 'axis' THEN 2
      ELSE NULL
    END AS team,
    c.flag_name,
    COUNT(*) AS credited_players
FROM ktp_flag_captures c
LEFT JOIN ktp_matches m ON m.match_id = c.match_id AND m.half = c.half
WHERE c.match_id = {{MATCH_ID}}
  AND (m.start_time IS NULL OR c.event_time > m.start_time)
GROUP BY c.half, c.event_time, c.team, c.flag_name
ORDER BY c.half, c.event_time, c.flag_name;
