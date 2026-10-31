#!/usr/bin/env bash
#
# ImageSrv -- the clone stick (#1914): a hybrid disk image (IMG) to write to a
# USB stick with Rufus in DD mode ("DD Image") -- or dd on Linux. balenaEtcher
# failed in the field (the college, 21/10, #1937): not recommended. The machine boots it
# straight to the cloner GUI's local clone screen -- disk to disk, no server.
#
#   sudo tools/build_usb_clone.sh --out DIR [--initrd FILE] [--publish DATA_DIR]
#                                 [--skip-apt] [--no-bios] [--survey]
#
# What is in it (an MBR disk, two FAT partitions, nothing that needs a loop
# device -- every step writes into a file, never a disk):
#   1  IMGSRV_USB  (FAT32, active)  /EFI/BOOT/BOOTX64.EFI = shim, grubx64.efi,
#      mmx64.efi; /EFI/debian/grub.cfg (+ /EFI/BOOT, /boot/grub copies);
#      /imagesrv/vmlinuz + initrd.img;
#      /imagesrv-usb.json (the version the screen shows)
#   2  IMGSRV_DATA (FAT)            settings.json and reports/ -- written by
#      the agent on the stick (usbclone.sh); empty at build time
#
# Legacy BIOS (#2186): GRUB i386-pc -- boot.img in the MBR's code bytes (0..439,
# the partition table untouched) and core.img in sectors 1..2047, before
# partition 1, both patched here the way grub-bios-setup (--no-rs-codes) patches
# them. core.img finds IMGSRV_USB by its label and reads the UEFI grub.cfg itself
# (tools/usb/grub-bios.cfg): one kernel line for both. Built with USB_BIOS_LOADER=grub.
# The default is syslinux WITHOUT --stupid: measured in QEMU/SeaBIOS over usb-storage
# (#2186, 25/10) 8-10s to the kernel, against 19-29s for GRUB and >400s for --stupid
# (one sector per BIOS call, #2010). In the field (HP 8100, 25/10) it booted in IDE and
# AHCI. GRUB stays for a BIOS that hangs after the syslinux banner (HP 8000 + AHCI, #2010).
#
# The boot chain is the PXE one (deploy/setup-boot-server.sh): shim-signed and
# grub-efi-amd64-signed from Debian, the same packages -- grubx64.efi.signed
# (the disk build) where PXE takes grubnetx64.efi.signed (the network build).
# The kernel is the host's newest non-cloud Debian kernel (signed), and the
# initramfs is built against it (build_initramfs.sh --with-gui), as
# boot-payload-build.sh does. Secure Boot on or off: shim verifies GRUB, GRUB
# verifies the kernel through shim; the initramfs is not signed and need not be.
#
# The kernel line is tools/usb/{grub,syslinux}.cfg.in: imagesrv.mode=usbclone
# and nothing else of ours (principle 2). tests/test_usb_clone.py guards it.
#
# --survey (#1914/#1921, Nadav 20/10): the survey stick -- the same image plus
# the marker /imagesrv/survey on IMGSRV_USB (agent/lib/usb/usbsurvey.sh reads it;
# the kernel line does not change, principle 2), IMGSRV_DATA of 128 MiB with
# survey/, and the name imagesrv-usb-survey-<version>.img. It keeps every
# disk's SMART (no serial), so it is never --publish'ed as the console's download.
#
# Output: $OUT/imagesrv-usb-<version>.img, .img.sha256 ("<sha>  <name>"), and
# imagesrv-usb.json. --publish DATA_DIR also puts the three in
# DATA_DIR/usb/ under fixed names, where the console's download reads them
# (server/console/console_usb.py) -- each written to .new first, renamed together.

set -euo pipefail

say() { printf 'build_usb_clone: %s\n' "$*" >&2; }
die() { say "$*"; exit 1; }

REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=""; INITRD=""; PUBLISH=""; SKIP_APT=(); BIOS=1; SURVEY=0
MODULES_DIR="${IMAGESRV_MODULES_DIR:-/lib/modules}"
KERNEL_DIR="${IMAGESRV_KERNEL_DIR:-/boot}"
SHIM="${USB_SHIM:-/usr/lib/shim/shimx64.efi.signed}"
MOKMGR="${USB_MOKMGR:-/usr/lib/shim/mmx64.efi.signed}"
GRUB="${USB_GRUB:-/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed}"
GRUB_PC="${USB_GRUB_PC_DIR:-/usr/lib/grub/i386-pc}"
BIOS_LOADER="${USB_BIOS_LOADER:-syslinux}"   # #2186: syslinux (fastest, measured), or grub for a BIOS syslinux hangs on
SYSLINUX_MBR="${USB_SYSLINUX_MBR:-/usr/lib/syslinux/mbr/mbr.bin}"
# #2010 (the field, 22/10: an HP 8000 in AHCI hung after "SYSLINUX 6.04 EDD", the same stick
# booted in IDE): --stupid = one sector per BIOS read. It was the default until #2186 (25/10):
# never shown to cure the hang, and slow. USB_SYSLINUX_OPTS=--stupid still builds it, to measure.
SYSLINUX_OPTS="${USB_SYSLINUX_OPTS-}"
LABEL=IMGSRV_USB
DATA_LABEL=IMGSRV_DATA
DATA_MB=32

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)      OUT="${2:?}"; shift 2 ;;
        --initrd)   INITRD="${2:?}"; shift 2 ;;
        --publish)  PUBLISH="${2:?}"; shift 2 ;;
        --skip-apt) SKIP_APT=(--skip-apt); shift ;;
        --no-bios)  BIOS=0; shift ;;
        --survey)   SURVEY=1; shift ;;
        *) die "unknown option: $1" ;;
    esac
done
[[ -n "$OUT" ]] || die "--out DIR is required"
[[ "$SURVEY" == 0 || -z "$PUBLISH" ]] || die "--survey is never published: the console's download is the regular stick"
[[ "$SURVEY" == 0 ]] || DATA_MB=128

for tool in mformat mcopy mmd mdir mtype sfdisk dd od sha256sum python3 git cpio gzip zstd stat truncate; do
    command -v "$tool" >/dev/null || die "missing $tool (apt install mtools fdisk coreutils python3 git)"
done
for f in "$SHIM" "$GRUB"; do
    [[ -s "$f" ]] || die "missing $f -- apt install shim-signed grub-efi-amd64-signed (the PXE boot chain's packages)"
done
if [[ "$BIOS" == 1 && "$BIOS_LOADER" == grub ]]; then
    command -v grub-mkimage >/dev/null || die "missing grub-mkimage (apt install grub-common grub-pc-bin), or --no-bios for UEFI only"
    for f in boot.img diskboot.img moddep.lst; do
        [[ -s "$GRUB_PC/$f" ]] || die "missing $GRUB_PC/$f (apt install grub-pc-bin), or --no-bios for UEFI only"
    done
elif [[ "$BIOS" == 1 && "$BIOS_LOADER" == syslinux ]]; then
    command -v syslinux >/dev/null || die "missing syslinux (apt install syslinux syslinux-common), or --no-bios for UEFI only"
    [[ -s "$SYSLINUX_MBR" ]] || die "missing $SYSLINUX_MBR (apt install syslinux-common), or --no-bios for UEFI only"
elif [[ "$BIOS" == 1 ]]; then
    die "USB_BIOS_LOADER is grub or syslinux, not '$BIOS_LOADER'"
fi

# --- version, kernel ---------------------------------------------------------------
VERSION=$(git -C "$REPO" describe --tags --always --dirty) || die "git describe failed in $REPO"
[[ "$VERSION" =~ ^[A-Za-z0-9._+-]+$ ]] || die "version is not a plain tag: '$VERSION'"
EPOCH=$(git -C "$REPO" log -1 --format=%ct) || die "git log failed in $REPO"
KVER=$(find "$MODULES_DIR" -mindepth 1 -maxdepth 1 -printf '%f\n' | awk '$0 !~ /cloud/' | sort -V | tail -n1)
[[ -n "$KVER" ]] || die "no non-cloud kernel in $MODULES_DIR (apt install linux-image-amd64)"
VMLINUZ="$KERNEL_DIR/vmlinuz-$KVER"
[[ -s "$VMLINUZ" ]] || die "missing $VMLINUZ"
say "version $VERSION, kernel $KVER"

install -d "$OUT"; OUT=$(cd "$OUT" && pwd)
WORK="$OUT/work"; rm -rf "$WORK"; install -d "$WORK"

# --- the initramfs --------------------------------------------------------------------
if [[ -z "$INITRD" ]]; then
    [[ "$(id -u)" -eq 0 ]] || die "building the initramfs needs root (or pass --initrd FILE)"
    INITRD="$WORK/initrd.img"
    say "building the GUI initramfs (build_initramfs.sh --with-gui) -- log: $WORK/initramfs.log"
    # #1937: + the old ATI/AMD (R600..Evergreen) microcode -- an HP 6005 had no screen without it
    bash "$REPO/tools/build_initramfs.sh" "${SKIP_APT[@]}" --with-gui --radeon-legacy-firmware --kernel-version "$KVER" \
        --source-date-epoch "$EPOCH" --version "$VERSION" --output "$INITRD" > "$WORK/initramfs.log" 2>&1 \
        || { tail -n 30 "$WORK/initramfs.log" >&2; die "build_initramfs.sh failed"; }
fi
[[ -s "$INITRD" ]] || die "the initramfs is missing or empty: $INITRD"
# its modules must be this kernel's -- another kernel's modules do not load, and
# that looks like "no disks" on the machine (build-iso.sh, the same check)
ikver=$( (zstd -dc "$INITRD" 2>/dev/null || gzip -dc "$INITRD" 2>/dev/null || cat "$INITRD") \
    | cpio -t 2>/dev/null | grep -m1 -o '^lib/modules/[^/]*' | cut -d/ -f3 || true)
[[ "$ikver" == "$KVER" ]] || die "the initramfs modules are for '${ikver:-none found}', the kernel is $KVER"

# --- the boot partition (a FAT32 image in a file) ------------------------------------------
STAGE="$WORK/esp"; install -d "$STAGE"
sed "s/@VERSION@/$VERSION/g" "$REPO/tools/usb/grub.cfg.in" > "$STAGE/grub.cfg"
sed "s/@VERSION@/$VERSION/g" "$REPO/tools/usb/syslinux.cfg.in" > "$STAGE/syslinux.cfg"
grep -q '^ *linux /imagesrv/vmlinuz imagesrv.mode=usbclone ' "$STAGE/grub.cfg" || die "grub.cfg has no usbclone kernel line"
if grep -hv '^ *#' "$STAGE/grub.cfg" "$STAGE/syslinux.cfg" | grep -q 'imagesrv\.server'; then die "imagesrv.server on the stick's kernel line"; fi
python3 - "$STAGE/imagesrv-usb.json" "$VERSION" "$KVER" "$EPOCH" "$(sha256sum "$INITRD" | cut -d' ' -f1)" "$SURVEY" <<'PY'
import json, sys
out, ver, kver, epoch, isha, survey = sys.argv[1:7]
json.dump({"product": "ImageSrv USB", "version": ver, "kernel": kver, "source_date_epoch": int(epoch),
           "initrd_sha256": isha, "label": "IMGSRV_USB", "data_label": "IMGSRV_DATA", "survey": survey == "1"},
          open(out, "w", encoding="utf-8"), indent=1)
PY

payload=$(( $(stat -c %s "$VMLINUZ") + $(stat -c %s "$INITRD") + $(stat -c %s "$SHIM") + $(stat -c %s "$GRUB") ))
ESP_MB=$(( payload * 13 / 10 / 1048576 + 64 ))
(( ESP_MB >= 64 )) || ESP_MB=64
ESP_SECT=$(( ESP_MB * 2048 )); ESP_START=2048
DATA_START=$(( ESP_START + ESP_SECT )); DATA_SECT=$(( DATA_MB * 2048 ))
TOTAL_SECT=$(( DATA_START + DATA_SECT + 2048 ))
say "partition 1 $LABEL ${ESP_MB}MiB, partition 2 $DATA_LABEL ${DATA_MB}MiB"

ESP="$WORK/esp.img"; DATA="$WORK/data.img"
truncate -s $(( ESP_SECT * 512 )) "$ESP"
# -H: hidden sectors = the partition's start; syslinux's boot sector reads it
mformat -i "$ESP" -F -c 1 -v "$LABEL" -N 19140001 -H "$ESP_START" -T "$ESP_SECT" -h 64 -s 32 :: \
    || die "mformat of the boot partition failed"
mmd -i "$ESP" ::/EFI ::/EFI/BOOT ::/EFI/debian ::/boot ::/boot/grub ::/imagesrv
mcopy -i "$ESP" "$SHIM" ::/EFI/BOOT/BOOTX64.EFI
mcopy -i "$ESP" "$GRUB" ::/EFI/BOOT/grubx64.efi
if [[ -s "$MOKMGR" ]]; then mcopy -i "$ESP" "$MOKMGR" ::/EFI/BOOT/mmx64.efi
else say "WARNING: no $MOKMGR -- MokManager not on the stick (only needed to enrol a key by hand)"; fi
for d in EFI/debian EFI/BOOT boot/grub; do mcopy -i "$ESP" "$STAGE/grub.cfg" "::/$d/grub.cfg"; done
mcopy -i "$ESP" "$VMLINUZ" ::/imagesrv/vmlinuz
mcopy -i "$ESP" "$INITRD" ::/imagesrv/initrd.img
mcopy -i "$ESP" "$STAGE/imagesrv-usb.json" ::/imagesrv-usb.json
if [[ "$SURVEY" == 1 ]]; then   # the marker usbsurvey.sh looks for
    printf 'ImageSrv USB survey stick (#1921): each disk SMART is kept on IMGSRV_DATA/survey, no serial.\r\n' > "$STAGE/survey"
    mcopy -i "$ESP" "$STAGE/survey" ::/imagesrv/survey
fi
if [[ "$BIOS" == 1 && "$BIOS_LOADER" == syslinux ]]; then
    mmd -i "$ESP" ::/syslinux
    mcopy -i "$ESP" "$STAGE/syslinux.cfg" ::/syslinux/syslinux.cfg
    # shellcheck disable=SC2086  # SYSLINUX_OPTS: zero or more flags, word-split on purpose
    syslinux --install $SYSLINUX_OPTS --directory /syslinux "$ESP" || die "syslinux --install failed"
fi

truncate -s $(( DATA_SECT * 512 )) "$DATA"
mformat -i "$DATA" -v "$DATA_LABEL" -N 19140002 -H "$DATA_START" -T "$DATA_SECT" -h 64 -s 32 :: \
    || die "mformat of the data partition failed"
mmd -i "$DATA" ::/reports
[[ "$SURVEY" == 0 ]] || mmd -i "$DATA" ::/survey
printf 'ImageSrv USB %s -- settings.json and reports/ are written here by the machine.\r\n%s\r\n' "$VERSION" \
    'To write a new version: Rufus, "DD Image" mode (balenaEtcher failed in the field, #1937). It erases the whole stick.' > "$STAGE/README.txt"
mcopy -i "$DATA" "$STAGE/README.txt" ::/README.txt

# --- the disk ------------------------------------------------------------------------------------
NAME="imagesrv-usb-$VERSION.img"; [[ "$SURVEY" == 0 ]] || NAME="imagesrv-usb-survey-$VERSION.img"; IMG="$OUT/$NAME"
rm -f "$IMG"; truncate -s $(( TOTAL_SECT * 512 )) "$IMG"
sfdisk --no-reread --no-tell-kernel "$IMG" > "$WORK/sfdisk.log" 2>&1 <<EOF || { cat "$WORK/sfdisk.log" >&2; die "sfdisk failed"; }
label: dos
label-id: 0x19141914
unit: sectors

start=$ESP_START, size=$ESP_SECT, type=c, bootable
start=$DATA_START, size=$DATA_SECT, type=c
EOF
dd if="$ESP" of="$IMG" bs=512 seek="$ESP_START" conv=notrunc,sparse status=none
dd if="$DATA" of="$IMG" bs=512 seek="$DATA_START" conv=notrunc,sparse status=none
if [[ "$BIOS" == 1 && "$BIOS_LOADER" == syslinux ]]; then
    dd if="$SYSLINUX_MBR" of="$IMG" bs=440 count=1 conv=notrunc status=none
elif [[ "$BIOS" == 1 ]]; then
    # #2186: what grub-bios-setup --no-rs-codes writes (grub util/setup.c), but into the file --
    # that tool opens the host's own disk to guess a root device, and needs root for it
    CORE="$WORK/core.img"
    grub-mkimage -O i386-pc -d "$GRUB_PC" -o "$CORE" -p /boot/grub -c "$REPO/tools/usb/grub-bios.cfg" \
        biosdisk part_msdos fat normal linux search configfile echo || die "grub-mkimage (i386-pc) failed"
    nsec=$(( ($(stat -c %s "$CORE") + 511) / 512 ))
    (( nsec >= 2 && nsec < ESP_START )) || die "core.img is $nsec sectors: it must fit in sectors 1..$(( ESP_START - 1 ))"
    truncate -s $(( nsec * 512 )) "$CORE"
    # shellcheck disable=SC2059  # $2 is the bytes, as printf escapes
    put() { printf "$2" | dd of="$3" bs=1 seek="$1" conv=notrunc status=none; }
    # diskboot's blocklist, the end of core's first sector: the rest is nsec-1 sectors from LBA 2
    put 508 "$(printf '\\%03o\\%03o' $(( (nsec - 1) & 255 )) $(( (nsec - 1) >> 8 )))" "$CORE"
    dd if="$CORE" of="$IMG" bs=512 seek=1 conv=notrunc status=none
    # boot.img's jump (0..2) and code (90..439); 3..89 (a BPB) and 440.. (the disk id and the
    # partition table sfdisk wrote) stay as they are
    dd if="$GRUB_PC/boot.img" of="$IMG" bs=1 count=3 conv=notrunc status=none
    dd if="$GRUB_PC/boot.img" of="$IMG" bs=1 skip=90 seek=90 count=350 conv=notrunc status=none
    put 92 '\001\0\0\0\0\0\0\0' "$IMG"   # the kernel sector: core.img at LBA 1
    put 100 '\377' "$IMG"                # boot drive 0xff: the one the BIOS booted from (DL)
    put 102 '\220\220' "$IMG"            # nop the jump over the drive check (no --allow-floppy)
fi

# --- read back from the image itself (principle 5: evidence, not exit codes) ------------
if [[ "$BIOS" == 1 && "$BIOS_LOADER" == grub ]]; then
    patched="$(od -A n -t x1 -j 92 -N 9 "$IMG" | tr -d ' \n')|$(od -A n -t x1 -j 102 -N 2 "$IMG" | tr -d ' \n')"
    [[ "$patched" == "0100000000000000ff|9090" ]] || die "the GRUB MBR does not read back: $patched"
    cmp -s <(dd if="$IMG" bs=1 skip=104 count=336 status=none) <(dd if="$GRUB_PC/boot.img" bs=1 skip=104 count=336 status=none) \
        || die "boot.img's code is not in the MBR"
    cmp -s <(dd if="$IMG" bs=512 skip=1 count="$nsec" status=none) "$CORE" || die "core.img is not in sectors 1..$nsec"
fi
layout=$(sfdisk -d "$IMG")
grep -q "^label-id: 0x19141914$" <<<"$layout" || die "the disk id is not as written: $layout"
grep -q "start= *$ESP_START, size= *$ESP_SECT, type=c, bootable" <<<"$layout" || die "partition 1 not as written: $layout"
grep -q "start= *$DATA_START, size= *$DATA_SECT, type=c" <<<"$layout" || die "partition 2 not as written: $layout"
listing=$(mdir -i "$IMG@@$(( ESP_START * 512 ))" -b ::/EFI/BOOT) || die "the boot partition does not read back"
for f in BOOTX64.EFI grubx64.efi grub.cfg; do grep -qi "/$f\$" <<<"$listing" || die "$f missing from /EFI/BOOT"; done
cmp -s <(mtype -i "$IMG@@$(( ESP_START * 512 ))" ::/EFI/BOOT/BOOTX64.EFI) "$SHIM" || die "shim in the image differs from $SHIM"
mdir -i "$IMG@@$(( DATA_START * 512 ))" ::/ > /dev/null || die "the data partition does not read back"
# the survey marker: on the survey stick, and only there (the regular stick keeps nothing about disks)
if mtype -i "$IMG@@$(( ESP_START * 512 ))" ::/imagesrv/survey > /dev/null 2>&1; then got_mark=1; else got_mark=0; fi
[[ "$got_mark" == "$SURVEY" ]] || die "survey marker on the stick: $got_mark, wanted $SURVEY"

SHA=$(sha256sum "$IMG" | cut -d' ' -f1)
printf '%s  %s\n' "$SHA" "$NAME" > "$IMG.sha256"
python3 - "$OUT/imagesrv-usb.json" "$STAGE/imagesrv-usb.json" "$NAME" "$SHA" "$(stat -c %s "$IMG")" "$BIOS" <<'PY'
import json, sys, time
out, inner, name, sha, size, bios = sys.argv[1:7]
d = json.load(open(inner, encoding="utf-8"))
d.update({"file": name, "sha256": sha, "size": int(size), "bios": bios == "1",
          "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
json.dump(d, open(out, "w", encoding="utf-8"), indent=1)
PY
say "ready: $IMG ($(( $(stat -c %s "$IMG") / 1048576 )) MiB)"
cat "$IMG.sha256"

if [[ -n "$PUBLISH" ]]; then
    dst="$PUBLISH/usb"; install -d "$dst"
    cp "$IMG" "$dst/imagesrv-usb.img.new"
    cp "$IMG.sha256" "$dst/imagesrv-usb.img.sha256.new"
    cp "$OUT/imagesrv-usb.json" "$dst/imagesrv-usb.json.new"
    got=$(sha256sum "$dst/imagesrv-usb.img.new" | cut -d' ' -f1)
    [[ "$got" == "$SHA" ]] || die "the copy in $dst does not match ($got != $SHA)"
    mv -f "$dst/imagesrv-usb.img.new" "$dst/imagesrv-usb.img"
    mv -f "$dst/imagesrv-usb.img.sha256.new" "$dst/imagesrv-usb.img.sha256"
    mv -f "$dst/imagesrv-usb.json.new" "$dst/imagesrv-usb.json"
    say "published to $dst (the console's download)"
fi
rm -rf "$WORK"
