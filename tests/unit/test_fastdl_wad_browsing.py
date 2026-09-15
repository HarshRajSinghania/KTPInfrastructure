"""The /dod index must LIST the texture wads, and maps/ must say where they are.

`fastdl.ktpdod.com/dod/maps/` carries .bsp, .res and .txt and no .wad, so a human
browsing for one found nothing. The engine is fine — it asks for
`/dod/<name>.wad`, one level up — and that difference is the whole point: the wads
must be listed where they are, never copied into maps/ where no client asks.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "ktp-fastdl-indexes.py"

WADS = ["dod_anzio.wad", "dod_kalt.wad", "gfx.wad"]
MAP_FILES = ["dod_anzio.bsp", "dod_anzio.res", "dod_anzio.txt"]


@pytest.fixture
def site(tmp_path):
    fastdl, demos = tmp_path / "fastdl", tmp_path / "demos"
    (fastdl / "dod" / "maps").mkdir(parents=True)
    (fastdl / "dod" / "sound").mkdir()
    demos.mkdir()
    for w in WADS:
        (fastdl / "dod" / w).write_bytes(b"WAD3" + b"\0" * 4096)
    (fastdl / "dod" / "delta.lst").write_bytes(b"x" * 16)
    for f in MAP_FILES:
        (fastdl / "dod" / "maps" / f).write_bytes(b"y" * 32)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    r = subprocess.run([sys.executable, str(SCRIPT), "--apply",
                        "--fastdl", str(fastdl), "--demos", str(demos)],
                       env=dict(os.environ, PATH=str(empty)),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return fastdl


def page(fastdl, rel):
    return (fastdl / rel).read_text(encoding="utf-8")


def test_the_dod_index_links_every_wad(site):
    dod = page(site, "dod/index.html")
    for w in WADS:
        assert 'href="%s"' % w in dod, w
    assert 'id="wads"' in dod
    assert "3 texture WADs" in dod
    # Control: a name that is not on disk must not appear, or the assert above
    # would pass on a page that simply lists everything imaginable.
    assert "zzznope.wad" not in dod.lower()


def test_the_dod_index_still_lists_the_other_loose_files(site):
    dod = page(site, "dod/index.html")
    assert 'href="delta.lst"' in dod
    assert "1 other loose files" in dod


def test_the_maps_page_says_where_the_wads_are(site):
    maps = page(site, "dod/maps/index.html")
    assert "/dod/&lt;name&gt;.wad" in maps
    assert 'href="/dod/#wads"' in maps


def test_no_wad_is_listed_under_maps(site):
    """Copying wads into maps/ is dead weight no client requests — the page must
    point at them, never reproduce them."""
    maps = page(site, "dod/maps/index.html")
    hrefs = set(re.findall(r'class="f" data-s="[^"]*" href="([^"]+)"', maps))
    assert hrefs == set(MAP_FILES), hrefs
    assert not (site / "dod" / "maps" / "dod_anzio.wad").exists()


def test_halflife_wad_is_named_as_deliberately_absent(site):
    """28 maps reference it and it ships with the game: a 404 there is correct,
    and without saying so the next reader treats it as a missing file."""
    assert "halflife.wad" in page(site, "dod/index.html")


def test_a_tree_with_no_wads_renders_without_the_section(tmp_path):
    fastdl, demos = tmp_path / "fastdl", tmp_path / "demos"
    (fastdl / "dod" / "maps").mkdir(parents=True)
    (fastdl / "dod" / "maps" / "dod_anzio.bsp").write_bytes(b"y")
    demos.mkdir()
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    r = subprocess.run([sys.executable, str(SCRIPT), "--apply",
                        "--fastdl", str(fastdl), "--demos", str(demos)],
                       env=dict(os.environ, PATH=str(empty)),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "texture WADs" not in (fastdl / "dod" / "index.html").read_text(encoding="utf-8")
    assert "/dod/#wads" not in (fastdl / "dod" / "maps" / "index.html").read_text(encoding="utf-8")
