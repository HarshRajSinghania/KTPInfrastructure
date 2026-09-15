### `scripts`: one path from a new .bsp to a deployable map bundle (2026-09-15)

`build_map_bundle.py` produces the four files a client needs for a map —
`maps/<map>.bsp`, `maps/<map>.res`, `overviews/<map>.txt`, `overviews/<map>.bmp`
— plus a `MANIFEST.json` of md5s to sweep the fleet against after deploying. It
stages to a directory and touches no server. `build_resgen.sh` fetches and builds
RESGen (GPL-2.0, `kriswema/resgen`, not vendored) at a pinned tag.
`docs/MAP_DEPLOY.md` carries the operator half, including that
`/home/dod/distribute/` is a live deploy path that syncs deletions and never
back-fills.

RESGen at the `2.0.3` tag was replayed against the fleet's existing corpus before
being trusted: all 44 maps on Dallas `dod-27015` that carry a RESGen-generated
`.res` were regenerated from their own BSP and compared entry-by-entry. 43 of 44
reproduced the shipped non-overview entry list exactly and in order — no model,
sound, sprite, skybox face or WAD added or dropped on any map. The exception is
`dod_anzio3_b1.res`, which lists `models/mapmodels/ivy5.mdl` while its BSP
contains no reference to it; that file disagrees with its own map, not with the
generator. Tags `2.0.2` and `2.0.3` are byte-identical on all 44 once the version
string in the header comment is masked.

Two traps the tool now enforces rather than documents. RESGen lists
`overviews/<map>.txt` and `.bmp` only when both already exist beside the map, so
the overview must be rendered first — ten of the eleven replay disagreements were
a `.res` generated on one side of its overview's existence, reported by nothing.
And `master` is 58 commits past the `2.0.3` tag, carries a behaviour change in
sentence/wav parsing, and still prints `RESGen version 2.0.3`, so the banner
cannot identify the build and the ref is pinned instead.
