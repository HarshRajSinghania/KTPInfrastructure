### `fastdl`: list the texture WADs, so a human browsing for one can find it (2026-09-15)

Operator, 2026-09-14: "players should be able to download wads on this page". They could not.
`/dod/maps/` lists `.bsp`, `.res` and `.txt` and no `.wad`, and the `/dod/` page counted its
loose files in a sentence without ever listing them — so the 71 wads on disk were linked
from nowhere on the site. The engine was never affected: it asks for `/dod/<name>.wad`, one
level above `maps/`, and was fetching them all along.

- `scripts/ktp-fastdl-indexes.py` renders the `/dod/` page's loose files, with the wads as
  their own linked section under `#wads` and the rest below it.
- The `maps/` page now says where the wads are and why they are not in there.
- ⛔ Nothing is copied into `maps/`: no client requests that path, so a copy there is dead
  weight to keep in sync. The tests pin that maps/ lists no wad.
- The section names `halflife.wad` as deliberately absent — 28 maps reference it, it ships
  with the game, and a 404 for it is correct.

Traps and the full BSP-vs-`.res` sweep behind this are in
`docs/runbooks/DATA_SERVER_CONFIG_TRAPS.md`.
