-- Grenade throws joined to their bursts (migration 034 + the lifecycle
-- tracker). One row per player who threw. The burst is ktp_grenade_entity_events
-- kind=tracked (Detonate's own TraceLine); the throw is the AmmoX edge. Join
-- rule from the migration-034 header: same match/half and thrower, compatible
-- weapon family, first tracked row within 8 s after the throw. Flight time
-- under the ~5 s fuse means the grenade was cooked; the daemon never
-- correlates, so an unmatched throw stays a throw with NULL flight.
WITH
throws AS (
    SELECT
        t.player_id,
        t.game_time AS thrown_at,
        (SELECT MIN(b.game_time)
         FROM ktp_grenade_entity_events b
         WHERE b.match_id = t.match_id AND b.half = t.half
           AND b.owner_player_id = t.player_id AND b.entity_kind = 'tracked'
           AND b.weapon_type = CASE t.weapon_type
                                   WHEN 'handgrenade_ex' THEN 'handgrenade'
                                   WHEN 'stickgrenade_ex' THEN 'stickgrenade'
                                   ELSE t.weapon_type END
           AND b.game_time > t.game_time AND b.game_time <= t.game_time + 8
        ) AS burst_at
    FROM ktp_grenade_throw_events t
    WHERE t.match_id = {{MATCH_ID}}
)
SELECT
    player_id,
    COUNT(*) AS grenade_throws,
    COUNT(burst_at) AS grenade_bursts_matched,
    ROUND(AVG(burst_at - thrown_at), 2) AS grenade_flight_avg,
    SUM(CASE WHEN burst_at - thrown_at < 3.5 THEN 1 ELSE 0 END) AS grenade_cooked
FROM throws
GROUP BY player_id
ORDER BY player_id
