#!/bin/bash
# Yomikata's nightly regression check (docs/regress.md), as an Unraid User Scripts job: every merged entry's bar
# screens read again with the catalog at main, in the tools image with no network. A clean night says nothing;
# a fall (exit status 1) sends one Unraid notification with the counts and the snaps' names, never game text.
#
# Settings (override them in the environment if your paths differ):
CATALOG="${YOMIKATA_CATALOG:-/mnt/cache_ad/appdata/yomikata-harness/catalog}"   # a clone of the catalog at main
SCRATCH="${YOMIKATA_SCRATCH:-/mnt/cache_ad/appdata/yomikata-harness}"           # the harness's scratch
GAMES="${YOMIKATA_GAMES:-/mnt/user/data/media/romm_library/roms}"
IMAGE="${YOMIKATA_TOOLS_IMAGE:-ghcr.io/zacaronii/yomikata-tools:latest}"
REPO="https://github.com/zacaronii/yomikata.git"

set -u
# The catalog at main, brought up to date (the only step that uses the network): with the host's git where it
# has one, else with git in a throwaway container.
if command -v git >/dev/null 2>&1; then
    run_git() { git "$@"; }; here="$CATALOG"
else
    run_git() { docker run --rm -v "$(dirname "$CATALOG"):/w" alpine/git:latest "$@"; }; here="/w/$(basename "$CATALOG")"
fi
if [ -d "$CATALOG/.git" ]; then
    run_git -C "$here" pull -q --ff-only || exit 2
else
    run_git clone -q "$REPO" "$here" || exit 2
fi
chown -R 99:100 "$CATALOG" 2>/dev/null

SUMMARY="$SCRATCH/regress/summary.txt"
rm -f "$SUMMARY"
# The tools image as published (the package is public): else a stale local copy is run night after night. A
# failed pull leaves the local image to run.
docker pull -q "$IMAGE" >/dev/null || true
docker run --rm --network none --user 99:100 \
    -v "$CATALOG:/catalog:ro" -v "$GAMES:/games:ro" -v "$SCRATCH:/scratch" \
    "$IMAGE" regress --catalog /catalog --games /games --scratch /scratch --summary /scratch/regress/summary.txt
status=$?
if [ "$status" -eq 1 ]; then
    /usr/local/emhttp/webGui/scripts/notify -e "Yomikata" -s "Yomikata: bar screens fell" -i "warning" \
        -d "$(head -c 900 "$SUMMARY" 2>/dev/null || echo 'see regress/ in the harness scratch')"
fi
exit "$status"
