#!/usr/bin/env python3
"""Build the KTPAntiCheat game-files integrity manifest.

Generates /opt/ktp-ac-api/game_files_manifest.json on the data server (and
locally for review). The manifest defines which game files the AC client
should hash + verify against expected SHA256s during session collection.

Three sources combined:
  1. .res files on the source server (maps/*.res — custom community map assets)
  2. KTPFileChecker ktp_file.ini (the engine consistency-check list the plugin
     actually loads: player models, player sounds — assets NOT referenced by
     any .res because they ship with stock DoD)
  3. Explicit additions (user policy: every stock held (p_) and world (w_)
     weapon model the game binaries load, the lowered/sprint _l set included,
     + grenade viewmodels at severity "review")

Excluded buckets (allowed modification): overviews/*, flag models
(w_aflag/gflag/wflag).

First-person grenade viewmodels (v_grenade/v_mills/v_stick) were violations
from 2026-07-07, left the manifest entirely on 2026-09-13 when the operator
ruled them allowable, and came back the same day at severity "review" when that
ruling was revised: still never scored, but a modified copy is captured for an
admin again. Excluding them could not do that -- the client only hashes paths
the manifest lists, so an excluded path is unobservable, not merely forgiven. A
viewmodel is drawn only in the holder's own hands; p_ (held, seen by others) and
w_ (thrown) grenade models are a different bucket and stay enforced.

Skyboxes (gfx/env/*) were an excluded bucket until 2026-08-27. They are now IN
scope at severity "review" -- an operator POLICY REVERSAL, not a bug fix. A
skybox mismatch is reported and its bytes captured for an admin, but it does not
count toward a verdict. Reverting = move "gfx/env/" back to
EXCLUDED_PATH_PREFIXES.

Usage:
  python3 build-game-files-manifest.py [--source-server <host>] [--out <path>]

Defaults:
  --source-server  74.91.121.9 (ATL1 :27015)
  --out            ./game_files_manifest.json (relative to CWD)

Re-run after a known map deploy or weekly via cron. Manifest then SCP'd to
data server at /opt/ktp-ac-api/game_files_manifest.json; the API serves it
via /api/game-files-manifest with ETag-based caching.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import paramiko


# --------------------------------------------------------------------------
# Policy — edit here to change manifest scope
# --------------------------------------------------------------------------

EXCLUDED_PATH_PREFIXES = ("overviews/",)

# Report-only scope. In the manifest, captured and shown to an admin, but the
# client's IsReview branch keeps it out of modified_game_files -- so it never
# flags a player. gfx/env/ added 2026-08-27 (skyboxes, previously excluded).
REVIEW_PATH_PREFIXES = ("gfx/env/",)
# First-person grenade viewmodels: only the holder ever sees them. p_/w_ grenade
# models are a different bucket and stay enforced.
GRENADE_VIEWMODELS = ("models/v_grenade.mdl", "models/v_mills.mdl", "models/v_stick.mdl")
# Same report-only scope as REVIEW_PATH_PREFIXES, matched whole rather than by
# prefix -- "models/" cannot be a prefix rule without releasing the weapon kit.
REVIEW_EXACT = frozenset(GRENADE_VIEWMODELS)

# Applied at every source, so a map .res or a future ktp_file.ini line cannot
# pull one of these back in.
EXCLUDED_EXACT = {
    "models/w_aflag.mdl",
    "models/w_gflag.mdl",
    "models/w_wflag.mdl",
}
# (pl_snow* exclusion removed 2026-07-07 — snow footsteps are back in
# ktp_file.ini enforcement, so the manifest must cover them again.)
EXCLUDED_FILELIST_PATTERNS = []

# Operator-curated alternate hashes — files where a community-distributed
# replacement is present on most player installs and should be accepted
# alongside the canonical Valve hash. AC client (0.5.2+) consults this list
# when comparing; mismatches that match an alternate are treated as clean.
#
# 2026-05-25: KTP league score-event ambient sound pack. Same `actual` hashes
# observed across operator's own machine + every player bundle in the corpus
# (Las1K64, arachnid, nein test bundles), confirming a community-standard
# replacement set predating AC. Without alternates these surface as 4
# false-positive violations on every legitimate player.
ALTERNATE_HASHES = {
    "sound/ambience/alliescap.wav": [
        "6a97244af9824daa97333986f2ed8db91f52c7203dd2b2db5aedef2943b79786",
    ],
    "sound/ambience/alliesscore.wav": [
        "1e465577efd267041e6db5a302e43b5fdb506e4d0d67b569bbfc28547c96a41e",
    ],
    "sound/ambience/axiscap.wav": [
        "071a41cc5f3886669ca05962f91bf127acd09d88300ce083ea9c86913a6d78a9",
    ],
    "sound/ambience/axisscore.wav": [
        "e775a4d4623b018da969d29148764b0c82be2c27d0a57e8640c86a85ee20cbe3",
        # Second score-jingle pack on the same path (jarrod / twist / 7735372).
        # Distinct hash, same file, both community content.
        "3f85263a0c68b3f2bdccb3bfe3ec4663c615b87370664a5b55454d9e0f35a1a3",
    ],
    # Without this the manifest gate, which runs FIRST, records a Violation that
    # scoring then quietly rescues: the verdict reads clean while the evidence file
    # says the player was modified.
    #
    # KEEP IN SYNC with SummaryGenerator.KnownBenignFileVariants. The AC repo has a
    # trait-gated guard (BenignVariantManifestSyncTests) that fails naming any entry
    # present in one and missing from the other, or allowlisted for an untracked path.
    #
    "dod_siena.wad": [
        "249f620741e27edcb84df33510b794798d972d81acd197b6eaa4db1aafb0c60f",
    ],
    # ⛔ models/v_{grenade,stick}.mdl get NO alternates — operator ruling 2026-09-14, and
    # re-adding them silently undoes it. An AllowedAlternateHashes match hits `continue`
    # BEFORE the IsReview branch, so an alternate is precisely what stops a copy being
    # taken; with none, every modified viewmodel reaches an admin, the known community
    # pack included. Visibility is the point of the revised ruling, and "we already
    # recognise this one" is not a reason to withhold its bytes.
}

# Every stock held (p_) and world (w_) weapon model the game loads, grouped by
# weapon. Each tuple: (family, [p_bases], [w_bases]); `.mdl` implicit. A family may
# hold several held models (bipod up/down, prone, sprint/lowered `_l`) and several
# world models (the dropped weapon and its projectile).
#
# 🔑 A name earns a place here by passing BOTH legs; one that fails either is a dead
# entry that reads as coverage:
#   1. the game binary loads it -- `strings` over dod/dlls/dod.so + dod/cl_dlls/client.so
#      names the literal path (the only format-string model path in either binary is
#      models/player/%s/%s.mdl, so a zero-hit name is a name nothing loads);
#   2. a stock client has it -- it is in Steam depot 31, the depot app 30 installs
#      (`DepotDownloader -app 90 -depot 31 -manifest-only`, anonymous).
# Presence on a server tree is NOT leg 2: the fleet install carries a community pack
# of extra p_*.mdl the engine never references. p_bar/p_mp44 sat here on exactly that
# mistake until 2026-09-15, reported missing by nearly every client scan while the
# real p_barbu/p_barbd/p_stg44 went unhashed. w_colt/w_luger/w_spade were the same
# bug on the w_ side: stock files, so never "missing", but pistols and melee cannot
# be dropped and no binary references them.
#
# The 2026-05-13 prune of "the _l/l pose variants" was half right. The no-underscore
# names (p_garandl, p_tommyl, ...) are community files; the underscore `_l` lowered/
# sprint held models are stock, loaded by the binary, and what opponents render while
# a player sprints. They carry the family's own severity. The community names are
# pinned out BY NAME in EXCLUDED_WEAPON_MODELS so "_l" is never read as a pattern.
WEAPON_FAMILIES = [
    # US
    ("amerknife",      ["p_amerk"],                                                  ["w_amerk"]),
    ("colt",           ["p_colt"],                                                   []),
    ("garand",         ["p_garand", "p_garand_l"],                                   ["w_garand"]),
    ("m1carb",         ["p_m1carb", "p_m1carb_l"],                                   ["w_m1carb"]),
    ("fcarb",          ["p_fcarb", "p_fcarb_l"],                                     ["w_fcarb"]),
    ("tommy",          ["p_tommy", "p_tommy_l"],                                     ["w_tommy"]),
    ("grease",         ["p_grease", "p_grease_l"],                                   ["w_greasegun"]),
    ("bar",            ["p_barbu", "p_barbd"],                                       ["w_bar"]),
    ("spring",         ["p_spring", "p_spring_l"],                                   ["w_spring"]),
    ("30cal",          ["p_30cal", "p_30calpr", "p_30calsr"],                        ["w_30cal"]),
    ("bazooka",        ["p_bazooka", "p_bazooka_l"],                                 ["w_bazooka", "w_bazooka_rocket"]),
    ("paraknife",      ["p_paraknife"],                                              ["w_paraknife"]),
    # Wehrmacht
    ("spade",          ["p_spade"],                                                  []),
    ("luger",          ["p_luger"],                                                  []),
    ("k98_unscoped",   ["p_k98", "p_k98_l"],                                         ["w_98k"]),
    ("k98_scoped",     ["p_k98s", "p_k98s_l"],                                       ["w_scoped98k"]),
    ("k43",            ["p_k43"],                                                    ["w_k43"]),
    ("mp40",           ["p_mp40"],                                                   ["w_mp40"]),
    ("mp44",           ["p_stg44"],                                                  ["w_mp44"]),
    ("fg42",           ["p_fg42bu", "p_fg42bd", "p_fg42pr", "p_fg42sr"],             ["w_fg42"]),
    ("fg42_scoped",    ["p_fg42s"],                                                  ["w_fg42s"]),
    ("mg42",           ["p_mg42bu", "p_mg42bd", "p_mg42pr", "p_mg42sr"],             ["w_mg42"]),
    ("mg34",           ["p_mg34bu", "p_mg34bd", "p_mg34pr", "p_mg34sr"],             ["w_mg34"]),
    ("pschreck",       ["p_pschreck", "p_pschreck_l"],                               ["w_pschreck", "w_pschreck_rocket"]),
    # British
    ("fairbairn",      ["p_fairbairn"],                                              []),
    ("webley",         ["p_webley"],                                                 []),
    ("enfield",        ["p_enfield", "p_enfield_l"],                                 ["w_enfield"]),
    ("enfield_scoped", ["p_enfields", "p_enfields_l"],                               ["w_enfield_scoped"]),
    ("sten",           ["p_sten"],                                                   ["w_sten"]),
    ("bren",           ["p_brenbu", "p_brenbd", "p_brenbr", "p_brensr", "p_bren_l"], ["w_bren"]),
]

# Names that look like kit members and are not, keyed so the reason travels with the
# name. _check_weapon_families() refuses a table that names one of these.
EXCLUDED_WEAPON_MODELS = {
    # On the fleet tree via a community pack; in no client install.
    "p_bar":        "not in depot 31; the game loads p_barbu/p_barbd",
    "p_mp44":       "not in depot 31; the game loads p_stg44",
    # The community half of the 2026-05-13 prune. Not the stock `_l` models, and not
    # to come back by pattern-matching on a trailing "l".
    "p_garandl":    "community lowered model, not in depot 31",
    "p_tommyl":     "community lowered model, not in depot 31",
    "p_k43l":       "community lowered model, not in depot 31",
    "p_98kl":       "community lowered model, not in depot 31",
    "p_k98sl":      "community lowered model, not in depot 31",
    "p_fcarbl":     "community lowered model, not in depot 31",
    "p_greasegunl": "community lowered model, not in depot 31",
    "p_m1carbl":    "community lowered model, not in depot 31",
    "p_springl":    "community lowered model, not in depot 31",
    # Stock and shipped, but no binary references them: pistols and melee cannot be
    # dropped, so there is no world model to load.
    "w_colt":       "in depot 31, referenced by neither binary",
    "w_luger":      "in depot 31, referenced by neither binary",
    "w_spade":      "in depot 31, referenced by neither binary",
    # Stock and referenced, but only by the mortar, which 1.3 cannot spawn: no class
    # offers it and both files are byte-identical copies of the PIAT models.
    "p_mortar":     "dead-code reference; no class can spawn a mortar",
    "w_mortar":     "dead-code reference; no class can spawn a mortar",
    # ⚠️ A DIFFERENT CLASS OF EXCLUSION FROM EVERY ENTRY ABOVE. These files are stock,
    # shipped, binary-referenced and loadable — they are out by LEAGUE POLICY, not
    # because they are dead. Operator ruling 2026-09-16: the PIAT is not used in KTP
    # competitive play. It is technically obtainable playing British with the bazooka
    # enabled, which normal competitive play does not do.
    # ➡️ If KTP ever enables British + bazooka in competitive play, these come back:
    #    delete the three lines and restore ("piat", ["p_piat"], ["w_piat", "w_piat_rocket"]).
    # 📌 Zero PIAT kills in 1,597,498 recorded frags at the time of the ruling, which is
    #    corroboration, not the reason — an anti-tank weapon can be held and seen without
    #    ever appearing in a frag row.
    "p_piat":        "not used in KTP competitive play (operator ruling 2026-09-16)",
    "w_piat":        "not used in KTP competitive play (operator ruling 2026-09-16)",
    "w_piat_rocket": "not used in KTP competitive play (operator ruling 2026-09-16)",
    # Stock, referenced by neither binary.
    "p_sten_l":     "in depot 31, referenced by neither binary",
}


def _check_weapon_families():
    named = [b for _, p_bases, w_bases in WEAPON_FAMILIES for b in (*p_bases, *w_bases)]
    excluded = sorted(set(named) & set(EXCLUDED_WEAPON_MODELS))
    if excluded:
        raise ValueError("WEAPON_FAMILIES names excluded models: "
                         + ", ".join(f"{n} ({EXCLUDED_WEAPON_MODELS[n]})" for n in excluded))
    dup = sorted(n for n, c in Counter(named).items() if c > 1)
    if dup:
        raise ValueError(f"WEAPON_FAMILIES names a model in more than one family: {dup}")


_check_weapon_families()


# --------------------------------------------------------------------------

def parse_res_files(ssh, dod_path):
    """Aggregate references from all maps/*.res files on the source server."""
    references = defaultdict(set)
    _, out, _ = ssh.exec_command(f"ls {dod_path}/maps/*.res 2>/dev/null", timeout=30)
    res_files = [l.strip() for l in out.read().decode().splitlines() if l.strip()]

    for res in res_files:
        map_name = res.split("/")[-1].replace(".res", "")
        _, out, _ = ssh.exec_command(f"cat '{res}'", timeout=30)
        for line in out.read().decode(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            # .res entries are plain paths; one per line, lowercase typical
            if re.match(r"^[a-zA-Z0-9_./\-]+\.(spr|wad|mdl|wav|tga|bmp|res|bsp)$", line, re.IGNORECASE):
                references[line.lower()].add(map_name)
    return references, len(res_files)


def parse_filelist_ini(filelist_path):
    """Parse local KTPFileChecker ktp_file.ini (or legacy filelist.ini).
    Handles both bare `player/...` and prefixed `sound/player/...` sound paths.
    Drop excluded patterns."""
    paths = []
    with open(filelist_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            # `player/...` entries are sound paths (sound/ prefix implicit in HL)
            if line.startswith("player/"):
                line = "sound/" + line
            if line in EXCLUDED_EXACT or any(p.match(line) for p in EXCLUDED_FILELIST_PATTERNS):
                continue
            paths.append(line)
    return paths


def hash_remote_file(ssh, full_path):
    """Returns (sha256, size) or None if file doesn't exist."""
    _, out, _ = ssh.exec_command(
        f"sha256sum '{full_path}' 2>/dev/null && stat -c '%s' '{full_path}' 2>/dev/null",
        timeout=15,
    )
    lines = out.read().decode().strip().splitlines()
    if len(lines) < 2 or " " not in lines[0]:
        return None
    return lines[0].split()[0], int(lines[1])


def variant_for(base):
    """`_l` is the stock lowered/sprint pose of a held model; everything else is primary."""
    return "lowered" if base.endswith("_l") else "primary"


def severity_for(path):
    """Severity for a manifest path: a violation unless it is in report-only scope."""
    if path in REVIEW_EXACT or any(path.startswith(p) for p in REVIEW_PATH_PREFIXES):
        return "review"
    return "violation"


def categorize(path):
    """Map a relative path to its category bucket."""
    p = path.lower()
    if p.endswith(".wad"):
        return "wad"
    if p.startswith("models/player/"):
        return "player_model"
    if p.startswith("models/mapmodels/"):
        return "mapmodel"
    if p.startswith("sound/ambience/"):
        return "ambience_sound"
    if p.startswith("sound/player/"):
        return "player_sound"
    if p.startswith("sprites/mapsprites/"):
        return "mapsprite"
    if p.startswith("sprites/"):
        return "sprite"
    if p.startswith("models/"):
        leaf = p.split("/")[-1].replace(".mdl", "")
        # Unambiguous, unlike p_/w_, whose prefix the weapon kit shares — so it can be
        # settled here and every route agrees. A .res reaching one of these otherwise
        # emits it as model_other and the dossier names the wrong bucket.
        if leaf in ("v_grenade", "v_mills", "v_stick"):
            return "grenade_model"
        if leaf.startswith("p_"):
            return "weapon_player_model"
        if leaf.startswith("w_"):
            return "weapon_world_model"
        if leaf in ("allied_ammo", "axis_ammo"):
            return "ammo_model"
        if leaf in ("hat_axis", "helmet_axis", "helmet_us"):
            return "equipment_model"
        if leaf == "player":
            return "player_model"
        return "model_other"
    return "other"


def build_manifest(ssh, dod_path, filelist_path):
    entries = []

    # 1. .res-derived files (custom-map assets)
    print(f"[build] Parsing .res files on {dod_path}...", file=sys.stderr)
    references, res_count = parse_res_files(ssh, dod_path)
    print(f"[build]   {res_count} .res files → {len(references)} unique referenced paths", file=sys.stderr)

    res_added = 0
    res_excluded = 0
    res_missing = []
    for path, ref_maps in sorted(references.items()):
        if any(path.startswith(p) for p in EXCLUDED_PATH_PREFIXES) or path in EXCLUDED_EXACT:
            res_excluded += 1
            continue
        result = hash_remote_file(ssh, f"{dod_path}/{path}")
        if result is None:
            res_missing.append(path)
            continue
        sha, size = result
        entries.append({
            "path": path,
            "sha256": sha,
            "size": size,
            "origin": ".res",
            "category": categorize(path),
            "severity": severity_for(path),
            "referenced_by": sorted(ref_maps),
        })
        res_added += 1

    if res_missing:
        print(f"[build]   ⚠ {len(res_missing)} files referenced but missing on server: {res_missing}",
              file=sys.stderr)
    print(f"[build]   .res added: {res_added} (excluded: {res_excluded})", file=sys.stderr)

    # 2. filelist.ini-derived files (base/stock assets)
    print(f"[build] Parsing filelist.ini at {filelist_path}...", file=sys.stderr)
    filelist_paths = parse_filelist_ini(filelist_path)
    print(f"[build]   {len(filelist_paths)} unique paths after exclusions", file=sys.stderr)

    seen = {e["path"] for e in entries}
    fl_added = 0
    fl_dedup = 0
    for path in filelist_paths:
        if path in seen:
            fl_dedup += 1
            continue
        result = hash_remote_file(ssh, f"{dod_path}/{path}")
        if result is None:
            continue
        sha, size = result
        cat = categorize(path)
        leaf = path.split("/")[-1].replace(".mdl", "")
        if leaf in ("p_grenade", "p_mills", "p_stick", "w_grenade", "w_mills", "w_stick"):
            cat = "grenade_model"
        entries.append({
            "path": path,
            "sha256": sha,
            "size": size,
            "origin": "filelist.ini",
            "category": cat,
            "severity": severity_for(path),
        })
        seen.add(path)
        fl_added += 1
    print(f"[build]   filelist.ini added: {fl_added} (dedup'd against .res: {fl_dedup})", file=sys.stderr)

    # 3. Explicit additions: equipment models + ammo + player.mdl
    explicit_singletons = [
        "models/allied_ammo.mdl", "models/axis_ammo.mdl",
        "models/hat_axis.mdl", "models/helmet_axis.mdl", "models/helmet_us.mdl",
        "models/player.mdl",
    ]
    print(f"[build] Explicit equipment/ammo/player.mdl additions...", file=sys.stderr)
    for path in explicit_singletons:
        if path in seen:
            continue
        result = hash_remote_file(ssh, f"{dod_path}/{path}")
        if result is None:
            print(f"[build]   ⚠ {path} not found on server", file=sys.stderr)
            continue
        sha, size = result
        entries.append({
            "path": path, "sha256": sha, "size": size,
            "origin": "explicit_2026-05-01",
            "category": categorize(path),
            "severity": severity_for(path),
        })
        seen.add(path)

    # 3b. Grenade viewmodels — guaranteed regardless of filelist content.
    # ktp_file.ini stopped listing them (KTPFileChecker 1bf59f6) and no .res
    # references them, so without this pass the report-only ruling would be a
    # policy nothing implements.
    print(f"[build] Grenade viewmodels ({len(GRENADE_VIEWMODELS)}, severity review)...", file=sys.stderr)
    for path in GRENADE_VIEWMODELS:
        if path in seen:
            continue
        result = hash_remote_file(ssh, f"{dod_path}/{path}")
        if result is None:
            print(f"[build]   ⚠ {path} not found on server", file=sys.stderr)
            continue
        sha, size = result
        entries.append({
            "path": path, "sha256": sha, "size": size,
            "origin": "explicit_2026-07-07_grenade_viewmodels",
            "category": "grenade_model",
            "severity": severity_for(path),
        })
        seen.add(path)

    # 4. Weapon kit families
    print(f"[build] Weapon-kit families ({len(WEAPON_FAMILIES)})...", file=sys.stderr)
    # Drop any prior weapon model entries from the .res / filelist sources so
    # the explicit weapon-kit pass owns the per-family categorization (severity,
    # weapon_family, variant fields).
    entries = [e for e in entries
               if e.get("category") not in ("weapon_player_model", "weapon_world_model")]
    seen = {e["path"] for e in entries}

    weapon_added = 0
    weapon_missing = []
    for family, p_bases, w_bases in WEAPON_FAMILIES:
        for base in (*p_bases, *w_bases):
            rel = f"models/{base}.mdl"
            if rel in seen:
                continue
            result = hash_remote_file(ssh, f"{dod_path}/{rel}")
            if result is None:
                weapon_missing.append(rel)
                continue
            sha, size = result
            entries.append({
                "path": rel, "sha256": sha, "size": size,
                "origin": "explicit_2026-05-02_full_kit",
                "category": "weapon_player_model" if base.startswith("p_") else "weapon_world_model",
                "severity": severity_for(rel),
                "weapon_family": family,
                "variant": variant_for(base),
            })
            seen.add(rel)
            weapon_added += 1

    # A kit path the source server lacks is a stock file gone missing from the tree,
    # never a file to skip quietly: every client has it and it would go unhashed.
    if weapon_missing:
        print(f"[build]   ⚠ weapon-kit paths not found on server: {weapon_missing}", file=sys.stderr)
    print(f"[build]   weapon-kit added: {weapon_added}", file=sys.stderr)

    return entries


def assemble_manifest(entries, source_server_label, dod_path):
    entries.sort(key=lambda e: (e["category"], e.get("severity", "violation"), e["path"]))

    # Apply operator-curated alternate hashes. Logged so re-runs surface any
    # ALTERNATE_HASHES keys that no longer match a manifest path (typo / file
    # removed from scope) — silent application would let the alternate quietly
    # stop having effect.
    alt_applied = 0
    alt_unmatched = []
    paths_in_manifest = {e["path"] for e in entries}
    for path, alts in ALTERNATE_HASHES.items():
        if path not in paths_in_manifest:
            alt_unmatched.append(path)
            continue
    for e in entries:
        alts = ALTERNATE_HASHES.get(e["path"])
        if alts:
            e["allowed_alternate_hashes"] = list(alts)
            alt_applied += 1
    if alt_applied:
        print(f"[build]   alternate hashes applied to {alt_applied} entries", file=sys.stderr)
    if alt_unmatched:
        print(f"[build]   WARNING: ALTERNATE_HASHES keys with no matching manifest entry: {alt_unmatched}", file=sys.stderr)

    cat_counts = Counter(e["category"] for e in entries)
    sev_counts = Counter(e["severity"] for e in entries)
    src_counts = Counter(e.get("origin", "?") for e in entries)
    total_size = sum(e["size"] for e in entries)

    # Include alternates in the version hash so adding/removing them invalidates
    # ETag caches and clients re-fetch. Without this, the version stays the same
    # when an alternate is added and clients on the old cached copy keep
    # false-positive-flagging the file.
    version = hashlib.sha256(
        json.dumps(
            [(e["path"], e["sha256"], tuple(e.get("allowed_alternate_hashes") or [])) for e in entries],
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]

    return {
        "_meta": {
            "version": version,
            "source_server": source_server_label,
            "source_path": dod_path,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_files": len(entries),
            "total_size_bytes": total_size,
            "by_category": dict(cat_counts),
            "by_severity": dict(sev_counts),
            "sources": dict(src_counts),
            "severity_semantics": {
                "violation": "Mismatch is a hard violation. Reported in dossier and counts toward verdict.",
                "review": "Mismatch surfaces in dossier as 'admin review' item, NOT a violation. Player's local file copied into session bundle's review_files/ subdirectory for admin inspection. Use cases: skyboxes (gfx/env/*), where custom sky packs are commonplace and legitimate but a transparent or flattened sky is a real visual advantage worth an admin's eyes; and first-person grenade viewmodels (models/v_{grenade,mills,stick}.mdl), which are allowed at any hash but are still worth an admin's eyes.",
            },
            "scope_notes": [
                "Every stock held (p_) and world (w_) weapon model the game binaries load -- US, Wehrmacht and British/paratrooper alike, the stock _l lowered/sprint held models included -- enforced as violations: these are what opponents render.",
                "v_*.mdl (first-person view models) NOT enforced, EXCEPT grenade viewmodels (v_grenade/v_mills/v_stick), which are IN scope at severity 'review' since 2026-09-13. They are allowed at any hash and never count toward a verdict; they are listed so a modified copy is still captured for admin review, which excluding them made impossible. p_/w_ grenade models stay enforced as violations.",
                "gfx/env/* (skyboxes) IN scope at severity 'review' since 2026-08-27 (was an excluded bucket). Reported and captured for admin review; never counts toward a verdict. Only skyboxes a map .res references enter scope -- stock skies are unreferenced and stay out.",
                "Kept out by name (EXCLUDED_WEAPON_MODELS): the no-underscore l-suffix names (p_garandl, p_tommyl, ...) are community files, not stock; w_colt/w_luger/w_spade are stock but loaded by nothing (pistols and melee cannot be dropped); the mortar models are stock but no 1.3 class can spawn the weapon.",
            ],
            "excluded_buckets": [
                "models/{w_aflag,w_gflag,w_wflag}.mdl (flag — cosmetic, allowed)",
                "overviews/* (top-down map BMPs — cosmetic, allowed)",
                # maps/*.bsp: NOT cosmetic, and this is a stated gap rather than a ruling.
                # A client-side BSP edit that removes cover is a real wallhack — the server
                # stays authoritative for collision, so the player is still blocked, but
                # they SEE through what they deleted. The reason maps are out of scope is
                # cost, not harmlessness: the current 213-entry set hashes ~147MB, and the
                # competitive rotation would add roughly 10-15 files at 10-50MB each, on
                # every scan, twice a session. Revisit with a cheaper design (hash only the
                # loaded map, or sample) rather than by silently bolting the rotation on.
                "maps/*.bsp (NOT cosmetic — real wallhack vector; excluded on hashing cost, see comment)",
            ],
        },
        "files": entries,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n\n")[0])
    ap.add_argument("--source-server", default="74.91.121.9",
                    help="Game-server SSH host (default: 74.91.121.9 = ATL1)")
    ap.add_argument("--source-user", default="dodserver")
    ap.add_argument("--source-port-dir",
                    default="/home/dodserver/dod-27015/serverfiles/dod",
                    help="dod/ directory on the source server")
    ap.add_argument("--filelist",
                    default=str(Path(__file__).resolve().parent.parent.parent /
                                "KTPFileChecker" / "ktp_file.ini"),
                    help="Local path to KTPFileChecker ktp_file.ini (the list the plugin loads)")
    ap.add_argument("--out", default="game_files_manifest.json",
                    help="Output JSON path (default: ./game_files_manifest.json)")
    ap.add_argument("--ssh-password", default=None,
                    help="SSH password for source-user (default: $KTP_FLEET_SSH_PASSWORD "
                         "or ~/.ktp_fleet_ssh_password)")
    args = ap.parse_args()

    if not args.ssh_password:
        args.ssh_password = os.environ.get("KTP_FLEET_SSH_PASSWORD")
    if not args.ssh_password:
        pw_file = Path.home() / ".ktp_fleet_ssh_password"
        if pw_file.exists():
            args.ssh_password = pw_file.read_text().strip()
    if not args.ssh_password:
        sys.exit("SSH password required: --ssh-password, $KTP_FLEET_SSH_PASSWORD, "
                 "or ~/.ktp_fleet_ssh_password")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(args.source_server, username=args.source_user, password=args.ssh_password)

    try:
        entries = build_manifest(ssh, args.source_port_dir, args.filelist)
        manifest = assemble_manifest(entries, f"{args.source_server} {args.source_port_dir}",
                                      args.source_port_dir)

        out_path = Path(args.out).resolve()
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        meta = manifest["_meta"]
        print(f"\n=== Manifest built ===", file=sys.stderr)
        print(f"  version:   {meta['version']}", file=sys.stderr)
        print(f"  total:     {meta['total_files']} files ({meta['total_size_bytes']/1024/1024:.1f} MB)",
              file=sys.stderr)
        print(f"  severity:  {meta['by_severity']}", file=sys.stderr)
        print(f"  category:  {meta['by_category']}", file=sys.stderr)
        print(f"  sources:   {meta['sources']}", file=sys.stderr)
        print(f"  output:    {out_path}", file=sys.stderr)
    finally:
        ssh.close()


if __name__ == "__main__":
    main()
