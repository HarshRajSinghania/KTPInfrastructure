"""Reading geometry out of a GoldSrc BSP.

The fixtures are built byte by byte rather than checked in: a real BSP is
megabytes, and a synthetic one lets a malformed lump be tested at all. What is
pinned against real maps lives in test_overview_descriptor.py.
"""
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.bsp_bounds import BSP_VERSION_GOLDSRC, BspError, Bounds, read

LUMP_COUNT = 15
LUMP_ENTITIES, LUMP_LEAFS, LUMP_MODELS = 0, 10, 14
HEADER_SIZE = 4 + LUMP_COUNT * 8
CONTENTS_EMPTY, CONTENTS_SOLID, CONTENTS_SKY = -1, -2, -6


def model(mins, maxs):
    return struct.pack("<9f4i3i", *mins, *maxs, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0)


def leaf(contents, mins, maxs):
    return struct.pack("<ii3h3hHH4B", contents, -1, *mins, *maxs, 0, 0, 0, 0, 0, 0)


def build_bsp(models=(), leaves=(), entities=b"", version=BSP_VERSION_GOLDSRC):
    """A BSP with only the three lumps this module reads populated."""
    payload = {LUMP_MODELS: b"".join(models), LUMP_LEAFS: b"".join(leaves),
               LUMP_ENTITIES: entities}
    table, body, offset = [], b"", HEADER_SIZE
    for index in range(LUMP_COUNT):
        chunk = payload.get(index, b"")
        table.append((offset, len(chunk)))
        body += chunk
        offset += len(chunk)
    header = struct.pack("<i", version)
    for entry in table:
        header += struct.pack("<ii", *entry)
    return header + body


def write(tmp, name, blob):
    path = Path(tmp) / name
    path.write_bytes(blob)
    return path


class Header(unittest.TestCase):
    def test_a_foreign_bsp_version_is_named_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "dod_x.bsp", build_bsp(models=[model((0, 0, 0), (1, 1, 1))],
                                                     version=29))
            with self.assertRaises(BspError) as caught:
                read(path)
            self.assertIn("29", str(caught.exception))

    def test_a_file_too_short_to_hold_a_header_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BspError):
                read(write(tmp, "dod_x.bsp", b"\x1e\x00\x00\x00short"))

    def test_a_lump_running_past_the_end_of_the_file_is_refused(self):
        # The reader must not hand back a silently short lump: a truncated MODELS
        # lump reads as a smaller map, which frames a confident picture of part of it.
        blob = bytearray(build_bsp(models=[model((-64, -64, -64), (64, 64, 64))]))
        struct.pack_into("<i", blob, 4 + LUMP_MODELS * 8 + 4, 1 << 20)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BspError) as caught:
                read(write(tmp, "dod_x.bsp", bytes(blob)))
            self.assertIn(str(LUMP_MODELS), str(caught.exception))


class WorldBounds(unittest.TestCase):
    def test_model_zero_is_the_world_and_later_models_are_ignored(self):
        blob = build_bsp(models=[model((-100.0, -200.0, -30.0), (300.0, 400.0, 70.0)),
                                 model((-9000.0, -9000.0, -9000.0), (9000.0, 9000.0, 9000.0))])
        with tempfile.TemporaryDirectory() as tmp:
            bounds = read(write(tmp, "dod_x.bsp", blob)).world_bounds()
        self.assertEqual(bounds.mins, (-100.0, -200.0, -30.0))
        self.assertEqual(bounds.maxs, (300.0, 400.0, 70.0))
        self.assertEqual(bounds.source, "bsp:model0")

    def test_extent_and_centre(self):
        bounds = Bounds((-100.0, -200.0, -30.0), (300.0, 400.0, 70.0), "t")
        self.assertEqual(bounds.extent, (400.0, 600.0, 100.0))
        self.assertEqual(bounds.centre, (100.0, 100.0, 20.0))

    def test_an_empty_models_lump_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(BspError):
                read(write(tmp, "dod_x.bsp", build_bsp())).world_bounds()

    def test_degenerate_world_bounds_are_refused(self):
        for mins, maxs in (((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                           ((10.0, 0.0, 0.0), (-10.0, 5.0, 5.0))):
            blob = build_bsp(models=[model(mins, maxs)])
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(BspError):
                    read(write(tmp, "dod_x.bsp", blob)).world_bounds()


class EmptyLeafBounds(unittest.TestCase):
    def setUp(self):
        self.leaves = [
            leaf(CONTENTS_SOLID, (0, 0, 0), (0, 0, 0)),          # leaf 0, always skipped
            leaf(CONTENTS_EMPTY, (-64, -32, -16), (64, 32, 16)),
            leaf(CONTENTS_EMPTY, (32, 16, 8), (128, 256, 48)),
            leaf(CONTENTS_SKY, (-4000, -4000, -4000), (4000, 4000, 4000)),
            leaf(CONTENTS_SOLID, (-999, -999, -999), (999, 999, 999)),
            leaf(CONTENTS_EMPTY, (0, 0, 0), (0, 0, 0)),          # placeholder, no bounds
        ]

    def test_only_empty_leaves_contribute(self):
        blob = build_bsp(models=[model((-1, -1, -1), (1, 1, 1))], leaves=self.leaves)
        with tempfile.TemporaryDirectory() as tmp:
            bounds = read(write(tmp, "dod_x.bsp", blob)).empty_leaf_bounds()
        self.assertEqual(bounds.mins, (-64.0, -32.0, -16.0))
        self.assertEqual(bounds.maxs, (128.0, 256.0, 48.0))
        self.assertEqual(bounds.source, "bsp:empty-leaves")

    def test_leaf_zero_is_skipped_even_when_it_carries_bounds(self):
        leaves = [leaf(CONTENTS_EMPTY, (-5000, -5000, -5000), (5000, 5000, 5000))] + self.leaves[1:]
        blob = build_bsp(models=[model((-1, -1, -1), (1, 1, 1))], leaves=leaves)
        with tempfile.TemporaryDirectory() as tmp:
            bounds = read(write(tmp, "dod_x.bsp", blob)).empty_leaf_bounds()
        self.assertEqual(bounds.mins, (-64.0, -32.0, -16.0))

    def test_no_empty_leaves_is_none_not_an_infinite_box(self):
        blob = build_bsp(models=[model((-1, -1, -1), (1, 1, 1))],
                         leaves=[self.leaves[0], self.leaves[3]])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read(write(tmp, "dod_x.bsp", blob)).empty_leaf_bounds())


class Entities(unittest.TestCase):
    ENTS = (b'{\n"classname" "worldspawn"\n"wad" "\\dod\\halflife.wad"\n}\n'
            b'{\n"origin" "16 -32 64"\n"classname" "info_player_allies"\n}\n'
            b'{\n"classname" "light"\n"origin" "1.5 2.5 -3.5"\n}\n'
            b'{\n"classname" "func_door"\n"model" "*1"\n}\n'
            b'{\n"classname" "broken"\n"origin" "1 2"\n}\n'
            b'{\n"classname" "alsobroken"\n"origin" "a b c"\n}\n')

    def bsp(self, tmp):
        return read(write(tmp, "dod_x.bsp",
                          build_bsp(models=[model((-1, -1, -1), (1, 1, 1))], entities=self.ENTS)))

    def test_key_values_survive_in_file_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            entities = self.bsp(tmp).entities()
        self.assertEqual(len(entities), 6)
        self.assertEqual(entities[0]["classname"], "worldspawn")
        self.assertEqual(entities[0]["wad"], "\\dod\\halflife.wad")
        self.assertEqual(entities[3]["model"], "*1")

    def test_origins_skip_brush_entities_and_unparseable_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            origins = self.bsp(tmp).entity_origins()
        self.assertEqual(origins, [("info_player_allies", (16.0, -32.0, 64.0)),
                                   ("light", (1.5, 2.5, -3.5))])

    def test_classname_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            origins = self.bsp(tmp).entity_origins({"light"})
        self.assertEqual([c for c, _ in origins], ["light"])


if __name__ == "__main__":
    unittest.main()
