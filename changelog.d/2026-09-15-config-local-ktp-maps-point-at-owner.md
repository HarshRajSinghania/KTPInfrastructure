### config: `config/local/ktp_maps.ini` names its owner (2026-09-15)

- **The dev stack's map table read like a source and was drifted from live.** `ktp_maps.ini` exists in
  three repos; `afraznein/KTPDoDServerConfig` owns the one the fleet loads and matched live byte for
  byte when measured (md5 `e70b3033784a458c8dad0f2d0e3e4f9f`, uniform on all 24 instances, 25
  sections). This copy has 32 and was last synced 2026-07-11, yet its header still said
  "MIRROR OF PRODUCTION" — the shape that sends the next reader to edit the file that changes nothing.
  - The header now names the owning repo, states that this copy is loaded only by the Docker dev stack
    and `scripts/spatial_map_registry.py`, and records the measured divergence.
  - **Not resynced, deliberately.** The 9 extra entries bind to configs `config/local/dod-configs/`
    still ships, and `tests/unit/test_spatial_map_registry.py` discovers against them.
  - `tests/config_parse/test_ktp_maps_ini_provenance.py` holds the header true. Its
    existence check fails rather than skips: a vanished map table makes every KTPMatchHandler lookup
    miss, and clan mode never arms.
