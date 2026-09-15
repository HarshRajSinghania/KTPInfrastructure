-- One row is an aggregate of player capture credits. It is not a count of
-- unique flag-control changes when multiple players receive credit.
--
-- The daemon tags a capture with match_id/half whenever its own "round_live"
-- flag is set at COMPLETION time, but that flag flips at KTP_MATCH_START --
-- a capture attempt begun in warmup a moment earlier can still complete a
-- fraction of a second after the flip and land here fully attributed to the
-- new half. Real captures cannot complete this fast (multi-second hold), so
-- excluding anything at or before the half's own start_time (same rounded
-- second) removes the warmup bleed-through without touching genuine early
-- captures. Found auditing S10 day-1 data 2026-09-13.
SELECT
    c.match_id,
    c.player_id,
    r.player_name AS player_name_at_match,
    r.team,
    c.flag_name,
    COUNT(*) AS capture_credits
FROM ktp_flag_captures c
LEFT JOIN ktp_match_players r
  ON r.match_id = c.match_id AND r.player_id = c.player_id
LEFT JOIN ktp_matches m
  ON m.match_id = c.match_id AND m.half = c.half
WHERE c.match_id = {{MATCH_ID}}
  AND (m.start_time IS NULL OR c.event_time > m.start_time)
GROUP BY c.match_id, c.player_id, r.player_name, r.team, c.flag_name
ORDER BY r.team, capture_credits DESC, r.player_name, c.flag_name;
