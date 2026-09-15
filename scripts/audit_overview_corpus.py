#!/usr/bin/env python3
"""Audit a whole DoD overview corpus: does each shipped image match its own descriptor?

An overview is three files: `maps/<map>.bsp` (the world), `overviews/<map>.bmp`
(the picture) and `overviews/<map>.txt` (ZOOM / ORIGIN / ROTATED, which say
where the picture sits in the world). The engine draws player positions by
projecting world coordinates through the `.txt` onto the `.bmp`. If the picture
and its own descriptor disagree, every dot on the spectator map lands in the
wrong place, and nothing in the game reports it.

This tool measures that disagreement. For each complete triple it renders the
map with `render_overview_bmp.py` **using the map's own shipped descriptor**,
so the two images are the same map under the same projection, and then compares
the two transparency footprints -- the set of pixels that are not the palette
key RGB(0,255,0). Same map, same projection, so every remaining difference is
either the reference or the renderer.

The number reported is intersection-over-union of the opaque footprints. It is
scored three ways:

- **baseline**: the images laid straight on top of each other. This is what the
  engine actually does.
- **best**: the highest IoU reachable by scaling the render about the image
  centre and translating it. The search says what transform would be needed to
  make the shipped image agree with its own descriptor.
- The **transform** at `best`. A map whose best needs `scale 1.00, dx 0, dy 0`
  is self-consistent. Anything else is a shipped-asset defect, and the size of
  the transform is the size of the error.

⚠️ A low baseline on its own says nothing about which side is wrong. Always read
it next to the transform: a low baseline that the search cannot improve is a
renderer gap; a low baseline that a shift or a scale fixes is a bad asset.

Transform convention, in target-image space, for output pixel (x, y):

    src_x = round_toward_zero(W/2 + (x - dx - W/2) / scale)
    src_y = round_toward_zero(H/2 + (y - dy - H/2) / scale)

so `dx`/`dy` are pixels the render must move right/down, and `scale` > 1 means
the render must grow, to land on the shipped image. At `scale 1.00` this is a
plain translation and is directly comparable to a hand shift.

Buckets, and the evidence that puts a map in each:

- **not-comparable** -- the footprint carries no information. Either the shipped
  BMP uses the key on zero pixels (a fully painted image: every pixel is opaque,
  so IoU degenerates to "what fraction of the image did we paint"), or the
  `.bsp` is missing or unreadable. These are excluded from scoring, never
  scored badly.
- **reference-defective** -- best needs a non-identity transform and that
  transform buys at least `--gain` IoU. The shipped image disagrees with the
  descriptor it ships with.
- **renderer-gap** -- best is at the identity transform, so placement is right,
  but IoU is under `--floor`. The two images sit in the same place and disagree
  about what is there.
- **agrees** -- identity transform and IoU at or above `--floor`.

Usage:
    python scripts/audit_overview_corpus.py --overviews DIR --maps DIR
    python scripts/audit_overview_corpus.py --overviews DIR --maps DIR \
        --json audit.json --only dod_armory_b4 --only dod_anzio3_b1

With no `--maps`, the inventory and duplicate-image passes still run; only the
IoU pass needs the BSPs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    from scripts import render_overview_bmp as ov
except ImportError:  # running from the repo root without the package on the path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts import render_overview_bmp as ov

WIDTH, HEIGHT = 1024, 768
_TO_BITS = bytes.maketrans(bytes(range(256)), b"0" + b"1" * 255)

# Coarse, then finer passes re-centred on what the last one found. The coarse
# scale reach has to get to 0.75: dod_anzio2_test3's shipped image is drawn at
# an effective zoom of 0.77 against the 1.03 in its own descriptor, and a search
# that stops at 0.90 reports that map as merely misaligned.
#
# Each entry is (scale step, offset step, radius in steps). A pass that ends on
# the boundary of its own box is re-centred and run again, because a best that
# sits on the edge is a clipped search, not an answer -- dod_railroad2_b3 wants
# dx -30 and a fixed +/-24 grid reports -24 with a straight face.
PASSES = ((0.05, 8, 6), (0.01, 2, 4), (0.005, 1, 3))
MAX_RECENTRES = 12
OFFSET_LIMIT = 160


def _grid(centre: float, step: float, radius: int) -> list[float]:
    return [round(centre + i * step, 6) for i in range(-radius, radius + 1)]


# --- footprints ---------------------------------------------------------------

def split_rows(mask: bytes, width: int, height: int) -> list[bytes]:
    """The flat opaque mask cut into rows, one bytes object per scanline."""
    if len(mask) != width * height:
        raise ValueError(f"mask is {len(mask)} bytes, expected {width * height}")
    return [mask[y * width:(y + 1) * width] for y in range(height)]


def pack_rows(byte_rows: list[bytes]) -> list[int]:
    """One integer per row, bit (width-1-x) set where pixel x is opaque.

    Packing the footprint into big integers makes the alignment search a few
    hundred thousand bitwise ops instead of a few hundred million per-pixel
    ones, which is the difference between a corpus sweep and an afternoon.
    """
    return [int(row.translate(_TO_BITS).decode("ascii"), 2) for row in byte_rows]


def footprint(path: Path) -> tuple[list[bytes], int, int, int]:
    """Opaque-footprint rows, width, height, and the count of key pixels."""
    info = ov.decode_bmp(path.read_bytes())
    mask = ov.opaque_mask(info)
    keyed = len(mask) - sum(mask)
    return split_rows(mask, info.width, info.height), info.width, info.height, keyed


def _source_index(target: int, centre: float, scale: float) -> int:
    return int(centre + (target - centre) / scale)


def resample(byte_rows: list[bytes], width: int, height: int, scale: float) -> list[int]:
    """Rows scaled about the image centre, returned packed one integer per row.

    Nearest-neighbour, because the thing being resampled is a footprint: a
    pixel is opaque or it is not, and an interpolated edge would invent a
    third state the comparison has no meaning for.
    """
    if scale == 1.0:
        return pack_rows(byte_rows)
    cx, cy = width / 2.0, height / 2.0
    # Index 0 of the padded row is a zero byte, so out-of-range columns read
    # transparent instead of needing a branch per pixel.
    cols = [c + 1 if 0 <= c < width else 0
            for c in (_source_index(x, cx, scale) for x in range(width))]
    blank = b"\x00" * width
    picked = []
    for y in range(height):
        sy = _source_index(y, cy, scale)
        if 0 <= sy < height:
            padded = b"\x00" + byte_rows[sy]
            picked.append(bytes(map(padded.__getitem__, cols)))
        else:
            picked.append(blank)
    return pack_rows(picked)


def iou(a_rows: list[int], b_rows: list[int], width: int, height: int,
        dx: int, dy: int) -> float:
    """IoU of two packed footprints with `b` moved (dx, dy) pixels."""
    mask = (1 << width) - 1
    inter = union = 0
    for y in range(height):
        sy = y - dy
        if 0 <= sy < height:
            row = b_rows[sy]
            b = (row >> dx) if dx >= 0 else ((row << -dx) & mask)
        else:
            b = 0
        a = a_rows[y]
        if a or b:
            inter += (a & b).bit_count()
            union += (a | b).bit_count()
    return inter / union if union else 0.0


@dataclass(frozen=True)
class Alignment:
    baseline: float
    best: float
    scale: float
    dx: int
    dy: int
    evaluations: int
    clipped: bool = False

    @property
    def identity(self) -> bool:
        return self.scale == 1.0 and self.dx == 0 and self.dy == 0

    @property
    def gain(self) -> float:
        return self.best - self.baseline


def search_alignment(a_rows: list[int], b_byte_rows: list[bytes], width: int, height: int,
                     passes=PASSES, refine: bool = True) -> Alignment:
    """Best scale/offset for `b` against `a`, re-centring until it stops moving.

    Ties go to the smaller transform: a map that reaches its best at several
    places should be reported as self-consistent, not as needing whichever
    shift the loop happened to visit first.
    """
    baseline = iou(a_rows, pack_rows(b_byte_rows), width, height, 0, 0)
    best = [baseline, 1.0, 0, 0]
    seen = 1
    clipped = False

    def cost(scale: float, dx: int, dy: int) -> float:
        return abs(dx) + abs(dy) + abs(scale - 1.0) * 100

    def sweep(scale_step, off_step, radius):
        """One box centred on the current best. True if the best sits on its edge."""
        nonlocal seen
        centre = tuple(best[1:])
        scales = [s for s in _grid(centre[0], scale_step, radius) if s > 0]
        dxs = [int(v) for v in _grid(centre[1], off_step, radius)
               if abs(v) <= OFFSET_LIMIT]
        dys = [int(v) for v in _grid(centre[2], off_step, radius)
               if abs(v) <= OFFSET_LIMIT]
        for scale in scales:
            scaled = resample(b_byte_rows, width, height, scale)
            for dy in dys:
                for dx in dxs:
                    value = iou(a_rows, scaled, width, height, dx, dy)
                    seen += 1
                    if value > best[0] + 1e-12 or (
                            abs(value - best[0]) <= 1e-12
                            and cost(scale, dx, dy) < cost(*best[1:])):
                        best[:] = [value, scale, dx, dy]
        edge = radius * off_step
        return (abs(best[2] - centre[1]) >= edge or abs(best[3] - centre[2]) >= edge
                or abs(best[1] - centre[0]) >= radius * scale_step - 1e-9)

    for index, (scale_step, off_step, radius) in enumerate(passes):
        if index and not refine:
            break
        for attempt in range(MAX_RECENTRES):
            if not sweep(scale_step, off_step, radius):
                break
        else:
            clipped = True
    return Alignment(baseline, best[0], best[1], best[2], best[3], seen, clipped)


# --- inventory ----------------------------------------------------------------

@dataclass
class Inventory:
    images: dict[str, Path] = field(default_factory=dict)
    descriptors: dict[str, Path] = field(default_factory=dict)
    maps: dict[str, Path] = field(default_factory=dict)
    uppercase_suffix: list[str] = field(default_factory=list)
    name_collisions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def empty_images(self) -> list[str]:
        return sorted(s for s, p in self.images.items() if p.stat().st_size == 0)

    @property
    def triples(self) -> list[str]:
        return sorted(set(self.images) & set(self.descriptors) & set(self.maps))

    @property
    def image_without_descriptor(self) -> list[str]:
        return sorted(set(self.images) - set(self.descriptors))

    @property
    def descriptor_without_image(self) -> list[str]:
        return sorted(set(self.descriptors) - set(self.images))

    @property
    def overview_without_map(self) -> list[str]:
        return sorted((set(self.images) | set(self.descriptors)) - set(self.maps))

    @property
    def map_without_overview(self) -> list[str]:
        return sorted(set(self.maps) - (set(self.images) | set(self.descriptors)))


def _collect(directory: Path, suffix: str, into: dict[str, Path],
             uppercase: list[str], collisions: dict[str, list[str]]) -> None:
    """Index a directory by stem, case-insensitively on the suffix.

    The engine asks for `overviews/<map>.bmp` in lower case. On the Windows
    workstation `dod_flugplatz.BMP` answers that request and on the Linux fleet
    it does not, so the suffix case is recorded rather than normalised away.
    """
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() != suffix:
            continue
        if path.suffix != suffix:
            uppercase.append(path.name)
        stem = path.stem
        if stem in into:
            collisions.setdefault(stem, [into[stem].name]).append(path.name)
            continue
        into[stem] = path


def take_inventory(overviews: Path, maps: Path | None) -> Inventory:
    inv = Inventory()
    _collect(overviews, ".bmp", inv.images, inv.uppercase_suffix, inv.name_collisions)
    _collect(overviews, ".txt", inv.descriptors, inv.uppercase_suffix, inv.name_collisions)
    if maps and maps.is_dir():
        _collect(maps, ".bsp", inv.maps, inv.uppercase_suffix, inv.name_collisions)
    return inv


# --- duplicate images ---------------------------------------------------------

def md5_of(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def describe_projection(projection) -> tuple:
    return (projection.zoom, projection.origin_x, projection.origin_y, projection.rotated)


def duplicate_groups(images: dict[str, Path],
                     descriptors: dict[str, Path]) -> list[dict]:
    """Byte-identical images, and whether the descriptors sharing one agree.

    One BMP legitimately serves several revisions of a map. It does not
    legitimately serve two descriptors that put it in different places: at most
    one of those can be right, and the others silently mis-place every player
    dot drawn on them.
    """
    by_hash: dict[str, list[str]] = {}
    for stem, path in images.items():
        # Every empty file shares one perfectly good md5, so left in they group
        # together and read as a real duplicate family. A zero-byte overview is
        # a broken deploy, and `empty_images` is where it gets reported.
        if path.stat().st_size == 0:
            continue
        by_hash.setdefault(md5_of(path), []).append(stem)
    groups = []
    for digest, stems in sorted(by_hash.items()):
        if len(stems) < 2:
            continue
        projections = {}
        unreadable = []
        for stem in sorted(stems):
            descriptor = descriptors.get(stem)
            if descriptor is None:
                unreadable.append(stem)
                continue
            try:
                projections[stem] = describe_projection(ov.read_descriptor(descriptor))
            except Exception:
                unreadable.append(stem)
        distinct = sorted(set(projections.values()))
        groups.append({
            "md5": digest,
            "members": sorted(stems),
            "descriptors_disagree": len(distinct) > 1,
            "distinct_projections": [
                {"zoom": z, "origin_x": ox, "origin_y": oy, "rotated": r,
                 "members": sorted(s for s, p in projections.items()
                                   if p == (z, ox, oy, r))}
                for (z, ox, oy, r) in distinct],
            "without_descriptor": sorted(unreadable),
        })
    groups.sort(key=lambda g: (not g["descriptors_disagree"], -len(g["members"])))
    return groups


# --- per-map audit ------------------------------------------------------------

@dataclass
class MapResult:
    name: str
    bucket: str
    reason: str
    key_pixels: int = 0
    baseline: float | None = None
    best: float | None = None
    scale: float | None = None
    dx: int | None = None
    dy: int | None = None
    gain: float | None = None
    clipped: bool = False
    zoom: float | None = None
    rotated: bool | None = None
    seconds: float | None = None
    shared_image_with: list[str] = field(default_factory=list)


def classify(alignment: Alignment, floor: float, gain: float) -> tuple[str, str]:
    if not alignment.identity and alignment.gain >= gain:
        return ("reference-defective",
                f"best needs scale {alignment.scale:.2f} dx {alignment.dx:+d} "
                f"dy {alignment.dy:+d}, worth {alignment.gain:+.3f} IoU")
    if alignment.best < floor:
        return ("renderer-gap",
                f"identity transform is already best and IoU {alignment.best:.3f} "
                f"is under the {floor:.2f} floor")
    return ("agrees", f"identity transform, IoU {alignment.best:.3f}")


def audit_map(name: str, inv: Inventory, floor: float, gain: float,
              refine: bool = True) -> MapResult:
    started = time.monotonic()
    shipped_rows, width, height, keyed = footprint(inv.images[name])
    if keyed == 0:
        return MapResult(name, "not-comparable",
                         "shipped image keys zero pixels; the footprint is the "
                         "whole image, so IoU carries no information",
                         key_pixels=0, seconds=time.monotonic() - started)
    try:
        projection = ov.read_descriptor(inv.descriptors[name])
    except Exception as exc:
        return MapResult(name, "not-comparable", f"unreadable descriptor: {exc}",
                         key_pixels=keyed, seconds=time.monotonic() - started)
    try:
        data, _ = ov.render(inv.maps[name], projection=projection)
        ours = ov.decode_bmp(data)
        our_rows = split_rows(ov.opaque_mask(ours), ours.width, ours.height)
    except Exception as exc:
        return MapResult(name, "not-comparable", f"render failed: {exc}",
                         key_pixels=keyed, zoom=projection.zoom,
                         rotated=projection.rotated,
                         seconds=time.monotonic() - started)
    if (ours.width, ours.height) != (width, height):
        return MapResult(name, "not-comparable",
                         f"shipped image is {width}x{height}, render is "
                         f"{ours.width}x{ours.height}",
                         key_pixels=keyed, seconds=time.monotonic() - started)
    alignment = search_alignment(pack_rows(shipped_rows), our_rows, width, height,
                                 refine=refine)
    bucket, reason = classify(alignment, floor, gain)
    return MapResult(name, bucket, reason, key_pixels=keyed,
                     baseline=round(alignment.baseline, 4),
                     best=round(alignment.best, 4), scale=alignment.scale,
                     dx=alignment.dx, dy=alignment.dy, clipped=alignment.clipped,
                     gain=round(alignment.gain, 4), zoom=projection.zoom,
                     rotated=projection.rotated,
                     seconds=round(time.monotonic() - started, 1))


# --- report -------------------------------------------------------------------

def build_report(inv: Inventory, results: list[MapResult]) -> dict:
    buckets: dict[str, list[str]] = {}
    for result in results:
        buckets.setdefault(result.bucket, []).append(result.name)
    defective = sorted((r for r in results if r.bucket == "reference-defective"),
                       key=lambda r: (-r.gain, r.baseline))
    return {
        "inventory": {
            "images": len(inv.images),
            "descriptors": len(inv.descriptors),
            "maps": len(inv.maps),
            "complete_triples": len(inv.triples),
            "image_without_descriptor": inv.image_without_descriptor,
            "descriptor_without_image": inv.descriptor_without_image,
            "overview_without_map": inv.overview_without_map,
            "map_without_overview": inv.map_without_overview,
            "uppercase_suffix": inv.uppercase_suffix,
            "empty_images": inv.empty_images,
            "name_collisions": inv.name_collisions,
        },
        "bucket_counts": {k: len(v) for k, v in sorted(buckets.items())},
        "ranked_defective": [asdict(r) for r in defective],
        "results": [asdict(r) for r in sorted(results, key=lambda r: r.name)],
    }


def print_report(report: dict, duplicates: list[dict], stream=sys.stdout) -> None:
    inv = report["inventory"]
    w = stream.write
    w("== inventory ==\n")
    w(f"   images {inv['images']}  descriptors {inv['descriptors']}  "
      f"maps {inv['maps']}  complete triples {inv['complete_triples']}\n")
    for label, key in (("image with no descriptor", "image_without_descriptor"),
                       ("descriptor with no image", "descriptor_without_image"),
                       ("overview with no map", "overview_without_map")):
        names = inv[key]
        if names:
            w(f"   {label} ({len(names)}): {', '.join(names)}\n")
    if inv.get("empty_images"):
        w(f"   ZERO-BYTE image ({len(inv['empty_images'])}): "
          f"{', '.join(inv['empty_images'])}\n")
    if inv["uppercase_suffix"]:
        w(f"   suffix not lower case ({len(inv['uppercase_suffix'])}): "
          f"{', '.join(inv['uppercase_suffix'])}\n")

    w("\n== buckets ==\n")
    for bucket, count in report["bucket_counts"].items():
        w(f"   {bucket:<22} {count}\n")

    w("\n== shipped image disagrees with its own descriptor (worst first) ==\n")
    if not report["ranked_defective"]:
        w("   none\n")
    for r in report["ranked_defective"]:
        w(f"   {r['name']:<30} baseline {r['baseline']:.3f} -> best {r['best']:.3f} "
          f"({r['gain']:+.3f}) at scale {r['scale']:.2f} dx {r['dx']:+d} dy {r['dy']:+d}\n")

    gaps = [r for r in report["results"] if r["bucket"] == "renderer-gap"]
    if gaps:
        w("\n== identity transform is best, but the footprints disagree ==\n")
        w("   Placement is right and the pictures still differ. On a map whose image is\n"
          "   its own, that is the renderer. On one sharing an image with other revisions,\n"
          "   the image was cut for a different revision and this is not evidence about us.\n")
        for r in sorted(gaps, key=lambda r: r["best"]):
            shared = r.get("shared_image_with") or []
            note = f"image shared with {', '.join(shared)}" if shared else "image is this map's own"
            w(f"   {r['name']:<30} IoU {r['best']:.3f}  zoom {r['zoom']}  "
              f"rotated {int(bool(r['rotated']))}  -- {note}\n")

    disagreeing = [g for g in duplicates if g["descriptors_disagree"]]
    w(f"\n== byte-identical images ({len(duplicates)} groups, "
      f"{len(disagreeing)} with descriptors that disagree) ==\n")
    for g in duplicates:
        flag = "DISAGREE" if g["descriptors_disagree"] else "agree   "
        w(f"   {flag} {g['md5'][:12]}  {', '.join(g['members'])}\n")
        if g["descriptors_disagree"]:
            for p in g["distinct_projections"]:
                w(f"            zoom {p['zoom']} origin ({p['origin_x']}, {p['origin_y']}) "
                  f"rotated {int(bool(p['rotated']))}: {', '.join(p['members'])}\n")

    nokey = [r for r in report["results"] if r["key_pixels"] == 0]
    w(f"\n== not comparable: shipped image keys zero pixels ({len(nokey)}) ==\n")
    for r in nokey:
        w(f"   {r['name']}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--overviews", type=Path, required=True,
                        help="directory holding <map>.bmp and <map>.txt")
    parser.add_argument("--maps", type=Path, help="directory holding <map>.bsp")
    parser.add_argument("--json", type=Path, help="write the full report here")
    parser.add_argument("--only", action="append", default=[],
                        help="audit just this map (repeatable)")
    parser.add_argument("--floor", type=float, default=0.85,
                        help="IoU at or above which an aligned pair agrees")
    parser.add_argument("--gain", type=float, default=0.02,
                        help="IoU a transform must buy to call the asset defective")
    parser.add_argument("--no-refine", action="store_true",
                        help="skip the fine pass; coarse grid only")
    parser.add_argument("--quiet", action="store_true", help="only write the JSON")
    args = parser.parse_args(argv)

    inv = take_inventory(args.overviews, args.maps)
    duplicates = duplicate_groups(inv.images, inv.descriptors)
    shared: dict[str, list[str]] = {}
    for group in duplicates:
        for stem in group["members"]:
            shared[stem] = [m for m in group["members"] if m != stem]

    names = inv.triples
    if args.only:
        wanted = set(args.only)
        missing = wanted - set(inv.images)
        if missing:
            print(f"no such overview: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
        names = [n for n in sorted(wanted) if n in inv.images]

    results = []
    for index, name in enumerate(names, 1):
        if name not in inv.descriptors:
            results.append(MapResult(name, "not-comparable", "no descriptor"))
            continue
        if name not in inv.maps:
            shipped = footprint(inv.images[name])
            results.append(MapResult(name, "not-comparable", "no .bsp for this overview",
                                     key_pixels=shipped[3]))
            continue
        result = audit_map(name, inv, args.floor, args.gain, refine=not args.no_refine)
        result.shared_image_with = shared.get(name, [])
        results.append(result)
        if not args.quiet:
            print(f"[{index}/{len(names)}] {result.name:<30} {result.bucket:<20} "
                  f"{result.reason}", flush=True)

    report = build_report(inv, results)
    report["duplicate_image_groups"] = duplicates
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not args.quiet:
        print()
        print_report(report, duplicates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
