"""World bounds and entities out of a GoldSrc BSP.

`precache_audit.py` already walks lump 0 for asset references. This reads the
same file for geometry: lump 14 (MODELS) for the worldspawn bounding box, lump
10 (LEAFS) for the playable volume, and lump 0 again for entity origins.

WHICH BOUNDS. `make_overview_descriptor.py` frames **model 0** -- the
worldspawn brush model's `mins`/`maxs`, which is every brush the mapper left in
the world, sky brushes included and brush entities (doors, func_walls, models
1..n) excluded. That is not an aesthetic choice: measured against the 67
overviews the fleet ships, model-0 bounds reproduce the human-framed ZOOM to a
median ratio of 0.999 on the 51 maps whose descriptor was authored for their own
BSP. Playable-volume bounds (`empty_leaf_bounds`) fit visibly tighter than the
shipped files do, so they are exposed but not the default.

The empty-leaf bounds are also what `ORIGIN z` tracks, so they are worth
reading even when the framing comes from model 0.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

BSP_VERSION_GOLDSRC = 30
LUMP_COUNT = 15
LUMP_ENTITIES, LUMP_LEAFS, LUMP_MODELS = 0, 10, 14
_MODEL_STRUCT = struct.Struct("<9f4i3i")       # mins, maxs, origin, headnode, visleafs+firstface+numfaces
_LEAF_STRUCT = struct.Struct("<ii3h3hHH4B")
CONTENTS_EMPTY = -1

_HEADER = struct.Struct("<i" + "ii" * LUMP_COUNT)
_ENTITY_KV = re.compile(rb'"([^"]*)"\s+"([^"]*)"')


class BspError(ValueError):
    """The file is not a BSP we can read, and guessing at its layout would be worse."""


@dataclass(frozen=True)
class Bounds:
    """An axis-aligned world-space box. `source` names how it was derived."""
    mins: tuple[float, float, float]
    maxs: tuple[float, float, float]
    source: str

    @property
    def extent(self) -> tuple[float, float, float]:
        return tuple(self.maxs[i] - self.mins[i] for i in range(3))

    @property
    def centre(self) -> tuple[float, float, float]:
        return tuple((self.maxs[i] + self.mins[i]) / 2.0 for i in range(3))


def _lumps(blob: bytes) -> tuple[int, list[tuple[int, int]]]:
    if len(blob) < _HEADER.size:
        raise BspError(f"file is {len(blob)} bytes; a BSP header alone is {_HEADER.size}")
    fields = _HEADER.unpack_from(blob, 0)
    version, rest = fields[0], fields[1:]
    if version != BSP_VERSION_GOLDSRC:
        raise BspError(f"BSP version {version}; only GoldSrc v{BSP_VERSION_GOLDSRC} is understood")
    table = [(rest[i * 2], rest[i * 2 + 1]) for i in range(LUMP_COUNT)]
    for index, (offset, length) in enumerate(table):
        if offset < 0 or length < 0 or offset + length > len(blob):
            raise BspError(f"lump {index} runs to {offset + length} in a {len(blob)}-byte file")
    return version, table


def _lump(blob: bytes, table, index: int) -> bytes:
    offset, length = table[index]
    return blob[offset:offset + length]


def read(path) -> "Bsp":
    path = Path(path)
    blob = path.read_bytes()
    version, table = _lumps(blob)
    return Bsp(name=path.stem, version=version, _blob=blob, _table=table)


@dataclass(frozen=True)
class Bsp:
    name: str
    version: int
    _blob: bytes
    _table: list

    def world_bounds(self) -> Bounds:
        """Model 0's bounding box: every worldspawn brush, sky brushes included."""
        data = _lump(self._blob, self._table, LUMP_MODELS)
        if len(data) < _MODEL_STRUCT.size:
            raise BspError(f"{self.name}: MODELS lump holds no worldspawn model")
        values = _MODEL_STRUCT.unpack_from(data, 0)
        mins, maxs = tuple(values[0:3]), tuple(values[3:6])
        if any(maxs[i] <= mins[i] for i in range(3)):
            raise BspError(f"{self.name}: worldspawn bounds are degenerate: {mins} .. {maxs}")
        return Bounds(mins, maxs, "bsp:model0")

    def empty_leaf_bounds(self) -> Bounds | None:
        """Union of the CONTENTS_EMPTY leaves -- roughly where a player can stand.

        Leaf 0 is the shared solid leaf and carries no bounds; leaves with an
        all-zero box are placeholders. None when the lump yields neither.
        """
        data = _lump(self._blob, self._table, LUMP_LEAFS)
        mins = [float("inf")] * 3
        maxs = [float("-inf")] * 3
        seen = 0
        for offset in range(_LEAF_STRUCT.size, len(data) - _LEAF_STRUCT.size + 1, _LEAF_STRUCT.size):
            fields = _LEAF_STRUCT.unpack_from(data, offset)
            if fields[0] != CONTENTS_EMPTY:
                continue
            box = fields[2:8]
            if not any(box):
                continue
            for axis in range(3):
                mins[axis] = min(mins[axis], box[axis])
                maxs[axis] = max(maxs[axis], box[axis + 3])
            seen += 1
        if not seen:
            return None
        return Bounds(tuple(float(v) for v in mins), tuple(float(v) for v in maxs), "bsp:empty-leaves")

    def entities(self) -> list[dict[str, str]]:
        """Key/value dicts from the entity lump, in file order."""
        out: list[dict[str, str]] = []
        current: dict[str, str] | None = None
        for line in _lump(self._blob, self._table, LUMP_ENTITIES).split(b"\n"):
            stripped = line.strip()
            if stripped.startswith(b"{"):
                current = {}
            elif stripped.startswith(b"}"):
                if current is not None:
                    out.append(current)
                current = None
            elif current is not None:
                match = _ENTITY_KV.match(stripped)
                if match:
                    current[match.group(1).decode("latin-1")] = match.group(2).decode("latin-1")
        return out

    def entity_origins(self, classnames=None) -> list[tuple[str, tuple[float, float, float]]]:
        """(classname, origin) for point entities, optionally filtered by classname."""
        out = []
        for entity in self.entities():
            raw = entity.get("origin")
            classname = entity.get("classname", "")
            if not raw or (classnames is not None and classname not in classnames):
                continue
            parts = raw.split()
            if len(parts) != 3:
                continue
            try:
                out.append((classname, (float(parts[0]), float(parts[1]), float(parts[2]))))
            except ValueError:
                continue
        return out
