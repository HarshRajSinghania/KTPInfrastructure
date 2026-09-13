"""The KTPR v2 floor and spatial-layers v2 must never silently disappear again.

PR #289 merged 2026-09-10 and GitHub showed it as MERGED, but its actual
content -- `analytics_report_dto.ktpr_display` and the v2 fields in
`spatial_layers.py` -- never reached `main`. It sat missing for three days,
through Season 10's own match day, caught only by direct file inspection
hours before the first real match, and restored as PR #337.

CI did not catch it because nothing asserted these symbols by name:
`test_analytics_report_dto.py` never imported or called `ktpr_display`, and
`test_spatial_layers.py`'s own `DEFINITION_VERSION` assertions vanished in
the same bad merge as the code they tested, so they never ran against the
broken state. A test added in the same PR as a feature can disappear with
that PR; this file is deliberately standalone in `main`, so a later PR's bad
merge cannot take it down with the code it guards.

Adding to either module is expected and passes. Removing or weakening either
fix fails here.
"""
import unittest

from scripts.analytics_report_dto import ktpr_display
from scripts.spatial_layers import DEFINITION_VERSION, LATTICE_SCHEME, SpatialLayersConfig


class KtprFloorLock(unittest.TestCase):
    def test_negative_z_scores_floor_at_50(self):
        # Operator ruling 2026-09-09: the website must never show a
        # negative KTPR v2 number. This is the exact defect PR #337 restored.
        self.assertEqual(ktpr_display(-9.0), 50.0)
        # Center 100, per_z 15: z=-3.33 is exactly where 100+15z crosses 50.
        self.assertEqual(ktpr_display(-3.4), 50.0)

    def test_a_mildly_negative_z_score_is_not_floored(self):
        # The floor only bites on an extreme outlier -- a modestly below-
        # average match should still show its own value, not a flat 50 for
        # everyone under the mean.
        self.assertEqual(ktpr_display(-1.0), 85.0)

    def test_center_and_scale_are_the_ruled_values(self):
        # z=0 -> the display center; a positive z moves up by per_z per unit.
        self.assertEqual(ktpr_display(0.0), 100.0)
        self.assertEqual(ktpr_display(1.0), 115.0)

    def test_none_passes_through_as_none_not_the_floor(self):
        # A missing rating is "unavailable", not a fabricated 50 — the floor
        # only applies to a real, computed z-score.
        self.assertIsNone(ktpr_display(None))

    def test_control_the_floor_has_a_real_effect(self):
        # Negative control: without a working floor, a "large enough z stays
        # unfloored" assertion below would pass even if floor() were a no-op.
        self.assertEqual(ktpr_display(5.0), 175.0)


class SpatialLayersV2Lock(unittest.TestCase):
    def test_definition_version_is_2(self):
        # v1 (world_256_v1) is exactly the state #289 silently reverted to.
        self.assertEqual(DEFINITION_VERSION, 2)

    def test_lattice_scheme_is_the_fine_grid(self):
        self.assertEqual(LATTICE_SCHEME, "world_grid_v2")

    def test_kill_paths_publish_by_default(self):
        # Operator ruling 2026-09-09: kill paths are public (name + team, no
        # ids), not the pre-ruling private-only default.
        self.assertTrue(SpatialLayersConfig().publish_frag_vectors)

    def test_grid_size_is_the_fine_128_unit_grid(self):
        # The other half of the v1->v2 regression: v1's cells were 256 units.
        self.assertEqual(SpatialLayersConfig().grid_size, 128.0)


if __name__ == "__main__":
    unittest.main()
