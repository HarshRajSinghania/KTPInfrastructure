"""The overview renderer emits the one BMP shape the engine keys on, puts
geometry where the shared projection says it goes, and drops roofs.

Most tests build a tiny synthetic BSP v30 in memory, so they run anywhere.
The footprint tests against real maps need the fleet corpus (BSPs plus the
shipped overview pairs) and are skipped without `KTP_OVERVIEW_CORPUS`; the
skip is loud on purpose, because a skipped footprint check is not a passed
one."""
from __future__ import annotations

import io
import math
import os
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts import render_overview_bmp as ov
from scripts.spatial_map_geometry import overview_matrices, project

CORPUS = os.environ.get("KTP_OVERVIEW_CORPUS")


# --- synthetic BSP -------------------------------------------------------------

def _pad4(blob: bytes) -> bytes:
    return blob + b"\0" * (-len(blob) % 4)


def build_bsp(quads, entities="", brush_models=(), clip_plane_z=None, empty_above=True):
    """A BSP v30 holding horizontal quads.

    `quads` is a list of (x0, y0, x1, y1, z, texture, facing_up). Each
    becomes one face on its own plane. `brush_models` lists (first, count)
    face ranges that become models 1.. so an entity can own them.

    `clip_plane_z` gives the map a one-node player hull split at that height;
    `empty_above` says which side a player fits in. Without it the clipnode
    lump is empty, which is how a BSP with no hull 1 reads.
    """
    planes, verts, faces, edges, surfedges, texinfo = [], [], [], [], [], []
    names = []
    for x0, y0, x1, y1, z, texture, up in quads:
        if texture not in names:
            names.append(texture)
        planenum = len(planes)
        planes.append((0.0, 0.0, 1.0, float(z), 2))
        base = len(verts)
        verts += [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]
        first_edge = len(edges)
        for k in range(4):
            edges.append((base + k, base + (k + 1) % 4))
        first_surfedge = len(surfedges)
        surfedges += list(range(first_edge, first_edge + 4))
        texinfo.append(names.index(texture))
        faces.append((planenum, 0 if up else 1, first_surfedge, 4, len(texinfo) - 1))

    lumps = [b""] * 15
    lumps[ov.LUMP_ENTITIES] = entities.encode("latin-1") + b"\0"
    lumps[ov.LUMP_PLANES] = b"".join(struct.pack("<ffffi", *p) for p in planes)
    tex_dir = struct.pack("<i", len(names))
    offsets, body = [], b""
    for name in names:
        offsets.append(4 + 4 * len(names) + len(body))
        body += name.encode("latin-1").ljust(16, b"\0") + struct.pack("<II4I", 16, 16, 0, 0, 0, 0)
    lumps[ov.LUMP_TEXTURES] = tex_dir + b"".join(struct.pack("<i", o) for o in offsets) + body
    lumps[ov.LUMP_VERTICES] = b"".join(struct.pack("<fff", *v) for v in verts)
    lumps[ov.LUMP_TEXINFO] = b"".join(struct.pack("<8fII", 1, 0, 0, 0, 0, 1, 0, 0, m, 0) for m in texinfo)
    lumps[ov.LUMP_FACES] = b"".join(struct.pack("<HHihh4Bi", *f, 0, 255, 255, 255, -1) for f in faces)
    lumps[ov.LUMP_EDGES] = b"".join(struct.pack("<HH", *e) for e in edges)
    lumps[ov.LUMP_SURFEDGES] = b"".join(struct.pack("<i", s) for s in surfedges)

    head = -1
    if clip_plane_z is not None:
        head = 0
        planes.append((0.0, 0.0, 1.0, float(clip_plane_z), 2))
        lumps[ov.LUMP_PLANES] = b"".join(struct.pack("<ffffi", *p) for p in planes)
        empty, solid = ov.CONTENTS_EMPTY, -2
        front, back = (empty, solid) if empty_above else (solid, empty)
        lumps[ov.LUMP_CLIPNODES] = struct.pack("<ihh", len(planes) - 1, front, back)

    world_count = len(faces) - sum(count for _, count in brush_models)
    xs = [v[0] for v in verts] or [0.0]
    ys = [v[1] for v in verts] or [0.0]
    zs = [v[2] for v in verts] or [0.0]
    models = [(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs), 0, 0, 0, 0, 0, 0, 0, 0, 0, world_count)]
    for first, count in brush_models:
        models.append((0,) * 6 + (0, 0, 0, 0, 0, 0, 0, 0, first, count))
    lumps[ov.LUMP_MODELS] = b"".join(struct.pack("<9f4iiii", *m) for m in models)

    header = struct.pack("<i", ov.BSP_VERSION)
    offset = 4 + 8 * 15
    directory, payload = b"", b""
    for blob in lumps:
        directory += struct.pack("<ii", offset + len(payload), len(blob))
        payload += _pad4(blob)
    return header + directory + payload


def write_bsp(tmp: Path, name: str, data: bytes) -> Path:
    path = tmp / f"{name}.bsp"
    path.write_bytes(data)
    return path


def opaque_box(mask: bytes, width=ov.WIDTH, height=ov.HEIGHT):
    xs, ys = [], []
    for y in range(height):
        row = mask[y * width:(y + 1) * width]
        if 1 in row:
            ys.append(y)
            xs.append(row.index(1))
            xs.append(width - 1 - row[::-1].index(1))
    return (min(xs), max(xs), min(ys), max(ys)) if xs else None


def iou(a: bytes, b: bytes) -> float:
    inter = sum(1 for x, y in zip(a, b) if x and y)
    union = sum(1 for x, y in zip(a, b) if x or y)
    return inter / union if union else 0.0


# One 512x512 slab centred on the origin, plus a roof 320 above it and one
# spawn on the slab. World box is the slab's, so ORIGIN is (0,0).
SLAB = (-256.0, -256.0, 256.0, 256.0, 0.0, "floor", True)
ROOF = (-256.0, -256.0, 256.0, 256.0, 320.0, "roof", True)
SPAWN = '{ "classname" "worldspawn" }\n{ "classname" "info_player_allies" "origin" "0 0 36" }\n'


class Format(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bmp, self.facts = ov.render(write_bsp(self.tmp, "slab", build_bsp([SLAB], SPAWN)))
        self.info = ov.decode_bmp(self.bmp)

    def test_header_and_dib_fields_match_the_shipped_reference(self):
        # dod_anzio3_b1.bmp on the fleet: 787,510 = 54 + 1024 + 786,432.
        self.assertEqual(len(self.bmp), 787_510)
        self.assertEqual(self.info.header_size, 787_510)
        self.assertEqual(self.info.data_offset, 1078)
        self.assertEqual(self.info.dib_size, 40)
        self.assertEqual((self.info.width, self.info.height), (1024, 768))
        self.assertEqual(self.info.bits_per_pixel, 8)
        self.assertEqual(self.info.compression, 0)
        self.assertEqual(len(self.info.palette), 256)
        raw_height = struct.unpack_from("<i", self.bmp, 22)[0]
        self.assertGreater(raw_height, 0, "rows must be bottom-up like every shipped overview")

    def test_exactly_one_palette_entry_is_the_key(self):
        self.assertEqual(ov.key_indices(self.info.palette), [ov.KEY_INDEX])

    def test_no_other_palette_entry_is_near_the_key(self):
        # dod_anjou_a5 shipped eight entries within 60 of the key and fringes green.
        distances = sorted(math.dist(rgb, ov.KEY_RGB) for i, rgb in enumerate(self.info.palette)
                           if i != ov.KEY_INDEX)
        self.assertGreaterEqual(distances[0], ov.MIN_KEY_DISTANCE)

    def test_the_anjou_failure_would_have_been_caught(self):
        # Control: a palette carrying the near-greens measured on dod_anjou_a5 fails the same check.
        bad = list(self.info.palette)
        bad[10:18] = [(0, 218, 8), (39, 209, 19), (0, 227, 0), (0, 235, 0),
                      (0, 243, 0), (0, 246, 6), (16, 248, 0), (42, 244, 5)]
        nearest = min(math.dist(rgb, ov.KEY_RGB) for i, rgb in enumerate(bad) if i != ov.KEY_INDEX)
        self.assertLess(nearest, ov.MIN_KEY_DISTANCE)

    def test_bmp_round_trips_through_the_decoder(self):
        pixels = bytearray(range(256)) * (ov.WIDTH * ov.HEIGHT // 256)
        again = ov.decode_bmp(ov.encode_bmp(bytes(pixels), ov.build_palette()))
        self.assertEqual(again.pixels, bytes(pixels))
        self.assertEqual(again.palette, ov.build_palette())

    def test_sidecar_names_the_bounds_convention(self):
        self.assertIn("models[0]", self.facts["bounds_convention"])
        self.assertEqual(self.facts["world_bounds"], {"mins": [-256.0, -256.0, 0.0],
                                                      "maxs": [256.0, 256.0, 0.0]})


class ProjectionAgreement(unittest.TestCase):
    """The image must land geometry where spatial_map_geometry's matrices put it."""

    def test_unrotated_matches_the_shared_matrices(self):
        projection = ov.Projection(1.11, 307.06, 372.72, False)
        shared = overview_matrices("dod_anzio", dict(zoom=1.11, origin_x=307.06, origin_y=372.72,
                                                     rotated=False, width=1024, height=768))
        for wx, wy in [(0.0, 0.0), (-1495.0, -326.0), (2048.5, -3071.25), (307.06, 372.72)]:
            expected = project(shared["world_to_pixel"], wx, wy)
            got = projection.to_pixel(wx, wy)
            self.assertAlmostEqual(got[0], expected[0], places=9)
            self.assertAlmostEqual(got[1], expected[1], places=9)

    def test_origin_lands_on_the_image_centre_both_ways(self):
        for rotated in (False, True):
            projection = ov.Projection(1.3, -4.0, 4.0, rotated)
            self.assertEqual(projection.to_pixel(-4.0, 4.0), (512.0, 384.0))

    def test_rotated_swaps_axes_the_way_hud_spectator_tiles_them(self):
        # DrawOverviewLayer, ROTATED branch: image columns run +world_x, rows run -world_y.
        projection = ov.Projection(1.0, 0.0, 0.0, True)
        self.assertEqual(projection.to_pixel(800.0, 0.0), (612.0, 384.0))
        self.assertEqual(projection.to_pixel(0.0, 800.0), (512.0, 284.0))
        unrotated = ov.Projection(1.0, 0.0, 0.0, False)
        self.assertEqual(unrotated.to_pixel(800.0, 0.0), (512.0, 284.0))
        self.assertEqual(unrotated.to_pixel(0.0, 800.0), (412.0, 384.0))


class Fit(unittest.TestCase):
    """fit_overview reproduces shipped descriptors from models[0] bounds.

    Bounds are lump 14 of the fleet's BSPs, read 2026-09-15; the descriptors
    are the shipped overviews/<map>.txt on Dallas."""

    def test_reproduces_shipped_descriptors(self):
        cases = {
            "dod_anzio2_test3": ((-2008, -4048, -704), (2816, 3896, 288), 1.03, 404.0, -76.0),
            "dod_armory_b6": ((-1520, -3484, -896), (1536, 3304, -23), 1.21, 8.0, -90.0),
            "dod_heutau": ((-3152, -3968, -544), (3696, 3936, 1344), 0.90, 272.0, -16.0),
            "dod_flugplatz": ((-3411, -4054, -559), (3810, 3988, 1152), 0.85, 199.5, -33.0),
            "dod_railyard_s9d": ((-2156, -3140, -160), (2148, 3148, 672), 1.30, -4.0, 4.0),
            "dod_glider": ((-2279, -3042, -847), (3131, 3658, 1552), 1.14, 426.0, 308.0),
            "dod_siena_test": ((-2144, -3088, -448), (2488, 3056, 592), 1.33, 172.0, -16.0),
            "dod_overlord": ((-3968, -4032, -1536), (3968, 4048, 384), 0.77, 0.0, 8.0),
        }
        for name, (mins, maxs, zoom, ox, oy) in cases.items():
            fit = ov.fit_overview(mins, maxs)
            self.assertEqual((fit.zoom, fit.origin_x, fit.origin_y), (zoom, ox, oy), name)

    def test_rotated_fit_uses_the_swapped_extents(self):
        # dod_thunder ships ROTATED 1 / ZOOM 1.00 for this box.
        fit = ov.fit_overview((-4000, -3808, -1168), (3872, 2352, 544), rotated=True)
        self.assertEqual((fit.zoom, fit.rotated), (1.00, True))
        self.assertEqual(ov.fit_overview((-4000, -3808, -1168), (3872, 2352, 544)).zoom, 0.78)

    def test_zoom_is_the_tighter_of_the_two_axes(self):
        tall = ov.fit_overview((0, 0, 0), (7000, 4000, 0))       # world x is the image's height
        self.assertEqual(tall.zoom, round(6144 / 7000, 2))
        wide = ov.fit_overview((0, 0, 0), (4000, 7000, 0))       # world y is the image's width
        self.assertEqual(wide.zoom, round(8192 / 7000, 2))

    def test_degenerate_box_is_refused(self):
        with self.assertRaises(ov.BspError):
            ov.fit_overview((0, 0, 0), (0, 100, 0))


class Descriptor(unittest.TestCase):
    def test_reads_the_shipped_layout_with_comments_and_tabs(self):
        text = ("// overview description file for dod_anzio3_b1\n\nglobal \n{\n\tZOOM\t1.03\n"
                "\tORIGIN\t404.00\t-76.00\t-688.00\n\tROTATED\t0\n}\n\nlayer \n{\n"
                '\tIMAGE\t"overviews/dod_anzio3_b1.bmp"\n\tHEIGHT\t-688.00\n}\n')
        tmp = Path(tempfile.mkdtemp()) / "x.txt"
        tmp.write_text(text)
        self.assertEqual(ov.read_descriptor(tmp), ov.Projection(1.03, 404.0, -76.0, False))

    def test_missing_field_is_an_error_not_a_default(self):
        tmp = Path(tempfile.mkdtemp()) / "x.txt"
        tmp.write_text("global { ZOOM 1.0 ROTATED 0 }")
        with self.assertRaises(ov.BspError):
            ov.read_descriptor(tmp)


class Geometry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def mask(self, quads, entities=SPAWN, clip_plane_z=None, empty_above=True, **kwargs):
        data = build_bsp(quads, entities, clip_plane_z=clip_plane_z, empty_above=empty_above)
        bmp, facts = ov.render(write_bsp(self.tmp, "m", data), **kwargs)
        return ov.opaque_mask(ov.decode_bmp(bmp)), facts

    def test_slab_lands_where_the_projection_puts_it(self):
        mask, facts = self.mask([SLAB], projection=ov.Projection(1.0, 0.0, 0.0, False))
        # 512 world units at zoom 1.0 is 64 pixels, centred on (512, 384).
        self.assertEqual(opaque_box(mask), (480, 543, 352, 415))

    def test_everything_off_the_footprint_is_the_key(self):
        mask, _ = self.mask([SLAB], projection=ov.Projection(1.0, 0.0, 0.0, False))
        self.assertEqual(sum(mask), 64 * 64)

    def test_rotated_slab_transposes(self):
        wide = (-512.0, -128.0, 512.0, 128.0, 0.0, "floor", True)
        flat, _ = self.mask([wide], projection=ov.Projection(1.0, 0.0, 0.0, False))
        rotated, _ = self.mask([wide], projection=ov.Projection(1.0, 0.0, 0.0, True))
        self.assertEqual(opaque_box(flat), (496, 527, 320, 447))
        self.assertEqual(opaque_box(rotated), (448, 575, 368, 399))

    def test_without_clipnodes_the_ceiling_falls_back_to_entity_anchors(self):
        _, facts = self.mask([SLAB, ROOF])
        self.assertEqual(facts["ceiling"], 36.0 + ov.STANDING_HEADROOM)
        self.assertEqual(facts["faces_drawn"], 1)

    def test_a_roof_a_player_can_stand_on_raises_the_ceiling_above_it(self):
        # hull 1 is empty above z 8, so the roof at 320 is a floor and survives.
        _, facts = self.mask([SLAB, ROOF], clip_plane_z=8.0, empty_above=True)
        self.assertIn("standable", facts["ceiling_rule"])
        self.assertEqual(facts["ceiling"], 320.0 + ov.STANDING_HEADROOM)
        self.assertEqual(facts["faces_drawn"], 2)

    def test_a_roof_with_no_headroom_is_not_a_floor_and_the_cut_lands_under_it(self):
        # hull 1 is solid above z 330, so nothing fits over the roof at 320.
        _, facts = self.mask([SLAB, ROOF], clip_plane_z=330.0, empty_above=False)
        self.assertIn("standable", facts["ceiling_rule"])
        self.assertEqual(facts["ceiling"], 0.0 + ov.STANDING_HEADROOM)
        self.assertEqual(facts["faces_drawn"], 1)

    def test_no_cut_draws_the_roof(self):
        _, facts = self.mask([SLAB, ROOF], no_cut=True)
        self.assertEqual(facts["faces_drawn"], 2)
        self.assertIsNone(facts["ceiling"])

    def test_explicit_cut_wins(self):
        _, facts = self.mask([SLAB, ROOF], ceiling=400.0)
        self.assertEqual(facts["faces_drawn"], 2)
        self.assertEqual(facts["ceiling_rule"], "explicit --cut")

    def test_ceiling_falls_back_without_anchors(self):
        _, facts = self.mask([SLAB, ROOF], entities='{ "classname" "worldspawn" }')
        self.assertIn("no floor-anchored entities", facts["ceiling_rule"])
        self.assertEqual(facts["ceiling"], 0.6 * 320.0)

    def test_sky_and_downward_faces_are_never_drawn(self):
        sky = (-256.0, -256.0, 256.0, 256.0, 100.0, "sky", True)
        ceiling = (-256.0, -256.0, 256.0, 256.0, 100.0, "plaster", False)
        _, facts = self.mask([sky, ceiling], no_cut=True)
        self.assertEqual(facts["faces_drawn"], 0)

    def test_a_masked_texture_paints_nothing_and_reveals_what_is_under_it(self):
        foliage = (-256.0, -256.0, 256.0, 256.0, 64.0, "{k2_bush1", True)
        mask, facts = self.mask([SLAB, foliage], projection=ov.Projection(1.0, 0.0, 0.0, False))
        self.assertEqual(facts["faces_drawn"], 1)
        self.assertEqual(sum(mask), 64 * 64)            # the slab's footprint, not the foliage's

    def test_masked_foliage_over_void_leaves_the_key(self):
        foliage = (-256.0, -256.0, 256.0, 256.0, 64.0, "{pk_chleaves1c", True)
        mask, facts = self.mask([foliage], projection=ov.Projection(1.0, 0.0, 0.0, False))
        self.assertEqual(facts["faces_drawn"], 0)
        self.assertEqual(sum(mask), 0)

    def test_water_gets_its_own_index_and_drops_get_an_outline(self):
        water = (-256.0, -256.0, 0.0, 256.0, -32.0, "!water", True)
        raised = (0.0, -256.0, 256.0, 256.0, 64.0, "floor", True)
        bmp, _ = ov.render(write_bsp(self.tmp, "w", build_bsp([water, raised], SPAWN)),
                           projection=ov.Projection(1.0, 0.0, 0.0, False))
        pixels = ov.decode_bmp(bmp).pixels
        self.assertIn(ov.WATER_INDEX, set(pixels))
        self.assertIn(ov.EDGE_INDEX, set(pixels))

    def test_brush_entity_faces_are_drawn_only_for_visible_classes(self):
        crate = (-64.0, -64.0, 64.0, 64.0, 32.0, "crate", True)
        entities = SPAWN + '{ "classname" "%s" "model" "*1" }\n'
        for classname, expected in (("func_wall", 2), ("trigger_multiple", 1)):
            data = build_bsp([SLAB, crate], entities % classname, brush_models=[(1, 1)])
            _, facts = ov.render(write_bsp(self.tmp, classname, data))
            self.assertEqual(facts["faces_drawn"], expected, classname)


class Rejects(unittest.TestCase):
    def test_wrong_version_is_refused(self):
        tmp = Path(tempfile.mkdtemp()) / "q3.bsp"
        tmp.write_bytes(struct.pack("<i", 29) + b"\0" * 120)
        with self.assertRaises(ov.BspError):
            ov.parse_bsp(tmp)

    def test_cli_reports_a_missing_map_without_a_traceback(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = ov.main(["dod_nope", "--maps-dir", tempfile.mkdtemp()])
        self.assertEqual(code, 1)


@unittest.skipUnless(CORPUS, "KTP_OVERVIEW_CORPUS not set: real-map footprint checks NOT run")
class Footprint(unittest.TestCase):
    """Render with the shipped descriptor and compare opaque regions with the
    shipped BMP. Only maps whose shipped BMP actually uses the key can be
    compared; the wrong-rotation control must score clearly lower.

    The corpus is the fleet's own `dod/` tree. A map NAME is not a map: a retail
    Steam install ships different builds under the same names, and its
    `dod_heutau` pair scores 0.70 where the fleet's scores 0.99. Point CORPUS at
    a game host's tree, not at Steam, or every floor here reads as a regression.

    Floors sit just under what the fleet corpus measures, so they fail on a real
    loss rather than absorbing one. The terrain cases are here because open
    ground is where this renderer is weakest."""

    CASES = {"dod_halle": 0.98, "dod_heutau": 0.98, "dod_koln": 0.97, "dod_thunder2": 0.97,
             "dod_ramelle": 0.98, "dod_siena_test": 0.94, "dod_anjou_a4": 0.94,
             "dod_thunder": 0.95,        # ROTATED 1
             "dod_caen2": 0.98, "para_kraftstoff": 0.93,
             "dod_railroad2_test": 0.79, "dod_railyard_s9a": 0.86}

    def test_footprints_match_and_the_control_does_not(self):
        root = Path(CORPUS)
        for name, floor in self.CASES.items():
            bsp = root / "maps" / f"{name}.bsp"
            shipped_path = root / "overviews" / f"{name}.bmp"
            if not (bsp.exists() and shipped_path.exists()):
                self.fail(f"{name}: corpus is missing the BSP or the shipped BMP")
            projection = ov.read_descriptor(root / "overviews" / f"{name}.txt")
            shipped = ov.opaque_mask(ov.decode_bmp(shipped_path.read_bytes()))
            mine = ov.opaque_mask(ov.decode_bmp(ov.render(bsp, projection)[0]))
            score = iou(mine, shipped)
            self.assertGreaterEqual(score, floor, f"{name}: IoU {score:.3f}")
            control = ov.Projection(projection.zoom, projection.origin_x, projection.origin_y,
                                    not projection.rotated)
            wrong = iou(ov.opaque_mask(ov.decode_bmp(ov.render(bsp, control)[0])), shipped)
            self.assertLess(wrong, score - 0.3, f"{name}: wrong rotation scored {wrong:.3f}")

    def test_the_ceiling_comes_from_hull_1_and_clips_nothing_the_reference_draws(self):
        """These maps caught the entity-anchored ceiling slicing a top floor off.

        Two properties, not two numbers: the ceiling is decided by the player
        hull, and cutting at it costs nothing against the shipped image.
        `dod_caen2` failed both -- ceiling 256 against a top floor at 400, IoU
        0.848 where uncut scored 0.988. Measured over 57 aligned maps in two
        corpora, this cut and no cut at all give the identical mask every time.
        """
        root = Path(CORPUS)
        for name in ("dod_caen2", "para_kraftstoff", "dod_ramelle", "dod_halle"):
            bsp_path = root / "maps" / f"{name}.bsp"
            if not bsp_path.exists():
                self.fail(f"{name}: corpus is missing the BSP")
            _, rule = ov.default_ceiling(ov.parse_bsp(bsp_path))
            self.assertIn("standable", rule, f"{name}: ceiling did not come from hull 1")

            projection = ov.read_descriptor(root / "overviews" / f"{name}.txt")
            shipped = ov.opaque_mask(ov.decode_bmp((root / "overviews" / f"{name}.bmp").read_bytes()))
            cut = iou(ov.opaque_mask(ov.decode_bmp(ov.render(bsp_path, projection)[0])), shipped)
            whole = iou(ov.opaque_mask(ov.decode_bmp(
                ov.render(bsp_path, projection, no_cut=True)[0])), shipped)
            self.assertEqual(cut, whole, f"{name}: the ceiling cost {whole - cut:.4f} of IoU")


if __name__ == "__main__":
    unittest.main()
