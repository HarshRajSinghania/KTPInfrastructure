"""Solving `overviews/<map>.txt` from a BSP.

The corpus below is the load-bearing part. Twelve of the 67 overviews the fleet
ships are pinned here beside the model-0 bounds of the BSP they describe, so
every claim about the solver is checked against a file a human made by eye and
the engine has been drawing for years -- including both ROTATED conventions.
"""
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.bsp_bounds import Bounds
from scripts.bsp_bounds import read as read_bsp
from scripts.make_overview_descriptor import (
    HEIGHT_BELOW_FLOOR, OVERVIEW_HEIGHT, OVERVIEW_WIDTH, UnusableMap, choose_rotated,
    main, project, render, solve, solve_zoom)
from tests.unit.test_bsp_bounds import build_bsp, leaf, model, write

CONTENTS_EMPTY, CONTENTS_SOLID = -1, -2

# Measured on the Dallas host 2026-09-15: `world_*` is BSP model 0's mins/maxs,
# `empty_*` the union of its CONTENTS_EMPTY leaves, and the rest is the shipped
# overviews/<map>.txt parsed verbatim.
SHIPPED = {
    "dod_anjou_a5": dict(
        world_mins=(-2164, -3280, -724), world_maxs=(2472, 2808, 116),
        empty_mins=(-2144, -3144, -656), empty_maxs=(2480, 2792, 100),
        zoom=1.34, origin_x=156, origin_y=-236, origin_z=-260, rotated=0, height=-637),
    "dod_armory_b6": dict(
        world_mins=(-1520, -3484, -896), world_maxs=(1536, 3304, -23),
        empty_mins=(-1372, -3412, -856), empty_maxs=(1472, 2856, 70),
        zoom=1.21, origin_x=8, origin_y=-90, origin_z=-425, rotated=0, height=-897),
    "dod_harrington": dict(
        world_mins=(-2104, -3264, -496), world_maxs=(2144, 1968, 400),
        empty_mins=(-2072, -3056, -504), empty_maxs=(2048, 1932, 360),
        zoom=1.45, origin_x=20, origin_y=-752, origin_z=-40, rotated=0, height=-481),
    "dod_railyard_s9d": dict(
        world_mins=(-2156, -3140, -160), world_maxs=(2148, 3148, 672),
        empty_mins=(-2124, -3100, -114), empty_maxs=(2116, 2860, 696),
        zoom=1.3, origin_x=-4, origin_y=4, origin_z=272, rotated=0, height=-129),
    "dod_orange": dict(
        world_mins=(-1428, -1404, -398), world_maxs=(1176, 1584, 320),
        empty_mins=(-1412, -1388, -412), empty_maxs=(1160, 1568, 304),
        zoom=2.39, origin_x=-126, origin_y=90, origin_z=-42, rotated=0, height=-389),
    "dod_overlord": dict(
        world_mins=(-3968, -4032, -1536), world_maxs=(3968, 4048, 384),
        empty_mins=(-3864, -3992, -1560), empty_maxs=(3864, 4056, 280),
        zoom=0.77, origin_x=0, origin_y=8, origin_z=-576, rotated=0, height=-1536),
    "dod_avalanche": dict(
        world_mins=(-1152, -2432, -640), world_maxs=(2000, 2752, 832),
        empty_mins=(-1144, -2392, -632), empty_maxs=(1984, 2712, 768),
        zoom=1.61, origin_x=424, origin_y=160, origin_z=112, rotated=0, height=-631),
    "dod_kalt": dict(
        world_mins=(-1720, -3848, -288), world_maxs=(1720, 2840, 528),
        empty_mins=(-1697, -3816, -296), empty_maxs=(1700, 2568, 512),
        zoom=1.3, origin_x=128.91, origin_y=-791.03, origin_z=128, rotated=0, height=-273),
    "dod_anzio3_b1": dict(
        world_mins=(-3120, -4048, -704), world_maxs=(3584, 4048, 400),
        empty_mins=(-3104, -4056, -792), empty_maxs=(3568, 4032, 384),
        zoom=1.03, origin_x=404, origin_y=-76, origin_z=-688, rotated=0, height=-688),
    # --- ROTATED 1 ---
    "dod_saints2_b3e": dict(
        world_mins=(-2924, -2340, -544), world_maxs=(3460, 3024, 492),
        empty_mins=(-2820, -2240, -488), empty_maxs=(3360, 2920, 420),
        zoom=1.24, origin_x=268, origin_y=342, origin_z=-545, rotated=1, height=-545),
    "dod_thunder": dict(
        world_mins=(-4000, -3808, -1168), world_maxs=(3872, 2352, 544),
        empty_mins=(-3992, -3776, -1176), empty_maxs=(3840, 2344, 408),
        zoom=1, origin_x=-64, origin_y=-728, origin_z=-368, rotated=1, height=-1152),
    "dod_flash": dict(
        world_mins=(-3680, -3088, -80), world_maxs=(3088.01, 2080, 480),
        empty_mins=(-3648, -3072, -88), empty_maxs=(3072, 2072, 640),
        zoom=1.19, origin_x=-295.99, origin_y=-514.16, origin_z=208, rotated=1, height=-191),
}

# The descriptor was authored against a sibling revision of the map, not this BSP:
# same zoom and origin as another map in the family whose worldspawn differs. Their
# framing error is a property of the shipped file, so they are excluded from the
# agreement bounds and kept for the ROTATED and formatting checks.
INHERITED_DESCRIPTOR = {"dod_anzio3_b1"}


def fake_bsp(name, row):
    class Fake:
        pass
    bsp = Fake()
    bsp.name = name
    bsp.world_bounds = lambda: Bounds(tuple(float(v) for v in row["world_mins"]),
                                      tuple(float(v) for v in row["world_maxs"]), "bsp:model0")
    bsp.empty_leaf_bounds = lambda: Bounds(tuple(float(v) for v in row["empty_mins"]),
                                           tuple(float(v) for v in row["empty_maxs"]),
                                           "bsp:empty-leaves")
    return bsp


class Projection(unittest.TestCase):
    """Both branches of CHudSpectator::DrawOverviewLayer, as affine maps."""

    def test_the_origin_lands_on_the_image_centre_either_way(self):
        for rotated in (0, 1):
            px, py = project(123.0, -456.0, 1.25, 123.0, -456.0, rotated)
            self.assertAlmostEqual(px, OVERVIEW_WIDTH / 2.0)
            self.assertAlmostEqual(py, OVERVIEW_HEIGHT / 2.0)

    # At zoom 1.0 the image spans 8192 world units across its 1024-px axis and
    # 6144 across its 768-px axis. These are the four corners that implies, and
    # they are the whole of the ROTATED finding: which world axis goes where,
    # and which way round. Each row is (world_x, world_y) -> (px, py).
    EDGES = {
        0: [((0.0, -4096.0), (1024.0, 384.0)), ((0.0, 4096.0), (0.0, 384.0)),
            ((3072.0, 0.0), (512.0, 0.0)), ((-3072.0, 0.0), (512.0, 768.0))],
        1: [((4096.0, 0.0), (1024.0, 384.0)), ((-4096.0, 0.0), (0.0, 384.0)),
            ((0.0, 3072.0), (512.0, 0.0)), ((0.0, -3072.0), (512.0, 768.0))],
    }

    def test_each_convention_puts_the_world_where_the_engine_puts_it(self):
        for rotated, cases in self.EDGES.items():
            for (world_x, world_y), (px, py) in cases:
                got = project(world_x, world_y, 1.0, 0.0, 0.0, rotated)
                self.assertAlmostEqual(got[0], px, places=6, msg=f"rotated={rotated}")
                self.assertAlmostEqual(got[1], py, places=6, msg=f"rotated={rotated}")

    def test_the_wide_image_axis_carries_x_when_rotated_and_y_when_not(self):
        for rotated, along_wide, along_tall in ((0, (0.0, 1.0), (1.0, 0.0)),
                                                (1, (1.0, 0.0), (0.0, 1.0))):
            centre = project(0.0, 0.0, 1.0, 0.0, 0.0, rotated)
            moved = project(along_wide[0] * 800, along_wide[1] * 800, 1.0, 0.0, 0.0, rotated)
            self.assertNotAlmostEqual(moved[0], centre[0])
            self.assertAlmostEqual(moved[1], centre[1])
            moved = project(along_tall[0] * 800, along_tall[1] * 800, 1.0, 0.0, 0.0, rotated)
            self.assertAlmostEqual(moved[0], centre[0])
            self.assertNotAlmostEqual(moved[1], centre[1])

    def test_both_conventions_share_one_scale(self):
        # zoom/8: one overview pixel is 8 world units at zoom 1.0, on both axes.
        for rotated in (0, 1):
            centre = project(0.0, 0.0, 2.0, 0.0, 0.0, rotated)
            for world in ((80.0, 0.0), (0.0, 80.0)):
                moved = project(world[0], world[1], 2.0, 0.0, 0.0, rotated)
                shift = abs(moved[0] - centre[0]) + abs(moved[1] - centre[1])
                self.assertAlmostEqual(shift, 20.0)

    def test_the_vertical_axis_increases_upward_in_both_conventions(self):
        # The image's 768-px axis carries world X unrotated and world Y rotated.
        # Either way more of it must mean a SMALLER py, or the picture is flipped
        # and every position drawn on it lands in the wrong half of the map.
        for rotated, up in ((0, (1.0, 0.0)), (1, (0.0, 1.0))):
            high = project(up[0] * 500, up[1] * 500, 1.0, 0.0, 0.0, rotated)
            low = project(up[0] * -500, up[1] * -500, 1.0, 0.0, 0.0, rotated)
            self.assertLess(high[1], low[1], f"rotated={rotated}")


class RotatedDecision(unittest.TestCase):
    def test_the_shipped_flag_is_reproduced_on_every_pinned_map(self):
        for name, row in SHIPPED.items():
            bsp = fake_bsp(name, row)
            self.assertEqual(choose_rotated(bsp.world_bounds()), row["rotated"], name)

    def test_the_rule_is_longer_along_x(self):
        self.assertEqual(choose_rotated(Bounds((0, 0, 0), (100, 50, 10), "t")), 1)
        self.assertEqual(choose_rotated(Bounds((0, 0, 0), (50, 100, 10), "t")), 0)

    def test_a_square_map_takes_the_majority_convention(self):
        self.assertEqual(choose_rotated(Bounds((-10, -10, 0), (10, 10, 10), "t")), 0)

    def test_an_override_is_honoured(self):
        row = SHIPPED["dod_saints2_b3e"]
        self.assertEqual(solve(fake_bsp("dod_saints2_b3e", row), rotated=0)["rotated"], 0)

    def test_a_nonsense_override_is_refused(self):
        row = SHIPPED["dod_anjou_a5"]
        for bad in (2, -1, "yes"):
            with self.assertRaises(UnusableMap):
                solve(fake_bsp("dod_anjou_a5", row), rotated=bad)


class RoundTrip(unittest.TestCase):
    """Solve each pinned map from its BSP bounds; compare against the shipped file."""

    def solved(self):
        for name, row in SHIPPED.items():
            if name in INHERITED_DESCRIPTOR:
                continue
            yield name, row, solve(fake_bsp(name, row))

    def test_zoom_lands_within_a_fifth_of_the_human_choice(self):
        for name, row, got in self.solved():
            ratio = got["zoom"] / row["zoom"]
            self.assertGreater(ratio, 0.80, f"{name} solved zoom {got['zoom']:.3f}")
            self.assertLess(ratio, 1.20, f"{name} solved zoom {got['zoom']:.3f}")

    def test_a_position_drawn_through_either_descriptor_lands_within_a_tile(self):
        # 128px is one overview tile. A reader nudging ZOOM afterwards is expected;
        # landing in the wrong quarter of the image would not be.
        for name, row, got in self.solved():
            worst = 0.0
            for x in (row["world_mins"][0], row["world_maxs"][0]):
                for y in (row["world_mins"][1], row["world_maxs"][1]):
                    a = project(x, y, row["zoom"], row["origin_x"], row["origin_y"], row["rotated"])
                    b = project(x, y, got["zoom"], got["origin"][0], got["origin"][1], got["rotated"])
                    worst = max(worst, ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)
            self.assertLess(worst, 128.0, f"{name} worst corner displacement {worst:.0f}px")

    def test_the_solved_framing_fits_the_map_on_the_canvas(self):
        # The property the solver actually guarantees, unlike agreement with a human.
        for name, row in SHIPPED.items():
            got = solve(fake_bsp(name, row))
            for x in (row["world_mins"][0], row["world_maxs"][0]):
                for y in (row["world_mins"][1], row["world_maxs"][1]):
                    px, py = project(x, y, got["zoom"], got["origin"][0], got["origin"][1],
                                     got["rotated"])
                    self.assertGreaterEqual(px, -1e-6, name)
                    self.assertLessEqual(px, OVERVIEW_WIDTH + 1e-6, name)
                    self.assertGreaterEqual(py, -1e-6, name)
                    self.assertLessEqual(py, OVERVIEW_HEIGHT + 1e-6, name)

    def test_the_framing_is_tight_on_at_least_one_axis(self):
        for name, row in SHIPPED.items():
            got = solve(fake_bsp(name, row))
            corners = [project(x, y, got["zoom"], got["origin"][0], got["origin"][1], got["rotated"])
                       for x in (row["world_mins"][0], row["world_maxs"][0])
                       for y in (row["world_mins"][1], row["world_maxs"][1])]
            used_x = max(p[0] for p in corners) - min(p[0] for p in corners)
            used_y = max(p[1] for p in corners) - min(p[1] for p in corners)
            self.assertTrue(abs(used_x - OVERVIEW_WIDTH) < 1e-6 or
                            abs(used_y - OVERVIEW_HEIGHT) < 1e-6,
                            f"{name} fills neither axis: {used_x:.1f}x{used_y:.1f}")

    def test_origin_xy_is_the_centre_of_the_framed_bounds(self):
        for name, row, got in self.solved():
            self.assertAlmostEqual(got["origin"][0],
                                   (row["world_mins"][0] + row["world_maxs"][0]) / 2.0, places=6)
            self.assertAlmostEqual(got["origin"][1],
                                   (row["world_mins"][1] + row["world_maxs"][1]) / 2.0, places=6)


class ZIsNotProjection(unittest.TestCase):
    """ORIGIN z and layer HEIGHT are a camera pivot and an image plane. Neither
    reaches the x/y projection, which is why retuning them invalidates nothing."""

    def test_moving_the_map_up_or_down_moves_no_pixel(self):
        # Shifting a map 4000 units in z changes both z fields and nothing else,
        # so nothing already drawn through ZOOM and ORIGIN x/y is invalidated.
        row = SHIPPED["dod_anjou_a5"]
        flat = solve(fake_bsp("dod_anjou_a5", row))
        lifted_row = dict(row)
        for key in ("world_mins", "world_maxs", "empty_mins", "empty_maxs"):
            lifted_row[key] = tuple(v + 4000 if axis == 2 else v
                                    for axis, v in enumerate(row[key]))
        lifted = solve(fake_bsp("dod_anjou_a5", lifted_row))
        self.assertNotAlmostEqual(flat["origin"][2], lifted["origin"][2])
        self.assertNotAlmostEqual(flat["height"], lifted["height"])
        self.assertAlmostEqual(flat["zoom"], lifted["zoom"])
        for world in ((0.0, 0.0), (1234.5, -987.25)):
            self.assertEqual(
                project(world[0], world[1], flat["zoom"], flat["origin"][0], flat["origin"][1],
                        flat["rotated"]),
                project(world[0], world[1], lifted["zoom"], lifted["origin"][0],
                        lifted["origin"][1], lifted["rotated"]))

    def test_height_sits_below_the_lowest_world_brush(self):
        # Above it, the image draws over the players standing on the floor.
        for name, row in SHIPPED.items():
            got = solve(fake_bsp(name, row))
            self.assertLess(got["height"], row["world_mins"][2], name)
            self.assertAlmostEqual(got["height"], row["world_mins"][2] - HEIGHT_BELOW_FLOOR)

    def test_origin_z_is_the_centre_of_the_playable_volume(self):
        for name, row in SHIPPED.items():
            got = solve(fake_bsp(name, row))
            self.assertAlmostEqual(got["origin"][2],
                                   (row["empty_mins"][2] + row["empty_maxs"][2]) / 2.0, places=6)

    def test_origin_z_falls_back_to_the_world_box_with_no_empty_leaves(self):
        class NoLeaves:
            name = "dod_x"
            world_bounds = staticmethod(
                lambda: Bounds((-10.0, -20.0, -30.0), (10.0, 20.0, 70.0), "bsp:model0"))
            empty_leaf_bounds = staticmethod(lambda: None)
        got = solve(NoLeaves())
        self.assertAlmostEqual(got["origin"][2], 20.0)


class Zoom(unittest.TestCase):
    def test_a_margin_shrinks_zoom_proportionally(self):
        bounds = Bounds((-1000, -2000, 0), (1000, 2000, 100), "t")
        tight = solve_zoom(bounds, 0)
        self.assertAlmostEqual(solve_zoom(bounds, 0, margin=0.20), tight * 0.80)

    def test_a_degenerate_footprint_is_refused_rather_than_divided_by(self):
        for maxs in ((0, 100, 10), (100, 0, 10)):
            with self.assertRaises(UnusableMap):
                solve_zoom(Bounds((0, 0, 0), maxs, "t"), 0)

    def test_a_nonsense_margin_is_refused(self):
        for margin in (-0.1, 1.0, 2.0):
            with self.assertRaises(UnusableMap):
                solve_zoom(Bounds((0, 0, 0), (100, 100, 10), "t"), 0, margin=margin)

    def test_a_sliver_map_is_limited_by_its_long_axis(self):
        bounds = Bounds((-4000, -10, 0), (4000, 10, 10), "t")
        self.assertEqual(choose_rotated(bounds), 1)
        self.assertAlmostEqual(solve_zoom(bounds, 1), 8.0 * OVERVIEW_WIDTH / 8000.0)


class OutputShape(unittest.TestCase):
    """The engine parses this file. COM_ParseFile is whitespace-tolerant, but the
    block structure and key spellings are not optional."""

    def setUp(self):
        self.text = render(solve(fake_bsp("dod_saints2_b3e", SHIPPED["dod_saints2_b3e"])))

    def test_it_has_the_two_blocks_the_parser_expects(self):
        self.assertIn("global \n{\n", self.text)
        self.assertIn("layer \n{\n", self.text)
        self.assertEqual(self.text.count("{"), 2)
        self.assertEqual(self.text.count("}"), 2)

    def test_every_key_the_parser_knows_is_spelled_as_it_expects(self):
        for key in ("ZOOM", "ORIGIN", "ROTATED", "IMAGE", "HEIGHT"):
            self.assertIn(f"\t{key}\t", self.text)

    def test_no_key_the_parser_does_not_know_appears(self):
        # ParseOverviewFile aborts the whole file on an unknown token, so a stray
        # key loses the overview rather than the line.
        known = {"global", "layer", "{", "}", "zoom", "origin", "rotated", "inset",
                 "image", "height"}
        body = [line for line in self.text.splitlines() if not line.startswith("//")]
        for line in body:
            token = line.strip().split("\t")[0].split(" ")[0].strip()
            if token and not token[0].isdigit() and token[0] not in "-.\"":
                self.assertIn(token.lower(), known, f"unknown token {token!r}")

    def test_the_image_path_is_quoted_and_game_relative(self):
        self.assertIn('IMAGE\t"overviews/dod_saints2_b3e.bmp"', self.text)

    def test_origin_is_three_numbers_on_one_line(self):
        line = next(l for l in self.text.splitlines() if "ORIGIN" in l)
        self.assertEqual(len(line.split("\t")[-1].split()), 3)

    def test_comments_use_the_line_form_the_parser_skips(self):
        for line in self.text.splitlines():
            if line.startswith("/"):
                self.assertTrue(line.startswith("//"), line)

    def test_it_is_ascii_and_lf_only(self):
        self.text.encode("ascii")
        self.assertNotIn("\r", self.text)
        self.assertTrue(self.text.endswith("}\n"))

    def test_it_reparses_to_the_values_it_was_given(self):
        solved = solve(fake_bsp("dod_thunder", SHIPPED["dod_thunder"]))
        parsed = reparse(render(solved))
        self.assertAlmostEqual(parsed["zoom"], solved["zoom"], places=2)
        self.assertEqual(parsed["rotated"], solved["rotated"])
        self.assertAlmostEqual(parsed["height"], solved["height"], places=2)
        for got, want in zip(parsed["origin"], solved["origin"]):
            self.assertAlmostEqual(got, want, places=2)


def reparse(text):
    """COM_ParseFile's contract, minimally: whitespace-separated tokens, // to EOL."""
    tokens = []
    for line in text.splitlines():
        line = line.split("//")[0]
        for raw in line.replace('"', ' " ').split():
            tokens.append(raw)
    out = {"origin": [0.0, 0.0, 0.0], "zoom": 1.0, "rotated": 0, "height": 0.0}
    index = 0
    while index < len(tokens):
        key = tokens[index].lower()
        if key == "zoom":
            out["zoom"] = float(tokens[index + 1]); index += 1
        elif key == "origin":
            out["origin"] = [float(tokens[index + n]) for n in (1, 2, 3)]; index += 3
        elif key == "rotated":
            out["rotated"] = int(tokens[index + 1]); index += 1
        elif key == "height":
            out["height"] = float(tokens[index + 1]); index += 1
        index += 1
    return out


class Cli(unittest.TestCase):
    def bsp_file(self, tmp, name="dod_cli.bsp"):
        blob = build_bsp(
            models=[model((-1024.0, -2048.0, -256.0), (1024.0, 2048.0, 256.0))],
            leaves=[leaf(CONTENTS_SOLID, (0, 0, 0), (0, 0, 0)),
                    leaf(CONTENTS_EMPTY, (-1000, -2000, -200), (1000, 2000, 100))])
        return write(tmp, name, blob)

    def test_it_writes_one_txt_per_bsp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.bsp_file(tmp)
            out = Path(tmp) / "overviews"
            self.assertEqual(main([str(path), "--out-dir", str(out)]), 0)
            written = (out / "dod_cli.txt").read_bytes()
            self.assertIn(b"ROTATED\t0", written)
            self.assertNotIn(b"\r\n", written)

    def test_an_unreadable_bsp_is_a_nonzero_exit_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = write(tmp, "dod_bad.bsp", b"not a bsp at all")
            self.assertEqual(main([str(bad)]), 1)

    def test_one_bad_map_does_not_stop_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = self.bsp_file(tmp)
            bad = write(tmp, "dod_bad.bsp", b"nope")
            out = Path(tmp) / "overviews"
            self.assertEqual(main([str(bad), str(good), "--out-dir", str(out)]), 1)
            self.assertTrue((out / "dod_cli.txt").exists())

    def test_json_carries_the_bounds_contract_for_the_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            solved = solve(read_bsp(self.bsp_file(tmp)))
        self.assertEqual(solved["bounds"]["source"], "bsp:model0")
        self.assertFalse(solved["bounds"]["brush_entities_included"])
        self.assertEqual(solved["bounds"]["mins"], [-1024.0, -2048.0, -256.0])
        self.assertEqual(solved["dimensions"], {"width": OVERVIEW_WIDTH, "height": OVERVIEW_HEIGHT})


if __name__ == "__main__":
    unittest.main()
