### `config`/`scripts`: seven overview descriptors moved onto the picture they ship with (2026-09-15)

`docs/OVERVIEW_CORPUS_AUDIT.md` measured which shipped overviews disagree with their own
`.txt`. This corrects the `.txt` — one line of text against re-cutting an image players have
read for seasons — for the seven where the picture is geometrically sound and only
mis-declared. The files are in `config/overviews/`; **nothing is deployed**, and
`docs/OVERVIEW_DESCRIPTOR_FIXES.md` carries the measurements and the runbook.

`scripts/correct_overview_descriptor.py` does the arithmetic. The audit's transform takes the
render to the shipped image, so `zoom' = scale * zoom` and the origin moves `d/s'` world units —
but **a pixel `dx` is not a world x**: the axes are swapped and negated against world x/y and
which one a pixel axis carries flips with `ROTATED`. Reading `dx` onto `origin_x` yields a file
that looks corrected and is wrong on both axes, and the unit test pins both branches.

| map | ZOOM | ORIGIN x, y | IoU as laid → after |
|---|---|---|---|
| `dod_anzio2_test3` | 1.03 → 0.77 | 404, −76 → 393.61, −86.39 | 0.391 → 0.929 |
| `dod_anzio_test4d` | 1.03 → 0.77 | 404, −76 → 393.61, −86.39 | 0.361 → 0.761 |
| `dod_anzio3_b1` | 1.03 → 0.77 | 404, −76 → 393.61, −86.39 | 0.362 → 0.760 |
| `dod_schwetz` | 0.99 → 1.04 | −608, −76 → −615.69, −83.69 | 0.882 → 0.980 |
| `dod_railroad2_s9a` | unchanged | 136, −337.31 → 136.00, −189.62 | 0.671 → 0.767 |
| `dod_rr2_test` | unchanged | −8, 2 → 133.54, −188.77 | 0.589 → 0.765 |
| `dod_aleutian2_test3` | 1.33 → 1.32 | 738, 24 → 731.94, 17.94 | 0.892 → 0.923 |

Every figure is a **re-render through the written file**, not the search's prediction. Two
measurements that are not the IoU search back each one: per-axis footprint extents, and
projecting the BSP's spawns and capture points onto the shipped image to count how many land on
drawn map rather than on the transparent key. That second one went from 282 of 362 anchors to
361 of 362 across the seven, and it is the one that answers the question players care about.

**`dod_rr2_test` and `dod_railroad2_s9a` are one picture under two descriptors** that disagreed
by 144 units in x and 339 in y. Corrected independently from their own BSPs they land 2.5 and
0.9 units apart, on a map 4000 units across.

**The anzio trade-off does not exist.** One image serves `anzio2_test3`, `anzio3_b1` and
`anzio_test4d`; sweeping zoom per map, all three peak at the same value and hold a 100% anchor
rate across the same band. The residual IoU on the two siblings is their own geometry — later
BSP revisions than the picture was cut from — not the projection, and no descriptor moves it.

**`dod_overlord` and `dod_dog1` are declined**, which is what the audit's own caveat asked for.
Both want a scale on **one axis only** (footprint extents 1.170 against 1.007, and 1.323 against
1.013 — a zoom scales both), both sit on a plateau rather than a peak, and the correction
*removes* anchors from drawn map: `dod_dog1` falls from 155/155 to 91/155, worse than a
deliberately-wrong 100-px-shifted control. **`para_hedgerow` is declined too** — its extents
match within 1.7% while it paints 1.26× the area, which is the open-terrain renderer gap, and
its descriptor already scores a clean 50/50.

Two things found while measuring. **Four descriptors name an image that is not their own file**
(`dod_rr2_test` → `dod_railroad2_test.bmp`, `dod_anzio2_test` and `dod_anzio2_test1i` →
`dod_anzio.bmp`, `dod_lennon4` → `dod_lennon2.bmp`); all resolve today, but an audit reading
`<map>.bmp` is not reading what the engine loads. And **Chicago carries 71 overview descriptors
where the other four hosts carry 72**.

⚠️ **The deploy has two ends and they are not connected.** The game instances are fed from
`/home/dod/distribute/`, which pushes to all 24 in ~15 s and syncs deletions — and which is
**not a mirror**: `dod_anzio_test4d` and `dod_anzio3_b1` are absent from it while present on all
24 instances. The copy a *client* reads is `/var/www/fastdl/dod/overviews/`, nothing syncs one
to the other, and a client that already has the file will not re-download it.
