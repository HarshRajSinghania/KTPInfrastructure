### `scripts`: the overview ceiling comes from the player hull, and masked textures stop painting (2026-09-15)

Two fixes to `render_overview_bmp.py`, and one honest negative result about open terrain.

Method throughout: render a map with **its own shipped `.txt`** and compare transparency masks
against **its own shipped `.bmp`** — same map, same projection, so a difference is renderer or
reference. A map is only scoreable if some pixel actually USES the key; a palette that merely
contains the key entry is not enough, and by that test `dod_railroad`, the whole `saints`
family, `dod_charlie`, `dod_caen` and much of the fleet tree besides are fully painted and
cannot be scored at all. A map whose best IoU sits at a nonzero shift has a defective shipped
image and is reported separately.

- **The ceiling is now the highest surface a player can stand on, decided by the BSP's own
  hull 1, plus standing headroom.** It was the highest floor-anchored point entity plus
  headroom, which finds a map's top floor only by luck. `dod_caen2` has no floor-anchored
  entity above z 96, so the cut landed at 256 and sliced off a top floor at 400: IoU 0.848
  against the shipped image, 0.988 uncut. `para_kraftstoff` the same, 0.804 against 0.938.
  Both are fixed. `--cut Z` and `--no-cut` are unchanged.
- **A `{` texture paints nothing.** It is alpha-masked in GoldSrc, so foliage, fences and
  grates cannot fill the footprint they cover. `{k2_bush1` and `{pk_chleaves1c` were the only
  textures in the corpus that were ~100% wrong wherever they appeared. Small and uniform:
  `para_glider` +0.020, `dod_lennon5_b1` +0.009, `dod_armory_b6` +0.0013,
  `dod_armory_b4` -0.0001 (22 pixels of 786,432).

Before -> after, both corpora, aligned and keyed maps only:

| corpus | maps | mean IoU | better | worse | same |
|---|---|---|---|---|---|
| retail Steam tree | 34 | 0.8905 -> 0.9036 | 25 | 1 | 8 |
| fleet game host tree | 26 | 0.8538 -> 0.8749 | 20 | 2 | 4 |

Controls: `dod_armory_b4` 0.9519 -> 0.9518, `dod_railroad2_b2` 0.9060 -> 0.9761. Terrain:
`dod_railroad2_testa9` 0.7792 -> 0.8195, `dod_rails_ktp1` 0.7811 -> 0.8168, `dod_railroad2_s9a`
0.7343 -> 0.7627 at its own +16 alignment, `dod_railyard_s9a` 0.8533 -> 0.8712. Losses:
`dod_lennon5_b1` -0.0029 and `dod_lennon2` -0.0085, where the shipped image genuinely omits
roofs the new ceiling keeps.

**The new ceiling lands above the roofs on every map measured, and that is the right answer
rather than a hole in the rule.** DoD roofs are surfaces a player stands on, and the shipped
overviews draw them — `dod_caen2`'s reference has a detached wooden roof pad floating in the
key. Across 57 aligned maps in both corpora this ceiling and no cut at all produce the
identical mask every time, and sweeping an arbitrary cut from 20% to 95% of world height beats
no cut on 2 of 23 maps, both lennon revisions. There is no ceiling that keeps roofs out
without clipping a top floor, because on this geometry they are the same surfaces.

**Open terrain is still the weak spot and it is not the renderer.** On `dod_railroad2_testa9`
we paint ~60k pixels of ground the shipped image leaves keyed out, and no ceiling removes them:
sweeping the cut from -300 to none leaves at least 47k. The exclusion is whole-face — 222 of
1707 drawn faces are absent from the shipped image while 1485 are >=90% present, with almost
nothing in between — so it is not a hand-crop either. Tested and refuted as the separating
property: texture class (the same `kl_grass2_2` and `cxrockfbase` account for most of the
correctly painted ground too), face height, slope, area, world model vs brush entity, BSP leaf
contents above the face, sky-brush footprint, sky surfaces painting the key (0.08 at best),
PVS from the descriptor's own ORIGIN (`dod_ramelle` keeps 509 of 17,952 marked faces and scores
0.99 without it), air reachability in hull 0, air reachability in the player hull, and distance
to the nearest walkable floor or floor-anchored entity. No geometric property separates the
ground a mapper kept from the ground they cut. An area-weighted height ramp was also measured
against the count-weighted one on `dod_charlie`, `dod_koln` and four others and moved grey
entropy either way by under 0.1 bit, so the ramp is left alone.

Tests: hull-1 ceiling and masked-texture rules get synthetic-BSP cases, with the fixture now
able to emit a one-node player hull. Corpus floors rise to just under what the fleet tree
measures (halle/heutau/ramelle/caen2 0.98, koln/thunder2 0.97) and four more maps join them,
`para_kraftstoff` and three terrain maps among them. A new corpus case asserts the properties —
the ceiling comes from hull 1, and cutting at it costs nothing against the shipped image —
rather than a number. The old renderer fails eight of these.

⚠️ `KTP_OVERVIEW_CORPUS` must point at a game host's `dod/` tree. A map NAME is not a map:
retail Steam ships different builds under the same names, and its `dod_heutau` pair scores 0.70
where the fleet's scores 0.99. The fleet's `dod_railroad2_b2.bmp` is fully painted while the
Steam copy is keyed, so the two trees do not even agree on which maps are scoreable.
