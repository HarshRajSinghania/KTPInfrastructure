-- One row per (match_id, player_id, half, weapon): the weapon_fact.sql columns
-- that have a half at the source, split by half. Read only. {{MATCH_ID}} is
-- replaced with one safely quoted SQL literal by scripts/match_analytics.py,
-- and the FRAG_HALF / DAMAGE_HALF tokens with the producer half where the
-- archive carries one, else the stored half. Hit locations (Statsme2) have no half.
WITH
roster AS (
    SELECT match_id, player_id, player_name, team
    FROM ktp_match_players WHERE match_id = {{MATCH_ID}}
),
frag AS (
    SELECT f.killerId AS player_id,
           {{FRAG_HALF}} AS half,
           f.weapon COLLATE utf8mb4_unicode_ci AS weapon,
           COUNT(*) AS kills,
           COALESCE(SUM(f.headshot), 0) AS headshot_kills
    FROM hlstats_Events_Frags f
    WHERE f.match_id = {{MATCH_ID}}
    GROUP BY 1, 2, 3
),
sm AS (
    SELECT s.playerId AS player_id, s.half,
           s.weapon COLLATE utf8mb4_unicode_ci AS weapon,
           SUM(s.shots) AS shots, SUM(s.hits) AS hits
    FROM hlstats_Events_Statsme s
    WHERE s.match_id = {{MATCH_ID}}
    GROUP BY 1, 2, 3
),
damage AS (
    SELECT de.attacker_id AS player_id,
           {{DAMAGE_HALF}} AS half,
           de.weapon COLLATE utf8mb4_unicode_ci AS weapon,
           SUM(CASE WHEN victim.team <> attacker.team
                    THEN de.damage_capped ELSE 0 END) AS damage_dealt
    FROM ktp_damage_events de
    JOIN roster attacker ON attacker.player_id = de.attacker_id
    JOIN roster victim ON victim.player_id = de.victim_id
    WHERE de.match_id = {{MATCH_ID}} AND de.attacker_id <> de.victim_id
    GROUP BY 1, 2, 3
),
weapon_keys AS (
    SELECT player_id, half, weapon FROM frag
    UNION
    SELECT player_id, half, weapon FROM sm
    UNION
    SELECT player_id, half, weapon FROM damage
)
SELECT
    r.match_id,
    r.player_id,
    r.player_name AS player_name_at_match,
    r.team,
    wk.half,
    wk.weapon,
    COALESCE(f.kills, 0) AS kills,
    COALESCE(f.headshot_kills, 0) AS headshot_kills,
    COALESCE(d.damage_dealt, 0) AS damage_dealt,
    COALESCE(sm.shots, 0) AS shots,
    COALESCE(sm.hits, 0) AS hits
FROM weapon_keys wk
JOIN roster r ON r.player_id = wk.player_id
LEFT JOIN frag f ON f.player_id = wk.player_id AND f.half <=> wk.half AND f.weapon = wk.weapon
LEFT JOIN sm ON sm.player_id = wk.player_id AND sm.half <=> wk.half AND sm.weapon = wk.weapon
LEFT JOIN damage d ON d.player_id = wk.player_id AND d.half <=> wk.half AND d.weapon = wk.weapon
ORDER BY wk.half, r.team, r.player_name, kills DESC, wk.weapon;
