"""The corpus audit measures what it says it measures.

Every test here builds its own images, so the suite runs without the game
corpus. The point of the audit is a claim about which SIDE of a mismatch is
wrong, so most of these construct a known defect -- a shift, a scale, a missing
key -- and check the tool recovers it and buckets it the way the defect
deserves.

The checks against real maps need the shipped corpus and are skipped without
`KTP_OVERVIEW_CORPUS`; the skip says so, because a skipped check is not a
passed one."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from scripts import audit_overview_corpus as audit
from scripts import render_overview_bmp as ov

CORPUS = os.environ.get("KTP_OVERVIEW_CORPUS")
W, H = 64, 48


def mask_from_rects(width, height, rects) -> bytes:
    """An opaque mask: 1 inside any rect, 0 (the transparent key) outside."""
    out = bytearray(width * height)
    for x0, y0, x1, y1 in rects:
        for y in range(max(0, y0), min(height, y1)):
            row = y * width
            for x in range(max(0, x0), min(width, x1)):
                out[row + x] = 1
    return bytes(out)


def rows_of(mask, width=W, height=H):
    return audit.split_rows(mask, width, height)


def shift_mask(mask, dx, dy, width=W, height=H):
    """The same picture moved (dx, dy), which is the defect the search hunts."""
    out = bytearray(width * height)
    for y in range(height):
        sy = y - dy
        if not 0 <= sy < height:
            continue
        for x in range(width):
            sx = x - dx
            if 0 <= sx < width and mask[sy * width + sx]:
                out[y * width + x] = 1
    return bytes(out)


def write_bmp(path: Path, mask: bytes, width=W, height=H, keyed=True) -> None:
    """An 8-bit BMP whose opaque pixels are index 1 and whose key is index 0.

    With `keyed=False` the key colour is absent from the palette entirely, which
    is the fully-painted case the audit must refuse to score.
    """
    palette = [(0, 255, 0) if keyed else (12, 12, 12)] + [(200, 30, 30)] * 255
    path.write_bytes(ov.encode_bmp(bytes(mask), palette, width, height))


class PackingTests(unittest.TestCase):
    def test_pack_sets_bit_per_opaque_pixel_msb_first(self):
        rows = audit.pack_rows([bytes([1, 0, 0, 1])])
        self.assertEqual(rows, [0b1001])

    def test_split_rows_rejects_a_mask_of_the_wrong_length(self):
        with self.assertRaises(ValueError):
            audit.split_rows(b"\x00" * 10, 4, 4)

    def test_pack_round_trips_every_byte_value_as_opaque_or_not(self):
        # opaque_mask only ever emits 0 and 1, but the translation table covers
        # all 256 so a future palette change cannot silently zero a row.
        self.assertEqual(audit.pack_rows([bytes([0, 5, 0, 255])]), [0b0101])


class IouTests(unittest.TestCase):
    def test_identical_footprints_score_one(self):
        rows = audit.pack_rows(rows_of(mask_from_rects(W, H, [(10, 10, 30, 30)])))
        self.assertEqual(audit.iou(rows, rows, W, H, 0, 0), 1.0)

    def test_disjoint_footprints_score_zero(self):
        a = audit.pack_rows(rows_of(mask_from_rects(W, H, [(0, 0, 10, 10)])))
        b = audit.pack_rows(rows_of(mask_from_rects(W, H, [(40, 30, 50, 40)])))
        self.assertEqual(audit.iou(a, b, W, H, 0, 0), 0.0)

    def test_two_empty_footprints_score_zero_rather_than_dividing_by_zero(self):
        empty = audit.pack_rows(rows_of(bytes(W * H)))
        self.assertEqual(audit.iou(empty, empty, W, H, 0, 0), 0.0)

    def test_half_overlap_scores_one_third(self):
        a = audit.pack_rows(rows_of(mask_from_rects(W, H, [(0, 0, 20, 10)])))
        b = audit.pack_rows(rows_of(mask_from_rects(W, H, [(10, 0, 30, 10)])))
        self.assertAlmostEqual(audit.iou(a, b, W, H, 0, 0), 100 / 300)

    def test_a_shift_recovers_a_shifted_copy_exactly(self):
        base = mask_from_rects(W, H, [(10, 10, 30, 26)])
        a = audit.pack_rows(rows_of(base))
        b = audit.pack_rows(rows_of(shift_mask(base, -5, 3)))
        self.assertLess(audit.iou(a, b, W, H, 0, 0), 1.0)
        # b was moved (-5, +3), so it takes (+5, -3) to put it back on a.
        self.assertEqual(audit.iou(a, b, W, H, 5, -3), 1.0)

    def test_dx_moves_the_second_image_right(self):
        base = mask_from_rects(W, H, [(4, 4, 8, 8)])
        a = audit.pack_rows(rows_of(mask_from_rects(W, H, [(9, 4, 13, 8)])))
        b = audit.pack_rows(rows_of(base))
        self.assertEqual(audit.iou(a, b, W, H, 5, 0), 1.0)

    def test_dy_moves_the_second_image_down(self):
        base = mask_from_rects(W, H, [(4, 4, 8, 8)])
        a = audit.pack_rows(rows_of(mask_from_rects(W, H, [(4, 7, 8, 11)])))
        b = audit.pack_rows(rows_of(base))
        self.assertEqual(audit.iou(a, b, W, H, 0, 3), 1.0)


class ResampleTests(unittest.TestCase):
    def test_scale_one_is_a_plain_pack(self):
        rows = rows_of(mask_from_rects(W, H, [(10, 10, 30, 30)]))
        self.assertEqual(audit.resample(rows, W, H, 1.0), audit.pack_rows(rows))

    def test_growing_a_footprint_makes_it_cover_more_pixels(self):
        rows = rows_of(mask_from_rects(W, H, [(24, 16, 40, 32)]))
        small = sum(r.bit_count() for r in audit.resample(rows, W, H, 1.0))
        big = sum(r.bit_count() for r in audit.resample(rows, W, H, 1.5))
        self.assertGreater(big, small)

    def test_scaling_is_about_the_image_centre(self):
        rows = rows_of(mask_from_rects(W, H, [(W // 2 - 4, H // 2 - 4,
                                               W // 2 + 4, H // 2 + 4)]))
        grown = audit.resample(rows, W, H, 2.0)
        # A centred square stays centred: the middle row is still set at the middle.
        self.assertTrue((grown[H // 2] >> (W - 1 - W // 2)) & 1)


class SearchTests(unittest.TestCase):
    def test_identical_images_need_no_transform(self):
        rows = rows_of(mask_from_rects(W, H, [(12, 8, 44, 36)]))
        result = audit.search_alignment(audit.pack_rows(rows), rows, W, H)
        self.assertEqual(result.baseline, 1.0)
        self.assertTrue(result.identity)
        self.assertEqual(result.gain, 0.0)

    def test_the_search_recovers_a_known_shift(self):
        base = mask_from_rects(W, H, [(12, 8, 44, 36)])
        shipped = audit.pack_rows(rows_of(shift_mask(base, 7, -5)))
        result = audit.search_alignment(shipped, rows_of(base), W, H)
        self.assertEqual((result.dx, result.dy), (7, -5))
        self.assertAlmostEqual(result.best, 1.0)
        self.assertGreater(result.gain, 0.0)

    def test_the_search_recovers_a_known_scale(self):
        big = mask_from_rects(W, H, [(8, 4, 56, 44)])
        small = mask_from_rects(W, H, [(20, 16, 44, 32)])
        result = audit.search_alignment(audit.pack_rows(rows_of(big)),
                                        rows_of(small), W, H)
        self.assertGreater(result.scale, 1.3)
        self.assertGreater(result.best, result.baseline)

    def test_a_tie_is_reported_as_the_smaller_transform(self):
        # A footprint symmetric about the centre matches equally at +d and -d.
        rows = rows_of(mask_from_rects(W, H, [(0, 0, W, H)]))
        result = audit.search_alignment(audit.pack_rows(rows), rows, W, H)
        self.assertTrue(result.identity, f"tie broke to {result}")

    def test_a_recovered_shift_is_not_flagged_clipped(self):
        base = mask_from_rects(W, H, [(12, 8, 44, 36)])
        shipped = audit.pack_rows(rows_of(shift_mask(base, 7, -5)))
        result = audit.search_alignment(shipped, rows_of(base), W, H)
        self.assertFalse(result.clipped)

    def test_an_optimum_the_search_cannot_reach_is_flagged_clipped(self):
        # A best that sits on the edge of the last box searched is a clipped
        # search, not an answer, and the report has to be able to say so.
        base = mask_from_rects(W, H, [(4, 20, 28, 28)])
        shipped = audit.pack_rows(rows_of(shift_mask(base, 24, 0)))
        with unittest.mock.patch.object(audit, "MAX_RECENTRES", 1):
            result = audit.search_alignment(shipped, rows_of(base), W, H,
                                            passes=((0.05, 8, 1),), refine=False)
        self.assertTrue(result.clipped)
        self.assertLess(result.dx, 24)


class DescriptorParsingTests(unittest.TestCase):
    def test_comments_do_not_supply_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.txt"
            path.write_text("// ZOOM 9.99 ORIGIN 1 2 3 ROTATED 1\n"
                            "global {\n ZOOM 1.21\n ORIGIN 8.0 -90 -425\n ROTATED 0\n}\n")
            projection = ov.read_descriptor(path)
            self.assertEqual(audit.describe_projection(projection),
                             (1.21, 8.0, -90.0, False))


class DuplicateTests(unittest.TestCase):
    def _corpus(self, tmp):
        overviews = Path(tmp) / "overviews"
        overviews.mkdir()
        mask = mask_from_rects(W, H, [(10, 10, 30, 30)])
        for name in ("map_a", "map_b", "map_c"):
            write_bmp(overviews / f"{name}.bmp", mask)
        (overviews / "map_a.txt").write_text(
            "global {\n ZOOM 1.00\n ORIGIN 0 0 0\n ROTATED 0\n}\n")
        (overviews / "map_b.txt").write_text(
            "global {\n ZOOM 1.00\n ORIGIN 0 0 0\n ROTATED 0\n}\n")
        return overviews

    def test_identical_images_with_identical_descriptors_are_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            overviews = self._corpus(tmp)
            inv = audit.take_inventory(overviews, None)
            groups = audit.duplicate_groups(inv.images, inv.descriptors)
            self.assertEqual(len(groups), 1)
            self.assertFalse(groups[0]["descriptors_disagree"])
            self.assertEqual(groups[0]["without_descriptor"], ["map_c"])

    def test_identical_images_with_different_descriptors_are_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            overviews = self._corpus(tmp)
            (overviews / "map_b.txt").write_text(
                "global {\n ZOOM 1.30\n ORIGIN 64 -8 0\n ROTATED 0\n}\n")
            inv = audit.take_inventory(overviews, None)
            groups = audit.duplicate_groups(inv.images, inv.descriptors)
            self.assertTrue(groups[0]["descriptors_disagree"])
            self.assertEqual(len(groups[0]["distinct_projections"]), 2)

    def test_a_rotation_difference_alone_counts_as_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            overviews = self._corpus(tmp)
            (overviews / "map_b.txt").write_text(
                "global {\n ZOOM 1.00\n ORIGIN 0 0 0\n ROTATED 1\n}\n")
            inv = audit.take_inventory(overviews, None)
            groups = audit.duplicate_groups(inv.images, inv.descriptors)
            self.assertTrue(groups[0]["descriptors_disagree"])

    def test_zero_byte_images_do_not_group_with_each_other(self):
        # Every empty file has the same md5, so they otherwise arrive as one
        # large "duplicate family" that does not exist. A half-finished copy
        # really does leave these behind.
        with tempfile.TemporaryDirectory() as tmp:
            overviews = Path(tmp) / "overviews"
            overviews.mkdir()
            for name in ("truncated_a", "truncated_b", "truncated_c"):
                (overviews / f"{name}.bmp").write_bytes(b"")
            inv = audit.take_inventory(overviews, None)
            self.assertEqual(audit.duplicate_groups(inv.images, inv.descriptors), [])
            self.assertEqual(inv.empty_images,
                             ["truncated_a", "truncated_b", "truncated_c"])

    def test_the_md5_of_an_empty_file_is_the_one_being_guarded_against(self):
        self.assertEqual(hashlib.md5(b"").hexdigest(),
                         "d41d8cd98f00b204e9800998ecf8427e")

    def test_a_unique_image_makes_no_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            overviews = Path(tmp) / "overviews"
            overviews.mkdir()
            write_bmp(overviews / "solo.bmp", mask_from_rects(W, H, [(1, 1, 5, 5)]))
            inv = audit.take_inventory(overviews, None)
            self.assertEqual(audit.duplicate_groups(inv.images, inv.descriptors), [])


class InventoryTests(unittest.TestCase):
    def test_parentless_assets_are_each_reported_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            overviews = Path(tmp) / "overviews"
            maps = Path(tmp) / "maps"
            overviews.mkdir()
            maps.mkdir()
            write_bmp(overviews / "both.bmp", mask_from_rects(W, H, [(1, 1, 5, 5)]))
            (overviews / "both.txt").write_text("ZOOM 1\nORIGIN 0 0 0\nROTATED 0\n")
            write_bmp(overviews / "image_only.bmp", mask_from_rects(W, H, [(1, 1, 5, 5)]))
            (overviews / "text_only.txt").write_text("ZOOM 1\nORIGIN 0 0 0\nROTATED 0\n")
            (maps / "both.bsp").write_bytes(b"\x1e\x00\x00\x00")
            (maps / "lonely_map.bsp").write_bytes(b"\x1e\x00\x00\x00")
            inv = audit.take_inventory(overviews, maps)
            self.assertEqual(inv.image_without_descriptor, ["image_only"])
            self.assertEqual(inv.descriptor_without_image, ["text_only"])
            self.assertEqual(inv.overview_without_map, ["image_only", "text_only"])
            self.assertEqual(inv.map_without_overview, ["lonely_map"])
            self.assertEqual(inv.triples, ["both"])

    def test_an_uppercase_suffix_is_recorded_not_normalised_away(self):
        # The engine asks for overviews/<map>.bmp in lower case, so on the Linux
        # fleet a .BMP is a missing overview however well it works on Windows.
        with tempfile.TemporaryDirectory() as tmp:
            overviews = Path(tmp) / "overviews"
            overviews.mkdir()
            write_bmp(overviews / "shouty.BMP", mask_from_rects(W, H, [(1, 1, 5, 5)]))
            inv = audit.take_inventory(overviews, None)
            self.assertEqual(inv.uppercase_suffix, ["shouty.BMP"])
            self.assertIn("shouty", inv.images)


class BucketTests(unittest.TestCase):
    def _alignment(self, baseline, best, scale=1.0, dx=0, dy=0):
        return audit.Alignment(baseline, best, scale, dx, dy, 1)

    def test_identity_and_a_high_score_agrees(self):
        bucket, _ = audit.classify(self._alignment(0.95, 0.95), 0.85, 0.02)
        self.assertEqual(bucket, "agrees")

    def test_identity_and_a_low_score_is_a_renderer_gap(self):
        bucket, reason = audit.classify(self._alignment(0.40, 0.40), 0.85, 0.02)
        self.assertEqual(bucket, "renderer-gap")
        self.assertIn("identity", reason)

    def test_a_worthwhile_shift_is_a_reference_defect(self):
        bucket, _ = audit.classify(self._alignment(0.59, 0.76, dx=-31, dy=23), 0.85, 0.02)
        self.assertEqual(bucket, "reference-defective")

    def test_a_shift_that_buys_almost_nothing_is_not_a_defect(self):
        # Noise in a nearest-neighbour footprint can move the optimum a pixel;
        # that is not evidence the shipped asset is misplaced.
        bucket, _ = audit.classify(self._alignment(0.90, 0.905, dx=1), 0.85, 0.02)
        self.assertEqual(bucket, "agrees")

    def test_a_low_score_a_shift_barely_helps_is_a_renderer_gap_not_a_defect(self):
        bucket, _ = audit.classify(self._alignment(0.40, 0.41, dx=2), 0.85, 0.02)
        self.assertEqual(bucket, "renderer-gap")


class NoKeyTests(unittest.TestCase):
    def test_a_fully_painted_image_is_not_comparable_rather_than_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            overviews = Path(tmp) / "overviews"
            maps = Path(tmp) / "maps"
            overviews.mkdir()
            maps.mkdir()
            write_bmp(overviews / "painted.bmp",
                      mask_from_rects(W, H, [(0, 0, W, H)]), keyed=False)
            (overviews / "painted.txt").write_text("ZOOM 1\nORIGIN 0 0 0\nROTATED 0\n")
            (maps / "painted.bsp").write_bytes(b"\x1e\x00\x00\x00")
            inv = audit.take_inventory(overviews, maps)
            result = audit.audit_map("painted", inv, 0.85, 0.02)
            self.assertEqual(result.bucket, "not-comparable")
            self.assertEqual(result.key_pixels, 0)
            self.assertIsNone(result.best)

    def test_a_keyed_image_reports_its_key_pixel_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.bmp"
            write_bmp(path, mask_from_rects(W, H, [(0, 0, W // 2, H)]))
            _, width, height, keyed = audit.footprint(path)
            self.assertEqual((width, height), (W, H))
            self.assertEqual(keyed, W * H // 2)

    def test_an_unreadable_bsp_is_not_comparable_not_a_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            overviews = Path(tmp) / "overviews"
            maps = Path(tmp) / "maps"
            overviews.mkdir()
            maps.mkdir()
            write_bmp(overviews / "broken.bmp", mask_from_rects(W, H, [(4, 4, 20, 20)]))
            (overviews / "broken.txt").write_text("ZOOM 1\nORIGIN 0 0 0\nROTATED 0\n")
            (maps / "broken.bsp").write_bytes(b"not a bsp at all")
            inv = audit.take_inventory(overviews, maps)
            result = audit.audit_map("broken", inv, 0.85, 0.02)
            self.assertEqual(result.bucket, "not-comparable")
            self.assertIn("render failed", result.reason)


class ReportTests(unittest.TestCase):
    def test_defective_maps_rank_by_how_much_the_transform_buys(self):
        results = [
            audit.MapResult("small", "reference-defective", "", baseline=0.70,
                            best=0.73, gain=0.03, scale=1.0, dx=-7, dy=0),
            audit.MapResult("large", "reference-defective", "", baseline=0.59,
                            best=0.76, gain=0.18, scale=1.0, dx=-31, dy=23),
            audit.MapResult("fine", "agrees", "", baseline=0.95, best=0.95),
        ]
        report = audit.build_report(audit.Inventory(), results)
        self.assertEqual([r["name"] for r in report["ranked_defective"]],
                         ["large", "small"])
        self.assertEqual(report["bucket_counts"],
                         {"agrees": 1, "reference-defective": 2})

    def test_the_printed_report_names_the_no_key_images(self):
        results = [audit.MapResult("painted", "not-comparable", "no key",
                                   key_pixels=0)]
        report = audit.build_report(audit.Inventory(), results)
        buffer = io.StringIO()
        audit.print_report(report, [], stream=buffer)
        text = buffer.getvalue()
        self.assertIn("keys zero pixels (1)", text)
        self.assertIn("painted", text)


class CliTests(unittest.TestCase):
    def test_json_report_carries_inventory_buckets_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            overviews = Path(tmp) / "overviews"
            overviews.mkdir()
            mask = mask_from_rects(W, H, [(8, 8, 40, 32)])
            write_bmp(overviews / "one.bmp", mask)
            write_bmp(overviews / "two.bmp", mask)
            for name in ("one", "two"):
                (overviews / f"{name}.txt").write_text(
                    "global {\n ZOOM 1.00\n ORIGIN 0 0 0\n ROTATED 0\n}\n")
            out = Path(tmp) / "report.json"
            with redirect_stdout(io.StringIO()):
                code = audit.main(["--overviews", str(overviews), "--json", str(out)])
            self.assertEqual(code, 0)
            report = json.loads(out.read_text())
            self.assertEqual(report["inventory"]["images"], 2)
            self.assertEqual(report["inventory"]["complete_triples"], 0)
            self.assertEqual(len(report["duplicate_image_groups"]), 1)
            self.assertFalse(report["duplicate_image_groups"][0]["descriptors_disagree"])

    def test_an_unknown_map_is_an_error_not_an_empty_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            overviews = Path(tmp) / "overviews"
            overviews.mkdir()
            with redirect_stdout(io.StringIO()):
                code = audit.main(["--overviews", str(overviews), "--only", "nope"])
            self.assertEqual(code, 2)


@unittest.skipUnless(CORPUS, "needs KTP_OVERVIEW_CORPUS (shipped maps + overviews)")
class CorpusTests(unittest.TestCase):
    """Anchors against real shipped assets, so a refactor cannot quietly move them."""

    @classmethod
    def setUpClass(cls):
        root = Path(CORPUS)
        cls.inv = audit.take_inventory(root / "overviews", root / "maps")

    def _audit(self, name):
        if name not in self.inv.triples:
            self.skipTest(f"{name} is not a complete triple in this corpus")
        return audit.audit_map(name, self.inv, 0.85, 0.02)

    def test_armory_b4_is_self_consistent(self):
        result = self._audit("dod_armory_b4")
        self.assertEqual(result.bucket, "agrees")
        self.assertEqual((result.scale, result.dx, result.dy), (1.0, 0, 0))
        self.assertGreater(result.best, 0.94)

    def test_anzio3_b1_is_drawn_at_about_three_quarters_of_its_declared_zoom(self):
        result = self._audit("dod_anzio3_b1")
        self.assertEqual(result.bucket, "reference-defective")
        self.assertLess(result.scale, 0.80)
        self.assertGreater(result.gain, 0.30)

    def test_railroad_ships_no_key_so_it_is_never_scored(self):
        if "dod_railroad" not in self.inv.images:
            self.skipTest("dod_railroad is not in this corpus")
        _, _, _, keyed = audit.footprint(self.inv.images["dod_railroad"])
        self.assertEqual(keyed, 0)


if __name__ == "__main__":
    unittest.main()
