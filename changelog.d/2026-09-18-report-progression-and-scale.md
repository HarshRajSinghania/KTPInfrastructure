### `scripts`: `progression` and `box_score_scale` in the report and the public DTO (2026-09-18)

The website's match page is getting Leetify-style fill bars and a kills-over-time chart, and
both would have been derived in the browser from data the site does not fully hold. A second
consumer (the HUD, the post-match message, the clip pipeline) would then derive them again,
separately, and the two would disagree — the divergence the HUD momentum work already measured
at 25% between two implementations of one curve. So both are computed once, here.

`scripts/progression.py` is one report stage: cumulative per-player series per half for kills
(cross-team frags with a producer clock; team kills and suicides excluded, matching the box
score), deaths (every frag as victim), and damage (`damage_capped` dealt cross-team, only when
per-hit damage with a clock was captured), plus a per-team flag differential seeded with the
same spawn ownership `flag_swing_v1` uses so the two agree on what a half started as. The
x-axis is `game_time` within the half — each half is its own map load and the clock restarts —
so series start at `[0, 0]` and halves are separate panels; no round index is invented. Every
rostered player gets a series for every available metric even when it is only `[[0, 0]]`,
because zero kills is data and a missing series reads as none. `coverage` says how many events
carried no clock, since a final point can trail the box score by exactly those. Cap
participation and cap breaks are not in v1: the former's per-event rows are keyed on wall
clock, the latter has no per-event fact query at all. Both are additive follow-ups.

`box_score_scale` is fill-bar normalisation over `players[]`: per numeric field the match max
(the bar's denominator, so a bar is reproducible), `higher_is_better` (false for deaths,
damage taken, team kills, suicides, grenade damage taken — a full bar there is not an
achievement), and `best`, the names holding the match-best value with ties kept. Scope is all
players in the match, not own team — operator ruling 2026-09-16.

Internal report: `shadow_explorations.progression`, schema 17 (player ids stay private).
Public DTO: top-level `progression` and `box_score_scale`, contract `analytics-report-dto-v1.5.0`,
names only. Neither block carries ids, positions, or a key the sanitizer forbids; the per-event
timeline stays private. keep-the-prac matches the `v1.` prefix, so the minor bump lands without
a consumer change.
