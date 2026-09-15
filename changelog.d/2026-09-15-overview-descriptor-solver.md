### `scripts`: overview descriptors are solved from the BSP, and `ROTATED 1` is settled (2026-09-15)

Three maps ship for S10 with no `overviews/<map>.txt`. `make_overview_descriptor.py` reads a
BSP and emits one: `ZOOM`, `ORIGIN`, `ROTATED` and layer `HEIGHT`, all four solved from the
map's own bounds rather than nudged by eye. `bsp_bounds.py` does the reading — lump 14 for
worldspawn bounds, lump 10 for the playable volume, lump 0 for entities.

**`ROTATED 1` is no longer refused.** `spatial_map_geometry.py` raised `UnsupportedOverview`
on it because the axis swap was untested and a wrong one renders a confident picture of the
wrong place. It is now read off `CHudSpectator::DrawOverviewLayer` in the HLSDK, whose
`if (rotated)` branch walks its quad grid along world X with a positive step where the other
walks world Y with a negative one:

    ROTATED 0                               ROTATED 1
    px = W/2 - (zoom/8)*(world_y - origin_y) px = W/2 + (zoom/8)*(world_x - origin_x)
    py = H/2 - (zoom/8)*(world_x - origin_x) py = H/2 - (zoom/8)*(world_y - origin_y)

ROTATED is therefore not a taste call: it is which world axis spends the image's 1024-px
axis, so it is `extent_x > extent_y`. Three independent measurements against the 67
overviews the fleet actually ships, not the one map the refusal named:

- The rule reproduces the shipped flag on **67 of 67** maps, twelve of which ship `ROTATED 1`.
- Projecting BSP entity origins onto the shipped BMP separates the two axis pairings cleanly —
  on `dod_saints2_b3e`, spawns and capture points land on drawn map at 100% under the rotated
  reading and 18% under the unrotated one, and the control inverts on `dod_anjou_a5`.
- Silhouette overlap between the empty-leaf raster and each shipped BMP ranks the derived
  convention first against all seven alternatives on every map whose footprint leaves enough
  background to tell them apart: `dod_thunder` (`ROTATED 1`) scores 0.933 IoU against 0.315 for
  the next-best, and five `ROTATED 0` controls agree with margins of +0.035 to +0.565.

**`ORIGIN`'s third component and layer `HEIGHT` are different things and neither moves a
pixel.** `ORIGIN z` is the overview camera's pivot height (`V_GetMapFreePosition` orbits it);
it tracks the vertical centre of the playable volume across the corpus, median deviation -4
units. `HEIGHT` is the z-plane the image quad is drawn on and must sit below the lowest floor
or the picture draws over the players; the SDK comment beside it reads `z_min - 32`, and the
commonest shipped value is exactly `z_min - 1`. A reader who dislikes either can retune it
without invalidating a single coordinate drawn through `ZOOM` and `ORIGIN x/y`.

Round-trip against all 67 shipped descriptors, framing BSP model 0: `ROTATED` agrees 67/67,
the solved `ZOOM` has a median ratio of 0.998 to the human choice, and the worst corner of the
map lands a median 20 px from where the shipped file puts it on a 1024x768 canvas — 42 of 67
within one 128-px tile, 66 of 67 within four. The tail is mostly the shipped file's, not the
solver's: sixteen maps carry a descriptor authored for a sibling revision whose worldspawn
differs (the anzio, lennon, saints and solitude families), and dropping those moves the median
to 16 px and the zoom ratio to 0.999.

**Bounds convention, for whatever renders the matching `.bmp`:** BSP model 0's `mins`/`maxs` —
worldspawn brushes, sky brushes included, brush entities (models 1..n) excluded. Descriptor and
image must frame the same box or every position drawn on the result is wrong, so `--json`
emits it.
