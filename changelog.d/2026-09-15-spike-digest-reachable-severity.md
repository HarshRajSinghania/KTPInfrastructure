### `scripts`: ktp-spike-digest severity floors were set above the fleet's range (2026-09-15)

Every severity branch in the daily spike digest except "a never-seen
fingerprint appeared" was unreachable, so the embed had one live path to a
non-green colour and took it twice in ten weeks. Measured over the 67 digests
posted since the 1.5.31 rewrite (2026-07-10 → 09-14): 65 green, 2 yellow, 0
red — and both yellows fired on the new-fingerprint path, not on magnitude or
on a baseline breach.

Replaying `ktp_spike_daily` through the shipped `breaches()` over all 68 days
the table covers returns green on every one of them. The floors, against what
the table actually holds:

- `BREACH_FLOOR = 20` gated the σ test on a single fingerprint clearing 20
  occurrences fleet-wide in a day. The largest such figure in the table's
  history is **13** (2026-07-12, `READ:10-25ms`), so the mean + 2.5σ
  comparison behind it had never been evaluated.
- `SEVERE_YELLOW_FLOOR = 5` promoted a ≥100ms fingerprint at five occurrences
  in one day. The table holds **two** ≥100ms frames all-time and both are
  single frames, so the tier the digest exists to surface could not fire.

It cost a real event. **2026-09-13 posted green** while carrying the only
≥100ms frame since July (`READ:100-250ms`, `74.91.123.64:27016`) on what was
simultaneously the worst fleet-day in the table — 14 material frames against a
trailing mean near 4, 13 of them on official-match hosts. The board
re-derivation that found that frame queried the table on a scheduled card
trigger the following day; the digest is cited nowhere in it.

- **`BREACH_FLOOR` 20 → 8.** Replayed across all 68 days this lifts non-green
  from 0 to 5 (7%) — 07-11, 07-26, 08-25, 09-06, 09-13 — each a genuine
  excursion against its own trailing baseline. 8 is the conservative end of a
  plateau: 6 selects the same five days, so the threshold is not balanced on
  an edge. `RED` stays unreached, now by 3-vs-8 rather than by 3-vs-20.
- **`SEVERE_YELLOW_FLOOR` 5 → 1.** A ≥100ms frame is a roughly monthly event
  on this fleet; one is the event. Alone this lifts non-green to 2 of 68, both
  of them the ≥100ms days.
- **`WARMUP_RED_FLOOR` unchanged.** It only applies before a baseline exists,
  where suppressing a σ claim is correct.

`tests/unit/test_spike_digest_severity.py` asserts each branch is reachable on
a day shape the fleet has actually produced, and that an ordinary day and a
large noise floor still post green. Six of its ten cases fail against the old
floors.

No threshold in `ktp-perf-rollup` was touched: its own `SPIKE_MIN_COUNT`
rationale is stale for a related reason and is tracked separately on the board.
