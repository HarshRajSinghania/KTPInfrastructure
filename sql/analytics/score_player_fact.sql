-- Expansion wave 2 per-player score attribution (migration 033): the engine's
-- own award events, so cap credit and territorial ticks are measured, not
-- inferred from zone occupancy. flag_index is -1 when the producer could not
-- resolve DLL index space; those points still count, they are just unplaced.
SELECT
    player_id,
    COUNT(*) AS score_events,
    SUM(delta) AS score_points,
    SUM(CASE WHEN flag_index >= 0 THEN delta ELSE 0 END) AS score_points_placed,
    SUM(CASE WHEN identity_resolved = 0 THEN 1 ELSE 0 END) AS score_events_unresolved
FROM ktp_score_events
WHERE match_id = {{MATCH_ID}}
GROUP BY player_id
ORDER BY player_id
