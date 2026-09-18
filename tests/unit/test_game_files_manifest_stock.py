"""`stock` flag + dead-entry warning for `scripts/build-game-files-manifest.py`.

A manifest entry the client reports as "missing" means two completely different things
and the manifest could not tell them apart. `sound/player/pl_step1.wav` absent is a
player who deleted their own footsteps — silent movement, a real advantage. A custom
map's `.mdl` absent is a player who has not played that map. 99% of the corpus's 19k
missing-file occurrences are the second kind, which is exactly why the first kind was
invisible: 182 occurrences across 4 players sat inside that noise, unflagged.

`stock` is the discriminator, and it is a fact about Valve's depot rather than a
judgement about the player — which is why it lives in the manifest and not in a
threshold somewhere.

The dead-entry warning is the other half. `p_bar` and `p_mp44` sat in WEAPON_FAMILIES
for months naming files no client has: on the fleet tree via a community pack, so the
generator hashed them, and missing on 681 of 682 client scans. Nothing caught it,
because a manifest cannot tell "everyone is missing this" from "this file does not
exist". The stock list can.

Loaded by path with `paramiko` stubbed, the same way the scope guards next door do it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "build-game-files-manifest.py"
STOCK_LIST = REPO / "scripts" / "data" / "dod-depot31-stock-paths.txt"

DOD = "/srv/dod"


@pytest.fixture(scope="module")
def mod():
    sys.modules.setdefault("paramiko", types.ModuleType("paramiko"))
    spec = importlib.util.spec_from_file_location("_ktp_game_files_manifest_stock", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Out:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text.encode()


class FakeSSH:
    def __init__(self, res_files, files):
        self._res_files = res_files
        self._files = files

    def exec_command(self, cmd, timeout=None):
        if cmd.startswith("ls "):
            return None, _Out("\n".join(f"{DOD}/maps/{n}.res" for n in self._res_files)), None
        if cmd.startswith("cat "):
            name = cmd.split("/")[-1].rstrip("'").replace(".res", "")
            return None, _Out("\n".join(self._res_files[name])), None
        if cmd.startswith("sha256sum "):
            rel = cmd.split("'")[1][len(DOD) + 1:]
            if rel not in self._files:
                return None, _Out(""), None
            body = self._files[rel]
            sha = hashlib.sha256(body.encode()).hexdigest()
            return None, _Out(f"{sha}  {DOD}/{rel}\n{len(body)}\n"), None
        raise AssertionError(f"unexpected command: {cmd}")


# ---------------------------------------------------------------- the checked-in list

def test_stock_list_is_present_and_parses(mod):
    stock = mod.load_stock_paths()
    assert len(stock) > 1000, (
        f"only {len(stock)} stock paths parsed — a truncated or comment-eaten list marks "
        f"real stock files non-stock, which is the direction that accuses a player"
    )
    # Positive AND negative control on the same call: a list that matched everything
    # would pass a presence-only assertion while saying nothing.
    assert "models/p_colt.mdl" in stock
    assert "sound/player/pl_step1.wav" in stock
    assert "dod_siena.wad" not in stock, "dod_siena is a community map wad, not stock"
    assert "models/p_bar.mdl" not in stock, "p_bar is the community pack's, not Valve's"


def test_stock_list_comparison_is_case_insensitive(mod):
    """101 depot paths carry upper case and the fleet tree holds them lower-cased.

    A case-sensitive join calls models/player/us-inf/us-infT.mdl non-stock — four real
    stock player models reclassified as suspicious on every scan.
    """
    stock = mod.load_stock_paths()
    assert mod.is_stock("models/player/us-inf/us-infT.mdl", stock)
    assert mod.is_stock("models/player/us-inf/us-inft.mdl", stock)
    assert mod.is_stock("MODELS/P_COLT.MDL", stock)
    assert not mod.is_stock("models/p_notathing.mdl", stock)


HASH_TOKEN = re.compile(r"\b[0-9a-fA-F]{40,64}\b")


def _hash_bearing_lines(text):
    return [l for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#") and HASH_TOKEN.search(l)]


def test_stock_list_carries_no_hashes():
    """Paths only. This repo is public; a hash list is an oracle for checking a
    modified file against ours, and a path list only repeats what Steam tells anyone.

    The detector gets a negative control first: an assertion over 1684 paths that
    contain no hex runs passes whether or not it is looking for anything.
    """
    planted = "models/deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef.mdl"
    assert _hash_bearing_lines(planted), "the hash detector does not detect a hash"
    assert not _hash_bearing_lines("# 0123456789abcdef0123456789abcdef01234567\nmodels/p_colt.mdl"), (
        "comment lines are provenance, not payload — the detector must skip them"
    )

    text = STOCK_LIST.read_text(encoding="utf-8")
    body = [l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")]
    assert len(body) > 1000, f"only {len(body)} entries — truncated list"
    assert _hash_bearing_lines(text) == []


# ---------------------------------------------------------------- the emitted flag

# Stock paths, chosen so each arrives by a DIFFERENT route into the manifest.
STOCK_VIA_RES = "dod_saints.wad"                     # stock wad a .res also references
STOCK_VIA_FILELIST = "sound/player/pl_step1.wav"     # ktp_file.ini
STOCK_VIA_WEAPON_KIT = "models/p_colt.mdl"           # explicit weapon kit
STOCK_VIA_EXPLICIT = "models/player.mdl"             # explicit singleton
STOCK_MIXED_CASE = "models/player/us-inf/us-infT.mdl"  # depot casing, via ktp_file.ini

# Not in the depot, and legitimately so: a custom map's own asset, delivered by FastDL
# to anyone who plays that map. It must come out stock=false WITHOUT being warned about.
CUSTOM_VIA_RES = "models/mapmodels/anjou_bakery.mdl"

RES_REFERENCED = [STOCK_VIA_RES, CUSTOM_VIA_RES]
FILELIST = [STOCK_VIA_FILELIST, STOCK_MIXED_CASE]


@pytest.fixture
def built(mod, tmp_path):
    ini = tmp_path / "ktp_file.ini"
    ini.write_text("// header\n" + "\n".join(
        p[len("sound/"):] if p.startswith("sound/player/") else p for p in FILELIST
    ) + "\n")
    every = RES_REFERENCED + FILELIST + [STOCK_VIA_WEAPON_KIT, STOCK_VIA_EXPLICIT]
    ssh = FakeSSH({"dod_anjou": RES_REFERENCED}, {p: f"bytes-of-{p}" for p in every})
    entries = mod.build_manifest(ssh, DOD, str(ini))
    return mod.assemble_manifest(entries, "fake", DOD)


def _by_path(manifest):
    return {e["path"]: e for e in manifest["files"]}


def test_every_entry_carries_a_stock_flag(built):
    missing = [e["path"] for e in built["files"] if "stock" not in e]
    assert not missing, (
        f"{len(missing)} entries have no stock flag; a consumer reading it as absent=false "
        f"would call a stock file custom: {missing[:5]}"
    )
    assert all(isinstance(e["stock"], bool) for e in built["files"])


@pytest.mark.parametrize("path", [
    STOCK_VIA_RES, STOCK_VIA_FILELIST, STOCK_VIA_WEAPON_KIT, STOCK_VIA_EXPLICIT,
    STOCK_MIXED_CASE,
])
def test_stock_files_are_marked_stock_by_every_route(built, path):
    entry = _by_path(built).get(path)
    assert entry is not None, f"{path} dropped out of the manifest"
    assert entry["stock"] is True, (
        f"{path} is in Steam depot 31; marking it non-stock hides a deleted stock file "
        f"in the custom-asset noise"
    )


def test_custom_map_asset_is_not_stock_and_is_not_warned_about(built):
    entry = _by_path(built)[CUSTOM_VIA_RES]
    assert entry["stock"] is False
    assert CUSTOM_VIA_RES not in built["_meta"]["dead_entry_candidates"], (
        "a .res-derived asset is absent from the depot by definition and reaches a player "
        "over FastDL — warning about it would bury the warning that matters"
    )


def test_meta_counts_stock_entries(built):
    meta = built["_meta"]
    assert meta["stock_files"] == sum(1 for e in built["files"] if e["stock"])
    assert 0 < meta["stock_files"] < meta["total_files"]


# ---------------------------------------------------------------- the dead-entry warning

def test_clean_manifest_reports_no_dead_entries(built):
    assert built["_meta"]["dead_entry_candidates"] == []


def test_explicit_entry_absent_from_the_depot_is_warned_about(mod, tmp_path, capsys):
    """The p_bar / p_mp44 shape: on the source tree, hashed happily, on no client.

    Reproduced through the real weapon-kit pass rather than by hand-building an entry —
    what went stale last time was an emit site, not a helper.
    """
    dead = "p_notinthedepot"
    families = mod.WEAPON_FAMILIES + [("fake_family", [dead], [])]
    ini = tmp_path / "ktp_file.ini"
    ini.write_text("// header\n")
    files = {f"models/{dead}.mdl": "bytes", STOCK_VIA_WEAPON_KIT: "bytes"}
    ssh = FakeSSH({"dod_anjou": []}, files)

    original = mod.WEAPON_FAMILIES
    mod.WEAPON_FAMILIES = families
    try:
        manifest = mod.assemble_manifest(mod.build_manifest(ssh, DOD, str(ini)), "fake", DOD)
    finally:
        mod.WEAPON_FAMILIES = original

    assert f"models/{dead}.mdl" in manifest["_meta"]["dead_entry_candidates"], (
        "an explicit entry no client can have must be named at generation — otherwise it "
        "is only ever found by noticing a 681-of-682 missing rate months later"
    )
    assert "WARNING" in capsys.readouterr().err


def test_dead_entry_candidates_ignores_res_derived_entries(mod):
    stock = mod.load_stock_paths()
    entries = [
        {"path": "models/mapmodels/whatever.mdl", "origin": ".res"},
        {"path": "models/p_bar.mdl", "origin": "explicit_2026-05-02_full_kit"},
        {"path": "models/p_colt.mdl", "origin": "explicit_2026-05-02_full_kit"},
    ]
    assert mod.dead_entry_candidates(entries, stock) == ["models/p_bar.mdl"]
