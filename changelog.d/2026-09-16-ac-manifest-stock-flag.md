### `scripts`: the manifest now says which files a clean install actually has (2026-09-16)

Every manifest entry carries a `stock` flag: is this path in Steam depot 31, the depot app 30
installs? That is the difference between the two things a "missing file" report has always
meant at once — a player who deleted their own footstep sounds, and a player who has never
connected to a given custom map. Across 682 covered client scans the corpus holds 19,307
missing-file occurrences, and 99.06% of them are the second kind. The 182 that are the first
kind — four players, seven bundles — sat inside that noise with nothing able to separate them.

The list is checked in at `scripts/data/dod-depot31-stock-paths.txt`: 1,684 paths, **paths
only**. It comes from `DepotDownloader -app 90 -depot 31 -manifest-only` (anonymous, no
account), manifest `3826716661969602728` dated 2020-08-18 — 1,745 rows of which 61 carry the
Directory flag. ⚠️ **The Flags column is hex**: reading `40` as decimal keeps all 61
directories in the list and drops nothing, so the count still looks plausible.

⚠️ **Compare case-insensitively.** 101 depot paths carry upper-case characters and the fleet
tree holds them lower-cased; a case-sensitive join marks `models/player/us-inf/us-infT.mdl`
and its three siblings non-stock. There are no case-collisions in the list, so folding is
lossless.

⛔ **No hashes in the checked-in list, and none to be added.** This repo is public. A path list
repeats what Steam tells anyone who asks; a hash list is an oracle for testing a modified file
against ours before uploading it.

The second half is a build-time warning. An entry from one of the generator's own explicit
passes is a claim that *every* client has the file, so an explicit entry absent from the depot
is a path nothing can deliver — `p_bar` and `p_mp44` were exactly that for months, present on
the fleet tree via a community pack and missing on 681 of 682 client scans. `assemble_manifest`
now names them on stderr and in `_meta.dead_entry_candidates`, so the next one is caught at
generation instead of by someone noticing a 681-of-682 missing rate. `.res`-derived entries are
deliberately not checked: a custom map's asset is absent from the depot by definition and
reaches a player over FastDL when they play that map.

`stock` is a fact about Valve's depot, not a judgement about a player, and nothing here scores
anyone. Consuming it is the AC repo's side.

⚠️ **The served manifest is unchanged until someone regenerates and installs it** — that is an
operator act, and the current served copy (`460a687a4f7ed682`, 462 entries) already carries no
dead explicit entries, so the warning has nothing to say about it today.
