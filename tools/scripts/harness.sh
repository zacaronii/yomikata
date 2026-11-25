#!/bin/sh
# Starts the emulator harness (docs/harness.md) as an MCP server on stdin and stdout, shut in:
# no network, the games, cores and inbox snapshots read-only, one scratch folder to write in, and nothing else
# of the machine. Where those are is yours to say, outside the repository:
#   YOMIKATA_GAMES     the library's game folders (psx/, ngc/, …)
#   YOMIKATA_CORES     a folder of libretro cores (tools/scripts/get-core.sh)
#   YOMIKATA_SNAPSHOTS an inbox's snapshots folder (readers' savestates)
#   YOMIKATA_SCRATCH   a folder of its own, owned by 99:100
#   YOMIKATA_SYSTEM    optional: a folder with your own consoles' BIOS files, for the cores that need one (read)
#   YOMIKATA_HARNESS_IMAGE (default ghcr.io/zacaronii/yomikata-harness:latest), YOMIKATA_HARNESS_CPUS (default 6)
# With Claude Code:  claude mcp add yomikata-harness -- /path/to/tools/scripts/harness.sh
set -eu
: "${YOMIKATA_GAMES:?where the games are}" "${YOMIKATA_CORES:?where the cores are}"
: "${YOMIKATA_SNAPSHOTS:?where the inbox keeps snapshots}" "${YOMIKATA_SCRATCH:?a scratch folder}"
system=""
[ -n "${YOMIKATA_SYSTEM:-}" ] && system="--system /system"
exec docker run --rm -i --name yomikata-harness \
    --network none --user 99:100 --cap-drop ALL --security-opt no-new-privileges \
    --cpus "${YOMIKATA_HARNESS_CPUS:-6}" --cpu-shares 256 --memory 8g --shm-size 2g --pids-limit 512 \
    -v "$YOMIKATA_GAMES:/games:ro" -v "$YOMIKATA_CORES:/cores:ro" -v "$YOMIKATA_SNAPSHOTS:/inbox:ro" \
    -v "$YOMIKATA_SCRATCH:/scratch" ${YOMIKATA_SYSTEM:+-v "$YOMIKATA_SYSTEM:/system:ro"} \
    "${YOMIKATA_HARNESS_IMAGE:-ghcr.io/zacaronii/yomikata-harness:latest}" $system "$@"
