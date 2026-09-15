"""World-to-overview projection: the origin lands on the image centre, the
inverse round-trips, malformed overviews fail closed, and the derived matrices
reproduce the ones the website already ships -- which is the regression that
proves replacing the hand-maintained table is a no-op."""
import json
import unittest
from pathlib import Path

from scripts.make_overview_descriptor import project as descriptor_project
from scripts.spatial_map_geometry import (
    SCHEME, UnsupportedOverview, geometry_version, overview_matrices, project)

SPATIAL_MAPS = Path(__file__).resolve().parents[2] / "config/analytics/spatial_maps"


# Committed on searse/keep-the-prac origin/main at
# src/features/public-match-report/fixtures/map-geometry.json. Hand-derived
# there, with no generator and no test. Pinning them here is the point of
# this module: the derivation must reproduce what production already draws.
SHIPPED_WORLD_TO_PIXEL = {
    "dod_anzio": [[0, -0.13875, 563.7149], [-0.13875, 0, 426.604575], [0, 0, 1]],
    "dod_thunder2": [[0, -0.14375, 532.697125], [-0.14375, 0, 370.2], [0, 0, 1]],
    "dod_lennon5_b1": [[0, -0.1425, 570.2825], [-0.1425, 0, 353.0775], [0, 0, 1]],
}

# As parsed from each map's own overviews/<map>.txt. anzio and thunder2 match
# the overview blocks committed in config/analytics/spatial_maps/; lennon5_b1
# has no config in this repo yet and is read straight from the .txt.
OVERVIEWS = {
    "dod_anzio": dict(zoom=1.11, origin_x=307.06, origin_y=372.72,
                      rotated=False, width=1024, height=768),
    "dod_thunder2": dict(zoom=1.15, origin_x=-96.0, origin_y=143.98,
                         rotated=False, width=1024, height=768),
    "dod_lennon5_b1": dict(zoom=1.14, origin_x=-217.0, origin_y=409.0,
                           rotated=False, width=1024, height=768),
}


class Geometry(unittest.TestCase):
    def test_reproduces_the_matrices_the_website_ships(self):
        for map_name, expected in SHIPPED_WORLD_TO_PIXEL.items():
            derived = overview_matrices(map_name, OVERVIEWS[map_name])["world_to_pixel"]
            for row in range(3):
                for col in range(3):
                    self.assertAlmostEqual(
                        derived[row][col], expected[row][col], places=9,
                        msg=f"{map_name} world_to_pixel[{row}][{col}]")

    def test_map_origin_lands_on_the_image_centre(self):
        for map_name, overview in OVERVIEWS.items():
            geometry = overview_matrices(map_name, overview)
            px, py = project(geometry["world_to_pixel"],
                             overview["origin_x"], overview["origin_y"])
            self.assertAlmostEqual(px, overview["width"] / 2.0, places=9, msg=map_name)
            self.assertAlmostEqual(py, overview["height"] / 2.0, places=9, msg=map_name)

    def test_inverse_round_trips(self):
        points = [(0.0, 0.0), (-1495.0, -326.0), (2048.5, -3071.25), (307.06, 372.72)]
        for map_name, overview in OVERVIEWS.items():
            geometry = overview_matrices(map_name, overview)
            for world_x, world_y in points:
                px, py = project(geometry["world_to_pixel"], world_x, world_y)
                back_x, back_y = project(geometry["pixel_to_world"], px, py)
                self.assertAlmostEqual(back_x, world_x, places=9, msg=map_name)
                self.assertAlmostEqual(back_y, world_y, places=9, msg=map_name)

    def test_scale_is_zoom_over_eight_world_units(self):
        geometry = overview_matrices("dod_anzio", OVERVIEWS["dod_anzio"])
        self.assertAlmostEqual(-geometry["world_to_pixel"][0][1], 1.11 / 8.0, places=12)

    def test_source_block_preserves_the_raw_parse(self):
        geometry = overview_matrices("dod_thunder2", OVERVIEWS["dod_thunder2"])
        self.assertEqual(geometry["source"], {"zoom": 1.15, "origin_x": -96.0,
                                              "origin_y": 143.98, "rotated": False})
        self.assertEqual(geometry["dimensions"], {"width": 1024, "height": 768})
        self.assertEqual(geometry["scheme"], SCHEME)


class CommittedConfigs(unittest.TestCase):
    """The literals above must not drift from the configs in this repo."""

    def configs(self):
        found = {}
        for path in sorted(SPATIAL_MAPS.glob("dod_*.json")):
            config = json.loads(path.read_text(encoding="utf-8-sig"))
            if config.get("overview"):
                found[str(config["map_name"]).lower()] = config["overview"]
        self.assertTrue(found, f"no spatial map configs with an overview under {SPATIAL_MAPS}")
        return found

    def test_every_committed_overview_projects(self):
        for map_name, overview in self.configs().items():
            geometry = overview_matrices(map_name, overview)
            px, py = project(geometry["world_to_pixel"],
                             overview["origin_x"], overview["origin_y"])
            self.assertAlmostEqual(px, overview["width"] / 2.0, places=9, msg=map_name)
            self.assertAlmostEqual(py, overview["height"] / 2.0, places=9, msg=map_name)

    def test_the_committed_overview_agrees_with_the_shipped_matrix(self):
        configs = self.configs()
        checked = set(configs) & set(SHIPPED_WORLD_TO_PIXEL)
        self.assertTrue(checked, "no committed config covers a map the website ships")
        for map_name in sorted(checked):
            derived = overview_matrices(map_name, configs[map_name])["world_to_pixel"]
            expected = SHIPPED_WORLD_TO_PIXEL[map_name]
            for row in range(3):
                for col in range(3):
                    self.assertAlmostEqual(derived[row][col], expected[row][col], places=9,
                                           msg=f"{map_name}[{row}][{col}]")


class Version(unittest.TestCase):
    def facts(self, **overrides):
        base = dict(scheme=SCHEME, map_name="dod_anzio", zoom=1.11, origin_x=307.06,
                    origin_y=372.72, rotated=False, width=1024, height=768)
        base.update(overrides)
        return base

    def test_is_stable_across_runs(self):
        self.assertEqual(geometry_version(self.facts()), geometry_version(self.facts()))

    def test_carries_the_scheme_and_a_digest(self):
        version = geometry_version(self.facts())
        self.assertTrue(version.startswith(f"{SCHEME}-"), version)
        self.assertEqual(len(version), len(SCHEME) + 1 + 12)

    def test_every_input_field_changes_it(self):
        baseline = geometry_version(self.facts())
        for field, value in (("map_name", "dod_thunder2"), ("zoom", 1.12),
                             ("origin_x", 307.07), ("origin_y", 372.73),
                             ("rotated", True), ("width", 1025), ("height", 769)):
            self.assertNotEqual(geometry_version(self.facts(**{field: value})), baseline,
                                msg=field)

    def test_matches_the_matrices_it_is_derived_with(self):
        geometry = overview_matrices("dod_anzio", OVERVIEWS["dod_anzio"])
        self.assertEqual(geometry["geometry_version"], geometry_version(self.facts()))


class Rotated(unittest.TestCase):
    """ROTATED 1 was refused here as unmeasured until 2026-09-15. It is now read
    off CHudSpectator::DrawOverviewLayer and pinned against the descriptor tool."""

    def test_a_rotated_overview_projects_along_world_x(self):
        overview = dict(OVERVIEWS["dod_anzio"], rotated=True)
        geometry = overview_matrices("dod_saints2_b3e", overview)
        self.assertTrue(geometry["source"]["rotated"])
        for world_x, world_y in ((0.0, 0.0), (1234.5, -987.25), (-3000.0, 2500.0)):
            derived = project(geometry["world_to_pixel"], world_x, world_y)
            expected = descriptor_project(world_x, world_y, overview["zoom"],
                                          overview["origin_x"], overview["origin_y"], 1,
                                          overview["width"], overview["height"])
            self.assertAlmostEqual(derived[0], expected[0], places=9)
            self.assertAlmostEqual(derived[1], expected[1], places=9)

    def test_the_two_conventions_are_not_the_same_projection(self):
        # Guards the refusal from having been lifted into a no-op: if these agreed,
        # the flag would be decorative and the finding vacuous.
        flat = overview_matrices("dod_x", dict(OVERVIEWS["dod_anzio"], rotated=False))
        turned = overview_matrices("dod_x", dict(OVERVIEWS["dod_anzio"], rotated=True))
        self.assertNotEqual(flat["world_to_pixel"], turned["world_to_pixel"])
        self.assertNotEqual(flat["geometry_version"], turned["geometry_version"])

    def test_a_rotated_projection_round_trips(self):
        geometry = overview_matrices("dod_x", dict(OVERVIEWS["dod_anzio"], rotated=True))
        for world_x, world_y in ((0.0, 0.0), (-1495.0, -326.0), (2048.5, -3071.25)):
            px, py = project(geometry["world_to_pixel"], world_x, world_y)
            back_x, back_y = project(geometry["pixel_to_world"], px, py)
            self.assertAlmostEqual(back_x, world_x, places=6)
            self.assertAlmostEqual(back_y, world_y, places=6)

    def test_a_rotated_map_origin_still_lands_on_the_image_centre(self):
        overview = dict(OVERVIEWS["dod_anzio"], rotated=True)
        geometry = overview_matrices("dod_x", overview)
        px, py = project(geometry["world_to_pixel"], overview["origin_x"], overview["origin_y"])
        self.assertAlmostEqual(px, overview["width"] / 2.0, places=9)
        self.assertAlmostEqual(py, overview["height"] / 2.0, places=9)

    def test_any_truthy_rotated_value_is_read_as_rotated(self):
        for value in (1, True, "1"):
            geometry = overview_matrices("dod_x", dict(OVERVIEWS["dod_anzio"], rotated=value))
            self.assertTrue(geometry["source"]["rotated"], value)


class FailClosed(unittest.TestCase):
    def test_missing_fields_are_named(self):
        for field in ("zoom", "origin_x", "origin_y", "width", "height"):
            overview = dict(OVERVIEWS["dod_anzio"])
            overview.pop(field)
            with self.assertRaises(UnsupportedOverview) as caught:
                overview_matrices("dod_x", overview)
            self.assertIn(field, str(caught.exception))

    def test_a_zero_or_negative_zoom_is_refused(self):
        for zoom in (0.0, -1.11):
            with self.assertRaises(UnsupportedOverview):
                overview_matrices("dod_x", dict(OVERVIEWS["dod_anzio"], zoom=zoom))

    def test_an_empty_bitmap_is_refused(self):
        with self.assertRaises(UnsupportedOverview):
            overview_matrices("dod_x", dict(OVERVIEWS["dod_anzio"], width=0))


if __name__ == "__main__":
    unittest.main()
