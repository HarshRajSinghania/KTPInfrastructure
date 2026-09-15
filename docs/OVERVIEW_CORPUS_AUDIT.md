# Overview corpus audit

Every DoD map draws its spectator overview from three files: `maps/<map>.bsp`,
`overviews/<map>.bmp` and `overviews/<map>.txt`. The `.txt` carries ZOOM, ORIGIN and
ROTATED, and the engine projects world coordinates through it onto the `.bmp` to put a
player dot on the map. Nothing checks that the picture and the descriptor agree. When they
do not, every dot lands somewhere the player is not, and the game reports nothing.

`scripts/audit_overview_corpus.py` measures that. For each complete triple it renders the
map through its **own shipped descriptor** with `render_overview_bmp.py` and compares the
transparency footprints -- the pixels that are not the palette key RGB(0,255,0). Same map,
same projection, so the residue is the reference or the renderer. It reports the IoU as
laid (baseline), the best reachable by scaling and translating, and the transform at best.
Identity means self-consistent. Anything else sizes the defect.

Reproduce with:

```bash
python scripts/audit_overview_corpus.py \
  --overviews <gamedir>/overviews --maps <gamedir>/maps --json audit.json
```

## How to read a number here

**A low IoU is not a verdict about the renderer.** It is the start of a question, and the
alignment search answers it. Three things produce a low baseline and they have opposite
remedies:

| Evidence | Bucket | What it means |
|---|---|---|
| A transform buys real IoU | `reference-defective` | The shipped image disagrees with its own `.txt`. Fix the asset. |
| Identity is already best, image is this map's own | `renderer-gap` | Placement is right, content is not. Fix the renderer. |
| Identity is best but the image is shared with other revisions | *confounded* | The image was cut for a different revision. Not evidence either way. |
| The image keys zero pixels | `not-comparable` | Fully painted, so IoU collapses to "what fraction did we paint". Excluded. |

The last row is the one that bites. A fully painted overview scores around 0.3-0.4 against
a correct render and reads exactly like a broken renderer. **Never score those.** They are
listed and excluded.

## Two corpora

The audit was run twice, because the workstation's Steam corpus and the fleet's are not the
same set and not always the same bytes.

| | Steam workstation | Dallas `dod-27015` |
|---|---|---|
| Images | 127 | 69 |
| Descriptors | 124 | 72 |
| Maps | 125 | 78 |
| Complete triples | 76 | 65 |
| **Triples scored here** | **76** | **30** |

⚠️ **The fleet pass is a 30-map subset of its 65 triples, not a sweep.** It was chosen as the
maps the fleet carries and the workstation does not, plus the maps a workstation finding
needed checking against. The remaining 35 fleet triples are unmeasured — treat the fleet
bucket counts below as counts within that subset, not as a fleet-wide census. Re-run with
`--overviews` and `--maps` pointed at an instance's `serverfiles/dod` to close the gap.

Six names carry different bytes in the two places, and the workstation's copy is not
reliably the newer one. This matters more than it sounds:

- **`dod_heutau` scores 0.695 against the workstation's BSP and 0.973 against the fleet's**,
  on a byte-identical BMP. The workstation's `.bsp` is a different revision of the map. Read
  as a renderer result, that 0.695 is simply wrong.
- **`dod_lennon2`'s image differs between the two.** The workstation ships the `dod_lennon4`
  image under the `lennon2` name; the fleet ships the `lennon5_b1` one.

➡️ **For any claim about what players see, audit the fleet copy.** The workstation corpus is
useful for breadth and for revisions the fleet does not carry, and it is not authority.

## Shipped images that disagree with their own descriptor

Ranked by the IoU a transform buys, worst first. `scale` and `dx`/`dy` are what the render
must be put through to land on the shipped image, so they measure how far that image
misplaces the world.

### Dallas fleet (30 of 65 triples scored)

| Map | baseline | best | gain | transform |
|---|---|---|---|---|
| `dod_anzio2_test3` | 0.391 | 0.948 | +0.557 | scale 0.74 |
| `dod_anzio_test4d` | 0.361 | 0.785 | +0.423 | scale 0.74 |
| `dod_anzio3_b1` | 0.362 | 0.784 | +0.423 | scale 0.74 |
| `dod_rr2_test` | 0.589 | 0.769 | +0.180 | dx −31, dy +23 |
| `dod_schwetz` | 0.882 | 0.990 | +0.108 | scale 1.04 |
| `dod_railroad2_s9a` | 0.671 | 0.765 | +0.095 | dx +24 |
| `dod_overlord` | 0.803 | 0.893 | +0.090 | scale 1.18, dx +67, dy −12 |
| `dod_dog1` | 0.801 | 0.878 | +0.077 | scale 1.34, dx −9, dy +3 |
| `para_hedgerow` | 0.619 | 0.662 | +0.043 | scale 1.06, dx −11 |
| `dod_aleutian2_test3` | 0.892 | 0.934 | +0.043 | scale 0.99 |

### Steam workstation (76 triples)

| Map | baseline | best | gain | transform |
|---|---|---|---|---|
| `dod_anzio3_b1` | 0.362 | 0.784 | +0.423 | scale 0.74 |
| `dod_railroad2_b3` | 0.589 | 0.769 | +0.180 | dx −31, dy +23 |
| `dod_railroad2_s9a` | 0.671 | 0.765 | +0.095 | dx +24 |
| `dod_anjou_a2` | 0.892 | 0.971 | +0.079 | scale 1.02 |
| `dod_anjou_draft_v3` | 0.891 | 0.959 | +0.069 | scale 1.02 |
| `dod_lennon_b2` | 0.929 | 0.994 | +0.065 | dy −9 |
| `dod_armory_b2` | 0.702 | 0.738 | +0.037 | scale 1.01, dx −7 |

**The anzio family is the worst defect in either corpus and it is one picture.**
`dod_anzio2_test3.bmp`, `dod_anzio3_b1.bmp` and `dod_anzio_test4d.bmp` are byte-identical
(`d1b852ea740b…`), all three descriptors declare essentially the same projection, and the
image is drawn at about 0.74 of it. Aligning `anzio2_test3` at that scale reaches 0.948,
which is renderer-quality agreement -- so the geometry in that picture is right and only its
scale is wrong. One re-cut fixes three maps.

`dod_overlord` and `dod_dog1` want large scale corrections (1.18 and 1.34) on top of an
already-decent baseline. Those two are the least certain entries in the table: a big scale
with a modest gain can also be a search finding a broad, flat optimum rather than a real
defect. Treat them as candidates, not conclusions.

## Duplicate images whose descriptors disagree

One BMP legitimately serves several revisions. It cannot legitimately serve two descriptors
that place it differently -- at most one of those is right, and the others misplace every
dot.

- **Fleet, `689528e8589e…`** — `dod_rr2_test` declares ORIGIN (−8, 2); `dod_railroad2_s9a`
  declares (136, −337.31). Same picture. The audit scores `rr2_test` best at `dx −31,
  dy +23` and `s9a` best at `dx +24`, so **neither matches it** and the image belongs to a
  third revision.
- **Workstation, same image, five members** — `dod_railroad2_b3`, `_s9a`, `_test`, `_testa6`,
  `_testa9`. Four share one ORIGIN, `s9a` has another. `_test` and `_testa9` are the two that
  need no shift.
- **Workstation, `fae9634d488d…`** — `dod_lennon_test1` declares zoom 1.35 / ORIGIN
  (−279.87, 40.71); `dod_lennon2_b1`, `dod_lennon_b4` and `dod_lennon_testfinal` declare
  1.39 / (320, −98).
- **Workstation, `b68796d7702d…`** — `dod_thunder2_b1c` declares ORIGIN (128, −144);
  `dod_thunder2_b1` declares (496, −1288), a 1144-unit difference on one image.

The workstation has fifteen duplicate groups of which three disagree; the fleet subset has
six of which one does. The rest agree on their descriptors and are fine — a single image
serving several revisions is the normal case, not a defect.

## Images that key zero pixels — excluded, not failed

A shipped overview with no transparent key is fully painted: every pixel opaque. Its
footprint is the whole canvas, so IoU degenerates and the map cannot be scored either way.

- **Workstation: 37 of 76 triples** (59 of 127 images corpus-wide).
- **Fleet: 6 of 30 triples** — `dod_anzio2_test1i`, `dod_railroad2_b2`, and the four
  `dod_saints2_b3b/c/d/e`.

⚠️ **This class is wider than the saints family**, which is how it has been mis-scored
before. `dod_railroad.bmp` is a no-key image: any IoU quoted for it, including the 0.451
figure that has circulated, is an artefact of scoring a fully painted picture and says
nothing about that map.

## Where the renderer is the problem

Seven of the thirty scored fleet maps reach their best at the identity transform and
still disagree. Four of the seven share their image with another revision, so they are
confounded and not evidence. The three that own their image, and what was measured on each:

| Map | IoU (default ceiling) | IoU (`--no-cut`) | Reading |
|---|---|---|---|
| `dod_caen2` | 0.848 | **0.988** | The ceiling rule, not terrain |
| `para_kraftstoff` | 0.804 | **0.938** | The ceiling rule, not terrain |
| `para_glider` | 0.712 | 0.712 | Genuine gap |

**Two of the three are the default ceiling, and that is a renderer bug worth fixing.**
`render_overview_bmp.py` derives its ceiling from the highest floor-anchored point entity
plus headroom. On `dod_caen2` that lands at z 256 and clips real top-floor geometry; drawing
everything recovers 0.139 IoU and takes the map to 0.988. `para_kraftstoff` behaves the same
way (ceiling 304, +0.134 to 0.938). Neither is a terrain problem.

`para_glider` is the genuine one, and the ceiling is not the lever: its entity-derived
ceiling is already 3212 so nothing is being cut, and forcing it lower collapses the render
from 2482 faces to 104 -- that map's playable space is in the air. We paint 46.6% of the
canvas against the shipped 35.4%, so the renderer over-paints open terrain. `para_hedgerow`
sits in the defective table on a marginal transform but its underlying IoU is 0.62 either
way, so it belongs to the same class. **The paratrooper/open-terrain maps are a real
renderer gap and this audit does not solve them.**

⚠️ A claim on the `overview-bmp-renderer` branch that `para_glider` is a shipped scale defect
("0.88 against 1.08") **is not reproduced here**. Sweeping scale 0.80–1.10 gives a single
peak at 1.00 (0.712), falling away on both sides. The disagreement is content, not scale.

## Where the renderer is right

Twenty-five workstation triples and seven fleet triples reach their best at the identity
transform at or above 0.85, topping out at `dod_thunder2_b2` 0.987, `dod_cal_sherman2`
0.983, `dod_koln` 0.981 and `dod_halle` 0.976. Both projection branches are represented:
`dod_zafod` (ROTATED 1) scores 0.976 and `dod_muhle_b2` (ROTATED 1) 0.906.

The fleet figures reproduce the ones recorded on the renderer branch to three decimals --
`dod_koln` 0.981, `dod_thunder` 0.965, `dod_ramelle` 0.964, `dod_heutau` 0.973 -- which is
the cross-check that the two measurements are of the same thing.

## Missing and parentless assets on the fleet

Measured on `dod-27015` with a case-insensitive match and a positive control.

- ⚠️ **`dod_flugplatz.BMP` is upper case on the fleet.** The engine requests
  `overviews/dod_flugplatz.bmp`; Linux is case-sensitive; the file is present on disk and
  invisible in game. **Rename it to `.bmp`.** The workstation copy is upper case too, which
  is why this survived — it works on Windows.
- **Three descriptors have no image at all**: `dod_anzio2_test`, `dod_emmanuel_b3`,
  `dod_railyard_b5`. The first two have a `.bsp`, so those maps are playable with a
  descriptor pointing at nothing.
- **`dod_zafod`** has an overview pair and no `.bsp`.
- **Eleven maps have no overview at all**: `dod_anjou_a3`, `dod_lennon5_test`,
  `dod_pandemic_aim`, `dod_rails_ktp1`, `dod_railyard_test`, `dod_saints2_b1`,
  `dod_saints2_b3a`, `dod_saints_b1`, `dod_saints_b5`, `dod_saints_b8`, `dod_saints_test`.
  ➡️ **Three of those have a usable overview in the workstation corpus** that could be copied
  across, and it was scored here against the map's own descriptor: `dod_anjou_a3` 0.971,
  `dod_railyard_test` 0.902, `dod_rails_ktp1` 0.781. The first two are as good as the best
  assets in either corpus. Copying is an operator call, not something this audit does.

On the workstation, six images have no descriptor and three descriptors have no image.

## Known limits

- The comparison is a **footprint** comparison. It sees where geometry is and is not, and it
  is blind to anything drawn inside a filled region. A picture can be correctly placed and
  still be the wrong map.
- The search is nearest-neighbour over integer offsets and a scale grid refined to 0.005. It
  re-centres until the optimum is interior and flags a result still on the boundary, but a
  broad flat optimum still produces a confident-looking transform — which is the caveat on
  `dod_overlord` and `dod_dog1` above.
- **No rotation is searched.** A ROTATED flag set wrongly in a descriptor would show up as a
  very low IoU that no transform improves, and would be bucketed `renderer-gap`. Nothing in
  either corpus currently looks like that, but the bucket cannot distinguish it.
- 37 workstation triples and 6 fleet triples are unscored because their images key no
  pixels. That is a real hole in coverage, not a clean bill.
