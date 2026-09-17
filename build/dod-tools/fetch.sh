#!/bin/bash
# Fetch the pinned dod-tools release (cgdangelo/dod-tools, GoldSrc demo parser).
#
# dod-tools is a prebuilt Rust binary, not something we compile, so unlike the
# other build/ inputs there is no Dockerfile - just a pinned release and its
# sha256, in the same wget | sha256sum -c shape as build/curl/Dockerfile.
# Never commit the binary. Bumping the version means re-deriving both hashes
# from the release page and re-running the demo_team_score unit fixtures.
#
#   ./build/dod-tools/fetch.sh                 # -> ./build/dod-tools/bin/dod-tools-cli
#   ./build/dod-tools/fetch.sh /usr/local/bin  # data server: install the CLI there
set -euo pipefail

VERSION="0.10.0"
BASE="https://github.com/cgdangelo/dod-tools/releases/download/v${VERSION}"
LINUX_TGZ="dod-tools-v${VERSION}-x86_64-unknown-linux-gnu.tgz"
LINUX_SHA="5f78bfe64e0461454330afff371d3aaeef73d3082510388ec26b6464c238f4de"
WINDOWS_ZIP="dod-tools-v${VERSION}-x86_64-pc-windows-msvc.zip"
WINDOWS_SHA="ab739872783cea64243f718b1e6a64e6f96ea92931b8d810548882ca2fd4d4cb"

dest="${1:-$(dirname "$0")/bin}"
mkdir -p "$dest"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

case "$(uname -s)" in
  Linux)
    (cd "$tmp" && wget -q --tries=3 --waitretry=5 "$BASE/$LINUX_TGZ" \
      && echo "$LINUX_SHA  $LINUX_TGZ" | sha256sum -c - \
      && tar xzf "$LINUX_TGZ" dod-tools-cli)
    install -m 0755 "$tmp/dod-tools-cli" "$dest/dod-tools-cli"
    ;;
  MINGW*|MSYS*|CYGWIN*)
    (cd "$tmp" && curl -sSL --retry 3 -o "$WINDOWS_ZIP" "$BASE/$WINDOWS_ZIP" \
      && echo "$WINDOWS_SHA  $WINDOWS_ZIP" | sha256sum -c - \
      && unzip -q -o "$WINDOWS_ZIP" dod-tools-cli.exe)
    cp "$tmp/dod-tools-cli.exe" "$dest/dod-tools-cli.exe"
    ;;
  *)
    echo "fetch.sh: no pinned dod-tools build for $(uname -s)" >&2
    exit 1
    ;;
esac
echo "dod-tools v${VERSION} -> $dest"
