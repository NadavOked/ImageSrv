#!/bin/sh
# What native-gui would add to the initramfs, measured the way
# tools/build_initramfs.sh packs a binary: the executable, every shared
# library ldd resolves for it, the font faces the page asks for, and the
# fontconfig file -- then cpio + zstd like the image itself.
# Run on the Debian 13 VM after `make`. No root needed.
set -eu
cd "$(dirname "$0")/.."
[ -x ./imagesrv-station-gui ] || { echo "build first: make" >&2; exit 1; }

root=$(mktemp -d "${TMPDIR:-/tmp}/native-gui-size.XXXXXX")
trap 'rm -rf "$root" "$root.cpio" "$root.cpio.zst"' EXIT
mkdir -p "$root/usr/bin" "$root/etc/imagesrv"
cp ./imagesrv-station-gui "$root/usr/bin/"
cp fonts.conf "$root/etc/imagesrv/fonts.conf"

# Same awk as copy_libs() in tools/build_initramfs.sh.
ldd ./imagesrv-station-gui | awk '/=>/ { print $3 } /^\s*\// { print $1 }' | while read -r lib; do
    [ -f "$lib" ] || continue
    mkdir -p "$root$(dirname "$lib")"
    cp -Ln "$lib" "$root$lib"
done

# Fonts: only the faces index.html loads. fonts-ibm-plex (contrib) puts
# them under /usr/share/fonts/truetype/ibm-plex (#120); the exact file
# names are looked up, and a face that is not found stops the measurement
# instead of quietly measuring a smaller image.
fontdir=/usr/share/fonts/truetype/ibm-plex
[ -d "$fontdir" ] || { echo "$fontdir not found: install fonts-ibm-plex (contrib)" >&2; exit 1; }
mkdir -p "$root$fontdir"
for face in IBMPlexSansHebrew-Regular IBMPlexSansHebrew-Medium IBMPlexSansHebrew-SemiBold \
            IBMPlexSansHebrew-Bold IBMPlexMono-Regular IBMPlexMono-Medium; do
    src=$(find "$fontdir" -iname "$face.ttf" | head -n 1)
    [ -n "$src" ] || { echo "face not found under $fontdir: $face.ttf" >&2; exit 1; }
    cp "$src" "$root$fontdir/"
done

echo "== files (largest last)"
find "$root" -type f -exec du -k {} + | sort -n | awk -v r="$root" '{ sub(r, "", $2); printf "%8d kB  %s\n", $1, $2 }'
echo "== uncompressed total"
du -sk "$root" | awk '{ printf "%d kB\n", $1 }'

echo "== compressed (cpio newc + zstd -19, as the initramfs)"
(cd "$root" && find . | cpio -o -H newc > "$root.cpio")
zstd -19 -q -f "$root.cpio" -o "$root.cpio.zst"
size=$(stat -c %s "$root.cpio.zst")
[ "$size" -gt 0 ] || { echo "compressed size is 0 -- the measurement itself failed" >&2; exit 1; }
echo "$size bytes ($((size / 1024)) kB) over the network per machine"
