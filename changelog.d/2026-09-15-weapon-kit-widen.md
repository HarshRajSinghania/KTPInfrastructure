### `scripts`: the weapon kit now hashes every stock held and world model the game loads, the `_l` sprint set included (2026-09-15)

`WEAPON_FAMILIES` covered 15 held and 14 world models. `strings` over the stock `dod/dlls/dod.so`
and `dod/cl_dlls/client.so` (the fleet copies are SHA1-identical to Steam depot 31's) name 63
held and 33 world models, and every one of them is in depot 31 with the fleet tree's bytes
matching the depot's SHA1. So a clean client has all of them at exactly the hash the builder
will record, and 62 models that opponents render — every sprint pose, the MG, FG42, bazooka,
Panzerschreck, grease gun, folding carbine and British/paratrooper families — went unhashed.

Every family now lists both sides: `(family, [p_bases], [w_bases])`, so a weapon can carry
its dropped model and its projectile. The 13 stock `_l` held models return at their family's
severity with `variant: "lowered"`. The 2026-05-13 prune removed them on the premise that
"the `_l`/`l` variants" were community files; that was true only of the no-underscore names
(`p_garandl`, `p_tommyl`, …), which are still out — now by name, in `EXCLUDED_WEAPON_MODELS`,
with a reason each, and an import-time check refuses a table that names one. Membership is a
two-leg test, and both legs are what the table's comment asks for: the binary loads the path,
and depot 31 ships it. A server tree proves neither.

Removed for failing leg one: `w_colt`, `w_luger`, `w_spade`. Stock files, so never reported
missing, but pistols and melee cannot be dropped and neither binary references them — the same
class of dead entry as `p_bar`, on the other side of the tuple. Kept out for the same reason:
`p_mortar`/`w_mortar`, referenced only by the mortar weapon no 1.3 class can spawn (0 kills in
1.59 M fleet frags; both files are byte-identical copies of the PIAT models), and `p_sten_l`,
shipped but referenced by nothing.

Cost, measured against the 694-bundle corpus: no bundle from any of its 170 players has ever
carried a `models/p_*` or `models/w_*` violation or review item, so the expected change to
flagged volume and capture load is nil; the kit's bytes grow from about 1.1 to 3.8 MB on a
scan that hashes 163 MB, with 0 timed-out final scans in the corpus. A kit path absent from the
source server is now logged instead of skipped, since every client has the stock file.

Regenerating the manifest is what puts this in front of clients; the served copy is unchanged
until then.
