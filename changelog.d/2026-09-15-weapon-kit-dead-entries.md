### `scripts`: the weapon kit hashed two models the game never loads, and missed the three it does (2026-09-15)

`WEAPON_FAMILIES` named `p_bar` and `p_mp44` as the BAR and MP44 held models. Neither is a
file Day of Defeat ships: `strings` over `dod/dlls/dod.so` and `dod/cl_dlls/client.so`
references `p_barbu` 4 times, `p_barbd`, and `p_stg44` twice, and `p_bar`/`p_mp44` zero times
(control: `p_colt`, 2). Steam depot 31 carries the same three and neither of the other two.

So the manifest spent two entries on paths no client has — reported missing by 690 of 691
successful scans — while the models other players actually see went unhashed. A modified
`p_barbu` or `p_stg44` changes what an opponent renders, which is the class this kit exists to
cover.

🔑 **Presence on a server tree is not evidence a file is stock.** The fleet install carries a
community pack of ~23 extra `p_*.mdl`, `p_bar` and `p_mp44` among them, so both names look
real on any host you check. That is how they survived: the name was confirmed against a tree
rather than against the binary. Confirm a held-model name with `strings` over `dod.so` /
`client.so` before adding it here.

The `p_` side of each tuple is now a list, because a family can hold several models
(`bar` holds bipod-up and bipod-down). The emit loop flattens `(*p_bases, w_base)`; a bare
string would still iterate, character by character, and emit nothing — which is what the new
shape test pins rather than a style preference.

⚠️ **Not changed, and an operator call:** the 2026-05-13 prune of "the `_l`/`l` pose variants"
rests on a half-false premise. The underscore `_l` lowered/sprint models (`p_garand_l`,
`p_tommy_l`, `p_k98_l`) are referenced by the binary and ship in depot 31; the no-underscore
ones (`p_garandl`, `p_tommyl`) are the community-mod files. Only the second half was ever "not
stock DoD", so the stock half is unhashed on the strength of a wrong reason.

Regenerating the manifest is what puts this in front of clients; the served copy is unchanged
until then.
