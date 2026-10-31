#!/usr/bin/env bash
# Refuse lab access material in an installer initrd unless explicitly allowed.
set -euo pipefail

[[ $# -ge 1 && $# -le 2 ]] || { echo "usage: initrd-lab-gate.sh INITRD [--lab]" >&2; exit 2; }
INITRD=$1
ALLOW_LAB=false
[[ ${2:-} == --lab ]] && ALLOW_LAB=true

listing=$(mktemp)
trap 'rm -f "$listing"' EXIT
(zstd -dc "$INITRD" 2>/dev/null || gzip -dc "$INITRD" 2>/dev/null || cat "$INITRD") \
    | cpio -t >"$listing" 2>/dev/null \
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
