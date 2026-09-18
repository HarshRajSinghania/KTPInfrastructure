### `scripts`: the PIAT leaves manifest scope — a policy exclusion, not a dead file (2026-09-16)

Operator ruling 2026-09-16: KTP does not play the PIAT in competition. It is reachable only on a
British class with the bazooka enabled, which normal competitive play does not do, so its three
models leave the weapon kit: `p_piat`, `w_piat` and the `w_piat_rocket` projectile.

🔑 **This is a different class of exclusion from every other name in `EXCLUDED_WEAPON_MODELS`, and
the table now says so.** The others are there because the file is not real — absent from Steam
depot 31 (`p_bar`, the community `_l` names) or shipped but referenced by neither binary (`w_colt`,
`p_sten_l`). The PIAT is stock, it ships, both binaries reference it, and DoD will load it. Only the
league's own rules put it out of scope. Left unlabelled, the next reader sees `p_piat` beside
`p_bar` ("not in depot 31"), reads the exclusion as a factual claim about the file, finds the claim
false, and re-adds the family.

The mortar stays excluded on its existing, factual grounds: no 1.3 class can spawn one, confirmed by
the operator. Its models are byte-identical copies of the PIAT's, so both pairs now leave together.

⚠️ Zero PIAT kills in 1,597,498 recorded frags is corroboration, not the reason. An anti-tank weapon
can be carried and seen for a whole map without landing a frag, so "no kills" would be weak grounds
on its own.

➡️ **Reversing this is three deleted lines**, and the table records exactly which: if British +
bazooka ever enters competitive play, the models go back in scope.

Tests: a dedicated suite pins the kit absence, the projectile going with the launcher, the reason
text reading as policy rather than fact, that factual exclusions do NOT drift into policy wording,
and that the import-time guard actually raises on a reinstated family. The kit-completeness
invariants now read "the stock loaded set MINUS policy exclusions" — the stock sets stay a factual
record of what the game loads, and policy is subtracted at the assert.

Regenerating the manifest is what removes the three paths from what clients hash; the served copy is
unchanged until then.
