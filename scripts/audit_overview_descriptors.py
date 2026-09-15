#!/usr/bin/env python3
"""Score `make_overview_descriptor.py` against the overviews a host already ships.

Every number the descriptor work claims -- the ROTATED rule holding 67/67, the
median zoom ratio, the corner displacement -- is this script's output on a game
host's `dod/` directory. It is here so the claim is re-derivable rather than a
figure in a changelog that nothing keeps true.

    rsync a host's dod/maps and dod/overviews somewhere local, then:
    python3 scripts/audit_overview_descriptors.py /path/to/dod

A map whose descriptor was authored for a sibling revision is flagged rather
than silently counted: same ZOOM and ORIGIN as another map in the pool whose
worldspawn differs. Their framing error belongs to the shipped file.
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bsp_bounds import BspError, read  # noqa: E402
from scripts.make_overview_descriptor import UnusableMap, project, solve  # noqa: E402

_TOKEN = re.compile(r'"[^"]*"|\S+')


def parse_descriptor(text: str) -> dict:
    """COM_ParseFile's contract: quoted or whitespace-separated tokens, `//` to EOL."""
    tokens = []
    for line in text.splitlines():
        tokens.extend(t.strip('"') for t in _TOKEN.findall(line.split("//")[0]))
    out = {"zoom": 1.0, "origin": [0.0, 0.0, 0.0], "rotated": 0, "height": None}
    index = 0
    while index < len(tokens):
        key = tokens[index].lower()
        try:
            if key == "zoom":
                out["zoom"] = float(tokens[index + 1]); index += 1
            elif key == "origin":
                out["origin"] = [float(tokens[index + n]) for n in (1, 2, 3)]; index += 3
            elif key == "rotated":
                out["rotated"] = int(float(tokens[index + 1])); index += 1
            elif key == "height":
                out["height"] = float(tokens[index + 1]); index += 1
        except (IndexError, ValueError):
            pass
        index += 1
    return out


def worst_corner_px(bounds, shipped, solved) -> float:
    """How far the two descriptors disagree about the map's own corners, in pixels."""
    worst = 0.0
    for world_x in (bounds["mins"][0], bounds["maxs"][0]):
        for world_y in (bounds["mins"][1], bounds["maxs"][1]):
            a = project(world_x, world_y, shipped["zoom"], shipped["origin"][0],
                        shipped["origin"][1], shipped["rotated"])
            b = project(world_x, world_y, solved["zoom"], solved["origin"][0],
                        solved["origin"][1], solved["rotated"])
            worst = max(worst, ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)
    return worst


def audit(dod_dir: Path) -> dict:
    rows, skipped = [], []
    for bsp_path in sorted((dod_dir / "maps").glob("*.bsp")):
        name = bsp_path.stem
        txt = dod_dir / "overviews" / f"{name}.txt"
        if not txt.exists():
            skipped.append((name, "no shipped overview"))
            continue
        try:
            solved = solve(read(bsp_path))
        except (BspError, UnusableMap, OSError) as exc:
            skipped.append((name, str(exc)))
            continue
        shipped = parse_descriptor(txt.read_text(errors="replace"))
        rows.append({
            "map": name, "shipped": shipped, "solved": solved,
            "rotated_agrees": shipped["rotated"] == solved["rotated"],
            "zoom_ratio": solved["zoom"] / shipped["zoom"] if shipped["zoom"] else float("nan"),
            "worst_px": worst_corner_px(solved["bounds"], shipped, solved),
        })

    # A descriptor shared by maps whose worldspawn differs was authored for one of them.
    families = defaultdict(list)
    for row in rows:
        s = row["shipped"]
        families[(s["zoom"], s["origin"][0], s["origin"][1])].append(row)
    for group in families.values():
        boxes = {tuple(round(v) for v in r["solved"]["bounds"]["mins"][:2]
                       + r["solved"]["bounds"]["maxs"][:2]) for r in group}
        for row in group:
            row["inherited_descriptor"] = len(group) > 1 and len(boxes) > 1
    return {"rows": rows, "skipped": skipped}


def _spread(label, values, fmt="{:.3f}"):
    values = sorted(values)
    n = len(values)
    print(f"  {label:26s} median " + fmt.format(statistics.median(values)) +
          "  p10 " + fmt.format(values[n // 10]) + "  p90 " + fmt.format(values[int(n * 0.9)]) +
          "  min " + fmt.format(values[0]) + "  max " + fmt.format(values[-1]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dod_dir", type=Path, help="a game host's dod/ directory")
    parser.add_argument("--verbose", action="store_true", help="one line per map")
    args = parser.parse_args(argv)

    result = audit(args.dod_dir)
    rows = result["rows"]
    if not rows:
        print(f"no map with both a .bsp and an overview .txt under {args.dod_dir}",
              file=sys.stderr)
        return 1

    if args.verbose:
        print(f"{'map':26s} {'rot':>3s} {'zoomShip':>8s} {'zoomSolved':>10s} "
              f"{'ratio':>6s} {'worstPx':>7s}  note")
        for row in sorted(rows, key=lambda r: -r["worst_px"]):
            note = "descriptor inherited from a sibling map" if row["inherited_descriptor"] else ""
            note += ("" if row["rotated_agrees"] else "  ROTATED DISAGREES")
            print(f"{row['map']:26s} {row['shipped']['rotated']:3d} "
                  f"{row['shipped']['zoom']:8.2f} {row['solved']['zoom']:10.2f} "
                  f"{row['zoom_ratio']:6.3f} {row['worst_px']:7.0f}  {note}")

    agree = sum(r["rotated_agrees"] for r in rows)
    print(f"\n{len(rows)} maps with a shipped descriptor ({len(result['skipped'])} skipped)")
    print(f"  ROTATED agreement:         {agree}/{len(rows)}")
    for row in rows:
        if not row["rotated_agrees"]:
            print(f"    DISAGREES: {row['map']} ships {row['shipped']['rotated']}, "
                  f"extent_x > extent_y says {row['solved']['rotated']}")

    for label, subset in (("all maps", rows),
                          ("descriptor authored for this BSP",
                           [r for r in rows if not r["inherited_descriptor"]]),
                          ("descriptor inherited from a sibling",
                           [r for r in rows if r["inherited_descriptor"]])):
        if not subset:
            continue
        print(f"\n{label} (n={len(subset)})")
        _spread("zoom ratio solved/shipped", [r["zoom_ratio"] for r in subset])
        _spread("worst-corner px error", [r["worst_px"] for r in subset], "{:7.1f}")
        for tile in (32, 64, 128):
            within = sum(1 for r in subset if r["worst_px"] <= tile)
            print(f"    within {tile:3d}px: {within}/{len(subset)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
