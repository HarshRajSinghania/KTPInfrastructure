### `scripts`: `key_moments` — the match's highlight windows, ranked on flag swing, in the report and the public DTO (2026-09-17)

Every consumer that wants "the big moments of this match" — the report page's key-moments
section, the HLTV viewer's deep links, the post-match message's best moment, the clip
pipeline — was going to derive them from `flag_swing.timeline` separately, and separately
derived lists disagree on a VOD. `scripts/highlight_windows.py` is one report stage that does
it once: events within 8 s cluster into a window, a window scores the momentum it moved plus
small bonuses for repeated killers and objective events, and the top 20 ship.

Two choices carried over from the prototype because they were measured, not guessed: a window
longer than 30 s is centred on its peak event instead of truncated from the start (truncation
was cutting away the moment the window was selected for), and `involved` is capped at four
players ranked kills-first, so the first entry is always the window's dominant killer and the
camera a reel would use. An uncapped list touched all twelve players on a busy sequence, which
is what turned a 20-minute render budget into two hours.

Internal report: `shadow_explorations.highlight_windows`, schema 16. Public DTO: top-level
`key_moments`, contract `analytics-report-dto-v1.4.0`, names only — the per-event timeline
stays private, and the block carries no ids or positions. Flag-only windows have an empty
`involved`: flag events in the timeline carry no player ids, capper attribution lives in
`capture_credits`, and a consumer falls back to a director view. Ran on all 93 reports in the
corpus: every one ranks, every one sanitizes. keep-the-prac matches the `v1.` prefix, so the
minor bump lands without a consumer change.
