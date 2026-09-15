"""World-to-overview projection matrices from a map's spectator overview.

GoldSrc ships a per-map spectator overview as `overviews/<map>.txt` plus a
BMP. `build_competitive_spatial_configs.py` already parses the four numbers
that matter into each `config/analytics/spatial_maps/<map>.json` under
`overview`; this turns them into the 3x3 affine pair the website draws with.

The scale is `zoom / 8` because one overview pixel is 8 world units at zoom
1.0, and the map origin lands on the image centre. ROTATED picks which world
axis spends the image's 1024-px axis:

    ROTATED 0                              ROTATED 1
    px = width/2  - s*(world_y - origin_y) px = width/2  + s*(world_x - origin_x)
    py = height/2 - s*(world_x - origin_x) py = height/2 - s*(world_y - origin_y)

Matrices are row-major and unrounded. They are a derivation, not a
measurement -- rounding them moves every point that is drawn through them.

ROTATED 1 was refused here until 2026-09-15 as untested. It is now read off
`CHudSpectator::DrawOverviewLayer` in the HLSDK, whose `if (rotated)` branch
walks its quad grid along world X with a positive step where the other walks
world Y with a negative one, and confirmed against the fleet's own overviews:
`extent_x > extent_y` reproduces the shipped flag on 67 of 67 maps, and
silhouette overlap against the shipped BMPs picks this convention over the
seven alternatives on every map whose footprint leaves enough background to
tell them apart. `scripts/make_overview_descriptor.py` carries the derivation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEME = "goldsrc_spectator_v1"
WORLD_UNITS_PER_PIXEL = 8.0
VERSION_DIGEST_CHARS = 12
_FACTS = ("scheme", "map_name", "zoom", "origin_x", "origin_y", "rotated", "width", "height")


class UnsupportedOverview(ValueError):
    """The overview cannot be projected, and guessing would be worse."""


def _facts(map_name: str, overview: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in ("zoom", "origin_x", "origin_y", "width", "height")
               if overview.get(key) is None]
    if missing:
        raise UnsupportedOverview(f"{map_name}: overview is missing {', '.join(missing)}")
    zoom = float(overview["zoom"])
    if zoom <= 0:
        raise UnsupportedOverview(f"{map_name}: overview zoom must be positive, got {zoom}")
    width, height = int(overview["width"]), int(overview["height"])
    if width <= 0 or height <= 0:
        raise UnsupportedOverview(f"{map_name}: overview is {width}x{height}")
    return {
        "scheme": SCHEME,
        "map_name": map_name,
        "zoom": zoom,
        "origin_x": float(overview["origin_x"]),
        "origin_y": float(overview["origin_y"]),
        "rotated": bool(overview.get("rotated")),
        "width": width,
        "height": height,
    }


def geometry_version(facts: dict[str, Any]) -> str:
    """Reproducible id for one projection: scheme plus a digest of its inputs.

    Canonical JSON over a fixed key order, so the same overview always yields
    the same string and any changed field yields a different one.
    """
    payload = json.dumps({key: facts[key] for key in _FACTS}, sort_keys=True,
                         separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{SCHEME}-{digest[:VERSION_DIGEST_CHARS]}"


def overview_matrices(map_name: str, overview: dict[str, Any]) -> dict[str, Any]:
    """Projection pair for one map's overview block. Raises UnsupportedOverview."""
    facts = _facts(map_name, overview)
    scale = facts["zoom"] / WORLD_UNITS_PER_PIXEL
    half_width, half_height = facts["width"] / 2.0, facts["height"] / 2.0
    inverse = 1.0 / scale

    if facts["rotated"]:
        # World X runs along the image's wide axis, left to right.
        world_to_pixel = [
            [scale, 0.0, half_width - scale * facts["origin_x"]],
            [0.0, -scale, half_height + scale * facts["origin_y"]],
            [0.0, 0.0, 1.0],
        ]
        pixel_to_world = [
            [inverse, 0.0, facts["origin_x"] - half_width * inverse],
            [0.0, -inverse, half_height * inverse + facts["origin_y"]],
            [0.0, 0.0, 1.0],
        ]
    else:
        world_to_pixel = [
            [0.0, -scale, half_width + scale * facts["origin_y"]],
            [-scale, 0.0, half_height + scale * facts["origin_x"]],
            [0.0, 0.0, 1.0],
        ]
        pixel_to_world = [
            [0.0, -inverse, half_height * inverse + facts["origin_x"]],
            [-inverse, 0.0, half_width * inverse + facts["origin_y"]],
            [0.0, 0.0, 1.0],
        ]
    return {
        "map_name": map_name,
        "scheme": SCHEME,
        "geometry_version": geometry_version(facts),
        "dimensions": {"width": facts["width"], "height": facts["height"]},
        "source": {key: facts[key] for key in ("zoom", "origin_x", "origin_y", "rotated")},
        "world_to_pixel": world_to_pixel,
        "pixel_to_world": pixel_to_world,
    }


def project(matrix: list[list[float]], u: float, v: float) -> tuple[float, float]:
    """Apply a row-major 3x3 affine to a point."""
    return (matrix[0][0] * u + matrix[0][1] * v + matrix[0][2],
            matrix[1][0] * u + matrix[1][1] * v + matrix[1][2])
