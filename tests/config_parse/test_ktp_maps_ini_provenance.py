"""`config/local/ktp_maps.ini` must say where the real one lives.

The fleet's map table is owned by `afraznein/KTPDoDServerConfig`. This repo's
copy is bound into the Docker dev stack by `docker-compose.local.yml` and is the
default input of `scripts/spatial_map_registry.py`, so it is genuinely loaded —
but only ever by the dev stack. It was last synced 2026-07-11 and has 32 sections
to live's 25.

A drifted copy that looks like a source is what sends the next reader to edit the
file that changes nothing. A header cannot stop that; a failing test can.
"""
from __future__ import annotations

from .conftest import CONFIG_ROOT

MAPS_INI = CONFIG_ROOT / "local" / "ktp_maps.ini"
OWNER = "KTPDoDServerConfig"


def test_file_exists():
    # Not a skip. If the dev stack's map table vanishes, that is the failure —
    # every KTPMatchHandler lookup misses and clan mode never arms.
    assert MAPS_INI.is_file(), f"{MAPS_INI} is missing"


def test_names_the_owning_repo():
    text = MAPS_INI.read_text(encoding="utf-8")
    assert OWNER in text, (
        f"config/local/ktp_maps.ini must name {OWNER} as the owner of the fleet's "
        f"copy, or a reader edits this one expecting a server to change"
    )


def test_says_it_is_the_dev_profile_and_not_in_sync():
    text = MAPS_INI.read_text(encoding="utf-8").lower()
    assert "local development" in text, "header must state the profile"
    assert "not a mirror" in text, (
        "the header carried 'MIRROR OF PRODUCTION' for two months after it stopped "
        "being one; it must say plainly that it is not kept in sync"
    )
