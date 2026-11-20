#!/bin/bash
# build.sh <out-dir> -- builds the libFuzzer harnesses of .github/fuzz/ with
# clang, ASan and UBSan, and their seed inputs. Used by .github/workflows/fuzz.yml
# and, by hand, on the testrunner (the same clang 19 Debian 13 has).
#
# A harness is built when the agent source it includes is in this tree, and
# skipped by name when it is not (chunkcast.c lives on the #2533 branches until
# it is merged). The canary is always built: it is the proof that the fuzzer and
# ASan can still fail (principle 5).
#
# Writes <out-dir>/harnesses: one line per harness built. The workflow fuzzes
# exactly those and fails on an empty list.
set -euo pipefail

out=${1:?usage: build.sh <out-dir>}
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
CC=${CC:-clang}
mkdir -p "$out/seeds"
: > "$out/harnesses"

# -fno-sanitize-recover: a UBSan report is a crash the fuzzer stops on, not a line it scrolls past.
flags=(-g -O1 -fno-omit-frame-pointer "-fsanitize=fuzzer,address,undefined"
       -fno-sanitize-recover=undefined -Wno-unused-function)

build() {   # build <name> <agent source it needs> [extra flags...]
    local name=$1 src=$2
    shift 2
    if [ ! -f "$root/agent/$src" ]; then
        echo "$name: agent/$src is not in this tree -- not built"
        return 0
    fi
    "$CC" "${flags[@]}" -o "$out/$name" "$here/$name.c" "$@"
    echo "$name" >> "$out/harnesses"
    echo "$name: built"
}

"$CC" "${flags[@]}" -o "$out/canary" "$here/canary.c"
echo "canary: built"

build fanout_readback fanout.c

# The seed of fanout_readback: a whole partclone v2 header with a 64-block map
# (flags 7: used, header CRC and map CRC are filled in by the harness, mode 0).
mkdir -p "$out/seeds/fanout_readback"
python3 - "$out/seeds/fanout_readback/valid" <<'EOF'
import struct, sys
head = bytearray(110)
head[0:15] = b"partclone-image"
head[30:34] = b"0002"
struct.pack_into("<H", head, 34, 0xC0DE)
struct.pack_into("<Q", head, 60, 64)      # total blocks
struct.pack_into("<I", head, 84, 4096)    # block size
struct.pack_into("<H", head, 92, 2)
head[105] = 1                             # a bit map follows
bitmap = bytes([0xFF, 0x0F, 0x00, 0xF0, 0x01, 0x80, 0x00, 0xFF])
seed = bytes([7, 0, 99, 1]) + bytes(head) + bitmap + bytes(4)
open(sys.argv[1], "wb").write(seed)
EOF
echo "seeds: $(find "$out/seeds" -type f | wc -l) file(s)"
