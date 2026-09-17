-- Precision on hits, from the anti-cheat ledger (direct read, decision 2 of
-- 2026-09-09). One row per roster player with at least one geometry row.
--
-- ktp_ac_weapon_fires.err_udeg is the angle (micro-degrees) between the
-- traced ray and the line to the HIT player's centre, stashed by dodx
-- KTPShotGeom only when the trace hit a player hitbox -- so this is "how
-- tight within the hitbox", not crosshair placement across all shots
-- (shot_placement_fact.sql is that). tgt_angvel_mdps is the target's bearing
-- rate across the shooter's view, -1 when there was no prior sighting.
--
-- Join: the AC ledger stores full "STEAM_0:X:Y"; the roster stores the
-- HLStatsX-normalised "X:Y" tail, in a different collation. Normalise both.
WITH
roster AS (
    SELECT player_id,
           CONVERT(steam_id USING utf8mb4) COLLATE utf8mb4_bin AS steam_tail
    FROM ktp_match_players
    WHERE match_id = {{MATCH_ID}}
),
fires AS (
    SELECT
        CONVERT(REPLACE(steam_id, 'STEAM_0:', '') USING utf8mb4) COLLATE utf8mb4_bin AS steam_tail,
        err_udeg, range_units, tgt_angvel_mdps
    FROM ktp_ac_weapon_fires
    WHERE match_id = {{MATCH_ID}} AND err_udeg IS NOT NULL
)
SELECT
    r.player_id,
    COUNT(*) AS ac_hits_with_geometry,
    ROUND(AVG(f.err_udeg) / 1000000.0, 2) AS ac_err_avg_deg,
    ROUND(AVG(f.range_units)) AS ac_range_avg,
    ROUND(AVG(CASE WHEN f.tgt_angvel_mdps >= 0 THEN f.tgt_angvel_mdps END) / 1000.0, 1)
        AS ac_target_angvel_avg_dps
FROM roster r
JOIN fires f ON f.steam_tail = r.steam_tail
GROUP BY r.player_id
ORDER BY r.player_id
