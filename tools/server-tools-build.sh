#!/usr/bin/env bash
#
# ImageSrv -- the programs of ours that run ON THE SERVER, built from the source
# tree the server already carries (#2533).
#
# Today that is one: chunkcast, the chunk engine's sender (agent/chunkcast.c).
# The boot payload (tools/boot-payload-build.sh) packs the same program into
# the stations' initrd, where it is the receiver; this script puts it where the
# server's own process finds it -- "chunkcast send" by PATH. There is no
# Debian package for chunkcast, so it is compiled here, with the flags of the
# initrd's build and against the libzstd every Debian carries (by its file
# name: no libzstd-dev).
#
# Called by tools/iso/firstboot.sh and tools/server-upgrade.sh, both AFTER the
# boot payload is settled -- on a prebuilt payload as well as a rebuilt one:
# a server that took the initrd ready-made still needs its own sender.
#
#   server-tools-build.sh --source DIR [--bin-dir DIR]
#
# Positive evidence only (principle 5): the new binary replaces the old one
# only after it ran, said its own usage line, and answered `--version` the way
# the server asks it before every wave (ChunkEngine.binary_problem). A build
# that fails leaves the previous binary as it was and exits non-zero, with one
# ASCII line that says why. Since #3173 the chunk engine is the only one, so a
# server without it sends no wave at all: the callers FAIL on that exit (owner
# 21/11) -- firstboot stops on its chunkcast-failed screen, the upgrade rolls back.
#
# IMAGESRV_SERVER_BIN_DIR overrides the directory (tests).
set -euo pipefail

SOURCE=""
BIN_DIR="${IMAGESRV_SERVER_BIN_DIR:-/usr/local/bin}"
die() { echo "server-tools-build: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source) SOURCE="${2:-}"; shift 2 ;;
        --bin-dir) BIN_DIR="${2:-}"; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done
[[ -n "$SOURCE" && -f "$SOURCE/agent/chunkcast.c" && -f "$SOURCE/agent/fanout.c" && -f "$SOURCE/agent/blake3/blake3_one.c" ]] \
    || die "--source must be the ImageSrv tree (agent/chunkcast.c, agent/fanout.c and agent/blake3/)"
command -v gcc >/dev/null 2>&1 || die "gcc is not installed; chunkcast was not built, the previous one is untouched"
install -d "$BIN_DIR" || die "cannot create $BIN_DIR"

new="$BIN_DIR/chunkcast.new"
rm -f "$new"
gcc -O2 -Wall -Wextra -pthread -o "$new" "$SOURCE/agent/chunkcast.c" "$SOURCE/agent/blake3/blake3_one.c" \
    "$SOURCE/agent/blake3/blake3_sse41_x86-64_unix.S" "$SOURCE/agent/blake3/blake3_sse2_x86-64_unix.S" -l:libzstd.so.1 \
    || { rm -f "$new"; die "chunkcast did not compile; the previous one is untouched"; }

# It runs, and it is the program we think it is: with no arguments it exits 2
# and names itself. Anything else -- a missing library, a truncated file -- is
# not a binary to hand to the server.
set +e
said=$("$new" 2>&1)
rc=$?
set -e
if [[ "$rc" != 2 || "$said" != *"usage: chunkcast send|recv|apply"* ]]; then
    rm -f "$new"
    die "the new chunkcast did not run as expected (exit $rc); the previous one is untouched"
fi
# The server's own question (binary_problem): `--version` exits 0 and names itself.
set +e
version=$("$new" --version 2>&1)
rc=$?
set -e
if [[ "$rc" != 0 || "$version" != "chunkcast "* ]]; then
    rm -f "$new"
    die "the new chunkcast did not answer --version (exit $rc); the previous one is untouched"
fi
chmod 0755 "$new"
mv -f "$new" "$BIN_DIR/chunkcast"
echo "server-tools-build: $BIN_DIR/chunkcast is ready ($version)"
