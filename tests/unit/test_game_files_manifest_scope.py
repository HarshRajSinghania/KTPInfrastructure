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


# ---------------------------------------------------------------------------
# The widened kit: every stock p_/w_ weapon model the game loads.
#
# Membership is a two-leg test, both legs measured 2026-09-15: (1) `strings` over
# dod/dlls/dod.so + dod/cl_dlls/client.so names the literal path (the fleet copies
# are SHA1-identical to depot 31's, so this is the stock binary); (2) the path is in
# Steam depot 31 (manifest 3826716661969602728, the depot app 30 installs) and the
# fleet tree's bytes match the depot's SHA1. Grenades pass both legs but enter through
# ktp_file.ini as grenade_model, so they are not kit members.

STOCK_LOADED_HELD_MODELS = frozenset("""
p_30cal p_30calpr p_30calsr p_amerk p_barbd p_barbu p_bazooka p_bazooka_l p_bren_l
p_brenbd p_brenbr p_brenbu p_brensr p_colt p_enfield p_enfield_l p_enfields p_enfields_l
p_fairbairn p_fcarb p_fcarb_l p_fg42bd p_fg42bu p_fg42pr p_fg42s p_fg42sr p_garand
p_garand_l p_grease p_grease_l p_k43 p_k98 p_k98_l p_k98s p_k98s_l p_luger p_m1carb
p_m1carb_l p_mg34bd p_mg34bu p_mg34pr p_mg34sr p_mg42bd p_mg42bu p_mg42pr p_mg42sr
p_mp40 p_paraknife p_piat p_pschreck p_pschreck_l p_spade p_spring p_spring_l p_sten
p_stg44 p_tommy p_tommy_l p_webley
""".split())

STOCK_LOADED_WORLD_MODELS = frozenset("""
w_30cal w_98k w_amerk w_bar w_bazooka w_bazooka_rocket w_bren w_enfield w_enfield_scoped
w_fcarb w_fg42 w_fg42s w_garand w_greasegun w_k43 w_m1carb w_mg34 w_mg42 w_mp40 w_mp44
w_paraknife w_piat w_piat_rocket w_pschreck w_pschreck_rocket w_scoped98k w_spring
w_sten w_tommy
""".split())

# The stock half of what the 2026-05-13 prune removed: underscore `_l`, in depot 31,
# referenced by the binary, drawn on a sprinting player for everyone else to see.
# Stock, shipped, binary-referenced and loadable — and OUT OF SCOPE anyway, by league
# policy rather than by any fact about the file. The PIAT is reachable only on a British
# class with the bazooka enabled, which KTP does not run in competitive play (operator
# ruling 2026-09-16). Kept as its own set so the sets above stay a factual record of what
# the GAME loads: subtract policy at the assert, never by editing the facts.
POLICY_EXCLUDED_MODELS = frozenset("p_piat w_piat w_piat_rocket".split())

STOCK_LOWERED_HELD_MODELS = frozenset("""
p_bazooka_l p_bren_l p_enfield_l p_enfields_l p_fcarb_l p_garand_l p_grease_l p_k98_l
p_k98s_l p_m1carb_l p_pschreck_l p_spring_l p_tommy_l
""".split())

# The community half: no underscore, on the fleet tree, in no client install. Kept out
# by name, because "ends in l" describes both halves.
COMMUNITY_LOWERED_NAMES = frozenset("""
p_garandl p_tommyl p_k43l p_98kl p_k98sl p_fcarbl p_greasegunl p_m1carbl p_springl
""".split())

# Stock files that nothing loads: no world model exists for a weapon that cannot be
# dropped, and the mortar has no class to spawn it. They were in the kit (the w_ three)
# or pass leg 2 (all five), which is why each needs a stated reason rather than absence.
NEVER_LOADED_STOCK_MODELS = ("w_colt", "w_luger", "w_spade", "p_mortar", "w_mortar")


def _kit_names(mod):
    # Normalise before comparing: on the pre-widening table the w_ side is a bare
    # string, and a bare string iterates character by character, so a comprehension
    # over it matches nothing and an absence assert passes vacuously.
    named = []
    for _, p_bases, w_bases in mod.WEAPON_FAMILIES:
        for side in (p_bases, w_bases):
            named.extend([side] if isinstance(side, str) else list(side))
    return named


def _build_full_kit(mod, tmp_path, extra=()):
    names = STOCK_LOADED_HELD_MODELS | STOCK_LOADED_WORLD_MODELS | set(extra)
    files = {f"models/{b}.mdl": f"{b}-body" for b in names}
    ini = tmp_path / "ktp_file.ini"
    ini.write_text("// header\n")
    return _by_path(mod.build_manifest(FakeSSH({}, files), DOD, str(ini)))


def test_every_stock_loaded_weapon_model_is_in_the_kit(mod):
    expected = (STOCK_LOADED_HELD_MODELS | STOCK_LOADED_WORLD_MODELS) - POLICY_EXCLUDED_MODELS
    absent = sorted(expected - set(_kit_names(mod)))
    assert not absent, (
        f"stock models the game loads that the kit does not hash: {absent} -- each is "
        "in depot 31 and named by dod.so/client.so, so an unhashed one is a real gap"
    )


def test_the_kit_names_nothing_outside_the_stock_loaded_set(mod):
    stray = sorted(set(_kit_names(mod)) - (STOCK_LOADED_HELD_MODELS | STOCK_LOADED_WORLD_MODELS))
    assert not stray, (
        f"kit names that fail a leg of the two-leg test: {stray} -- a name no binary "
        "loads is a dead entry (w_colt/w_luger/w_spade were that bug on the w_ side)"
    )


def test_families_carry_world_models_as_a_list(mod):
    bad = [fam for fam, _, w_bases in mod.WEAPON_FAMILIES if not isinstance(w_bases, (list, tuple))]
    assert not bad, f"these families carry a bare string instead of a list of world models: {bad}"


def test_stock_lowered_models_reach_the_manifest_as_violations(mod, tmp_path):
    got = _build_full_kit(mod, tmp_path)
    for base in sorted(STOCK_LOWERED_HELD_MODELS):
        entry = got.get(f"models/{base}.mdl")
        assert entry is not None, f"{base} is stock and loaded on every sprint, and reached the manifest by no route"
        assert entry["severity"] == "violation", f"{base} is what opponents render; it carries the severity of its family"
        assert entry["category"] == "weapon_player_model", base
        assert entry["variant"] == "lowered", base


def test_primary_models_keep_the_primary_variant(mod, tmp_path):
    got = _build_full_kit(mod, tmp_path)
    covered = (STOCK_LOADED_HELD_MODELS | STOCK_LOADED_WORLD_MODELS) - POLICY_EXCLUDED_MODELS
    for base in sorted(covered - STOCK_LOWERED_HELD_MODELS):
        assert got[f"models/{base}.mdl"]["variant"] == "primary", base


def test_lowered_is_read_from_the_underscore_name_not_a_trailing_l(mod):
    assert mod.variant_for("p_garand_l") == "lowered"
    assert mod.variant_for("p_garand") == "primary"
    assert mod.variant_for("p_garandl") == "primary", "a no-underscore community name must never read as the stock pose"


def test_community_lowered_names_are_excluded_by_name(mod):
    excluded = getattr(mod, "EXCLUDED_WEAPON_MODELS", {})
    in_kit = sorted(COMMUNITY_LOWERED_NAMES & set(_kit_names(mod)))
    assert not in_kit, f"community lowered models re-entered the kit: {in_kit}"
    unpinned = sorted(COMMUNITY_LOWERED_NAMES - set(excluded))
    assert not unpinned, (
        f"community names are kept out only by absence, not by name: {unpinned} -- the "
        "next reader pattern-matching on a trailing l re-adds them"
    )


def test_never_loaded_stock_models_are_pinned_out_with_a_reason(mod):
    excluded = getattr(mod, "EXCLUDED_WEAPON_MODELS", {})
    names = set(_kit_names(mod))
    for base in NEVER_LOADED_STOCK_MODELS:
        assert base not in names, f"{base} is loaded by nothing and is back in the kit"
        assert excluded.get(base), f"{base} passes the depot leg, so its exclusion needs a stated reason"


def test_dead_held_models_are_pinned_out_too(mod):
    excluded = getattr(mod, "EXCLUDED_WEAPON_MODELS", {})
    for base in DEAD_HELD_MODELS:
        assert excluded.get(base), f"{base} is excluded by absence only; the reason belongs in the table"


def test_the_guard_refuses_a_table_that_names_an_excluded_model(mod, monkeypatch):
    monkeypatch.setattr(mod, "WEAPON_FAMILIES", [("bar", ["p_bar", "p_barbu"], ["w_bar"])])
    with pytest.raises(ValueError, match="p_bar"):
        mod._check_weapon_families()


def test_the_guard_refuses_a_model_in_two_families(mod, monkeypatch):
    monkeypatch.setattr(mod, "WEAPON_FAMILIES", [("a", ["p_garand"], []), ("b", ["p_garand"], [])])
    with pytest.raises(ValueError, match="p_garand"):
        mod._check_weapon_families()


def test_a_family_with_two_world_models_emits_both(mod, tmp_path):
    got = _build_full_kit(mod, tmp_path)
    # w_piat/w_piat_rocket were a third example here until the 2026-09-16 policy
    # exclusion; bazooka and pschreck still prove the *w_bases flattening.
    for base in ("w_bazooka", "w_bazooka_rocket", "w_pschreck", "w_pschreck_rocket"):
        assert f"models/{base}.mdl" in got, f"{base} did not reach the manifest -- the emit loop must flatten *w_bases"
        assert got[f"models/{base}.mdl"]["category"] == "weapon_world_model", base


def test_the_full_kit_is_exactly_the_stock_loaded_set(mod, tmp_path):
    # The fixture also offers the community names and the never-loaded stock files, so
    # this fails if the emit loop picks any of them up, not only if it drops a member.
    got = _build_full_kit(mod, tmp_path, extra=COMMUNITY_LOWERED_NAMES | set(NEVER_LOADED_STOCK_MODELS))
    emitted = {p[len("models/"):-len(".mdl")] for p, e in got.items()
               if e["category"] in ("weapon_player_model", "weapon_world_model")}
    assert emitted == (STOCK_LOADED_HELD_MODELS | STOCK_LOADED_WORLD_MODELS) - POLICY_EXCLUDED_MODELS, (
        "the kit is the stock loaded set MINUS the policy exclusions -- if a PIAT model is back, "
        "either the ruling changed or the exclusion table was edited without the sets"
    )
    assert {e["severity"] for e in got.values()} == {"violation"}
