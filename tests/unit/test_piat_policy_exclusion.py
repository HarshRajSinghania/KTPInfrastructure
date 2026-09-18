"""The PIAT is excluded by LEAGUE POLICY, not because the file is dead.

Every other name in `EXCLUDED_WEAPON_MODELS` is there because the file is not real:
absent from Steam depot 31, or shipped but referenced by neither game binary. The
PIAT is none of those things. It is stock, it ships, both binaries reference it, and
DoD will happily load it — a player on a British class with the bazooka enabled can
hold one. KTP simply does not play that way, so the operator ruled on 2026-09-16 that
its models are out of manifest scope.

That distinction is the whole point of this file. A future reader who sees `p_piat`
sitting beside `p_bar` ("not in depot 31") could reasonably conclude the exclusion is
a factual claim about the file and "correct" it by re-adding the family the moment
someone notices the PIAT is perfectly real. It is not a factual claim; it is a policy
one, and it reverses the day British + bazooka enters competitive play.

⚠️ Zero PIAT kills in ~1.6M recorded frags is corroboration, NOT the reason. An
anti-tank weapon can be carried and seen for a whole map without ever landing a frag,
so "no kills" would be weak grounds on its own — the ruling is what carries this.
"""
import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build-game-files-manifest.py"

PIAT_MODELS = ("p_piat", "w_piat", "w_piat_rocket")

# Excluded for a different reason: the game loads these by name from nowhere. Kept
# here so the two classes are visibly distinct in one place.
DEAD_BY_FACT = ("p_mortar", "w_mortar", "p_bar", "w_colt")


@pytest.fixture(scope="module")
def mod():
    sys.modules.setdefault("paramiko", types.ModuleType("paramiko"))
    spec = importlib.util.spec_from_file_location("_ktp_piat_policy", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _kit_names(mod):
    out = set()
    for _, p_bases, w_bases in mod.WEAPON_FAMILIES:
        # Normalise first: a bare string iterates character by character, so a naive
        # update() would add letters and every membership assert below would pass
        # vacuously against exactly the table shape it is meant to catch.
        for side in (p_bases, w_bases):
            out.update([side] if isinstance(side, str) else side)
    return out


def test_piat_models_are_not_in_the_weapon_kit(mod):
    named = _kit_names(mod)
    present = sorted(n for n in PIAT_MODELS if n in named)
    assert not present, (
        f"PIAT models are back in the kit: {present}. Operator ruling 2026-09-16 put "
        "them out of scope because KTP does not play British + bazooka in competition."
    )


def test_every_piat_model_carries_the_policy_reason(mod):
    excluded = getattr(mod, "EXCLUDED_WEAPON_MODELS", {})
    for base in PIAT_MODELS:
        reason = excluded.get(base)
        assert reason, f"{base} is excluded by absence alone; a policy exclusion needs its reason recorded"
        assert "KTP" in reason or "ruling" in reason.lower(), (
            f"{base}'s reason reads as a claim about the FILE ({reason!r}). It is stock, shipped and "
            "binary-referenced — the reason must say it is a league-policy exclusion, or the next "
            "reader will 'fix' it by re-adding the family."
        )


def test_the_projectile_went_with_the_launcher(mod):
    # w_piat_rocket is the round the launcher fires. Excluding the launcher and
    # leaving its projectile hashed is the asymmetry this pins.
    excluded = getattr(mod, "EXCLUDED_WEAPON_MODELS", {})
    assert "w_piat_rocket" in excluded, "the PIAT projectile is still in scope while its launcher is not"


def test_policy_and_factual_exclusions_stay_distinguishable(mod):
    # Both classes live in one table, so the table itself has to carry the difference.
    excluded = getattr(mod, "EXCLUDED_WEAPON_MODELS", {})
    for base in DEAD_BY_FACT:
        reason = excluded.get(base, "")
        assert reason, f"{base} lost its exclusion reason"
        assert "KTP" not in reason, (
            f"{base} is excluded because the file is dead, not by league policy, but its reason "
            f"now reads like a policy call: {reason!r}"
        )


def test_the_exclusion_guard_would_catch_a_reinstated_piat(mod):
    # The import-time guard is what actually enforces this; prove it fires rather
    # than trusting that it exists.
    original = list(mod.WEAPON_FAMILIES)
    try:
        mod.WEAPON_FAMILIES.append(("piat", ["p_piat"], ["w_piat", "w_piat_rocket"]))
        with pytest.raises(ValueError, match="excluded"):
            mod._check_weapon_families()
    finally:
        mod.WEAPON_FAMILIES[:] = original
    mod._check_weapon_families()  # control: the real table still passes
