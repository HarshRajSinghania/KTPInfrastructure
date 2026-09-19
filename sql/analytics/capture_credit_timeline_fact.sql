-- One row per capture CREDIT with its wall-clock time: the per-credit feed
-- flag_swing needs to split a cap's swing across its cappers. Neither
-- capture_credit_fact (per player+flag totals) nor capture_event_fact (per
-- event, cappers counted not named) carries both the player and the time.
--
-- Same warmup-bleed guard as capture_credit_fact.sql. `team` is mapped to
-- the engine side the way objective_timeline_fact does, because the TSV
-- layer int-coerces a column named team.
SELECT
    c.half,
    c.player_id,
    CASE LOWER(c.team)
      WHEN 'allies' THEN 1
      WHEN 'axis' THEN 2
      ELSE NULL
    END AS team,
    c.flag_name,
    c.event_time,
    UNIX_TIMESTAMP(c.event_time) AS event_unix
FROM ktp_flag_captures c
LEFT JOIN ktp_matches m
  ON m.match_id = c.match_id AND m.half = c.half
WHERE c.match_id = {{MATCH_ID}}
  AND (m.start_time IS NULL OR c.event_time > m.start_time)
ORDER BY c.half, c.event_time, c.flag_name, c.player_id;
