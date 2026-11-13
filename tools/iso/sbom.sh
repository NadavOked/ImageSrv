#!/usr/bin/env bash
# sbom.sh -- #2999: a software bill of materials for one ISO, in SPDX and CycloneDX.
#
#   tools/iso/sbom.sh --iso imagesrv-v0.93.0-amd64.iso --out DIR
#
# What it lists: everything a customer's installed server gets from the ISO -- the
# Debian packages in pool/ (and the live files), the Python packages and the
# server tree on the ISO, and the files of the live initramfs (the agent's
# binaries). It scans the extracted ISO plus the unpacked live initrd with syft,
# one scan, so the two formats describe the same set.
#
# The SBOM belongs to ONE ISO: the sha256 of the ISO is written next to it
# (`<name>.sbom.txt`) and recomputed here. An SBOM whose ISO hash does not match
# is the one failure that makes the file worse than no file, so a mismatch with
# `<iso>.sha256` stops the tool.
#
# Needs: xorriso, cpio, gzip or zstd, python3, and syft (IMAGESRV_SYFT = the
# binary; nothing is downloaded here). Free, MIT-licensed tooling only.
set -euo pipefail

SYFT_VERSION="1.55.0"   # the version the output was checked with; another one is another check

die() { echo "error: $*" >&2; exit 1; }
usage() { echo "Usage: sbom.sh --iso FILE.iso --out DIR   (env IMAGESRV_SYFT = syft $SYFT_VERSION)"; }

iso=""; out=""
while (($#)); do
    case "$1" in
        --iso) (($# >= 2)) || die "--iso requires a path"; iso=$2; shift 2 ;;
        --out) (($# >= 2)) || die "--out requires a directory"; out=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[[ -n "$iso" && -n "$out" ]] || { usage >&2; exit 2; }
[[ -r "$iso" && "$iso" == *.iso ]] || die "--iso must be a readable .iso file: $iso"
syft=${IMAGESRV_SYFT:-syft}
command -v "$syft" >/dev/null || die "syft is not installed (IMAGESRV_SYFT): no SBOM can be made"
for tool in xorriso cpio python3 sha256sum; do command -v "$tool" >/dev/null || die "$tool is required"; done
have=$("$syft" version </dev/null | awk '$1 == "Version:" {print $2}') || die "'$syft version' failed"
[[ "$have" == "$SYFT_VERSION" ]] || die "syft is '$have', this tool was checked with $SYFT_VERSION"

name=$(basename "$iso" .iso)
sha=$(sha256sum "$iso" | cut -d' ' -f1) || die "could not hash $iso"
if [[ -r "$iso.sha256" ]]; then
    want=$(awk '{print $1; exit}' "$iso.sha256")
    [[ "$want" == "$sha" ]] || die "$iso has sha256 $sha, but $iso.sha256 says $want: this is not the ISO that was tested"
else
    echo "warning: no $iso.sha256 next to the ISO; the hash below is computed, not compared" >&2
fi

work=$(mktemp -d "${TMPDIR:-/tmp}/sbom.XXXXXX")
trap 'chmod -R u+w "$work" 2>/dev/null; rm -rf "$work"' EXIT
mkdir -p "$work/scan/iso" "$work/scan/live-initrd" "$out"

# xorriso -osirrox: read-only extraction, no mount and no root.
xorriso -osirrox on -indev "$iso" -extract / "$work/scan/iso" >"$work/xorriso.log" 2>&1 \
    || { tail -5 "$work/xorriso.log" >&2; die "could not extract $iso"; }
debs=$(find "$work/scan/iso" -name '*.deb' | wc -l)
((debs > 0)) || die "no .deb under pool/ in $iso: the extraction or the ISO is not what this tool expects"

# The live initrd: the agent's binaries and the toolbox live there, not in pool/.
initrd="$work/scan/iso/live/initrd.img"
[[ -r "$initrd" ]] || die "live/initrd.img is not on the ISO"
magic=$(head -c 4 "$initrd" | od -An -tx1 | tr -d ' \n')
case "$magic" in
    1f8b*)    unpack=(gzip -dc) ;;
    28b52ffd) unpack=(zstd -dc) ;;
    *) die "live/initrd.img has an unknown compression (magic $magic)" ;;
esac
( cd "$work/scan/live-initrd" && "${unpack[@]}" "$initrd" | cpio -idm --no-absolute-filenames >"$work/cpio.log" 2>&1 ) \
    || { tail -5 "$work/cpio.log" >&2; die "could not unpack live/initrd.img"; }
[[ -n "$(ls -A "$work/scan/live-initrd")" ]] || die "live/initrd.img unpacked to nothing"

spdx="$out/$name.spdx.json"; cdx="$out/$name.cdx.json"
SYFT_CHECK_FOR_APP_UPDATE=false "$syft" scan "dir:$work/scan" --source-name "$name" \
    -o "spdx-json=$spdx" -o "cyclonedx-json=$cdx" >"$work/syft.log" 2>&1 \
    || { tail -10 "$work/syft.log" >&2; die "syft failed"; }

# Positive evidence (principle 5): both files parse, are the right format, and the
# deb count is not a rounding error against the .deb files that are really on the ISO.
counts=$(python3 -I - "$spdx" "$cdx" "$debs" <<'EOF'
import json, sys
spdx, cdx, debs = json.load(open(sys.argv[1], encoding="utf-8")), json.load(open(sys.argv[2], encoding="utf-8")), int(sys.argv[3])
assert str(spdx.get("spdxVersion", "")).startswith("SPDX-"), "not an SPDX document"
assert cdx.get("bomFormat") == "CycloneDX", "not a CycloneDX document"
pk, comp = spdx.get("packages", []), cdx.get("components", [])
deb = [c for c in comp if str(c.get("purl", "")).startswith("pkg:deb/")]
assert pk and comp, "an empty SBOM"
assert len(deb) >= debs * 0.9, f"{len(deb)} deb components for {debs} .deb files on the ISO"
print(len(pk), len(comp), len(deb))
EOF
) || die "the SBOM does not describe the ISO (see above): $counts"
read -r n_spdx n_cdx n_deb <<<"$counts"

cat > "$out/$name.sbom.txt" <<EOF
iso: $(basename "$iso")
iso_sha256: $sha
syft: $SYFT_VERSION
generated_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)
deb_files_on_iso: $debs
spdx_packages: $n_spdx
cyclonedx_components: $n_cdx
cyclonedx_deb_components: $n_deb
files: $name.spdx.json $name.cdx.json
EOF
echo "sbom: $name -- $n_spdx SPDX packages, $n_cdx CycloneDX components ($n_deb deb of $debs .deb files), iso sha256 $sha"
