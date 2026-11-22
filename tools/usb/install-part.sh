# shellcheck shell=bash
# install-part.sh -- #2062: the stick's third partition, the server's install ISO.
# Sourced by tools/build_usb_clone.sh (--server-iso FILE); uses its say/die.
#
# Nadav 12/11: one stick clones and installs a server, and the packages are on the
# stick -- no internet at install time. The partition IS the ISO, byte for byte
# (label IMAGESRV_INSTALL, iso9660, MBR type 83): the same file tools/iso/
# install-test.py installs from end to end at every release, with its permissions,
# links and the .git of imagesrv-src -- nothing a FAT copy would keep. The agent
# mounts it read-only on /cdrom (agent/lib/usb/usbinstall.sh) and the live installer
# in the stick's own initramfs runs from it.
#
# Every check here stops the build (principle 5): a stick with a partition the
# button cannot install from is worse than a stick without one.

INSTALL_LABEL=IMAGESRV_INSTALL

usb_iso_check() {
    # $1 = the ISO, $2 = the stick's version, $3 = 1 when --lab-iso allows another version.
    # Sets ISO_TAG ISO_SHA ISO_BYTES ISO_SECT (512-byte sectors, whole MiB).
    local iso=$1 version=$2 any=$3 volid man lab
    [[ -s "$iso" ]] || die "--server-iso is not a file: $iso"
    [[ "$(basename "$iso")" =~ ^[A-Za-z0-9._+-]+\.iso$ ]] || die "--server-iso: the file name is not a plain <name>.iso: $(basename "$iso")"
    command -v xorriso >/dev/null || die "missing xorriso (apt install xorriso) -- needed to read --server-iso"
    volid=$(xorriso -indev "$iso" -pvd_info 2>/dev/null | sed -n 's/^Volume Id *: *//p') \
        || die "xorriso could not read $iso"
    [[ "$volid" == "$INSTALL_LABEL" ]] \
        || die "$iso is labelled '${volid:-nothing read}', not $INSTALL_LABEL -- not an ImageSrv server ISO (tools/iso/build-iso.sh)"
    man="$WORK/imagesrv-iso.json"; rm -f "$man"
    xorriso -osirrox on -indev "$iso" -extract /imagesrv-iso.json "$man" >/dev/null 2>&1 \
        || die "$iso has no imagesrv-iso.json -- not an ImageSrv server ISO"
    [[ -s "$man" ]] || die "$iso has no imagesrv-iso.json -- not an ImageSrv server ISO"
    ISO_TAG=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["imagesrv_tag"])' "$man") \
        || die "imagesrv_tag could not be read from the ISO's manifest"
    lab=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("lab_overlay") is False)' "$man") \
        || die "lab_overlay could not be read from the ISO's manifest"
    [[ "$ISO_TAG" =~ ^[A-Za-z0-9._+-]+$ ]] || die "the ISO's version is not a plain tag: '$ISO_TAG'"
    [[ "$lab" == True ]] || die "$iso is a lab ISO (lab_overlay is not false) -- a stick is built from a clean release ISO only"
    if [[ "$ISO_TAG" != "$version" ]]; then
        [[ "$any" == 1 ]] || die "the ISO is $ISO_TAG and the stick is $version -- the installer in the stick's initramfs and the packages must be one release (--lab-iso builds it anyway, for the lab only)"
        say "WARNING: --lab-iso -- the ISO is $ISO_TAG, the stick is $version: a lab stick, never a release"
    fi
    ISO_BYTES=$(stat -c %s "$iso")
    ISO_SHA=$(sha256sum "$iso" | cut -d' ' -f1)
    # shellcheck disable=SC2034  # read by build_usb_clone.sh, which sources this file
    ISO_SECT=$(( (ISO_BYTES + 1048575) / 1048576 * 2048 ))
    say "server ISO $ISO_TAG: $(( ISO_BYTES / 1048576 )) MiB, sha256 $ISO_SHA"
}

usb_iso_initrd_check() {
    # $1 = the initramfs. The button execs the live installer out of this image: without
    # it (an --initrd built without --installer) the partition is dead weight.
    local list want
    # shellcheck source=tools/initramfs-format.sh
    source "$REPO/tools/initramfs-format.sh"
    list=$(initramfs_decompress "$1" | cpio -t 2>/dev/null) || die "the initramfs could not be listed: $1"
    for want in usr/lib/imagesrv/installer/imagesrv-installer usr/lib/imagesrv/installer/imagesrv-install \
                usr/lib/imagesrv/installer/gui-bridge.sh usr/lib/imagesrv/usbinstall.sh usr/bin/imagesrv-station-gui; do
        grep -qxF -e "$want" -e "./$want" <<<"$list" \
            || die "the initramfs has no $want -- with --server-iso it must be built with --installer --with-gui (build_initramfs.sh)"
    done
}

usb_iso_readback() {
    # $1 = the image, $2 = the partition's first sector, $3 = the ISO. What is in the image is
    # the ISO, byte for byte. cmp on the two files, not `tail | head | sha256sum`: head ends at
    # the ISO's last byte, the reader before it dies of SIGPIPE on the partition's padding, and
    # under pipefail that is exit 141 -- a build that fails on a stick that is whole (the first
    # real build, 12/11). 1 = the bytes differ, 2 = cmp could not read: neither is a stick.
    local rc=0 label
    cmp -s -n "$ISO_BYTES" -i "$(( $2 * 512 )):0" "$1" "$3" || rc=$?
    [[ "$rc" == 0 ]] || die "partition 3 does not read back as the ISO (cmp exit $rc: 1 = the bytes differ, 2 = not read)"
    # the label where the agent's blkid reads it: the primary volume descriptor, sector 16 of the partition
    label=$(dd if="$1" bs=1 skip=$(( $2 * 512 + 32808 )) count=32 status=none | tr -d ' \0')
    [[ "$label" == "$INSTALL_LABEL" ]] || die "partition 3's volume label reads back as '$label', not $INSTALL_LABEL"
}
