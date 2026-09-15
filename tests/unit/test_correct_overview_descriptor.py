"""Correcting a descriptor onto the picture that ships with it.

The load-bearing test is `test_corrected_projection_reproduces_the_transform`:
the corrected descriptor must put every world point exactly where the measured
transform puts the render, on both ROTATED branches. Everything else here
guards the file edit, which has bitten twice -- a comment that mentions ZOOM,
and CRLF.
"""
import tempfile
import unittest
from pathlib import Path

from scripts.correct_overview_descriptor import (
    DescriptorError, correct_file, corrected, rewrite)
from scripts.render_overview_bmp import Projection, read_descriptor

WIDTH, HEIGHT = 1024, 768
WORLD = [(0.0, 0.0), (1024.0, -768.0), (-2048.0, 1536.0), (317.5, -913.25)]
# Shipped bytes, tabs and all. dod_aleutian2_test3 leads with a comment naming a
# zoom, dod_rr2_test is CRLF with no trailing newline.
ALEUTIAN = (b"// Overview: Zoom 1.31, Map Origin (738.00, 24.00, 120.00), Rotated 0\n"
            b"\nglobal \n{\n\tZOOM\t1.33\n\tORIGIN\t738.00 \t\t24.00\t\t120.00\n"
            b"\tROTATED\t0\n}\n")
RR2 = (b"// overview description file for dod_railroad2_test.bmp\r\n\r\nglobal \r\n{\r\n"
       b"\tZOOM\t1.30\r\n\tORIGIN\t-8\t2\t-83\r\n\tROTATED\t0\r\n}")


def transformed(projection, world_x, world_y, scale, dx, dy):
    """Where the audit's transform puts the render's pixel for this world point."""
    px, py = projection.to_pixel(world_x, world_y)
    return (WIDTH / 2 + scale * (px - WIDTH / 2) + dx,
            HEIGHT / 2 + scale * (py - HEIGHT / 2) + dy)


class CorrectedProjection(unittest.TestCase):
    def test_corrected_projection_reproduces_the_transform(self):
        for rotated in (False, True):
            for scale, dx, dy in ((0.745, -1, -1), (1.0, -31, 23), (1.045, 4, -9), (1.0, 0, 0)):
                source = Projection(1.03, 404.0, -76.0, rotated)
                fixed = corrected(source, scale, dx, dy)
                for world_x, world_y in WORLD:
                    want = transformed(source, world_x, world_y, scale, dx, dy)
                    got = fixed.to_pixel(world_x, world_y)
                    self.assertAlmostEqual(want[0], got[0], places=6,
                                           msg=f"px rotated={rotated} {scale},{dx},{dy}")
                    self.assertAlmostEqual(want[1], got[1], places=6,
                                           msg=f"py rotated={rotated} {scale},{dx},{dy}")

    def test_offset_moves_the_axis_the_pixel_axis_carries(self):
        """A pixel dx is not a world x, and the pairing flips with ROTATED."""
        flat = corrected(Projection(1.3, -8.0, 2.0, False), 1.0, dx=-31, dy=23)
        self.assertAlmostEqual(flat.origin_x, -8.0 + 23 / (1.3 / 8), places=6)
        self.assertAlmostEqual(flat.origin_y, 2.0 + -31 / (1.3 / 8), places=6)
        spun = corrected(Projection(1.3, -8.0, 2.0, True), 1.0, dx=-31, dy=23)
        self.assertAlmostEqual(spun.origin_x, -8.0 - -31 / (1.3 / 8), places=6)
        self.assertAlmostEqual(spun.origin_y, 2.0 + 23 / (1.3 / 8), places=6)

    def test_scale_multiplies_zoom_and_identity_changes_nothing(self):
        source = Projection(1.03, 404.0, -76.0, False)
        self.assertAlmostEqual(corrected(source, 0.745, 0, 0).zoom, 1.03 * 0.745, places=9)
        self.assertEqual(corrected(source), source)


class RewriteBytes(unittest.TestCase):
    def test_a_comment_naming_zoom_is_not_the_zoom(self):
        out = rewrite(ALEUTIAN, 1.32, 731.94, 17.94)
        self.assertIn(b"// Overview: Zoom 1.31,", out)
        self.assertIn(b"\tZOOM\t1.32\n", out)
        self.assertIn(b"\tORIGIN\t731.94 \t\t17.94\t\t120.00\n", out)

    def test_crlf_and_a_missing_trailing_newline_survive(self):
        out = rewrite(RR2, 1.30, 133.54, -188.77)
        self.assertEqual(out.count(b"\r\n"), RR2.count(b"\r\n"))
        self.assertFalse(out.endswith(b"\n"))
        self.assertIn(b"\tORIGIN\t133.54\t-188.77\t-83\r\n", out)

    def test_the_camera_pivot_is_left_alone(self):
        self.assertIn(b"\t-83\r\n", rewrite(RR2, 1.30, 133.54, -188.77))

    def test_a_descriptor_missing_a_line_raises_rather_than_half_writing(self):
        with self.assertRaises(DescriptorError):
            rewrite(b"global\n{\n\tROTATED\t0\n}\n", 1.0, 0.0, 0.0)

    def test_round_trip_through_the_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dod_rr2_test.txt"
            path.write_bytes(RR2)
            body, before, after = correct_file(path, 1.0, -31, 23)
            path.write_bytes(body)
            reread = read_descriptor(path)
            self.assertEqual(before.zoom, 1.3)
            self.assertAlmostEqual(reread.zoom, after.zoom, places=2)
            self.assertAlmostEqual(reread.origin_x, after.origin_x, places=2)
            self.assertAlmostEqual(reread.origin_y, after.origin_y, places=2)


if __name__ == "__main__":
    unittest.main()
