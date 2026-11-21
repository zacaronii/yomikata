#!/bin/sh
# Download a libretro core's official Linux x86_64 build for `yomikata replay`, and print its SHA-256 so a run can
# record exactly which build it used. Cores are native code: run replays in an isolated container (docs/replay.md).
#   tools/scripts/get-core.sh pcsx_rearmed ./cores
set -eu
core="${1:?core name, e.g. pcsx_rearmed}"
dest="${2:-./cores}"
mkdir -p "$dest"
url="https://buildbot.libretro.com/nightly/linux/x86_64/latest/${core}_libretro.so.zip"
tmp="$(mktemp -d)"
curl -fsSL -o "$tmp/core.zip" "$url"
python3 -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$tmp/core.zip" "$dest"
rm -rf "$tmp"
sha256sum "$dest/${core}_libretro.so"
