-- Expansion wave 1 per-player facts (migration 032): applied damage and the
-- per-life shot counters. One row per roster player. Read only; {{MATCH_ID}}
-- is replaced with one safely quoted SQL literal by scripts/match_analytics.py.
--
-- Every column is NULL, not 0, when the producer predates 1.21.0: the daemon
-- writes NULL for absent fields and this query must not COALESCE it away, or
-- a mixed-fleet match reads as "this player applied no damage".
WITH
roster AS (
    SELECT match_id, player_id, team
    FROM ktp_match_players
    WHERE match_id = {{MATCH_ID}}
),
applied AS (
    SELECT
        r.player_id,
        SUM(d.damage_applied) AS damage_applied,
        SUM(CASE WHEN d.damage_applied IS NOT NULL THEN d.damage_capped END)
            AS damage_capped_with_applied,
        COUNT(d.damage_applied) AS hits_with_applied
    FROM roster r
    JOIN ktp_damage_events d
      ON d.match_id = r.match_id AND d.attacker_id = r.player_id
    LEFT JOIN roster victim ON victim.player_id = d.victim_id
    WHERE d.victim_id <> r.player_id AND (victim.team IS NULL OR victim.team <> r.team)
    GROUP BY r.player_id
),
lives AS (
    SELECT
        player_id,
        SUM(shots) AS life_shots,
        SUM(shots_hitscan) AS life_shots_hitscan,
        COUNT(first_shot_delay) AS lives_fired,
        ROUND(AVG(first_shot_delay), 2) AS first_shot_delay_avg
    FROM ktp_life_events
    WHERE match_id = {{MATCH_ID}} AND boundary_kind = 'end'
    GROUP BY player_id
)
SELECT
    r.player_id,
    a.damage_applied,
    a.damage_capped_with_applied,
    a.hits_with_applied,
    l.life_shots,
    l.life_shots_hitscan,
    l.lives_fired,
    l.first_shot_delay_avg
FROM roster r
LEFT JOIN applied a ON a.player_id = r.player_id
LEFT JOIN lives l ON l.player_id = r.player_id
ORDER BY r.player_id
