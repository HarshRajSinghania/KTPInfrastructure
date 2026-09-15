### `scripts`: spatial map discovery reads the bindings the server reads, not a chat line (2026-09-14)

`spatial_map_registry.py` discovered maps by regexing `say KTP <map> Match Config Executed`
out of each `ktp_*.cfg`. That line is chat text nothing consumes, and on the custom pool it
names a different map than the config serves — `ktp_saints.cfg` announces `dod_saints` while
serving `dod_saints2_b3e`. Five of the nine maps the fleet actually plays were invisible to
every count this script produced, and a bogus map name was indistinguishable from a real one.

Discovery now reads `config/local/ktp_maps.ini`, the map-to-config binding table
KTPMatchHandler itself parses in `load_map_mappings()`. The ini parse mirrors the plugin's,
including the `.bsp` strip, the lowercase, and a section staying open until a `config` key
closes it.

- `registry.json` carried two override keys that are not maps — `dod_lennon_test` and
  `dod_saints`. They are now `dod_lennon5_b1` and `dod_saints2_b3e`, and the topology claims
  filed under the old names are dropped rather than carried across, because they were written
  about names no BSP carries. The review queue is reordered by measured play, which puts
  `dod_armory_b6` on it for the first time.
- Three things that were silently dropped are now reported, without failing validation:
  bindings whose config file is missing (`exec_map_config` execs a path that does not exist and
  nothing reports it), configs no map is bound to, and configs whose `say` line names a map they
  do not serve.
- `--maps-ini` selects the binding file. The registry payload is `schema_version` 2.
