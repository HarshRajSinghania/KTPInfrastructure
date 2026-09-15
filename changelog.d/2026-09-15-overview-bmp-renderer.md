### `scripts`: render a map's spectator-overview BMP from its BSP (2026-09-15)

`scripts/render_overview_bmp.py` takes a map name or `.bsp` path and writes the 1024x768
8-bit overview BMP the engine keys on, plus a `<map>.overview.json` sidecar holding the
projection it was drawn with. It is stdlib-only and reads the BSP v30 geometry lumps
directly, the way `precache_audit.py` already reads lump 0.

- **Format**, re-verified against all 68 shipped BMPs on Dallas and the open reimplementation
  in xash3d-fwgs: the transparent key is the palette **colour** RGB(0,255,0) at any index
  (shipped files use 0, 2, 236, 244, 250, 255 interchangeably), not index 0 or 255. The
  palette is built by hand, never quantised, so no entry but the key comes within 100 of it;
  `dod_anjou_a5`'s green fringe came from eight near-green gradient entries a converter
  produced, and a test carries that palette as a control.
- **Projection** is the one `spatial_map_geometry.py` ships, derived from
  `hud_spectator.cpp::DrawOverviewLayer`, and now the ROTATED 1 branch too:
  `px = W/2 + s*(x - ox)`, `py = H/2 - s*(y - oy)`. Verified on `dod_thunder` (ROTATED 1):
  footprint IoU 0.965 against the shipped BMP, 0.188 with the rotation flag flipped.
- **Bounds convention**, for the paired `.txt`: ZOOM/ORIGIN come from `models[0].mins/maxs`
  (worldspawn, sky brushes included, brush entities excluded). On the fleet's pairs that box's
  centre is the shipped ORIGIN and `fit_overview()` reproduces the shipped ZOOM to the
  shipped two decimals (anzio2_test3, armory_b6, heutau, flugplatz, railyard, glider, siena,
  overlord, thunder). `--descriptor overviews/<map>.txt` makes the image follow an existing
  descriptor instead.
- **Roofs**: only upward-facing faces are drawn, painter's order by height, and faces above a
  ceiling are dropped. The default ceiling is the highest floor-anchored point entity
  (spawns, control points, ammo, weapons) plus 160 units of headroom; `--cut Z` and
  `--no-cut` override it. A depth buffer marks every step down of 24+ units with a dark
  outline so walls and ledges read without textures.

Footprint against shipped BMPs that use the key, rendered with the shipped descriptor:
halle 0.976, koln 0.981, thunder2 0.980, heutau 0.973, thunder 0.965, ramelle 0.961,
anjou_a4 0.953, siena_test 0.937, armory_b6 0.915, railyard_b6 0.899, aleutian2 0.892.
Below 0.8 the mismatch is a uniform scale: `dod_anzio2_test3`'s shipped image (also shipped,
byte-identical, as `dod_anzio3_b1.bmp` and `dod_anzio_test4d.bmp`) is drawn at an effective
zoom of 0.77 against the 1.03 in its own `.txt`, and `para_glider` at 0.88 against 1.08.
Those are shipped-asset defects the renderer does not reproduce.

Tests: `tests/unit/test_render_overview_bmp.py` builds synthetic BSPs for the format, palette,
projection, fit, ceiling and brush-entity rules; the real-map footprint checks run only with
`KTP_OVERVIEW_CORPUS` set and say so when skipped.
