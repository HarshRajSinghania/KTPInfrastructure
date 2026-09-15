#!/usr/bin/env python3
"""Render a GoldSrc spectator-overview BMP straight from a DoD map's BSP.

The overview a map ships is `overviews/<map>.bmp` (the picture) plus
`overviews/<map>.txt` (ZOOM / ORIGIN / ROTATED, which say where the picture
sits in the world). This tool makes the picture. It reads the BSP's geometry
lumps, keeps the faces that point upward and sit below a ceiling, paints them
top-down as a height-shaded flat render, and writes the one BMP shape the
engine accepts.

What the engine accepts, measured on the fleet's shipped overviews and matched
against the open reimplementation in xash3d-fwgs (`img_bmp.c`, IL_OVERVIEW):

- 1024x768, 8 bits per pixel, 40-byte BITMAPINFOHEADER, BI_RGB, 256 palette
  entries, pixel data at offset 1078, rows bottom-up, no row padding
  (1024 is a multiple of 4). File size 787,510 bytes.
- The transparent key is the palette COLOUR RGB(0,255,0), at any index. Every
  palette entry that is exactly that colour becomes fully transparent; every
  other entry is fully opaque. Shipped files key on index 0, 2, 236, 244,
  250, 255 and the engine treats them alike.
- Near-greens are NOT transparent. `dod_anjou_a5` was quantised by a generic
  converter into eight near-green gradient entries around the key; edges that
  landed on them fringe green in game. The palette here is built by hand and
  never quantised, so nothing but the key comes anywhere near the key.

Projection, shared with `scripts/spatial_map_geometry.py` and derived from
`cl_dll/hud_spectator.cpp::DrawOverviewLayer` (the tile loop, both branches):

    s  = zoom / 8                      # one pixel is 8 world units at zoom 1
    ROTATED 0:  px = W/2 - s * (world_y - origin_y)
                py = H/2 - s * (world_x - origin_x)
    ROTATED 1:  px = W/2 + s * (world_x - origin_x)
                py = H/2 - s * (world_y - origin_y)

Bounds convention for deriving ZOOM/ORIGIN when no descriptor is supplied:
the worldspawn model's bounding box, `models[0].mins/maxs` (lump 14), which
includes sky brushes and excludes brush entities. That is what the shipped
descriptors were cut from: on the fleet's 68 pairs the centre of that box IS
the shipped ORIGIN for the large majority, and `fit_overview()` reproduces
the shipped ZOOM to within rounding on the same maps.

Roofs: a top-down render of every upward face shows rooftops and nothing
else. The default ceiling is derived from the entity lump -- the highest
floor-anchored point entity (spawns, control points, ammo/health, weapons)
plus standing headroom -- so roofs above the top playable floor are dropped
and everything a player can stand on is kept. `--cut` overrides it, `--no-cut`
draws everything.

Usage:
    python scripts/render_overview_bmp.py dod_anzio3_b1 --maps-dir maps/ --out out/
    python scripts/render_overview_bmp.py path/to/dod_x.bsp --descriptor overviews/dod_x.txt --out out/

Writes `<out>/<map>.bmp` and `<out>/<map>.overview.json` (the projection facts
the image was drawn with, so the .txt half can be checked against it).
"""
from __future__ import annotations

import argparse
import json
import math
from array import array
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

WIDTH, HEIGHT = 1024, 768
WORLD_UNITS_PER_PIXEL = 8.0
KEY_RGB = (0, 255, 0)
KEY_INDEX = 0
MIN_KEY_DISTANCE = 100.0        # every non-key palette entry sits at least this far from the key
BSP_VERSION = 30
LUMP_ENTITIES, LUMP_PLANES, LUMP_TEXTURES, LUMP_VERTICES = 0, 1, 2, 3
LUMP_TEXINFO, LUMP_FACES, LUMP_EDGES, LUMP_SURFEDGES, LUMP_MODELS = 6, 7, 12, 13, 14
LUMP_COUNT = 15

# Tool textures that never render in game; old compilers leave some in the BSP.
SKIP_TEXTURES = frozenset({"sky", "null", "aaatrigger", "clip", "hint", "skip", "bevel", "origin"})
# Brush entities whose faces are part of what a player sees from above.
DRAWN_BRUSH_CLASSES = frozenset({
    "func_wall", "func_wall_toggle", "func_illusionary", "func_breakable", "func_pushable",
    "func_door", "func_door_rotating", "func_rotating", "func_button", "func_train",
    "func_tracktrain", "func_water", "func_conveyor", "func_platrot", "func_plat",
    "dod_object", "func_detail",
})
# Point entities that sit on a floor players reach; their z anchors the ceiling.
FLOOR_ANCHOR_CLASSES = ("info_player_", "dod_control_point", "item_", "ammo_", "weapon_",
                        "dod_score_ent", "info_doddetect")
STANDING_HEADROOM = 160.0       # player is 72 tall; roofs sit well above floor + this
FALLBACK_CEILING_FRACTION = 0.6  # no anchors: cut this far up the world's z extent

PALETTE_GREY_LO, PALETTE_GREY_HI = 72, 216
GREY_COUNT = 253                # palette 1..253
EDGE_INDEX, WATER_INDEX = 254, 255
EDGE_RGB = (28, 28, 28)
WATER_RGB = (58, 96, 168)
EDGE_DROP = 24.0                # a step down of this many units beside a pixel draws an outline


class BspError(ValueError):
    """The file is not a BSP this tool can render."""


@dataclass
class Bsp:
    planes: list[tuple[float, float, float, float]]
    vertices: list[tuple[float, float, float]]
    texture_names: list[str]
    texinfo_miptex: list[int]
    faces: list[tuple[int, int, int, int, int]]   # planenum, side, firstedge, numedges, texinfo
    edges: list[tuple[int, int]]
    surfedges: list[int]
    models: list[tuple[tuple[float, float, float], tuple[float, float, float], int, int]]
    entities: list[dict[str, str]] = field(default_factory=list)


def _lump(data: bytes, index: int) -> bytes:
    offset, length = struct.unpack_from("<ii", data, 4 + 8 * index)
    if offset < 0 or length < 0 or offset + length > len(data):
        raise BspError(f"lump {index} is out of range ({offset}+{length} of {len(data)})")
    return data[offset:offset + length]


def parse_entities(text: str) -> list[dict[str, str]]:
    return [dict(re.findall(r'"([^"]*)"\s+"([^"]*)"', block))
            for block in re.findall(r"\{(.*?)\}", text, re.S)]


def parse_bsp(path: Path) -> Bsp:
    data = path.read_bytes()
    if len(data) < 4 + 8 * LUMP_COUNT:
        raise BspError(f"{path}: too short to hold a BSP header")
    version = struct.unpack_from("<i", data)[0]
    if version != BSP_VERSION:
        raise BspError(f"{path}: BSP version {version}, expected {BSP_VERSION}")

    raw = _lump(data, LUMP_PLANES)
    planes = [struct.unpack_from("<ffff", raw, i * 20) for i in range(len(raw) // 20)]
    raw = _lump(data, LUMP_VERTICES)
    vertices = [struct.unpack_from("<fff", raw, i * 12) for i in range(len(raw) // 12)]

    raw = _lump(data, LUMP_TEXTURES)
    names: list[str] = []
    if len(raw) >= 4:
        count = struct.unpack_from("<i", raw)[0]
        for i in range(max(count, 0)):
            if 4 + 4 * i + 4 > len(raw):
                break
            offset = struct.unpack_from("<i", raw, 4 + 4 * i)[0]
            name = raw[offset:offset + 16].split(b"\0")[0] if 0 <= offset < len(raw) else b""
            names.append(name.decode("latin-1").lower())

    raw = _lump(data, LUMP_TEXINFO)
    texinfo_miptex = [struct.unpack_from("<I", raw, i * 40 + 32)[0] for i in range(len(raw) // 40)]
    raw = _lump(data, LUMP_FACES)
    faces = [struct.unpack_from("<HHihh", raw, i * 20) for i in range(len(raw) // 20)]
    raw = _lump(data, LUMP_EDGES)
    edges = [struct.unpack_from("<HH", raw, i * 4) for i in range(len(raw) // 4)]
    raw = _lump(data, LUMP_SURFEDGES)
    surfedges = [struct.unpack_from("<i", raw, i * 4)[0] for i in range(len(raw) // 4)]

    raw = _lump(data, LUMP_MODELS)
    models = []
    for i in range(len(raw) // 64):
        fields = struct.unpack_from("<9f4iiii", raw, i * 64)
        models.append((fields[0:3], fields[3:6], fields[14], fields[15]))
    if not models:
        raise BspError(f"{path}: no models (lump {LUMP_MODELS} is empty)")

    entities = parse_entities(_lump(data, LUMP_ENTITIES).split(b"\0")[0].decode("latin-1"))
    return Bsp(planes, vertices, names, texinfo_miptex, faces, edges, surfedges, models, entities)


def face_vertices(bsp: Bsp, face_index: int) -> list[tuple[float, float, float]]:
    _, _, first, count, _ = bsp.faces[face_index]
    out = []
    for k in range(count):
        surfedge = bsp.surfedges[first + k]
        edge = bsp.edges[abs(surfedge)]
        out.append(bsp.vertices[edge[0] if surfedge >= 0 else edge[1]])
    return out


def face_texture(bsp: Bsp, face_index: int) -> str:
    texinfo = bsp.faces[face_index][4]
    if 0 <= texinfo < len(bsp.texinfo_miptex):
        miptex = bsp.texinfo_miptex[texinfo]
        if 0 <= miptex < len(bsp.texture_names):
            return bsp.texture_names[miptex]
    return ""


def face_up_component(bsp: Bsp, face_index: int) -> float:
    planenum, side = bsp.faces[face_index][0], bsp.faces[face_index][1]
    nz = bsp.planes[planenum][2]
    return -nz if side else nz


# --- projection ---------------------------------------------------------------

@dataclass(frozen=True)
class Projection:
    zoom: float
    origin_x: float
    origin_y: float
    rotated: bool
    width: int = WIDTH
    height: int = HEIGHT

    @property
    def scale(self) -> float:
        return self.zoom / WORLD_UNITS_PER_PIXEL

    def to_pixel(self, world_x: float, world_y: float) -> tuple[float, float]:
        s = self.scale
        if self.rotated:
            return (self.width / 2.0 + s * (world_x - self.origin_x),
                    self.height / 2.0 - s * (world_y - self.origin_y))
        return (self.width / 2.0 - s * (world_y - self.origin_y),
                self.height / 2.0 - s * (world_x - self.origin_x))

    def as_dict(self) -> dict:
        return {"zoom": self.zoom, "origin_x": self.origin_x, "origin_y": self.origin_y,
                "rotated": self.rotated, "width": self.width, "height": self.height,
                "world_units_per_pixel_at_zoom_1": WORLD_UNITS_PER_PIXEL}


def world_bounds(bsp: Bsp) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The worldspawn model's box, sky included, brush entities excluded."""
    mins, maxs, _, _ = bsp.models[0]
    return tuple(mins), tuple(maxs)


def fit_overview(mins, maxs, rotated: bool = False, decimals: int = 2) -> Projection:
    """ZOOM/ORIGIN that fit a world box on the image, the way the shipped .txt files do.

    Zoom is rounded to the nearest two decimals, which is what the shipped
    descriptors carry (armory_b6 1.2068 -> 1.21, heutau 0.8972 -> 0.90,
    thunder 0.9974 -> 1.00); rounding up overflows the box by under half a
    percent, a few pixels of sky brush.
    """
    extent_x = maxs[0] - mins[0]
    extent_y = maxs[1] - mins[1]
    if extent_x <= 0 or extent_y <= 0:
        raise BspError(f"degenerate world bounds {mins}..{maxs}")
    # Image width covers world Y and height covers world X when not rotated; swapped otherwise.
    across, down = (extent_x, extent_y) if rotated else (extent_y, extent_x)
    zoom = min(WIDTH * WORLD_UNITS_PER_PIXEL / across, HEIGHT * WORLD_UNITS_PER_PIXEL / down)
    zoom = round(zoom, decimals)
    return Projection(zoom, (mins[0] + maxs[0]) / 2.0, (mins[1] + maxs[1]) / 2.0, rotated)


_DESCRIPTOR_RE = {
    "zoom": re.compile(r"\bZOOM\s+([-+]?[\d.]+)", re.I),
    "origin": re.compile(r"\bORIGIN\s+([-+]?[\d.]+)\s+([-+]?[\d.]+)\s+([-+]?[\d.]+)", re.I),
    "rotated": re.compile(r"\bROTATED\s+(\d)", re.I),
}


def read_descriptor(path: Path) -> Projection:
    text = "\n".join(line.split("//", 1)[0]
                     for line in path.read_text(encoding="utf-8", errors="replace").splitlines())
    zoom = _DESCRIPTOR_RE["zoom"].search(text)
    origin = _DESCRIPTOR_RE["origin"].search(text)
    rotated = _DESCRIPTOR_RE["rotated"].search(text)
    if not (zoom and origin and rotated):
        raise BspError(f"{path}: could not parse ZOOM/ORIGIN/ROTATED")
    return Projection(float(zoom.group(1)), float(origin.group(1)), float(origin.group(2)),
                      rotated.group(1) == "1")


# --- ceiling ------------------------------------------------------------------

def _origin_z(entity: dict[str, str]) -> float | None:
    parts = entity.get("origin", "").split()
    if len(parts) != 3:
        return None
    try:
        return float(parts[2])
    except ValueError:
        return None


def floor_anchor_heights(bsp: Bsp) -> list[float]:
    heights = []
    for entity in bsp.entities:
        classname = entity.get("classname", "")
        if classname.startswith(FLOOR_ANCHOR_CLASSES):
            z = _origin_z(entity)
            if z is not None:
                heights.append(z)
    return heights


def default_ceiling(bsp: Bsp) -> tuple[float, str]:
    """Highest floor-anchored entity plus headroom; falls back to a fraction of the z extent."""
    heights = floor_anchor_heights(bsp)
    if heights:
        return max(heights) + STANDING_HEADROOM, f"max floor-anchored entity z + {STANDING_HEADROOM:g}"
    mins, maxs = world_bounds(bsp)
    cut = mins[2] + FALLBACK_CEILING_FRACTION * (maxs[2] - mins[2])
    return cut, f"no floor-anchored entities; {FALLBACK_CEILING_FRACTION:g} of the world z extent"


# --- face selection -----------------------------------------------------------

@dataclass
class DrawnFace:
    points: list[tuple[float, float, float]]   # pixel x, pixel y, world z
    z_mean: float
    z_max: float
    water: bool

    def depth_plane(self) -> tuple[float, float, float]:
        """z = a*px + b*py + c through the polygon; a flat z_mean when it is degenerate."""
        p0 = self.points[0]
        for i in range(1, len(self.points) - 1):
            p1, p2 = self.points[i], self.points[i + 1]
            u = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
            v = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
            nx = u[1] * v[2] - u[2] * v[1]
            ny = u[2] * v[0] - u[0] * v[2]
            nz = u[0] * v[1] - u[1] * v[0]
            if abs(nz) > 1e-6:
                a, b = -nx / nz, -ny / nz
                return a, b, p0[2] - a * p0[0] - b * p0[1]
        return 0.0, 0.0, self.z_mean


def brush_model_indices(bsp: Bsp) -> list[int]:
    out = []
    for entity in bsp.entities:
        model = entity.get("model", "")
        if model.startswith("*") and entity.get("classname", "") in DRAWN_BRUSH_CLASSES:
            try:
                index = int(model[1:])
            except ValueError:
                continue
            if 0 < index < len(bsp.models):
                out.append(index)
    return out


def select_faces(bsp: Bsp, projection: Projection, ceiling: float | None) -> list[DrawnFace]:
    drawn: list[DrawnFace] = []
    for model_index in [0] + brush_model_indices(bsp):
        _, _, first_face, face_count = bsp.models[model_index]
        for face_index in range(first_face, first_face + face_count):
            if face_index >= len(bsp.faces):
                break
            if face_up_component(bsp, face_index) <= 0.0:
                continue
            texture = face_texture(bsp, face_index)
            if texture in SKIP_TEXTURES:
                continue
            verts = face_vertices(bsp, face_index)
            if len(verts) < 3:
                continue
            zs = [v[2] for v in verts]
            z_mean = sum(zs) / len(zs)
            if ceiling is not None and z_mean > ceiling:
                continue
            drawn.append(DrawnFace([projection.to_pixel(v[0], v[1]) + (v[2],) for v in verts],
                                   z_mean, max(zs), texture.startswith("!")))
    return drawn


# --- raster -------------------------------------------------------------------

def fill_convex(pixels: bytearray, width: int, height: int,
                points: list[tuple[float, ...]], colour: int,
                depth: array | None = None, plane: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> int:
    """Scanline-fill a convex polygon at pixel centres. Returns pixels written.

    With `depth`, each written pixel also records the polygon's z there
    (`plane` is z = a*px + b*py + c), which the outline pass reads.
    """
    y_lo = max(0, math.ceil(min(p[1] for p in points) - 0.5))
    y_hi = min(height - 1, math.floor(max(p[1] for p in points) - 0.5))
    if y_hi < y_lo:
        return 0
    n = len(points)
    written = 0
    for row in range(y_lo, y_hi + 1):
        sample = row + 0.5
        x_min, x_max = math.inf, -math.inf
        for i in range(n):
            x0, y0 = points[i][0], points[i][1]
            x1, y1 = points[(i + 1) % n][0], points[(i + 1) % n][1]
            if (y0 <= sample < y1) or (y1 <= sample < y0):
                x = x0 + (sample - y0) * (x1 - x0) / (y1 - y0)
                x_min = min(x_min, x)
                x_max = max(x_max, x)
        if x_max < x_min:
            continue
        left = max(0, math.ceil(x_min - 0.5))
        right = min(width - 1, math.floor(x_max - 0.5))
        if right < left:
            continue
        start = row * width + left
        count = right - left + 1
        pixels[start:start + count] = bytes((colour,)) * count
        if depth is not None:
            a, b, c = plane
            base = b * sample + c
            depth[start:start + count] = array("f", [a * (x + 0.5) + base for x in range(left, right + 1)])
        written += count
    return written


def build_palette() -> list[tuple[int, int, int]]:
    """Index 0 is the key; 1..253 a grey ramp; 254 outline; 255 water. Nothing else is green."""
    palette = [KEY_RGB]
    for i in range(1, GREY_COUNT + 1):
        v = PALETTE_GREY_LO + (PALETTE_GREY_HI - PALETTE_GREY_LO) * (i - 1) / (GREY_COUNT - 1)
        palette.append((int(round(v)),) * 3)
    palette.append(EDGE_RGB)
    palette.append(WATER_RGB)
    return palette


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    pos = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[pos]


def outline_drops(pixels: bytearray, depth: array, width: int, height: int,
                  drop: float = EDGE_DROP) -> int:
    """Darken every drawn pixel that sits above a step down (or the void) on any side."""
    void = -math.inf
    marked = 0
    for y in range(height):
        row = y * width
        for x in range(width):
            i = row + x
            d = depth[i]
            if d == void:
                continue
            limit = d - drop
            if ((x > 0 and depth[i - 1] < limit) or (x + 1 < width and depth[i + 1] < limit)
                    or (y > 0 and depth[i - width] < limit)
                    or (y + 1 < height and depth[i + width] < limit)):
                pixels[i] = EDGE_INDEX
                marked += 1
    return marked


def rasterise(faces: list[DrawnFace], width: int = WIDTH, height: int = HEIGHT,
              outline: bool = True) -> bytearray:
    """Painter's order by height: higher walkable surfaces overpaint lower ones."""
    pixels = bytearray((KEY_INDEX,)) * (width * height)
    if not faces:
        return pixels
    depth = array("f", [-math.inf]) * (width * height)
    heights = [f.z_mean for f in faces]
    z_lo, z_hi = _percentile(heights, 0.02), _percentile(heights, 0.98)
    span = (z_hi - z_lo) or 1.0
    for face in sorted(faces, key=lambda f: (f.z_max, f.z_mean)):
        if face.water:
            colour = WATER_INDEX
        else:
            t = min(1.0, max(0.0, (face.z_mean - z_lo) / span))
            colour = 1 + int(round(t * (GREY_COUNT - 1)))
        fill_convex(pixels, width, height, face.points, colour, depth, face.depth_plane())
    if outline:
        outline_drops(pixels, depth, width, height)
    return pixels


# --- BMP ----------------------------------------------------------------------

def encode_bmp(pixels: bytes, palette: list[tuple[int, int, int]],
               width: int = WIDTH, height: int = HEIGHT) -> bytes:
    if len(palette) != 256:
        raise ValueError(f"palette must have 256 entries, got {len(palette)}")
    stride = (width + 3) & ~3
    if len(pixels) != width * height:
        raise ValueError(f"pixel buffer is {len(pixels)} bytes, expected {width * height}")
    image_size = stride * height
    offset = 14 + 40 + 256 * 4
    header = struct.pack("<2sIHHI", b"BM", offset + image_size, 0, 0, offset)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 8, 0, image_size, 2835, 2835, 256, 0)
    pal = b"".join(struct.pack("<BBBB", b, g, r, 0) for r, g, b in palette)
    pad = b"\0" * (stride - width)
    rows = [bytes(pixels[y * width:(y + 1) * width]) + pad for y in range(height - 1, -1, -1)]
    return header + dib + pal + b"".join(rows)


@dataclass
class BmpInfo:
    file_size: int
    header_size: int
    data_offset: int
    dib_size: int
    width: int
    height: int
    bits_per_pixel: int
    compression: int
    palette: list[tuple[int, int, int]]
    pixels: bytes                      # top-down, width*height indices


def decode_bmp(data: bytes) -> BmpInfo:
    if data[:2] != b"BM":
        raise ValueError("not a BMP")
    file_size, offset = struct.unpack_from("<I4xI", data, 2)
    dib_size, width, height, _, bpp, compression, _, _, _, colours_used, _ = \
        struct.unpack_from("<IiiHHIIiiII", data, 14)
    if bpp != 8:
        raise ValueError(f"{bpp} bpp; only 8-bit overviews are supported")
    count = colours_used or 256
    palette = [tuple(data[54 + 4 * i:54 + 4 * i + 3][::-1]) for i in range(count)]
    stride = (abs(width) + 3) & ~3
    rows = []
    for y in range(abs(height)):
        start = offset + y * stride
        rows.append(data[start:start + abs(width)])
    if height > 0:
        rows.reverse()
    return BmpInfo(len(data), file_size, offset, dib_size, abs(width), abs(height), bpp,
                   compression, palette, b"".join(rows))


def key_indices(palette: list[tuple[int, int, int]]) -> list[int]:
    return [i for i, rgb in enumerate(palette) if tuple(rgb) == KEY_RGB]


def opaque_mask(info: BmpInfo) -> bytes:
    keys = set(key_indices(info.palette))
    return bytes(0 if p in keys else 1 for p in info.pixels)


# --- driver -------------------------------------------------------------------

def resolve_bsp(target: str, maps_dir: Path | None) -> Path:
    path = Path(target)
    if path.suffix.lower() == ".bsp" and path.exists():
        return path
    if maps_dir is None:
        raise BspError(f"{target}: not a .bsp path, and no --maps-dir to look it up in")
    candidate = maps_dir / f"{target}.bsp"
    if not candidate.exists():
        raise BspError(f"{candidate}: no such map")
    return candidate


def render(bsp_path: Path, projection: Projection | None = None, ceiling: float | None = None,
           no_cut: bool = False, rotated: bool = False) -> tuple[bytes, dict]:
    bsp = parse_bsp(bsp_path)
    mins, maxs = world_bounds(bsp)
    if projection is None:
        projection = fit_overview(mins, maxs, rotated=rotated)
    if no_cut:
        ceiling, ceiling_rule = None, "none (--no-cut)"
    elif ceiling is None:
        ceiling, ceiling_rule = default_ceiling(bsp)
    else:
        ceiling_rule = "explicit --cut"
    faces = select_faces(bsp, projection, ceiling)
    pixels = rasterise(faces)
    palette = build_palette()
    facts = {
        "map_name": bsp_path.stem,
        "projection": projection.as_dict(),
        "bounds_convention": "models[0].mins/maxs (worldspawn, sky included, brush entities excluded)",
        "world_bounds": {"mins": list(mins), "maxs": list(maxs)},
        "ceiling": ceiling,
        "ceiling_rule": ceiling_rule,
        "faces_drawn": len(faces),
        "key": {"index": KEY_INDEX, "rgb": list(KEY_RGB)},
    }
    return encode_bmp(bytes(pixels), palette), facts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", help="map name (with --maps-dir) or a .bsp path")
    parser.add_argument("--maps-dir", type=Path, help="directory holding <map>.bsp")
    parser.add_argument("--out", type=Path, default=Path("."), help="output directory")
    parser.add_argument("--descriptor", type=Path,
                        help="existing overviews/<map>.txt; its ZOOM/ORIGIN/ROTATED win")
    parser.add_argument("--zoom", type=float)
    parser.add_argument("--origin", type=float, nargs=2, metavar=("X", "Y"))
    parser.add_argument("--rotated", action="store_true", help="ROTATED 1 projection")
    parser.add_argument("--cut", type=float, help="ceiling z; faces above it are not drawn")
    parser.add_argument("--no-cut", action="store_true", help="draw every upward face (roofs too)")
    args = parser.parse_args(argv)

    try:
        bsp_path = resolve_bsp(args.target, args.maps_dir)
        projection = None
        if args.descriptor:
            projection = read_descriptor(args.descriptor)
        elif args.zoom is not None or args.origin is not None:
            if args.zoom is None or args.origin is None:
                parser.error("--zoom and --origin go together")
            projection = Projection(args.zoom, args.origin[0], args.origin[1], args.rotated)
        bmp, facts = render(bsp_path, projection, args.cut, args.no_cut, args.rotated)
    except (BspError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{bsp_path.stem}.bmp").write_bytes(bmp)
    (args.out / f"{bsp_path.stem}.overview.json").write_text(json.dumps(facts, indent=2) + "\n")
    p = facts["projection"]
    print(f"{bsp_path.stem}: {len(bmp)} bytes, zoom {p['zoom']} origin ({p['origin_x']}, {p['origin_y']}) "
          f"rotated {int(p['rotated'])}, ceiling {facts['ceiling']} ({facts['ceiling_rule']}), "
          f"{facts['faces_drawn']} faces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
