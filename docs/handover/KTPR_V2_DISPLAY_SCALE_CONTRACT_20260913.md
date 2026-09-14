# KTPR v2 display scale — incident, contract, and what's left

**Date:** 2026-09-13
**Trigger:** every match page showed KTPR v2 = `100.0` for all twelve players.
**Status:** fixed on both sides. One design decision remains open (§5).

---

## 1. What happened

Reported live on
`https://ktpleague.gg/stats/matches/1789326741-NY3`: the KTPR v2 column read
exactly `100.0` for every player. Not a rounding artefact — the best player
(`122.33`) and the worst (`78.13`) both rendered `100.0`. The column carried
no information at all, and had not since the scale changed.

The underlying data was never wrong. The synced payload for that match holds
ratings from `122.33` down to `78.13`, mean exactly `100.00`.

## 2. Cause: one requirement, implemented twice, in two layers

Operator ruling 2026-09-09: *KTPR v2 must never show a player a negative
number.* That rule ended up enforced **twice**, by two different transforms,
in two repos, neither aware of the other.

| Layer | Transform | Result on a z of 1.49 |
|---|---|---|
| Producer (`analytics_report_dto.ktpr_display`) | `max(50, 100 + 15z)` | `122.33` — published |
| Website (`ratingIndex`, keep-the-prac #679/#691) | `100 / (1 + e^-x)` | applied to `122.33` → `100.0` |

The logistic curve is correct for a z-score: `0 → 50`, `1.5 → 81.8`. It is
catastrophic for a value already centred on 100, because `e^-122` is
indistinguishable from zero in float64. Every input above roughly 10
saturates to `100.0`. The floor value 50 saturates too, so *every* player
collapsed to the same number.

**Neither change was wrong when written.** #679/#691 were a correct response
to players seeing negative ratings, under the belief that `rating` was still
a z-score. The defect is that `rating` had stopped being one and nothing in
the payload said so.

## 3. Why the payload actively misled the consumer

This is the part worth keeping. The published block contained:

```json
"parameters": { "normalization": "per_match_z_scores", ... }
```

That is true of the internal math and true of `components`. It is **false of
the published `rating`**, which `ktpr_display()` had already rescaled. A
consumer reading that metadata would reasonably conclude `rating` is a
z-score — which is exactly the conclusion that produced the bug.

`parameters` describes *the model*. Nothing described *the published
numbers*. That gap is the root cause, not the arithmetic.

## 4. What was changed

**keep-the-prac — [PR #730](https://github.com/searse/keep-the-prac/pull/730)**

- Render `rating` as published; drop the second transform. This makes the
  match view agree with `season-view.tsx`, which renders the same
  producer-scaled field raw and was **never affected**.
- Keep the logistic mapping on the components, which genuinely are raw
  z-scores and genuinely need it. Renamed `ratingIndex` → `componentIndex`
  so the distinction is visible at the call site, with the saturation trap
  documented on the function.
- Corrected the card description, which described `rating` on the
  components' scale.
- Restored and tightened the fixture assertion (see §6).

**KTPInfrastructure — this branch**

- Publish an explicit `display_scale` block next to the ratings, in both the
  per-match DTO and the season leaderboard, stating for each field whether it
  is already scaled (`floored_index`, with its `center` / `per_z` / `floor`)
  or raw (`raw_z_score`). A consumer no longer has to infer.
- Tests that pin the advertised numbers to the real transform: the scale
  block must reproduce `ktpr_display()`'s own output, and components must
  still publish raw and negative. The two cannot drift apart silently.

## 5. Open decision — should components move onto the rating's scale?

Right now one table shows two scales side by side:

| Column | Scale | Example |
|---|---|---|
| KTPR v2 | centred 100, floor 50 | `122.3` |
| swing / output / multikill | 0–100, 50 = average | `81.6` |

Both are "higher is better" and neither is wrong, but a reader is entitled to
assume two adjacent numeric columns mean the same kind of thing. Options:

- **A — leave it.** Zero risk, ships now. Cost: the visual inconsistency
  above, and a permanent footnote in the card description.
- **B — put components on the display scale too**, in the producer, and
  delete `componentIndex` from the website entirely. The never-negative rule
  then lives in exactly one place, and the website becomes a pure renderer —
  which matches its own stated architecture ("the website never computes a
  stat; it renders payloads").

**If B is chosen, the ordering is a trap.** Shipping the producer change
alone would re-create this exact bug on the component columns: the website's
curve would saturate the newly-scaled components to `100.0`. The two changes
must land together, or the website change must land first. The tests added in
§4 will fail loudly on a lone producer change, which is the intended
behaviour.

Recommendation: **B**, once someone owns both repos for one merge window. Not
urgent — A is correct and honest in the meantime.

## 6. Why tests didn't catch it

The website fixture test asserted the floor (`rating >= 50`) until
2026-09-11, when the same commit that introduced the logistic transform
weakened it to "is a finite number", reasoning that the floor had moved to
the display layer. The fixture committed in that repo has always held
`127.93, 116.06, 113.06…` — values that plainly are not z-scores. The
evidence to contradict the assumption was in the same commit's diff context.

Now restored and tightened to pin **both** halves of the contract: `rating`
floored at 50 with at least one value above the floor, and `components` raw
with at least one negative.

The producer side already had `test_ktpr_rating_never_negative`, which is why
the producer was never wrong — it just never advertised what it was doing.

## 7. Verification performed

- Root cause reproduced arithmetically against the real production payload
  for `1789326741-NY3`, not a fixture.
- Website: `node --test tests/public-match-report-fixture.test.mjs` 5/5;
  `tsc --noEmit` clean in changed files; full suite 4102/4115, with the 12
  failures confirmed pre-existing by running one against unmodified `main`.
- Producer: `pytest tests/unit/test_analytics_report_dto.py` 23 passed.
- Season leaderboard confirmed unaffected by reading `season-view.tsx`
  (renders raw) and the `>= 50` assertion already in the website fixture test.

## 8. For whoever picks this up

The two PRs are independent and safe to merge in either order — the producer
change is purely additive metadata. The only sequencing hazard is §5 option B.

If KTPR ever changes scale again, the checklist is: update `ktpr_display()`,
update `KTPR_DISPLAY_SCALE` beside it (the tests force this), and grep
keep-the-prac for `componentIndex` before assuming the website will follow.
