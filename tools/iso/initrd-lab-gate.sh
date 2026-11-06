#!/usr/bin/env bash
# Refuse lab access material in an installer initrd unless explicitly allowed.
set -euo pipefail

[[ $# -ge 1 && $# -le 2 ]] || { echo "usage: initrd-lab-gate.sh INITRD [--lab]" >&2; exit 2; }
INITRD=$1
ALLOW_LAB=false
[[ ${2:-} == --lab ]] && ALLOW_LAB=true
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
source "$SCRIPT_DIR/../initramfs-format.sh"

listing=$(mktemp)
trap 'rm -f "$listing"' EXIT
# #1630: every concatenated archive, not only the first -- `cpio -t` stops at the
# first TRAILER!!!, the kernel unpacks what follows it. A stream the lister cannot
# read to its end is a failed inspection, not a clean one.
initramfs_decompress "$INITRD" \
    | python3 "$SCRIPT_DIR/initrd-list.py" >"$listing" \
    || { echo "build-iso: cannot inspect initrd contents" >&2; exit 1; }

# #1412: what a lab overlay leaves in the image -- the marker build_initramfs.sh
# writes for any overlay, a site hook, dropbear, an authorized_keys anywhere.
LAB_RE='(^|/)dropbear$|(^|/)authorized_keys$|(^|/)etc/imagesrv/overlay\.txt$|(^|/)etc/imagesrv/hooks\.d/[^/]+$'
# grep: 0 = found, 1 = not found (an answer), anything else = the check broke (#346, #1630).
rc=0; grep -Eq "$LAB_RE" "$listing" || rc=$?
case $rc in
    0) $ALLOW_LAB || {
           echo "build-iso: initrd contains lab access (overlay, site hook or SSH); pass --lab to build a marked lab ISO" >&2
           exit 1
       }
       echo true ;;
    1) echo false ;;
    *) echo "build-iso: cannot search the initrd listing (grep rc=$rc)" >&2; exit 1 ;;
esac
