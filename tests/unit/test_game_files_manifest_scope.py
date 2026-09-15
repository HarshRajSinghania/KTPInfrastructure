"""Scope + severity guards for `scripts/build-game-files-manifest.py`.

The manifest's severity field is the whole safety margin of the 2026-08-27
skybox policy reversal: `gfx/env/*` came into scope, but at `"review"`, which
the AC client's `IsReview` branch keeps out of `modified_game_files`. If a
future edit lets a `gfx/env/` entry out as `"violation"`, every player running
a custom sky pack -- ordinary, legitimate, extremely common -- starts flagging.

So these run the real `build_manifest` against a fake SSH server rather than
asserting on the helper alone. What broke historically in this script was never
the pure function; it was an emit site that kept its own hardcoded literal after
the policy moved. Four of the five emit sites can never see a `gfx/env/` path,
which is exactly why one of them going stale would be invisible.

The partner assertion matters as much: nothing outside the report-only sets may
become `"review"`. A rule that only ever downgrades would quietly empty the
manifest of anything that can flag a player.

The grenade viewmodels are the second entry in that scope, and they arrived by a
route worth pinning: excluded outright on 2026-09-13, back at `"review"` the same
day when the ruling was revised. Exclusion and `"review"` look interchangeable --
both stop a player being flagged -- and they are not. The client hashes only the
paths the manifest lists, so an excluded path can never be captured. Which is why
these assert PRESENCE and severity together; either alone passes for the wrong
manifest.

Loaded by path with `paramiko` stubbed -- the script imports it at module scope
and none of it is reachable here, so the Tier 1 gate does not grow an SSH
dependency.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "build-game-files-manifest.py"

DOD = "/srv/dod"


@pytest.fixture(scope="module")
def mod():
    sys.modules.setdefault("paramiko", types.ModuleType("paramiko"))
    spec = importlib.util.spec_from_file_location("_ktp_game_files_manifest", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Out:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text.encode()


class FakeSSH:
    """Serves a synthetic dod/ tree over the three shell commands the script runs.

    Every path in `files` exists; anything else hashes as absent, which is the
    same answer a real server gives and the path `hash_remote_file` returns None on.
    """

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


# One skybox is six faces; two packs so a per-file rule cannot pass by accident.
SKYBOX = [f"gfx/env/dod_{sky}{face}.tga"
          for sky in ("kraft", "siena")
          for face in ("up", "dn", "lf", "rt", "ft", "bk")]

# Cosmetic buckets that must STAY out entirely -- the reversal was scoped to skies.
STILL_EXCLUDED = ["overviews/dod_kraftstoff.bmp", "models/w_aflag.mdl"]

RES_REFERENCED = SKYBOX + STILL_EXCLUDED + [
    "models/mapmodels/barrel.mdl",
    "sprites/mapsprites/flame.spr",
    "dod_siena.wad",
]

WEAPON_AND_EXPLICIT = [
    "models/p_garand.mdl", "models/w_garand.mdl",
    "models/p_k98.mdl", "models/w_98k.mdl",
    "models/allied_ammo.mdl", "models/axis_ammo.mdl",
    "models/helmet_us.mdl", "models/player.mdl",
]

# Held (p_) and thrown (w_) grenades are seen by other players; they stay enforced.
GRENADE_WORLD_MODELS = [f"models/{kind}_{nade}.mdl"
                        for kind in ("p", "w") for nade in ("grenade", "mills", "stick")]

# First-person grenade viewmodels: allowed at any hash, but listed so a modified copy
# is captured. The fixture offers them through every route a path can enter by -- on
# disk, in a .res and in ktp_file.ini -- so a route that emits them at the wrong
# severity is caught wherever it is.
GRENADE_VIEWMODELS = ["models/v_grenade.mdl", "models/v_mills.mdl", "models/v_stick.mdl"]

FILELIST = (["models/player/gerinf/gerinf.mdl", "sound/player/die1.wav"]
            + GRENADE_WORLD_MODELS + GRENADE_VIEWMODELS)


@pytest.fixture
def built(mod, tmp_path):
    ini = tmp_path / "ktp_file.ini"
    ini.write_text("// header\n" + "\n".join(
        # `player/...` sound entries arrive without the sound/ prefix, as on the fleet.
        p[len("sound/"):] if p.startswith("sound/player/") else p for p in FILELIST
    ) + "\n")

    every = RES_REFERENCED + WEAPON_AND_EXPLICIT + FILELIST
    files = {p: f"bytes-of-{p}" for p in every}
    ssh = FakeSSH({"dod_kraftstoff": RES_REFERENCED + GRENADE_VIEWMODELS}, files)
    return mod.build_manifest(ssh, DOD, str(ini))


def _by_path(entries):
    return {e["path"]: e for e in entries}


def test_skyboxes_are_in_scope_and_report_only(built):
    got = _by_path(built)
    missing = [p for p in SKYBOX if p not in got]
    assert not missing, f"skyboxes referenced by a .res must be in the manifest: {missing}"
    assert {got[p]["severity"] for p in SKYBOX} == {"review"}, (
        "a gfx/env/ entry emitted as 'violation' flags every player running a custom "
        "sky pack -- the policy reversal is report-only"
    )


def test_review_severity_reaches_nothing_else(built):
    allowed = set(SKYBOX) | set(GRENADE_VIEWMODELS)
    stray = sorted(e["path"] for e in built
                   if e["severity"] == "review" and e["path"] not in allowed)
    assert not stray, (
        f"only gfx/env/ and the grenade viewmodels are report-only; these were "
        f"downgraded too: {stray}"
    )


def test_everything_else_still_violates(built):
    enforced_filelist = [p for p in FILELIST if p not in GRENADE_VIEWMODELS]
    for path in WEAPON_AND_EXPLICIT + enforced_filelist + ["models/mapmodels/barrel.mdl",
                                                  "sprites/mapsprites/flame.spr",
                                                  "dod_siena.wad"]:
        entry = _by_path(built).get(path)
        assert entry is not None, f"{path} dropped out of the manifest"
        assert entry["severity"] == "violation", f"{path} must still count toward a verdict"


def test_grenade_viewmodels_are_in_scope_at_review_severity(built):
    got = _by_path(built)
    missing = [p for p in GRENADE_VIEWMODELS if p not in got]
    assert not missing, (
        f"a path the manifest omits is never hashed, so a modified copy of it can never "
        f"be captured -- the revised ruling needs these listed: {missing}"
    )
    for path in GRENADE_VIEWMODELS:
        assert got[path]["severity"] == "review", (
            f"{path} is allowed at any hash; 'violation' would flag every player running "
            f"a custom viewmodel"
        )
        assert got[path]["category"] == "grenade_model", path


def test_grenade_viewmodels_enter_even_when_no_source_lists_them(mod, tmp_path):
    # The live ktp_file.ini stopped listing them and no .res references them, so the
    # explicit pass is the only thing putting them in the real manifest. A fixture that
    # feeds them in by another route would pass with that pass deleted.
    ini = tmp_path / "ktp_file.ini"
    ini.write_text("// header\nmodels/player/gerinf/gerinf.mdl\n")

    every = RES_REFERENCED + WEAPON_AND_EXPLICIT + GRENADE_VIEWMODELS
    ssh = FakeSSH({"dod_kraftstoff": RES_REFERENCED}, {p: f"bytes-of-{p}" for p in every})
    got = _by_path(mod.build_manifest(ssh, DOD, str(ini)))

    for path in GRENADE_VIEWMODELS:
        assert path in got, f"{path} reached the manifest by no route at all"
        assert got[path]["severity"] == "review", path


def test_viewmodels_carry_no_alternate_hashes(mod, built):
    # Operator ruling 2026-09-14: every modified copy is captured, the known community
    # pack included. An alternate is exactly what would prevent that -- the client's
    # AllowedAlternateHashes match returns before it reaches the IsReview branch, so a
    # re-added alternate here is a silent capture suppressor, not a tidy-up.
    manifest = mod.assemble_manifest(list(built), "fixture", DOD)
    got = _by_path(manifest["files"])
    for path in GRENADE_VIEWMODELS:
        assert not got[path].get("allowed_alternate_hashes"), (
            f"{path} must be compared against the stock hash alone, so any other copy of "
            f"it is captured for an admin"
        )
    stray = sorted(set(mod.ALTERNATE_HASHES) & set(GRENADE_VIEWMODELS))
    assert not stray, f"alternate-hash table re-acquired a ruled viewmodel: {stray}"


def test_held_and_thrown_grenade_models_still_violate(built):
    got = _by_path(built)
    for path in GRENADE_WORLD_MODELS:
        entry = got.get(path)
        assert entry is not None, f"{path} dropped out -- only the v_ viewmodels were released"
        assert entry["category"] == "grenade_model", path
        assert entry["severity"] == "violation", f"{path} is seen by other players and must still flag"


def test_no_alternate_hash_targets_an_excluded_path(mod):
    # An alternate for a path the manifest never emits is inert, and the AC client's
    # KnownBenignFileVariants is kept in sync with this table.
    stale = sorted(set(mod.ALTERNATE_HASHES) & set(mod.EXCLUDED_EXACT))
    assert not stale, f"ALTERNATE_HASHES entries for excluded paths: {stale}"


def test_the_other_cosmetic_buckets_stayed_excluded(built):
    got = _by_path(built)
    present = [p for p in STILL_EXCLUDED if p in got]
    assert not present, (
        f"the reversal was scoped to skyboxes; these are still allowed modification: {present}"
    )


def test_severity_counts_partition_the_manifest(built):
    counts = Counter(e["severity"] for e in built)
    review = len(SKYBOX) + len(GRENADE_VIEWMODELS)
    assert set(counts) == {"violation", "review"}
    assert counts["review"] == review
    assert counts["violation"] == len(built) - review


def test_severity_for_reads_the_policy_tuple_not_a_literal(mod):
    # Pins the indirection itself: the emit sites must go through severity_for, so
    # editing REVIEW_PATH_PREFIXES or REVIEW_EXACT is sufficient to change or revert policy.
    assert mod.severity_for("gfx/env/dod_kraftup.tga") == "review"
    assert mod.severity_for("models/v_mills.mdl") == "review"
    assert mod.severity_for("models/p_garand.mdl") == "violation"
    assert mod.severity_for("gfx/shell.spr") == "violation", "prefix must not match loosely"
    assert mod.severity_for("models/v_mills.mdl.bak") == "violation", "exact set must not match by prefix"


def test_viewmodels_categorize_the_same_by_every_route(mod):
    # The .res pass takes categorize() at its word while the explicit pass hardcodes the
    # bucket. They disagreed, and the dossier prints the category.
    for path in GRENADE_VIEWMODELS:
        assert mod.categorize(path) == "grenade_model", path


def test_the_viewmodels_left_the_excluded_set(mod):
    # Belt to test_grenade_viewmodels_are_in_scope_at_review_severity's braces, one layer
    # down: EXCLUDED_EXACT is applied at every source, so an entry left there would drop
    # them again from a route the fixture does not happen to exercise.
    assert not set(mod.GRENADE_VIEWMODELS) & mod.EXCLUDED_EXACT
    assert set(mod.GRENADE_VIEWMODELS) == set(mod.REVIEW_EXACT)


# Held models the DoD binaries never load. They sat in WEAPON_FAMILIES until
# 2026-09-15 in place of the real ones, so the BAR and STG44 held models -- what
# OPPONENTS see -- went unhashed while two names no client has were reported
# missing by 690 of 691 scans. They are present on the fleet tree (a community
# pack ships them), which is why "it exists on a server" is not the test; the
# test is whether dod.so/client.so reference it.
DEAD_HELD_MODELS = ("p_bar", "p_mp44")

# Verified 2026-09-15 by `strings` over dod/dlls/dod.so + dod/cl_dlls/client.so:
# p_barbu 4 refs, p_stg44 2 refs, p_colt 2 refs (control), p_bar 0, p_mp44 0.
REAL_HELD_MODELS = ("p_barbu", "p_barbd", "p_stg44")


def test_weapon_kit_names_no_model_the_game_never_loads(mod):
    # Normalise the shape before comparing: a bare string iterates CHARACTER by
    # character, so the naive comprehension matches nothing and the assert passes
    # vacuously against the very code it is meant to catch. Measured -- this test
    # passed on the unfixed table until the flattening was made explicit.
    named = set()
    for _, p_bases, _ in mod.WEAPON_FAMILIES:
        named.update([p_bases] if isinstance(p_bases, str) else p_bases)
    dead = sorted(named & set(DEAD_HELD_MODELS))
    assert not dead, (
        f"WEAPON_FAMILIES names held models the DoD binaries never reference: {dead}. "
        "Confirm a name with `strings` over dod.so/client.so before adding it -- "
        "presence on a server tree proves nothing, the fleet install carries a community pack."
    )


def test_the_real_bar_and_stg44_held_models_are_in_the_kit(mod):
    named = {b for _, p_bases, _ in mod.WEAPON_FAMILIES for b in p_bases}
    absent = sorted(set(REAL_HELD_MODELS) - named)
    assert not absent, (
        f"held models the game actually loads are missing from the kit: {absent} -- "
        "these are the models other players see, so an unhashed one is a real gap"
    )


def test_every_family_carries_its_held_models_as_a_list(mod):
    # The p_ side became a list so a family can hold several models (bipod up/down).
    # A bare string would still iterate -- character by character -- and emit nothing,
    # which is the failure this pins rather than a style preference.
    bad = [fam for fam, p_bases, _ in mod.WEAPON_FAMILIES if not isinstance(p_bases, (list, tuple))]
    assert not bad, f"these families carry a bare string instead of a list of held models: {bad}"


def test_both_bar_held_models_reach_the_manifest(mod, tmp_path):
    # bar is the only family with two held models; the emit loop flattens (*p_bases, w_base),
    # and a regression there would drop the second silently.
    files = {f"models/{b}.mdl": f"{b}-body" for b in ("p_barbu", "p_barbd", "p_stg44")}
    files["models/w_bar.mdl"] = "w_bar-body"
    files["models/w_mp44.mdl"] = "w_mp44-body"
    ini = tmp_path / "ktp_file.ini"
    ini.write_text("// header\n")
    ssh = FakeSSH({}, files)
    entries = mod.build_manifest(ssh, DOD, str(ini))
    got = {e["path"] for e in entries}
    for base in ("p_barbu", "p_barbd", "p_stg44"):
        assert f"models/{base}.mdl" in got, f"{base} did not reach the manifest"
