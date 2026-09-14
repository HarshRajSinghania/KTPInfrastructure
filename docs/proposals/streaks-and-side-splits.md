# Kill streaks, clutches, per-side and per-class splits: data proposal

Status: proposal only. Nothing here is built, deployed or regenerated.
Builds on open PR #348 (`site/report-dto-result-and-halves`, head `e84fe1f`,
report schema 10, `analytics-report-dto-v1.1.0`). Everything proposed below is
additive and targets report schema 11 / `analytics-report-dto-v1.2.0`.

Consumers waiting on it: the `/stats` kill streak / clutch item, the `/players`
sprite hover (`searse/keep-the-prac`#717, per-side splits) and the player page
(`searse/keep-the-prac`#729, per-class splits).

Measured 2026-09-13 on the first five S10 `.ktp` matches (`1789326428-NY1`,
`1789326628-NY5`, `1789326741-NY3`, `1789326983-NY4`, `1789328375-NY2`: 10
halves, 60 player-matches, 120 player-halves). Read only: the SELECT-only
`hlstatsx` account behind a SELECT/SHOW/DESCRIBE guard, the HUD observer's
settled `<observer-root>/<match_id>/events.jsonl`, and `ktp.match_report` over
anon PostgREST. Player identities appear here only as aggregates.

## Answers at a glance

| Stat | Reconstructible | Source | Compute in | DTO (v1.2.0) |
|---|---|---|---|---|
| Kill streak per player per half / match | Yes, S10 only | `hlstats_Events_Frags` (producer clock) + `ktp_life_events` | new `scripts/kill_streaks.py`, called from `build_report` | `kill_streaks` block, `players[].best_streak`, `player_halves.rows[].best_streak` |
| Kill streak per side | Yes | as above; side from the life ledger | same | `player_halves.rows[].side` (a half is one side) |
| Duels per side | Yes | `hlstats_Events_Frags` + life ledger side | builder | new `duels_by_side[]` |
| Cap breaks per side | Yes | `hlstats_Events_PlayerActions` (`cap_break`, `producer_half`, `producer_game_time`) + life ledger | `player_half_fact.sql` + side | `player_halves.rows[].side` |
| Weapon kills / HS / accuracy / damage per side, exact incl. pickups | Yes | Frags, `hlstats_Events_Statsme.half`, `ktp_damage_events.producer_half`, side per player-half | new `sql/analytics/weapon_half_fact.sql` + side in builder | new `weapon_sides[]` |
| Per-class splits | Yes, per life | `ktp_life_events.player_class` (read at spawn) | builder | new `player_classes[]` |
| Momentum (accumulation) per side | Computable, **not publishable yet** | `accumulation_v3` contribution sources already carry `half` | scorer | none until the profile leaves DRAFT |
| Clutches | Alive state yes; "clutch" needs a ruling | life ledger (validated against position samples) + fight windows | `scripts/flag_fights.py` / builder | `clutches` block, gated |
| Anything before 2026-08-15 (S9) | No | the `ktp_*` ledgers do not exist that early | none | `status: unavailable` |

## Facts every definition below depends on

**1. The producer clock totally orders kills within a half.**
- `game_time` is the server's `get_gametime()` at 0.01 s resolution, stamped by `ktp_stats_capture.inc` in the same `client_death` call that emits both the frag context and the victim's life end.
- 2,710 of 2,739 frags carry it.
- `producer_sequence` (per event type, per match and half) is strictly consistent with it: 0 inversions over 2,700 adjacent pairs, 0 duplicate sequences.
- **Ties:** 23 groups share one `(half, game_time)` (22 pairs, 1 triple). None of them has a player both killing and dying at that tick, and no player's own life end shares a tick with one of their kills. So tie order cannot change a streak. Flipping the policy (kill before death vs death before kill) changes 0 of 120 player-halves.
- **Life ledger ordering:** `producer_sequence` is not monotone with `game_time` on 6 of 5,824 adjacent pairs (it is buffered separately), so order life rows by `game_time`, never by sequence.

**2. The life ledger is complete for deaths.**
- All 2,710 timed frags have the victim's `end/death` boundary at the identical `game_time`.
- The 29 frags without a producer clock have daemon receipt time only. 18 recover a time from the victim's life end within ±2 s of `event_epoch`; 11 (0.40%) do not.
- `end/death` rows total 2,801: 2,728 enemy frags, 62 teamkills and suicides, and the 11 unrecovered frags.
- `hlstats_Events_Teamkills` and `hlstats_Events_Suicides` have no producer clock. Their deaths are ordered through the life ledger, not their own rows.

**3. A player's side is constant within a half.**
- 0 of 120 player-halves have lives on two sides.
- Half 1's side is the opposite of `ktp_match_players.team` for 60 of 60 players, and half 2's side equals it for 60 of 60. That is #348's "team is the terminal half" rule, measured independently.
- A per-side split is therefore a per-half split with a side label. The label comes from the life ledger's `team` (1 Allies, 2 Axis) at the event time.
- `killerRole` agrees on every frag where both exist (2,605 of 2,605, 0 disagreements), but it is empty on 122 frags (4.5%). The life ledger labels 2,727 of 2,728 ordered frags.

**4. Weapon, side and class are three different things.**
- 251 of 2,739 kills (9.2%), in 65 of 120 player-halves, were made with the other side's weapon. The largest cases are Kar kills by Allied players (129) and BAR kills by Axis players (88).
- Any "faction of the weapon" split mislabels those. Split on the player's side, and key the weapon inside it.

**5. The observer stream is a separate record, not a copy.**
- It includes kills outside the live windows: 50 across the five matches, before `golive` or during halftime. So it must be windowed on `match_phase: golive` to `half_end` / `ktp_match_end`.
- Inside those windows it has 16 more normal kills than hlstatsx (2 to 5 per match), consistent with dropped log lines on the hlstatsx side.
- hlstatsx stays the authority (see `KTPHLStatsX/CLAUDE.md` on warmup in HUD-derived tables). The observer is the independent check.

## 1. Kill streaks (`kill_streak_v1`)

### Definition

A kill streak is the number of enemy kills a player makes between two of their
own life ends, within one half. `best_streak` is the longest such run.

- **Counts:** rows in `hlstats_Events_Frags` with the player as killer. Teamkills are in `hlstats_Events_Teamkills` and never count.
- **Resets:** every `end` boundary for that player in `ktp_life_events`:
  - death to an enemy;
  - death to a teammate (the victim of a teamkill resets);
  - suicide or world death;
  - `disconnect`.
- **Teamkill by the player:** neither counts nor resets. The player is still alive.
- **Respawn without a recorded end:** does not reset; the player never died. The ledger has 112 `start` rows while already alive: 64 are the `context_live` baseline followed by the real spawn, and 48 are spawn after spawn. 70% of the 112 fall within 15 s of the player-half's first boundary.
- **Half boundary:** always resets. A half is a separate map load, `game_time` restarts and every player respawns. The match value is the maximum over halves, and overtime halves (producer half 3+) are ordinary halves. A game split across two `match_id`s (the halftime server-change trap in `KTPHLStatsX/CLAUDE.md`) is two matches here too.
- **Posthumous kills** (a grenade landing after the thrower died): the kill counts and belongs to the counter as it stands at that instant, which the death has already reset. That is how the in-game HUD counter works (kills since last death). Measured: 62 of 2,739 kills (2.3%) land after the killer's own life end: 61 grenades and 1 BAR.
- **Picked-up weapons:** irrelevant to the count. The per-side row uses the player's side, never the weapon's.
- **Pause:** a technical pause freezes the server, so no kill or life boundary can be produced during it and it cannot split or extend a run. Not exercised by the validation set.
- **Warmup:** excluded by construction; hlstatsx frag rows are match-scoped.
- **Frag with no producer clock:** its time is recovered from the victim's life end (±2 s on `event_epoch`). If that fails, the frag is left out of streaks, counted in `coverage.unordered_frags`, and the affected row is marked `lower_bound: true` because its streak may be understated.

### Source and computation site

- **Queries:** no new SQL. `build_report` already loads `frag_context_fact.sql` (producer half, `game_time`) and `life_boundary_fact.sql` for the revenge, life and clutch explorations. A new `scripts/kill_streaks.py` takes those two row sets and returns per-player-half rows, and `analytics_report_dto.py` copies them.
- **Gating:** on the existing `frag_context`, `frag_event_clock` and `life_boundaries` source capabilities. A report without them publishes `status: unavailable`, so S9 is unavailable rather than zero.
- **Not the stock `kill_streak_N` player actions.** hlstatsx already writes `kill_streak_2` … `kill_streak_12` (`endKillStreak` in `HLstats_EventHandlers.plib`). They are unsuitable:
  - One action per ended life, capped at 12. A measured 16-kill run is stored as `kill_streak_12`.
  - Ordered by daemon receipt, with no `half` or producer clock.
  - Also ended by round-end and map-change handlers, and not match-scoped.
  - Per player-match, their maximum equals `kill_streak_v1`'s on only 51 of 60.
- **Not the observer's `best_streak`.** The HUD observer plugin keeps its own per-half counter (normal kills, reset on death, zeroed at `golive`). It agrees, but it is maintained outside this repo and its stream carries warmup. It is the cross-check below, not the source.

### Validation (three independent routes)

- **A.** `kill_streak_v1` over hlstatsx frags and the life ledger.
- **B.** The observer's `kill` events windowed from `golive` to half end: `normal` kills increment, any kill of the player or their disconnect resets.
- **C.** The observer plugin's own `player_score.best_streak`, maximum within the same window.

| Comparison over 120 player-halves | Agree |
|---|---|
| A vs B, best streak | 119 |
| A vs C, best streak | 118 |
| B vs C, best streak | 119 |

- **A vs B:** the one disagreement is a player-half whose run contains one of the 11 unrecoverable frags (A 6, B 7). Exactly the understatement `lower_bound` flags.
- **C:** disagrees with both A and B on one player-half (A = B = 4, C = 3), inside the observer plugin's own counter.
- **Largest runs:** A, B and C all agree on the six largest (16, 8, 8, 7, 7, 7).
- **Match-level distribution** (route A): best streak 2: 1 · 3: 8 · 4: 17 · 5: 9 · 6: 14 · 7: 8 · 8: 2 · 16: 1.
- **Controls:**
  - A bogus match id returns 0 frags, 0 life rows, 0 teamkills, 0 captures and 0 attempts.
  - The observer has no stream for it.
  - `ktp.match_report` returns `[]` for it.

### DTO shape

```json
"kill_streaks": {
  "definition": "kill_streak_v1",
  "definition_version": 1,
  "status": "available",
  "flags": [],
  "coverage": {"ordered_frags": 2710, "recovered_frags": 18, "unordered_frags": 11},
  "rows": [
    {"name": "<alias>", "team": 1, "half": 1, "side": "Axis",
     "kills": 23, "best_streak": 7, "streaks_3_plus": 2, "lower_bound": false}
  ]
}
```

Also `players[].best_streak` (match maximum) and `player_halves.rows[].best_streak`.
`team` keeps #348's meaning (match team number); `side` uses #348's `"Allies"` / `"Axis"` strings.

## 2. Clutches

DoD has respawn waves: 1,248 spawn clusters across the five matches, a mean of 2.35
players each, and a median of 18 s between a side's waves (p10 12 s, p90 30 s). There is
no round in which a side is eliminated, so the Counter-Strike "last player alive
wins the round" has no direct analogue. What the data can support was measured.

### Alive state is reconstructible

- **Coverage:** `ktp_life_events` gives every roster player's alive/dead state at 2,715 of 2,728 kill instants (99.5%). The 13 censored instants are moments when at least one player had no boundary yet in that half.
- **Victims:** every victim is alive just before their death (2,715 of 2,715).
- **Killers:** the killer is already dead at 61 of them, all grenade or BAR kills landing after the thrower died. So "alive when the kill happened" and "made the kill" are different predicates.
- **Independent check** against `ktp_position_samples` on NY1:
  - 12,077 samples, cadence 2.0 s, emitted only while alive;
  - 12,077 of 12,077 fall inside a ledger alive interval, and none inside a dead interval;
  - sample team equals ledger team on all 12,077.
- **Ledger anomalies:** 112 `start` while already alive (explained above) and 2 `end` while not alive.

### Candidate definitions, measured

| Candidate | Rule at the kill instant | Count (5 matches) |
|---|---|---|
| Last-alive kill | killer is the only living member of their side, and at least 2 enemies are alive | 9 |
| Outnumbered kill | living enemies minus living teammates (killer included) is at least 2 | 467 (17.2% of 2,715) |
| Fight clutch (`fight_clutch_v1`, exists as a private shadow in `scripts/flag_fights.py`) | the winning side of an objective fight has exactly one living member at the fight window's close, against at least 2 living enemies | not published; runs inside `build_report` |

Kills happen with close to full teams on both sides. The most common states are 5v6
(371), 6v6 (368), 5v5 (324) and 6v5 (307), so "last alive" is momentary and rare.

### Recommendation

- **Outnumbered kills:** publish as a descriptive count per player-half. It is honest and frequent enough to compare.
- **Clutches:** publish `fight_clutch_v1`, because it ties the lone survivor to an objective outcome, the nearest DoD analogue to a round.
- **Last-alive kills:** at about two per match they suit a highlight, not a rate.
- **Naming** any of these "clutch" on the site is an operator ruling. Until then, the block ships with `status: unavailable`.

### Not reconstructible

- Whether the enemies counted as "alive" were in the same fight. The counts are map-wide; positions at a 2 s cadence could localise them, but that is a separate definition.
- Anything inside the first seconds of a half before every player's first boundary.
- Any S9 match.

### DTO shape (behind the ruling)

```json
"clutches": {
  "definition_versions": {"outnumbered_kill_v1": 1, "fight_clutch_v1": 1},
  "status": "unavailable",
  "parameters": {"outnumbered_margin": 2, "fight_clutch_min_living_enemies": 2},
  "coverage": {"kill_instants": 2728, "censored_instants": 13},
  "rows": [{"name": "<alias>", "team": 1, "half": 1, "side": "Allies",
            "outnumbered_kills": 4, "fight_clutches": 1, "last_alive_kills": 0}]
}
```

## 3. Per-side (Allies / Axis) splits

### Side attribution rule

- **Rule:** a player's side for an event is `ktp_life_events.team` on the latest `start` boundary at or before the event's `game_time` in that half.
- **Coverage:** measured to label every sided event in the validation set.
- **`killerRole`:** a secondary check, never the source; 122 frags have it empty.
- **Result:** because side is constant per player-half, the builder resolves one side per `(player, half)` and attaches it to rows.

### 3.1 Kill streak per side

The `kill_streaks.rows[]` above are per half and carry `side`. A player's Allies
streak is the best over the halves they played as Allies.

### 3.2 Duels per side

- **Today:** the DTO's `duels[]` is name-keyed killer/victim/kills with no side, and its total equals the match frag count on all five reports.
- **Proposal:** a new `duels_by_side[]` (`killer`, `victim`, `killer_side`, `kills`) built from the same frag rows plus the per-player-half side.
- **Size:** cells go from 70–72 per match to 127–142.
- **Checks:** the split sums back to `duels[]` exactly, with 0 unsided frags. `duels[]` stays as it is.

### 3.3 Cap breaks per side

- **Source:** `hlstats_Events_PlayerActions` rows with code `cap_break` carry `producer_half` and `producer_game_time` on all 32 S10 rows (8, 6, 8, 3, 7). Every one is sided through the life ledger, and per-match totals equal the DTO's `players[].cap_breaks`.
- **Proposal:** #348's `player_half_fact.sql` places breaks by `eventTime`. It should prefer `producer_half` when present, and the builder adds `side` to `player_halves.rows[]`. With that, breaks, kills, deaths, damage and every other #348 column split by side for free.
- **Not breaks:** `ktp_flag_captures.team` is capture credit and does not describe breaks.
- **Team-level only:** `ktp_objective_attempt_events` (`capturing_team`, `stop_reason = capture_stopped`) gives attempts stopped per side, not per player.

### 3.4 Weapon split per side (exact, pickups included)

- **New SQL:** `sql/analytics/weapon_half_fact.sql`, one row per `(player, half, weapon)`:
  - kills and headshot kills from `hlstats_Events_Frags` grouped on `COALESCE(producer_half, half)`;
  - shots and hits from `hlstats_Events_Statsme.half`, which exists and is populated (NY1: 2,316 / 493 shots/hits in half 1, 2,810 / 526 in half 2);
  - damage from `ktp_damage_events.producer_half`.
- **Builder:** attaches the player-half side and publishes `weapon_sides[]` (`name`, `team`, `side`, `weapon`, `kills`, `headshot_kills`, `shots`, `hits`, `damage_dealt`).
- **Checks, on all five reports:**
  - raw frags per weapon equal the DTO's `weapons[]` kills per weapon;
  - the side split sums back to them, with 0 frags left without a side.
- **Size:** rows go from 59–73 to 66–82 per match.
- **Why not add `side` to `weapon_fact.sql`:** that would change the meaning of an existing v1.0 array.

### 3.5 Momentum per side

- **Computable:** `accumulation_v3.score_match` records every contribution with its `half` and time (`record_source`), so a per-half total, and therefore a per-side total, is a sum inside the scorer.
- **Not publishable:** the block is `experimental_shadow` / `publication_state: DRAFT` on all five S10 reports, and the site should not publish a split of a number the pipeline does not publish.
- **Recommendation:** revisit when the profile is promoted.

## 4. Per-class splits

- **Source:** `ktp_life_events.player_class` is read at spawn, so every life carries exactly one class id.
- **Cross-check:** against `killerRole` on 2,605 kills it maps each class id to one role string 99.81% of the time. One id splits across two role strings (3.1% of its 161 kills), and two ids share one role string. `killerRole` is a daemon-side label, while the class id is read from the player at spawn by the plugin, so the life ledger is the source.
- **Class labels:** a checked-in mapping (`config/analytics/dod_classes.toml`) with a test that pins it against `killerRole` on a fixture.
- **Row definition:** one row per `(player, half, class id)`:
  - `lives`: `start` boundaries with that class;
  - `kills`: killer's class on the latest `start` at or before the kill, so posthumous grenades count to the class that threw them;
  - `deaths`: the victim's class at death;
  - `headshot_kills`.
- **Alive time:** `alive_seconds` is left out of v1. The ledger has no half-close boundary, so the last life of every half would need a close taken from another clock.
- **Weapon ≠ class:** 9.2% of kills use a picked-up weapon, so a class row's kills include other weapons. `weapon_sides[]` is the place for weapons.

DTO: `player_classes[]` = `{name, team, half, side, class_id, class_code, lives, kills, deaths, headshot_kills}`,
24 to 29 rows per match (measured).

## 5. Where each piece is computed

| Piece | Site | Why |
|---|---|---|
| Streak runs, outnumbered kills, class attribution, side per player-half | Python in the report builder | needs event order and the life ledger, both already loaded; the site only has name-keyed aggregates |
| Per-half weapon totals | new SQL fact | a straight GROUP BY over three tables with a `half` column |
| Side labels on #348 rows | builder, after `player_half_fact.sql` | one lookup per player-half |
| Rendering, side toggles, thin-sample dimming | site | presentation only; no event data reaches the site |

Nothing is computed site-side from raw events, and no new identity crosses the DTO
(names only, as today).

## 6. Payload size

Estimated as canonical JSON with placeholder names at the published payload's mean
name length (6), using each match's real row counts. Reports today are 326–368 KB
(schema 9); #348 adds about 11.5 KB.

| Addition | Bytes per match | Share of today's payload |
|---|---|---|
| `kill_streaks` (24 rows) | ~3.0 KB | ~0.9% |
| `duels_by_side` | 9.0–10.1 KB | ~2.8% |
| `weapon_sides` | 5.9–7.4 KB | ~2.0% |
| `player_classes` (24–29 rows) | 3.4–4.1 KB | ~1.1% |
| `clutches` (24 rows) | ~3.0 KB | ~0.9% |
| `side` + `best_streak` on #348's `player_halves`, `best_streak` on `players[]` | ~1.0 KB | ~0.3% |

Total 25–29 KB per match (7–8%) on top of #348.
`duels_by_side` is the only item worth trimming; keeping only cells with kills (as today) already does most of that.

## 7. What is not reconstructible, and why

- **S9 and earlier:** `ktp_life_events` and producer clocks on frags start in August 2026, so every block publishes `unavailable` for those reports.
- **The 11 frags (0.40%) with no producer clock and no matching victim life end:** they cannot be placed in a run. Rows touching them carry `lower_bound`.
- **Kills the game logged but hlstatsx never received:** 16 across the five matches, seen only in the observer stream. Streaks are defined over the hlstatsx record.
- **Sub-tick order** between a player's own kill and own death: none occurred, and the rule (kill first) is stated rather than inferred.
- **"Clutch" as a round concept:** DoD respawns in waves. What exists is outnumbered-at-the-moment and lone-survivor-at-fight-close.
- **Momentum per side as a published number:** computable, but the profile is DRAFT.
- **Accuracy per class:** `hlstats_Events_Statsme` has a half but no class or time, so shots cannot be assigned to a life.

## 8. Suggested build order

1. `side` on `player_halves.rows[]` and `producer_half` for cap breaks (small, extends #348 once merged).
2. `kill_streak_v1` + `kill_streaks` block (+ `best_streak` fields).
3. `weapon_half_fact.sql` + `weapon_sides[]`.
4. `duels_by_side[]`.
5. `player_classes[]` with the class mapping and its test.
6. `clutches`, behind the naming ruling.

Each lands with a schema bump to 11 on the first merged item and a `contract_version`
of `analytics-report-dto-v1.2.0`; later items in the same minor only add keys.
