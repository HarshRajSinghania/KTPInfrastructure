-- Expansion wave 2 duel matrix (migration 033): dodx get_user_vstats deltas per
-- (attacker, victim) per half, summed over the match. Unlike duel_matrix_v1
-- (kills from the frag timeline) this carries hits, damage and hit groups.
--
-- dodx semantics (KTPAMXX 1.24.1+): `kills` = attacker's kills on the victim
-- (the module's victims[].deaths slot); `deaths` and `shots` are 0 by
-- construction -- the module never writes its kills slot and counts shots
-- only on victims[0] -- so per-opponent accuracy is NOT answerable here; use
-- hits/damage. Rows from 1.23.x producers are one life per player, not a
-- half (victims[] resets per life); gate on producer_version >= 1.24.1.
SELECT
    attacker_id, victim_id,
    SUM(kills) AS kills, SUM(deaths) AS deaths, SUM(headshots) AS headshots,
    SUM(shots) AS shots, SUM(hits) AS hits, SUM(damage) AS damage,
    SUM(bh_head) AS head_hits, SUM(bh_chest) AS chest_hits,
    SUM(bh_stomach) AS stomach_hits,
    SUM(bh_leftarm + bh_rightarm) AS arm_hits,
    SUM(bh_leftleg + bh_rightleg) AS leg_hits
FROM ktp_duel_stats
WHERE match_id = {{MATCH_ID}}
GROUP BY attacker_id, victim_id
ORDER BY attacker_id, victim_id
