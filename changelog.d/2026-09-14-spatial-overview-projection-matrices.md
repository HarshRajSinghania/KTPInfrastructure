### `scripts`: world-to-overview projection matrices have a producer (2026-09-14)

The three `world_to_pixel` / `pixel_to_world` matrices the public match report draws with live
only in `searse/keep-the-prac`, hand-committed, with no generator and no test. This repo had no
code that emitted, derived or even named them — `world_to_pixel` appeared once on `main`, in a
docstring.

`spatial_map_geometry.py` derives them from the `overview` block
`build_competitive_spatial_configs.py` already parses out of each map's `overviews/<map>.txt`
and BMP header: scale is `zoom / 8`, because one overview pixel is 8 world units at zoom 1.0,
and the map origin lands on the image centre.

- The derivation reproduces all three shipped matrices to 1e-9, and that equality is pinned as
  a test. Replacing the hand-maintained table is therefore a provable no-op rather than a
  hopeful one.
- `geometry_version` is a digest over the canonical projection facts, so it is reproducible and
  moves when any input does. The three strings in the website's table are not reproducible —
  nothing in either repo computes them — so the pin compares matrices, never version strings.
- `ROTATED 1` raises `UnsupportedOverview`. The axis swap for it is untested against any real
  map, and getting it wrong does not look wrong: it renders a confident picture of the wrong
  place. `dod_saints2_b3e` is the one map in the current pool that ships `ROTATED 1`.
