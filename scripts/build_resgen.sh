#!/bin/bash
# build_resgen.sh -- fetch and build RESGen, the .res generator build_map_bundle.py drives.
#
# RESGen is GPL-2.0 third-party code (https://github.com/kriswema/resgen) and is
# deliberately NOT vendored into this repo. This script clones it into a scratch
# directory and builds it there.
#
# Pinned to the 2.0.3 tag, not to master. Two reasons, both measured:
#   - master is 58 commits past the tag and includes a behaviour change
#     ("Add parsing for wav files in sentences"), while still reporting
#     `RESGen version 2.0.3`. The banner cannot tell you which one you built,
#     so the ref is the only identity -- pin it.
#   - The 2.0.3 tag reproduced the fleet's existing .res corpus entry-for-entry
#     (44 maps replayed, one shipped file disagreeing for a reason of its own).
#     That is the build that earned trust; master has not been through the bar.
#
# 2.0.3 vs the 2.0.2 that made the fleet's files: 2.0.3 adds only the opt-in `-n`
# flag. The skyname/WAD parsing fix is 2.0.2's own changelog entry, so it is
# already in the fleet corpus. Output is identical once the version string in
# the header comment is masked.
#
# Usage:
#   scripts/build_resgen.sh [dest] [ref]
#   export KTP_RESGEN=<dest>/bin/resgen
#
# Exit: 0 built, 1 build failed, 2 missing toolchain.

set -euo pipefail

DEST="${1:-${KTP_RESGEN_SRC:-$HOME/.cache/ktp/resgen}}"
REF="${2:-2.0.3}"
REPO="https://github.com/kriswema/resgen.git"

command -v git >/dev/null || { echo "ERROR: git not found" >&2; exit 2; }
command -v g++ >/dev/null || { echo "ERROR: g++ not found (apt install g++)" >&2; exit 2; }

if [ ! -d "$DEST/.git" ]; then
    mkdir -p "$(dirname "$DEST")"
    git clone --quiet "$REPO" "$DEST"
fi

git -C "$DEST" fetch --quiet --tags origin
git -C "$DEST" checkout --quiet --detach "$REF"

make -C "$DEST" all >/dev/null

BIN="$DEST/bin/resgen"
[ -x "$BIN" ] || { echo "ERROR: build produced no binary at $BIN" >&2; exit 1; }

echo "built: $BIN"
echo "ref:   $REF ($(git -C "$DEST" rev-parse --short HEAD))"
"$BIN" -h 2>&1 | head -1
echo
echo "export KTP_RESGEN=$BIN"
