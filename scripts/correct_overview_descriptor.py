#!/usr/bin/env python3
"""Rewrite `overviews/<map>.txt` so it describes the picture that actually ships.

`audit_overview_corpus.py` measures, per map, the scale and pixel offset a
correct render must go through to land on the shipped BMP. That transform *is*
the misplacement: every player dot drawn through the descriptor lands that far
from where the picture says it should. The cheap remedy is to move the
descriptor onto the picture -- one line of a text file against re-cutting an
image players have read for seasons.

THE DERIVATION. The audit's transform takes the render to the shipped image,
about the image centre C:

    target = C + scale*(src - C) + (dx, dy)

and the projection (`render_overview_bmp.Projection`, scale s = zoom/8) is

    ROTATED 0   px = W/2 - s*(world_y - origin_y)    py = H/2 - s*(world_x - origin_x)
    ROTATED 1   px = W/2 + s*(world_x - origin_x)    py = H/2 - s*(world_y - origin_y)

Substituting and matching coefficients gives s' = scale*s, so `zoom' =
scale*zoom`, and an origin moved by d/s' **world** units. Which origin
component moves, and with which sign, is not free: the pixel axes are swapped
and negated relative to world x/y, and the swap differs by ROTATED.

    ROTATED 0   origin_x' = origin_x + dy/s'    origin_y' = origin_y + dx/s'
    ROTATED 1   origin_x' = origin_x - dx/s'    origin_y' = origin_y + dy/s'

⚠️ A pixel dx is not a world x. Reading `dx` onto `origin_x` is the obvious
mistake and it produces a descriptor that looks corrected and is wrong on both
axes.

WHAT THIS DOES NOT DO. It does not decide that a map needs correcting. A large
scale bought with a modest IoU gain can be the search sliding along a flat
ridge rather than a real defect -- check the per-axis footprint extents and the
entity-anchor test in `docs/OVERVIEW_DESCRIPTOR_FIXES.md` before trusting one.

Usage:
    python scripts/correct_overview_descriptor.py overviews/dod_schwetz.txt \\
        --scale 1.045 --dx -1 --dy -1 --out-dir corrected/
    python scripts/correct_overview_descriptor.py overviews/dod_rr2_test.txt \\
        --dx -31 --dy 23 --in-place
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.render_overview_bmp import (  # noqa: E402
    WORLD_UNITS_PER_PIXEL, Projection, read_descriptor)

ZOOM_RE = re.compile(rb"(\bZOOM[ \t]+)([-+0-9.]+)", re.I)
ORIGIN_RE = re.compile(rb"(^[ \t]*ORIGIN[ \t]+)([-+0-9.]+)([ \t]+)([-+0-9.]+)", re.I | re.M)
DECIMALS = 2


class DescriptorError(ValueError):
    """The file does not carry the line this needs, and guessing would be worse."""


def corrected(projection: Projection, scale: float = 1.0, dx: int = 0, dy: int = 0) -> Projection:
    """The projection whose render IS the shipped image, given that transform."""
    zoom = projection.zoom * scale
    s = zoom / WORLD_UNITS_PER_PIXEL
    if projection.rotated:
        origin_x = projection.origin_x - dx / s
        origin_y = projection.origin_y + dy / s
    else:
        origin_x = projection.origin_x + dy / s
        origin_y = projection.origin_y + dx / s
    return Projection(zoom, origin_x, origin_y, projection.rotated,
                      projection.width, projection.height)


def rewrite(raw: bytes, zoom: float, origin_x: float, origin_y: float,
            decimals: int = DECIMALS) -> bytes:
    """Replace ZOOM and ORIGIN x/y in place, touching nothing else.

    Edits the code half of each line only: `dod_aleutian2_test3` opens with
    `// Overview: Zoom 1.31, Map Origin (738.00, ...)`, and a regex run over the
    whole file rewrites that comment, leaves the live ZOOM alone, and reads as a
    clean edit. ORIGIN's third component is the overview camera's pivot height
    and never reaches the projection, so it is left where the author put it.
    """
    def num(value: float) -> bytes:
        return f"{value:.{decimals}f}".encode()

    done = {"zoom": 0, "origin": 0}
    out = []
    for line in raw.splitlines(keepends=True):
        code, sep, comment = line.partition(b"//")
        if not done["zoom"]:
            code, n = ZOOM_RE.subn(lambda m: m.group(1) + num(zoom), code, count=1)
            done["zoom"] += n
        if not done["origin"]:
            code, n = ORIGIN_RE.subn(
                lambda m: m.group(1) + num(origin_x) + m.group(3) + num(origin_y),
                code, count=1)
            done["origin"] += n
        out.append(code + sep + comment)
    if done["zoom"] != 1 or done["origin"] != 1:
        raise DescriptorError(
            f"matched ZOOM {done['zoom']} time(s) and ORIGIN {done['origin']} time(s); "
            "expected exactly one of each outside a comment")
    return b"".join(out)


def correct_file(path: Path, scale: float, dx: int, dy: int,
                 decimals: int = DECIMALS) -> tuple[bytes, Projection, Projection]:
    before = read_descriptor(path)
    after = corrected(before, scale, dx, dy)
    body = rewrite(path.read_bytes(), after.zoom, after.origin_x, after.origin_y, decimals)
    return body, before, after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("descriptor", type=Path, nargs="+", help="overviews/<map>.txt")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="scale the render needs to land on the shipped image")
    parser.add_argument("--dx", type=int, default=0, help="pixels right, same convention")
    parser.add_argument("--dy", type=int, default=0, help="pixels down, same convention")
    parser.add_argument("--decimals", type=int, default=DECIMALS,
                        help=f"decimals to write (default {DECIMALS}, which is what "
                             "every shipped descriptor uses)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out-dir", type=Path, help="write <map>.txt here")
    group.add_argument("--in-place", action="store_true")
    args = parser.parse_args(argv)

    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
    for path in args.descriptor:
        try:
            body, before, after = correct_file(path, args.scale, args.dx, args.dy,
                                               args.decimals)
        except (DescriptorError, OSError) as exc:
            print(f"error: {path}: {exc}", file=sys.stderr)
            return 1
        target = path if args.in_place else args.out_dir / path.name
        target.write_bytes(body)
        print(f"{path.stem}: zoom {before.zoom} -> {after.zoom:.{args.decimals}f}, "
              f"origin ({before.origin_x}, {before.origin_y}) -> "
              f"({after.origin_x:.{args.decimals}f}, {after.origin_y:.{args.decimals}f}) "
              f"rotated {int(before.rotated)} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
