#!/usr/bin/env python3
"""Emit a map's spectator overview descriptor (`overviews/<map>.txt`) from its BSP.

Three maps ship for S10 with no overview. The descriptor is four numbers, and
all four are solvable from the BSP rather than nudged by eye: the projection is
fixed by the engine, so inverting it against the map's own bounds is arithmetic.

THE PROJECTION IS NOT INFERRED. `cl_dll/hud_spectator.cpp` in the HLSDK
(`CHudSpectator::DrawOverviewLayer`) lays the overview out as a world-space quad
grid under a top-down camera, and both branches of its `if (rotated)` reduce to
an affine map with scale `zoom / 8`:

    ROTATED 0    px = W/2 - s*(world_y - origin_y)      ROTATED 1   px = W/2 + s*(world_x - origin_x)
                 py = H/2 - s*(world_x - origin_x)                  py = H/2 - s*(world_y - origin_y)

The whole of ROTATED is which world axis lands on the image's 1024-px axis:
ROTATED 0 spends the wide axis on world Y, ROTATED 1 on world X. Both cover
8192/zoom world units across 1024 px and 6144/zoom across 768 -- which is why
`rotated` is not a taste call. It is `extent_x > extent_y`, and that rule
reproduces the flag on all 67 overviews the fleet ships.

Z IS TWO SEPARATE NUMBERS AND NEITHER MOVES A PIXEL.

  * `ORIGIN`'s third component is the overview camera's pivot height
    (`V_GetMapFreePosition` orbits it); it never reaches the projection. Across
    the shipped corpus it tracks the vertical centre of the playable volume --
    median deviation -4 units, quartiles [-64, +16] -- so that is what we emit.
  * layer `HEIGHT` is the z-plane the image quad is drawn on. It has to sit at
    or below the lowest floor or the picture draws over the players standing on
    it; the SDK's own comment next to it reads `gOverviewData.z_min - 32`. The
    shipped files agree: the single commonest value is exactly `world z-min - 1`.

Neither affects where a position lands on the image, so a human who dislikes
either can retune it without invalidating any coordinate drawn through ZOOM and
ORIGIN x/y.

CONTRACT WITH THE RENDERER. The bounds framed here are BSP **model 0** --
worldspawn brushes, sky brushes included, brush entities (models 1..n)
excluded; see `bsp_bounds.py`. Anything drawing the matching BMP must frame the
same box through the same projection, or the descriptor and the image disagree
and every position drawn on it is wrong. `--json` emits the box for exactly that
purpose.

Usage:
  python3 scripts/make_overview_descriptor.py dod_kalt.bsp
  python3 scripts/make_overview_descriptor.py maps/*.bsp --out-dir dod/overviews
  python3 scripts/make_overview_descriptor.py dod_kalt.bsp --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bsp_bounds import Bounds, BspError, read  # noqa: E402

OVERVIEW_WIDTH, OVERVIEW_HEIGHT = 1024, 768
WORLD_UNITS_PER_PIXEL = 8.0
# Below the lowest floor, not on it. The SDK comment reads z_min - 32; the shipped
# corpus's commonest value is z_min - 1. Either renders correctly; this matches the corpus.
HEIGHT_BELOW_FLOOR = 1.0


class UnusableMap(ValueError):
    """The BSP cannot be framed, and emitting a descriptor anyway would be worse."""


def choose_rotated(bounds: Bounds) -> int:
    """1 when the map is longer along world X, so the longer axis gets the wider image axis.

    A tie goes to 0: it is the convention 55 of the fleet's 67 overviews use, and
    at equal extents the two are equally tight.
    """
    extent_x, extent_y, _ = bounds.extent
    return 1 if extent_x > extent_y else 0


def solve_zoom(bounds: Bounds, rotated: int, width=OVERVIEW_WIDTH, height=OVERVIEW_HEIGHT,
               margin: float = 0.0) -> float:
    """Largest ZOOM that still fits `bounds` on a width x height overview.

    Inverts `px = W/2 -+ (zoom/8)*(world - origin)`. `margin` is the fraction of
    each axis to leave empty (0.05 keeps a 5% border).
    """
    if not 0.0 <= margin < 1.0:
        raise UnusableMap(f"margin must be in [0, 1), got {margin}")
    extent_x, extent_y, _ = bounds.extent
    if extent_x <= 0 or extent_y <= 0:
        raise UnusableMap(f"{bounds.source}: world extent is {extent_x} x {extent_y}")
    across, down = (extent_x, extent_y) if rotated else (extent_y, extent_x)
    usable = 1.0 - margin
    return min(WORLD_UNITS_PER_PIXEL * width * usable / across,
               WORLD_UNITS_PER_PIXEL * height * usable / down)


def project(world_x: float, world_y: float, zoom: float, origin_x: float, origin_y: float,
            rotated: int, width=OVERVIEW_WIDTH, height=OVERVIEW_HEIGHT) -> tuple[float, float]:
    """World position to overview pixel, both ROTATED conventions. See module docstring."""
    scale = zoom / WORLD_UNITS_PER_PIXEL
    if rotated:
        return (width / 2.0 + scale * (world_x - origin_x),
                height / 2.0 - scale * (world_y - origin_y))
    return (width / 2.0 - scale * (world_y - origin_y),
            height / 2.0 - scale * (world_x - origin_x))


def solve(bsp, rotated: int | None = None, margin: float = 0.0,
          width=OVERVIEW_WIDTH, height=OVERVIEW_HEIGHT) -> dict:
    """Every field of the descriptor, plus the bounds the renderer must reuse."""
    world = bsp.world_bounds()
    playable = bsp.empty_leaf_bounds() or world
    if rotated is None:
        rotated = choose_rotated(world)
    if rotated not in (0, 1):
        raise UnusableMap(f"rotated must be 0 or 1, got {rotated!r}")
    zoom = solve_zoom(world, rotated, width, height, margin)
    centre_x, centre_y, _ = world.centre
    return {
        "map_name": bsp.name,
        "zoom": zoom,
        "origin": (centre_x, centre_y, playable.centre[2]),
        "rotated": rotated,
        "height": world.mins[2] - HEIGHT_BELOW_FLOOR,
        "image": f"overviews/{bsp.name}.bmp",
        "dimensions": {"width": width, "height": height},
        "bounds": {
            "source": world.source,
            "mins": list(world.mins),
            "maxs": list(world.maxs),
            "brush_entities_included": False,
            "sky_brushes_included": True,
        },
        "playable_bounds": {
            "source": playable.source,
            "mins": list(playable.mins),
            "maxs": list(playable.maxs),
        },
    }


def _number(value: float) -> str:
    """Two decimals, as the shipped files write them. COM_ParseFile takes any float."""
    return f"{value:.2f}"


def render(solved: dict) -> str:
    """The descriptor text. LF, tab-indented, `global`/`layer` blocks -- the shipped shape."""
    origin = " ".join(_number(v) for v in solved["origin"])
    return (
        f"// overview description file for {solved['map_name']}\n"
        f"// generated by scripts/make_overview_descriptor.py from the map's own BSP bounds\n"
        f"// framing: {solved['bounds']['source']} "
        f"{[round(v) for v in solved['bounds']['mins']]}..{[round(v) for v in solved['bounds']['maxs']]}\n"
        "\n"
        "global \n"
        "{\n"
        f"\tZOOM\t{_number(solved['zoom'])}\n"
        f"\tORIGIN\t{origin}\n"
        f"\tROTATED\t{solved['rotated']}\n"
        "}\n"
        "\n"
        "layer \n"
        "{\n"
        f"\tIMAGE\t\"{solved['image']}\"\n"
        f"\tHEIGHT\t{_number(solved['height'])}\n"
        "}\n"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("bsp", nargs="+", type=Path, help="one or more <map>.bsp files")
    parser.add_argument("--out-dir", type=Path,
                        help="write <map>.txt here instead of printing to stdout")
    parser.add_argument("--rotated", choices=("auto", "0", "1"), default="auto",
                        help="override the extent_x > extent_y rule (default: auto)")
    parser.add_argument("--margin", type=float, default=0.0,
                        help="fraction of each axis to leave empty, e.g. 0.05 (default: 0)")
    parser.add_argument("--json", action="store_true",
                        help="emit the solved facts and the framed bounds instead of the .txt")
    args = parser.parse_args(argv)

    failures = 0
    for path in args.bsp:
        try:
            solved = solve(read(path),
                           rotated=None if args.rotated == "auto" else int(args.rotated),
                           margin=args.margin)
        except (BspError, UnusableMap, OSError) as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            failures += 1
            continue
        if args.json:
            print(json.dumps(solved, indent=2, default=list))
            continue
        text = render(solved)
        if args.out_dir:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            target = args.out_dir / f"{solved['map_name']}.txt"
            target.write_text(text, encoding="ascii", newline="\n")
            print(f"wrote {target}")
        else:
            sys.stdout.write(text)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
