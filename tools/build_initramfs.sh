#!/bin/bash
# build_initramfs.sh -- builds the ImageSrv agent initramfs on a Debian
# machine (the boot server itself is fine). The output is a cpio.gz that
# GRUB loads after the distro kernel; nothing in it needs signing.
#
# Usage:
#   sudo ./tools/build_initramfs.sh [--output FILE] [--kernel-version VER]
#                                   [--firmware DIR]...
#                                   [--with-gui] [--installer] [--skip-apt]
#                                   [--radeon-legacy-firmware]
#                                   [--tools-selection FILE]
#                                   [--source-date-epoch SECONDS]
#                                   [--version VERSION]
#
# The output is reproducible (#1125, feeding #1078): the same checkout and
# the same builder give the same bytes. Every mtime in the image is set to
# SOURCE_DATE_EPOCH -- the flag, the environment variable, or the time of
# the commit the checkout is at, in that order.
#
# IMAGESRV_EXTRA_OVERLAY=<dir> (environment, optional) is the lab/site
# overlay (#1266): files the product does not know about, copied over the
# image just before packing -- see "site/lab overlay" near the end.
#
# --tools-selection packs the IT toolbox the operator chose in the console
# (#1050): FILE is <data_dir>/tools-selection.json (interfaces.md §23). Its
# "build" ids are mapped to binaries through agent/lib/tools/toolbins.sh -- the
# same table the station filters by -- and the file itself rides into the
# initrd as /etc/imagesrv/tools-selection.json. Without the flag no toolbox
# binary beyond the base set is packed and the tools screen is empty.
#
# The agent scripts are taken from the agent/ directory next to this
# repository checkout.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
AGENT_DIR="$SCRIPT_DIR/../agent"
OUTPUT="$PWD/imagesrv-initramfs.cpio.gz"
KVER="$(uname -r)"
SKIP_APT=0
WITH_GUI=0
WITH_INSTALLER=0
TOOLS_SELECTION_FILE=""
SOURCE_VERSION=""
#: `auto` (ברירת המחדל — `finish_and_stop` גוזר מהתפקיד: כיתה=reboot,
#: בנייה/שיכפול=poweroff), `poweroff` או `reboot`. `reboot` הוא ההגדרה
#: שכלי המעבדה כותב (tools/lab/after-task-reboot.sh).
AFTER_TASK=auto
FIRMWARE_DIRS=("rtl_nic")
RADEON_LEGACY_FW=0

while [ $# -gt 0 ]; do
    case "$1" in
        --output)         OUTPUT="$2"; shift 2 ;;
        --kernel-version) KVER="$2"; shift 2 ;;
        --firmware)       FIRMWARE_DIRS+=("$2"); shift 2 ;;
        --after-task)     AFTER_TASK="$2"; shift 2 ;;
        --with-gui)       WITH_GUI=1; shift ;;
        --radeon-legacy-firmware) RADEON_LEGACY_FW=1; shift ;;   # #1937: the USB stick's
        --installer)      WITH_INSTALLER=1; shift ;;
        --skip-apt)       SKIP_APT=1; shift ;;
        --tools-selection) TOOLS_SELECTION_FILE="$2"; shift 2 ;;
        --source-date-epoch) SOURCE_DATE_EPOCH="$2"; shift 2 ;;
        --version)        SOURCE_VERSION="$2"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root (file ownership in the cpio)" >&2; exit 1; }
[ -f "$AGENT_DIR/init" ] || { echo "agent/ not found next to tools/" >&2; exit 1; }

# The overlay is checked here, before apt and the compiles: a typo in the
# path should cost a second, not the whole build. Set but not a directory
# is an error, never "no overlay" -- that would be a lab image built
# without the lab's files and looking exactly like one built with them.
EXTRA_OVERLAY="${IMAGESRV_EXTRA_OVERLAY:-}"
if [ -n "$EXTRA_OVERLAY" ]; then
    [ -d "$EXTRA_OVERLAY" ] \
        || { echo "IMAGESRV_EXTRA_OVERLAY is not a directory: $EXTRA_OVERLAY" >&2; exit 1; }
fi

# --- the toolbox selection (--tools-selection, #1050) ---------------------------
# Resolved here, before apt and the compiles, for the same reason as the
# overlay: an id the table does not know should cost a second, not a build.
# tools_bins_plan (agent/lib/tools/toolbins.sh -- POSIX sh, bash sources it fine)
# prints one binary per line and exits 1 naming every unknown id; an empty
# "build" list is a valid answer and packs nothing.
TOOL_BINS=()
if [ -n "$TOOLS_SELECTION_FILE" ]; then
    [ -f "$TOOLS_SELECTION_FILE" ] \
        || { echo "tools selection file not found: $TOOLS_SELECTION_FILE" >&2; exit 1; }
    . "$AGENT_DIR/lib/tools/toolbins.sh"
    _plan=$(tools_bins_plan "$TOOLS_SELECTION_FILE") \
        || { echo "--tools-selection: the selection is not packable (see above)" >&2; exit 1; }
    [ -z "$_plan" ] || mapfile -t TOOL_BINS <<< "$_plan"
    echo "tools: packing ${#TOOL_BINS[@]} toolbox binaries: ${TOOL_BINS[*]:-(none)}"
fi

# The Debian 13 package for each conditionally packed binary. Toolbox entries
# come from --tools-selection; the four installer entries come only from
# --installer. A planned binary missing from this map is a build error.
declare -A TOOL_PKG=(
    [blkdiscard]=util-linux  [wipefs]=util-linux   [hdparm]=hdparm
    [sgdisk]=gdisk           [nvme]=nvme-cli       [nwipe]=nwipe
    [tpm2_clear]=tpm2-tools  [chntpw]=chntpw       [ntfscat]=ntfs-3g
    [ntfsundelete]=ntfs-3g   [photorec]=testdisk   [testdisk]=testdisk
    [scalpel]=scalpel        [foremost]=foremost   [extundelete]=extundelete
    [magicrescue]=magicrescue [recoverjpeg]=recoverjpeg
    [unsquashfs]=squashfs-tools [bsdtar]=libarchive-tools
    [ddrescue]=gddrescue     [safecopy]=safecopy   [fsck.vfat]=dosfstools
)
# --installer only: binaries needed by installer/, never by cloner images.
# A table of its own: TOOL_PKG is the toolbox (#1050) and moves with
# tools/toolbins.sh; these are the live installer's (#1190).
declare -A INSTALLER_PKG=(
    [debootstrap]=debootstrap [mkfs.vfat]=dosfstools [chroot]=coreutils
    [mkfs.ext4]=e2fsprogs [dpkg-deb]=dpkg [eject]=eject [perl]=perl-base
    [dash]=dash [tar]=tar [mdadm]=mdadm
)
if [ "$WITH_INSTALLER" -eq 1 ]; then
    # eject (#1190): "remove the media and restart" -- the bridge ejects the ISO
    # device on a VM; blkid is already in BINARIES (the media is found by label).
    # perl (#1190): debootstrap's pkgdetails is a perl one-liner unless the
    # d-i udeb binary is present; without perl "Installing Debian base" fails
    # (ESXi, 21/09). Only the interpreter + libperl: the snippets use no modules.
    # dash + GNU tar (#1190): Debian's busybox sh runs its own applets --
    # dpkg-deb, dpkg, tar, chroot -- instead of the packed tools whatever
    # PATH says (FEATURE_SH_STANDALONE), and busybox dpkg-deb has no
    # --fsys-tarfile while busybox dpkg ran maintainer scripts with an
    # empty argument (ESXi, 21/09). debootstrap and the engine run under
    # dash, which honours PATH; the shadowing applet links go below.
    TOOL_BINS+=(debootstrap dpkg-deb mkfs.vfat chroot mkfs.ext4 eject perl dash tar)
    # mdadm (#1989): the installer builds the RAID array (installer/lib/raid.sh).
    TOOL_BINS+=(mdadm)
fi
TOOL_PKGS=()
for _tb in "${TOOL_BINS[@]}"; do
    _tp="${TOOL_PKG[$_tb]:-${INSTALLER_PKG[$_tb]:-}}"
    [ -n "$_tp" ] \
        || { echo "tools: no apt package known for binary $_tb (toolbins.sh and TOOL_PKG drifted)" >&2; exit 1; }
    case " ${TOOL_PKGS[*]:-} " in *" $_tp "*) ;; *) TOOL_PKGS+=("$_tp") ;; esac
done

# --- הצהרת הקיוסק (--with-gui) ------------------------------------------------
# שתי רשימות ולא אחת, כי הן נכשלות בשתי נקודות שונות בזמן: חבילה שאין
# ממנה מועמד ב-apt נתפסת **לפני** ש-apt רץ, ונתיב שלא הופיע על הדיסק
# נתפס אחרי ההתקנה. עד כאן לא נבדקה אף אחת מהן.
GUI_PACKAGES=(fonts-ibm-plex fontconfig-config
              libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0
              libcairo2 libpixman-1-0 libharfbuzz0b libfribidi0
              libfreetype6 libfontconfig1 libglib2.0-0t64 libdrm2
              # #690: LibVNCServer runtime for imagesrv-monitor. Its .so
              # closure rides along via ldd like any packed binary's.
              libvncserver1)

# ‏`truetype` ולא `opentype`: ‏fonts-ibm-plex בדביאן מתקינה
# ל-`/usr/share/fonts/truetype/ibm-plex`, והנתיב שהיה כאן מעולם לא היה
# קיים. ‏`if [ -d "$dir" ]` דילג עליו בלי מילה, ולכן קיוסק בלי גופן עברי
# נראה כמו בנייה שהצליחה — אותו דפוס כמו ‏`[ -e ] && cp` של ה-gconv
# ב-#33. הרשימה הזאת נבדקת, ומה שאין בו עוצר את הבנייה (#120).
#
# ‏/etc/fonts ו-/usr/share/fontconfig הם הזוג ולא אחד מהם: קובצי
# ‏conf.d הם קישורים סימבוליים אל conf.avail, וקישור יתום בתוך
# ה-initramfs שקול לקובץ חסר. בלי תצורת fontconfig הממשק אינו מוצא
# **שום** גופן — גם כשהקובץ ארוז לידו — והעברית יוצאת ריבועים.
GUI_PATHS=(/usr/share/fonts/truetype/ibm-plex
           /etc/fonts                  /usr/share/fontconfig)

# החבילות נבדקות כאן, לפני apt ולפני הקומפילציות, מאותו טעם כמו תיקיית
# ה-overlay למעלה. ‏`apt-get install` על חבילה שאינה בקומפוננטות המופעלות
# עונה `E: Unable to locate package` ויוצא 100 — הודעה שאינה מבדילה בין
# "אין חבילה כזאת בדביאן" לבין "היא קיימת, אבל ה-sources.list כאן לא
# מכיל את הקומפוננטה שלה". ההבדל הוא כל התשובה: ‏`fonts-ibm-plex`
# **קיימת** בדביאן 13 (‏6.1.1-1), ב-contrib, וה-sources.list של השרת
# מכיל `main non-free-firmware` בלבד (#120).
if [ "$WITH_GUI" -eq 1 ] && [ "$SKIP_APT" -eq 0 ]; then
    _no_candidate=""
    for _pkg in "${GUI_PACKAGES[@]}"; do
        # ‏`apt-cache policy` על חבילה לא מוכרת יוצא 0 עם פלט ריק, ולכן
        # קוד היציאה אינו הראיה — שורת ה-Candidate היא. גם `(none)`
        # (מוכרת באינדקס, בלי גרסה בת-התקנה) הוא "אין".
        _cand=$(apt-cache policy "$_pkg" 2>/dev/null | sed -n 's/^ *Candidate: *//p')
        case "$_cand" in
            ""|"(none)") _no_candidate="$_no_candidate $_pkg" ;;
        esac
    done
    if [ -n "$_no_candidate" ]; then
        echo "--with-gui: apt has no installable candidate for:$_no_candidate" >&2
        echo "A package that exists in Debian but sits in a component this machine" >&2
        echo "does not enable looks exactly like one that does not exist at all." >&2
        echo "fonts-ibm-plex is in contrib (Debian 13: 6.1.1-1); a sources.list" >&2
        echo "carrying only main will never find it. Enable the component it needs," >&2
        echo "run apt-get update, and build again." >&2
        exit 1
    fi
fi

# Binaries the agent scripts call. tests/test_agent.py cross-checks this
# list against the actual commands in agent/ -- update both together.
# udp-sender (#715): the build machine streams its own disk to the cloning
# machines; the udpcast package ships both halves, so nothing new to install.
# efibootmgr (#433): the Boot#### entry for the restored Windows on UEFI;
# copy_bin pulls libefiboot/libefivar/libpopt through ldd and stops the
# build if one is missing.
# dmsetup (#1466): the local clone shrinks a RAM copy-on-write view of the source (dm-snapshot over the partition, brd as the store) -- copy_bin pulls libdevmapper and its libs through ldd and stops the build if one is missing.
# nice (#1493): the native GUI starts as `/usr/bin/nice -n 10`. Debian 13's
# static busybox (1:1.37.0-6+b9, lab server 16/10) has no nice applet, so
# coreutils' own binary is packed (coreutils is in the apt list below).
BINARIES=(curl jq zstd pv sgdisk blockdev sha256sum od hdparm ntfsresize openssl
          ntfs-3g ntfs-3g.probe ntfsfix umount blkid df mount ethtool smartctl
          e2fsck resize2fs btrfs xfs_growfs
          udp-receiver udp-sender partclone.ntfs partclone.fat partclone.ext4
          partclone.btrfs partclone.dd
          dmidecode efibootmgr dmsetup nice)

if [ "$SKIP_APT" -eq 0 ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y --no-install-recommends \
        busybox-static zstd partclone udpcast gdisk curl jq pv \
        ntfs-3g libhivex-dev hdparm coreutils util-linux openssl \
        e2fsprogs btrfs-progs xfsprogs cpio gzip gcc libc6-dev ethtool \
        smartmontools dmidecode efibootmgr dmsetup
    # #1050: the toolbox delta -- only what the selection needs, or nothing.
    if [ "${#TOOL_PKGS[@]}" -gt 0 ]; then
        echo "tools: installing ${TOOL_PKGS[*]}"
        apt-get install -y --no-install-recommends "${TOOL_PKGS[@]}" \
            || { echo "tools: apt-get install of the toolbox packages failed" >&2; exit 1; }
    fi
fi

# ‏`$TMPDIR` ולא `/tmp` קשיח: עץ הבנייה הוא מאות MB לפני הדחיסה, ועל
# שרת עם `/tmp` ב-tmpfs זה אומר לכתוב מאות MB אל תוך ה-RAM. תבנית
# ‏`mktemp` שמתחילה ב-`/` מתעלמת מ-TMPDIR לגמרי, ולכן `TMPDIR=...`
# מהקורא לא עשה דבר.
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/imagesrv-initramfs.XXXXXX")
trap 'rm -rf "$ROOT"' EXIT

mkdir -p "$ROOT"/{bin,sbin,usr/bin,usr/sbin,usr/lib/imagesrv,etc/imagesrv,proc,sys,dev,run,tmp,lib}
. "$SCRIPT_DIR/initramfs-version.sh"
stamp_initramfs_version "$SCRIPT_DIR/.." "$SOURCE_VERSION" \
    "${IMAGESRV_ISO_MANIFEST:-/etc/imagesrv/iso-release.json}" "$ROOT"

# --- busybox and its applets -------------------------------------------------

BUSYBOX=$(command -v busybox)
cp "$BUSYBOX" "$ROOT/bin/busybox"
for applet in $("$BUSYBOX" --list); do
    case "$applet" in
        busybox) continue ;;
    esac
    ln -sf busybox "$ROOT/bin/$applet"
done

# --- real binaries and their libraries ---------------------------------------

copy_libs() {
    # Pull in every shared library the binary needs, keeping paths.
    # ldd on a shell wrapper (hivexget on Debian 13) exits nonzero —
    # that is fine and must not kill the build (#12). A real ELF whose
    # ldd prints "not found", or a cp that fails, must (#507).
    _ldd_out=$(ldd "$1" 2>/dev/null) || true
    case "$_ldd_out" in
        *"not found"*)
            echo "missing shared library for $1:" >&2
            printf '%s\n' "$_ldd_out" >&2
            exit 1
            ;;
    esac
    [ -n "$_ldd_out" ] || return 0
    while read -r lib; do
        [ -f "$lib" ] || continue
        mkdir -p "$ROOT$(dirname "$lib")"
        cp -Ln "$lib" "$ROOT$lib"
    done < <(printf '%s\n' "$_ldd_out" | awk '/=>/ { print $3 } /^\s*\// { print $1 }')
}

copy_bin() {
    local src dst
    src=$(command -v "$1") || { echo "missing binary: $1" >&2; exit 1; }
    dst="$ROOT/usr/bin/$(basename "$src")"
    cp -L "$src" "$dst"
    copy_libs "$src"
}

for b in "${BINARIES[@]}"; do
    copy_bin "$b"
done

# The dynamic loader and the NSS libraries curl resolves hostnames with.
# Required, not best-effort: `cp ... 2>/dev/null || true` let a missing
# loader or libnss build clean, and the station then failed in curl and
# DNS with messages that name none of them (#1125, the #33
# pattern). `-n` stays: the loader is in every ldd closure and copy_libs
# has usually packed it already -- that skip exits 0 (coreutils 9.7).
for extra in /lib64/ld-linux-x86-64.so.2 \
             /lib/x86_64-linux-gnu/libnss_dns.so.2 \
             /lib/x86_64-linux-gnu/libnss_files.so.2 \
             /lib/x86_64-linux-gnu/libresolv.so.2; do
    [ -e "$extra" ] || { echo "loader/NSS library missing: $extra" >&2; exit 1; }
    mkdir -p "$ROOT$(dirname "$extra")"
    cp -Ln "$extra" "$ROOT$extra"
    [ -s "$ROOT$extra" ] || { echo "loader/NSS library not packed: $extra" >&2; exit 1; }
done
# passwd/group as well as hosts: anything that resolves an account goes
# through NSS, and a database with no line here is the #33 failure mode
# again -- the lookup fails with nothing useful in the log.
cat > "$ROOT/etc/nsswitch.conf" << 'EOF'
hosts:  files dns
passwd: files
group:  files
shadow: files
EOF

# --- the agent ---------------------------------------------------------------

install -m 0755 "$AGENT_DIR/init"           "$ROOT/init"
install -m 0755 "$AGENT_DIR/imagesrv-agent" "$ROOT/usr/bin/imagesrv-agent"
bash "$SCRIPT_DIR/agent-lib-flatten.sh" "$AGENT_DIR/lib" "$ROOT/usr/lib/imagesrv"
# #1921: the two knowledge tables (SMART, hardware) -- smartjudge.sh reads them from here, and the
# server reads the same files from the repo. A build without them would judge nothing: stop.
install -d "$ROOT/usr/lib/imagesrv/data"
for _kb in smart-knowledge.tsv hw-issues.tsv; do
    [ -s "$AGENT_DIR/lib/data/$_kb" ] || { echo "build_initramfs: agent/lib/data/$_kb missing" >&2; exit 1; }
    install -m 0644 "$AGENT_DIR/lib/data/$_kb" "$ROOT/usr/lib/imagesrv/data/$_kb"
done
if [ "$WITH_INSTALLER" -eq 1 ]; then
    # --installer (#1190): the live installer -- engine, its libs, the console
    # process, and the GUI bridge (part 2; packed when present, missing = the
    # console process says so on screen rather than the image failing to boot).
    INSTALLER_SRC="$AGENT_DIR/../installer"
    # debootstrap is a shell script plus /usr/share/debootstrap/{functions,
    # scripts/*}; copy_bin packs only the binary. Without the data dir the
    # engine fails at "Installing Debian base" (measured, ESXi 21/09).
    [ -f /usr/share/debootstrap/functions ] || { echo "--installer: /usr/share/debootstrap is missing on the builder (apt-get install debootstrap)" >&2; exit 1; }
    mkdir -p "$ROOT/usr/share/debootstrap"
    cp -a /usr/share/debootstrap/. "$ROOT/usr/share/debootstrap/"
    [ -e "$ROOT/usr/share/debootstrap/scripts/trixie" ] || { echo "--installer: debootstrap has no trixie script" >&2; exit 1; }
    # debootstrap wants these at runtime beyond its own script: ar/tar/gzip
    # come from busybox; perl is not needed with --no-check-gpg; pkgdetails
    # is /usr/share/debootstrap/pkgdetails (shell) or /usr/lib/debootstrap/pkgdetails.
    if [ -x /usr/lib/debootstrap/pkgdetails ]; then
        mkdir -p "$ROOT/usr/lib/debootstrap"
        cp -L /usr/lib/debootstrap/pkgdetails "$ROOT/usr/lib/debootstrap/pkgdetails"
        copy_libs /usr/lib/debootstrap/pkgdetails
    fi
    [ -x "$INSTALLER_SRC/imagesrv-install" ] && [ -x "$INSTALLER_SRC/imagesrv-installer" ] \
        || { echo "--installer: $INSTALLER_SRC lacks imagesrv-install/imagesrv-installer" >&2; exit 1; }
    install -d "$ROOT/usr/lib/imagesrv/installer/lib"
    install -m 0755 "$INSTALLER_SRC/imagesrv-install"   "$ROOT/usr/lib/imagesrv/installer/imagesrv-install"
    install -m 0755 "$INSTALLER_SRC/imagesrv-installer" "$ROOT/usr/lib/imagesrv/installer/imagesrv-installer"
    install -m 0644 "$INSTALLER_SRC"/lib/*.sh           "$ROOT/usr/lib/imagesrv/installer/lib/"
    # #1447: the zone list answers.sh checks the timezone against (not a .sh)
    install -m 0644 "$INSTALLER_SRC/lib/zones.txt"      "$ROOT/usr/lib/imagesrv/installer/lib/zones.txt"
    if [ -f "$INSTALLER_SRC/gui-bridge.sh" ]; then
        install -m 0755 "$INSTALLER_SRC/gui-bridge.sh" "$ROOT/usr/lib/imagesrv/installer/gui-bridge.sh"
    else
        echo "--installer: installer/gui-bridge.sh not present -- the GUI cannot drive the engine in this image" >&2
    fi
fi

# fanout: the isolated multi-drawer writer. A shell cannot do non-blocking
# writes to several drives at once, and `tee` would let one stalled drive
# halt the whole machine.
echo "compiling fanout..."
gcc -O2 -Wall -Wextra -static -o "$ROOT/usr/bin/fanout" "$AGENT_DIR/fanout.c"

# #1971: netlink KOBJECT_UEVENT listener for immediate drawer changes.
echo "compiling uevent listener..."
gcc -O2 -Wall -Wextra -static -o "$ROOT/usr/bin/imagesrv-uevent" "$AGENT_DIR/uevent.c"
[ -x "$ROOT/usr/bin/imagesrv-uevent" ] || { echo "imagesrv-uevent missing" >&2; exit 1; }

# hivewrite: single-value registry reads/writes that preserve the key's
# other values (#33). Links against libhivex (no static build shipped),
# so its shared libraries ride along like any packed binary's.
echo "compiling hivewrite..."
gcc -O2 -Wall -Wextra -o "$ROOT/usr/bin/hivewrite" "$AGENT_DIR/hivewrite.c" -lhivex
copy_libs "$ROOT/usr/bin/hivewrite"

# lldpsniff: one LLDP frame → JSON in hello (#1048). libc only; packed in
# every image (classroom stations report switch/port too, not only GUI).
echo "compiling lldpsniff..."
gcc -O2 -Wall -Wextra -o "$ROOT/usr/bin/imagesrv-lldpsniff" "$AGENT_DIR/lldpsniff.c"
[ -s "$ROOT/usr/bin/imagesrv-lldpsniff" ] && [ -x "$ROOT/usr/bin/imagesrv-lldpsniff" ] \
    || { echo "imagesrv-lldpsniff missing or not executable" >&2; exit 1; }

# libhivex converts registry key names with glibc's iconv, and iconv
# loads its converters at runtime from the gconv directory. Without
# these files every hivex key lookup fails as "key not found" -- which
# is how the hostname write silently did nothing for months (#33).
# Registry names are UTF-16 or Latin-1, so only those two converters
# (plus the module index) are needed.
GCONV_SRC=$(dirname "$(find /usr/lib -name gconv-modules -path '*/gconv/*' 2>/dev/null | head -1)")
[ -d "$GCONV_SRC" ] || { echo "glibc gconv directory not found" >&2; exit 1; }
mkdir -p "$ROOT$GCONV_SRC"
# The converters are required, not best-effort. `[ -e ] && cp` skipped a
# missing one without a word, so a glibc that moved ISO8859-1.so would
# have produced an initramfs that builds clean and writes no hostname --
# #33 all over again. A missing converter is a failed build.
for f in gconv-modules ISO8859-1.so UTF-16.so; do
    [ -e "$GCONV_SRC/$f" ] || { echo "gconv converter missing: $GCONV_SRC/$f" >&2; exit 1; }
    cp -L "$GCONV_SRC/$f" "$ROOT$GCONV_SRC/$f"
    [ -s "$ROOT$GCONV_SRC/$f" ] || { echo "gconv converter empty: $f" >&2; exit 1; }
done
# The cache is a genuine optimisation -- glibc falls back to gconv-modules.
if [ -e "$GCONV_SRC/gconv-modules.cache" ]; then
    cp -L "$GCONV_SRC/gconv-modules.cache" "$ROOT$GCONV_SRC/gconv-modules.cache"
fi

# --- tools: framework (#649) -------------------------------------------------
# The IT toolbox: agent/lib/tools/tools.sh and every agent/lib/tools/tools_<domain>.sh
# ride with the recursive library copy above, so a new domain module is packed by
# existing -- this gate proves it landed. The framework's built-in tools need
# dmidecode (in BINARIES and in the apt list, so tests/test_agent.py
# cross-checks it) and smartctl (packed since #652).
for _t in "$ROOT/usr/lib/imagesrv"/tools_*.sh "$ROOT/usr/lib/imagesrv/toolbins.sh"; do
    [ -s "$_t" ] \
        || { echo "tools: $(basename "$_t") was not packed" >&2; exit 1; }
done
[ -x "$ROOT/usr/bin/dmidecode" ] || copy_bin dmidecode

# --- tools: the selection (#1050) ----------------------------------------------
# The binaries TOOL_BINS planned above, each with its ldd closure (copy_bin
# stops the build on a missing one -- a selected tool that is not on the
# builder is a failed build, not an empty row on the station). The selection
# file itself is what the station filters by: no file, no tools.
for _tb in "${TOOL_BINS[@]}"; do
    copy_bin "$_tb"
    if [ "$_tb" = tpm2_clear ]; then
        # libtss2-tctildr picks the kernel-device transport with dlopen, so
        # ldd on tpm2_clear cannot see it; without it every tpm2_* call fails
        # "no TCTI" on a machine whose TPM is right there.
        _tcti=$(ldconfig -p | awk '/libtss2-tcti-device\.so/{print $NF; exit}')
        [ -n "$_tcti" ] && [ -f "$_tcti" ] \
            || { echo "tools: tpm2_clear packed but libtss2-tcti-device.so is missing" >&2; exit 1; }
        mkdir -p "$ROOT$(dirname "$_tcti")"
        cp -L "$_tcti" "$ROOT$_tcti"
        copy_libs "$_tcti"
    fi
done
if [ "$WITH_INSTALLER" -eq 1 ]; then
    # A busybox applet link in /bin shadows the real tool for anything that
    # looks /bin up first -- debootstrap's in_target sets
    # PATH=/sbin:/usr/sbin:/bin:/usr/bin, so busybox chroot ran and its
    # dpkg applet configured the target (ESXi, 21/09). Only names that
    # have a real binary lose their link; the rest of busybox stays.
    for _shadow in "${TOOL_BINS[@]}"; do
        [ -L "$ROOT/bin/$_shadow" ] && [ -x "$ROOT/usr/bin/$_shadow" ] || continue
        rm "$ROOT/bin/$_shadow"
        echo "installer: busybox applet $_shadow unlinked, /usr/bin/$_shadow is the real one"
    done
fi
if [ -n "$TOOLS_SELECTION_FILE" ]; then
    install -m 0644 "$TOOLS_SELECTION_FILE" "$ROOT/etc/imagesrv/tools-selection.json"
    [ -s "$ROOT/etc/imagesrv/tools-selection.json" ] \
        || { echo "tools: tools-selection.json was not packed" >&2; exit 1; }
fi

cat > "$ROOT/etc/imagesrv/udhcpc.script" << 'EOF'
#!/bin/sh
# Minimal udhcpc hook: configure the interface, default route and DNS.
# udhcpc stays resident and renews (#1949), so this also runs mid-stream:
# an address that is already ours is never taken down -- a flush drops the
# routes under a running restore. A lost lease keeps the address too; the
# next bound replaces it if the server hands out a different one.
case "$1" in
    deconfig)
        ip link set "$interface" up
        ;;
    bound|renew)
        if ! ip -4 addr show dev "$interface" 2>/dev/null | grep -qF "inet $ip/"; then
            ip addr flush dev "$interface" 2>/dev/null
            ifconfig "$interface" "$ip" netmask "${subnet:-255.255.255.0}"
        fi
        [ -n "${router:-}" ] && route add default gw "${router%% *}" 2>/dev/null
        : > /etc/resolv.conf
        for d in ${dns:-}; do echo "nameserver $d" >> /etc/resolv.conf; done
        ;;
esac
exit 0
EOF
chmod 0755 "$ROOT/etc/imagesrv/udhcpc.script"

# #2040: the probe hook (udhcpc -q in agent/init). It configures nothing --
# it only records which DHCP server answered on this NIC, so init can take the
# NIC whose server-id is our server and leave the classroom NIC untouched.
cat > "$ROOT/etc/imagesrv/udhcpc.probe" << 'EOF'
#!/bin/sh
case "$1" in
    bound|renew)
        printf 'serverid=%s\nip=%s\n' "${serverid:-}" "${ip:-}" \
            > "/run/imagesrv/dhcp-probe.$interface"
        ;;
esac
exit 0
EOF
chmod 0755 "$ROOT/etc/imagesrv/udhcpc.probe"

# --- the account --------------------------------------------------------------
# The one account in the image. Something has to answer getpwnam, and the
# password field is locked on purpose: this file travels inside an
# initramfs served over plain HTTP, so any password in it is a published
# one. Nothing in the product image listens for a login (#1266).
printf 'root:x:0:0:root:/root:/bin/sh\n' > "$ROOT/etc/passwd"
printf 'root:x:0:\n'                     > "$ROOT/etc/group"
printf 'root:*:19000:0:99999:7:::\n'     > "$ROOT/etc/shadow"
chmod 0600 "$ROOT/etc/shadow"
mkdir -p "$ROOT/root"
chmod 0700 "$ROOT/root"

# מה עושים כשמשימה נגמרה. ברירת המחדל היא `auto`: `finish_and_stop`
# גוזר מהתפקיד — תחנת כיתה מאתחלת, בנייה/שיכפול מתכבים (מגירות
# מוחלפות במכונה כבויה, נספח א׳). ‏`reboot` מפורש נועד למעבדה מרוחקת,
# שבה מכונה שכבתה היא מכונה שאיש אינו יכול להדליק — הצי מתאתחל לבד
# (tools/lab/after-task-reboot.sh). נקרא ב-`finish_and_stop`.
case "$AFTER_TASK" in
    auto|poweroff|reboot) ;;
    *) echo "build_initramfs: --after-task must be auto, poweroff or reboot, got '$AFTER_TASK'" >&2; exit 2 ;;
esac
echo "$AFTER_TASK" > "$ROOT/etc/imagesrv/after-task"
echo "after-task: $AFTER_TASK"

# --- the kiosk (optional): the build machine's graphical face ----------------
# Native Pango/Cairo rendering onto DRM/KMS, with evdev input. Apt installs
# transitive runtime dependencies; ldd selects the actual shared-library
# closure, including any X11 libraries linked by Debian's Cairo build.

if [ "$WITH_GUI" -eq 1 ]; then
    if [ "$SKIP_APT" -eq 0 ]; then
        apt-get install -y --no-install-recommends "${GUI_PACKAGES[@]}"
        [ "$RADEON_LEGACY_FW" -eq 0 ] || apt-get install -y --no-install-recommends firmware-amd-graphics   # #1937
        # Build-host tools/headers only; none are copied into the image.
        apt-get install -y --no-install-recommends make pkg-config \
            libpango1.0-dev libcairo2-dev libdrm-dev libvncserver-dev
    fi
    echo "compiling the native station GUI..."
    GUI_DIR="$SCRIPT_DIR/../native-gui"
    # Force a fresh host build rather than reuse a binary from another host.
    make -B -C "$GUI_DIR" imagesrv-station-gui
    GUI_BIN="$GUI_DIR/imagesrv-station-gui"
    [ -s "$GUI_BIN" ] && [ -x "$GUI_BIN" ] \
        || { echo "--with-gui: native GUI binary missing or not executable: $GUI_BIN" >&2; exit 1; }
    install -m 0755 "$GUI_BIN" "$ROOT/usr/bin/imagesrv-station-gui"
    # Same closure as measure-size.sh/copy_libs(), but this is a known ELF:
    # unresolved libraries or a failed copy must stop the GUI build.
    #
    # ldd is the whole closure here -- measured, not assumed (#1125, 19/09):
    # a 32-screen `--png` render inside a chroot of the packed initrd under
    # LD_DEBUG=files loaded 0 libraries dynamically (every load was a
    # NEEDED one); libpango, libcairo and libvncserver import no dlopen at
    # all, and Debian 13 ships no pango/cairo module directory. The dlopen
    # extras that do exist -- gconv for hivex, the TCTI for tpm2 -- are
    # handled by name above.
    _gui_ldd=$(ldd "$GUI_BIN")
    if [[ "$_gui_ldd" == *"not found"* ]]; then
        printf '%s\n' "--with-gui: unresolved native GUI libraries:" "$_gui_ldd" >&2
        exit 1
    fi
    while read -r lib; do
        [ -f "$lib" ] \
            || { echo "--with-gui: native GUI library missing: $lib" >&2; exit 1; }
        mkdir -p "$ROOT$(dirname "$lib")"
        cp -L "$lib" "$ROOT$lib"
    done < <(printf '%s\n' "$_gui_ldd" | awk '/=>/ { print $3 } /^[[:space:]]*\// { print $1 }')
    # רכיבי רינדור וגופנים — נתיבים שלמים, לא בינארי בודד. נתיב מוצהר
    # שאינו כאן עוצר את הבנייה; ‏`if [ -d "$dir" ]` דילג עליו בשקט,
    # וזה מה שהסתיר את נתיב הגופן השגוי (#120). כולם נאספים לפני
    # ההודעה, כדי שלא יתגלו אחד-אחד בשש בנייות.
    _no_path=""
    for _p in "${GUI_PATHS[@]}"; do
        [ -d "$_p" ] || _no_path="$_no_path $_p"
    done
    if [ -n "$_no_path" ]; then
        echo "--with-gui: declared paths missing after install:$_no_path" >&2
        echo "The kiosk needs every one of them; a skipped path is a kiosk that" >&2
        echo "starts and renders nothing, which looks like a clean build." >&2
        exit 1
    fi
    for _p in "${GUI_PATHS[@]}"; do
        mkdir -p "$ROOT$_p"
        # The package carries every Plex family; copy only the six faces
        # named by native-gui/tools/measure-size.sh below.
        [ "$_p" = /usr/share/fonts/truetype/ibm-plex ] && continue
        cp -a "$_p/." "$ROOT$_p/"
    done
    fontdir=/usr/share/fonts/truetype/ibm-plex
    for face in IBMPlexSansHebrew-Regular IBMPlexSansHebrew-Medium IBMPlexSansHebrew-SemiBold \
                IBMPlexSansHebrew-Bold IBMPlexMono-Regular IBMPlexMono-Medium; do
        src=$(find "$fontdir" -iname "$face.ttf" -print -quit)
        [ -n "$src" ] && [ -s "$src" ] \
            || { echo "--with-gui: font face missing or empty: $fontdir/$face.ttf" >&2; exit 1; }
        cp -L "$src" "$ROOT$fontdir/"
    done
    # gui_main starts the GUI as `/usr/bin/nice -n 10` so partclone/fanout win
    # the CPU (#1493). That is coreutils' nice from BINARIES (the static busybox
    # has no applet); without it the GUI never starts, and that shows only on
    # a machine -- so stop here.
    [ -x "$ROOT/usr/bin/nice" ] \
        || { echo "--with-gui: no nice binary packed ($ROOT/usr/bin/nice); the GUI starts under it (#1493)" >&2; exit 1; }
    # 17/10 (owner): the last-resort screen when the GUI is not coming back --
    # rendered here by the GUI itself, painted on the machine by
    # agent/lib/gui/splash.sh, which reads the size from the file name. After the
    # closure, font and nice gates: the binary needs its libraries and fonts.
    SPLASH_W=640 SPLASH_H=240
    SPLASH_RAW="$ROOT/usr/share/imagesrv/splash-${SPLASH_W}x$SPLASH_H.raw"
    mkdir -p "$ROOT/usr/share/imagesrv"
    "$GUI_BIN" --splash-raw "$SPLASH_RAW" --size "${SPLASH_W}x$SPLASH_H" \
        || { echo "--with-gui: the last-resort screen did not render" >&2; exit 1; }
    [ -f "$SPLASH_RAW" ] && [ "$(stat -c %s "$SPLASH_RAW")" -eq $((SPLASH_W * SPLASH_H * 4)) ] \
        || { echo "--with-gui: $SPLASH_RAW is not ${SPLASH_W}x$SPLASH_H XRGB8888" >&2; exit 1; }

    cat > "$ROOT/usr/bin/imagesrv-kiosk" << 'EOF'
#!/bin/sh
LIB_DIR=${LIB_DIR:-/usr/lib/imagesrv}
export LIB_DIR
. "$LIB_DIR/guibridge.sh"
gui_main "$@"
EOF
    chmod 0755 "$ROOT/usr/bin/imagesrv-kiosk"

    # --- the remote monitor (#690) -------------------------------------------
    # An RFB server exposing /dev/fb0 over TCP 5900, packed only in the GUI
    # image: build and cloner have a framebuffer, and a classroom station
    # never starts it (agent/lib/core/monitor.sh gates by role). Input mode needs
    # the uinput module, declared in REQUIRED_MODULES below.
    echo "compiling the remote monitor..."
    gcc -O2 -Wall -Wextra -o "$ROOT/usr/bin/imagesrv-monitor" \
        "$AGENT_DIR/monitor.c" "$AGENT_DIR/hmac_sha256.c" -lvncserver
    [ -s "$ROOT/usr/bin/imagesrv-monitor" ] && [ -x "$ROOT/usr/bin/imagesrv-monitor" ] \
        || { echo "--with-gui: imagesrv-monitor missing or not executable" >&2; exit 1; }
    # Same closure gate as the native GUI binary: an unresolved .so or a
    # failed copy is a build that looks clean and a monitor that never
    # listens -- the #78 failure mode, caught here instead of on the metal.
    _mon_ldd=$(ldd "$ROOT/usr/bin/imagesrv-monitor")
    if [[ "$_mon_ldd" == *"not found"* ]]; then
        printf '%s\n' "--with-gui: unresolved imagesrv-monitor libraries:" "$_mon_ldd" >&2
        exit 1
    fi
    echo "imagesrv-monitor ldd closure:"
    printf '%s\n' "$_mon_ldd"
    while read -r lib; do
        [ -f "$lib" ] \
            || { echo "--with-gui: imagesrv-monitor library missing: $lib" >&2; exit 1; }
        mkdir -p "$ROOT$(dirname "$lib")"
        cp -L "$lib" "$ROOT$lib"
    done < <(printf '%s\n' "$_mon_ldd" | awk '/=>/ { print $3 } /^[[:space:]]*\// { print $1 }')
fi

# --- kernel modules and firmware ---------------------------------------------
# Storage + wired NIC drivers for the serviced fleet. Built-ins cover the
# rest; a missing module is logged by init, not fatal.

MODSRC="/lib/modules/$KVER"
[ -d "$MODSRC" ] || { echo "no modules for kernel $KVER" >&2; exit 1; }

# #1918: the power button. ACPI's button driver turns a short press into the
# "Power Button" input device (KEY_POWER) the native GUI reads
# (native-gui/src/input.c); without it a short press does nothing. Built in
# or a module depends on the kernel's config, so it is read from
# modules.builtin, as fuse is below, never assumed: 0 = built in, nothing to
# pack or load; 1 = not built in, button.ko is a declared module (BUTTON_MOD,
# added to REQUIRED_MODULES below) -- packed, loaded at boot, and a missing
# one stops the build; anything else = the file was not read (_btn_miss),
# and that stops the build too.
BUTTON_MOD=""; _btn_miss=""; _btn_rc=0
grep -qx "kernel/drivers/acpi/button.ko" "$MODSRC/modules.builtin" || _btn_rc=$?
case "$_btn_rc" in
    0) echo "power button: built into $KVER" ;;
    1) BUTTON_MOD=button ;;
    *) _btn_miss=" button(modules.builtin-unreadable)" ;;
esac

MODULE_SUBDIRS=(kernel/drivers/net/ethernet kernel/drivers/net/phy
                kernel/drivers/net/mdio     kernel/drivers/nvme
                kernel/drivers/ata          kernel/drivers/scsi
                kernel/drivers/cpufreq
                kernel/drivers/usb/storage
                # #1914 (metal, the college, 20/10): Intel VMD -- with "RST/VMD"
                # on in the firmware the NVMe/SATA disks sit behind the VMD
                # domain, and without vmd.ko the machine had no disks at all
                # ("לא נמצאו דיסקים"). CONFIG_VMD=m in 6.12.113+deb13-amd64.
                kernel/drivers/pci/controller
                # Hyper-V: vmbus הוא התשתית, net/hyperv הוא הכרטיס.
                # בלעדיהם הסוכן במעבדת ה-VM (issue #12) עולה בלי דיסק ורשת.
                kernel/drivers/hv           kernel/drivers/net/hyperv
                # מקלדת — תמיד, לא רק לקיוסק: אשף השחזור (ESC) קורא ממנה.
                # ‏usbhid לחומרה, hyperv-keyboard למעבדת ה-VM. בלעדיהם האשף
                # מוצג אבל אף הקשה לא מגיעה (issue #43).
                kernel/drivers/hid          kernel/drivers/input/serio
                kernel/drivers/input/keyboard
                # ‏usbhid מצהיר depends: usbcore,hid — ובלי הבקר עצמו אף
                # התקן USB אינו מתאמן. ‏#43 הוסיף את hid ונעצר שם, ולכן
                # מקלדת USB עדיין לא עבדה על חומרה (‏#77). ‏PS/2 הסתירה
                # את זה: ‏i8042 הוא built-in.
                kernel/drivers/usb/core     kernel/drivers/usb/host
                # ‏עכבר. ‏VM של ESXi שנוצר ב-govc יש לו בקר PS/2 בלבד —
                # בלי בקר USB — ובלעדי psmouse (שכולל את פרוטוקול
                # vmmouse) אין עכבר במתקין הגרפי בכלל, אף שהמקלדת עבדה
                # (i8042 built-in). ‏native-gui/src/input.c כבר תומך
                # בעכבר יחסי ובמצביע מוחלט (#949) — הדרייבר היה החסר
                # (#1204).
                kernel/drivers/input/mouse
                # מערכות קבצים. בלעדיהן ה-initramfs יכול לעגן NTFS דרך
                # FUSE ותו לא, וזה נראה כמו שלושה באגים נפרדים: שם
                # המחשב בלינוקס לא נכתב כי `mount -t ext4` נכשל (#62),
                # ‏`used_bytes` היה 0 בכל מניפסט כי המדידה מודדת אחרי
                # מאונט, ו-`secure_boot` דיווח `false` כי efivarfs הוא
                # מודול (‏CONFIG_EFIVAR_FS=m) שלא נארז (#84).
                #
                # ‏nls במלואו ולא תת-קבוצה: ‏vfat דורש את קידוד ברירת
                # המחדל של הקרנל, וכאן זה `cp437` **וגם** `ascii`
                # (‏CONFIG_FAT_DEFAULT_CODEPAGE/IOCHARSET). בחירת תת-
                # קבוצה היא בדיוק הניחוש שנכשל ב-#33, ‏#76 ו-#77.
                # ‏mbcache, ‏jbd2, ‏crc16, ‏xor, ‏raid6_pq ו-libcrc32c
                # נגררים מ-modules.dep — לכן הם אינם ברשימה הזו.
                kernel/fs/fat               kernel/fs/nls
                kernel/fs/ext4              kernel/fs/btrfs
                kernel/fs/xfs               kernel/fs/efivarfs)
# פלטפורמות היעד המוצהרות, וזוג המודולים שכל אחת מהן לא עולה בלעדיו:
# כרטיס הרשת ובקר הדיסק. עד כאן ה-initramfs כיסה הייפרווייזר אחד —
# זה שעליו הוא נבנה — ומכונה על ESXi, ‏KVM או Xen עלתה בלי רשת,
# וחלקן גם בלי דיסק (#78).
#
# לפי שם ולא לפי תיקייה, כי חלקם אינם יושבים בתיקייה בכלל:
# ‏`virtio_net` ו-`xen-netfront` הם קבצים בודדים ישירות תחת
# ‏kernel/drivers/net, ולולאת התיקיות לעולם לא תיגע בהם — אותה צורה
# בדיוק כמו ‏`mbcache` ב-#84.
#
# ‏`virtio`, ‏`virtio_ring`, ‏`virtio_pci` ו-`xenbus` הם built-in בקרנל
# של דביאן, ולכן אינם כאן ואין צורך בעצי `virtio`/`xen` כלל.
#
# ולארוז את ‏kernel/drivers/net כולו זו לא התשובה: ‏+11MB דחוסים על
# ‏35.9MB קיימים, ‏31% לכל אתחול PXE בכיתה, כדי לקבל דרייברים לחומרה
# שלא קיימת במכללה. ההצהרה כאן עולה ‏~150KB — והיא גם נבדקת.
# These four are not a platform: dm-mod + dm-snapshot (+ dm-bufio, its
# dependency) make the copy-on-write view, and brd is its RAM store
# (agent/lib/localclone/lcsnap.sh). They are loaded on demand by the agent with modprobe,
# NOT at boot, so they are deliberately absent from etc/imagesrv/modules. A
# missing module stops the build like any declared module (#78). A kernel with
# one built in also stops here on purpose: the agent sizes brd through its
# rd_size module parameter, and a built-in brd cannot be sized.
REQUIRED_MODULES=(hv_netvsc   hv_storvsc      # Hyper-V
                  vmxnet3     vmw_pvscsi      # VMware ESXi
                  virtio_net  virtio_blk      # KVM / Proxmox
                  xen-netfront xen-blkfront   # Xen
                  dm-mod dm-snapshot dm-bufio brd   # #1466: local clone, read-only source
                  af_alg algif_hash sha256-ssse3 pcspkr)   # #1596/#1967: hash and speaker

# מערכות הקבצים שהמערכת נשענת עליהן — מוצהרות, ולא נוכחות במקרה. עד
# כאן הן הגיעו כתוצר לוואי של `MODULE_SUBDIRS` וסגירת התלויות, ולכן
# נשירה של אחת מהן מסתיימת ב-exit 0 ומתגלה רק מול מכונה: ‏`exfat`
# ו-`isofs` נשרו בין שתי גרסאות, ‏kernel/fs ירד מ-73 ל-69 קבצים, ואיש
# לא ידע עד שהשוו רשימות בידיים (#121). מה תלוי במה: ‏ext4/btrfs/xfs —
# שחזור לינוקס, כתיבת `/etc/hostname` (#107, ‏#62) והרחבת XFS (#667);
# ‏vfat/fat — מחיצת
# ה-ESP; ‏nls_cp437/nls_ascii — הקידודים ש-vfat דורש (#84);
# ‏efivarfs — ‏`secure_boot` במניפסט (#84).
#
# ‏`exfat` ו-`isofs` אינם כאן בכוונה, אחרי חיפוש: אין בקוד קורא להם.
# הסוכן עולה מהרשת ולא ממדיה אופטית — `sr0` רק נספר ב-hello (#1485: ‏sr_mod
# ברשימת הטעינה למטה; נטען מעץ kernel/drivers/scsi), אף פעם לא מעוגן; אין `iso9660` ואין
# ‏`mount` על מדיה בשום מקום ברפו — ומחיצת exFAT באורח נשלחת
# ל-`partclone.dd` (ברירת המחדל של `_tool_for` ב-restore.sh), שקורא
# בלוקים ולא מערכת קבצים. הדבר היחיד שהמודול היה מוסיף הוא `used_bytes`
# אמיתי למחיצה כזאת, ובלעדיו `_used_bytes` מדווח 0 **עם אזהרה ביומן**
# ולא בשקט. אם יתברר שכן צריך אותם — הוספת שם לרשימה הזאת היא כל
# השינוי, והבנייה תאכוף אותו מיד.
REQUIRED_FS_MODULES=(ext4 btrfs xfs vfat fat nls_cp437 nls_ascii efivarfs
                     crc32c_generic crc32c-intel)
if [ "$WITH_INSTALLER" -eq 1 ]; then
    # ‏#1190: המתקין החי עולה **ממדיה** — ISO על CD וירטואלי (sr_mod/cdrom)
    # או על USB — וקורא iso9660 (isofs). בלי שלושתם `blkid -L` לא מוצא את
    # התווית ו-init אומר "no media" (נמדד ב-ESXi, 21/09). לתמונת ה-PXE הם
    # נשארים בחוץ בכוונה (ראו למעלה).
    REQUIRED_FS_MODULES+=(isofs sr_mod cdrom)
    # ‏#1989: ‏RAID במתקין — ‏md-mod והאישיויות שהמנוע טוען (modprobe) לפני
    # ‏`mdadm --create`. מוצהרים: חסר אחד עוצר את הבנייה, ולא מתגלה מול
    # שרת עם שלושה דיסקים כ-"mdadm could not create the array".
    REQUIRED_MODULES+=(md-mod raid1 raid456 raid10)
fi

if [ "$WITH_GUI" -eq 1 ]; then
    # רק מודולי התצוגה/קלט של חומרת הקיוסק; modules.dep מוסיף תלויות
    # (#641: אריזת כל drivers/gpu+input ניפחה וקרסה על הברזל). את הדרייבר
    # הגנרי של HID מספק `hid.ko` עצמו — אין `hid_generic.ko` נפרד בקרנל
    # דביאן 13, ולכן הוא **אינו** נכנס לרשימה כאן (הלולאה מחפשת קובץ ותיכשל);
    # ה-modules-load עדיין קורא ל-hid-generic כ-no-op לא-מזיק.
    # ‏uinput (#690): המוניטור במצב input יוצר מקלדת/עכבר וירטואליים דרכו.
    # ‏psmouse (#1204): עכבר PS/2 של VM/חומרה בלי בקר USB (govc, ‏ESXi) —
    # בלעדיו המקלדת עובדת (i8042 built-in) אבל אין עכבר במתקין הגרפי,
    # ולא נראה כתקלה בבנייה. מודול מוצהר חסר עוצר את הבנייה, כמו כל השאר.
    REQUIRED_MODULES+=(i915 evdev usbhid hid uinput psmouse)
    # ‏i915 דורש firmware; modules.dep לא מעתיק firmware, ורק rtl_nic ברירת
    # מחדל — בלעדיו התצוגה עלולה לצאת מנוונת/שחורה.
    FIRMWARE_DIRS+=(i915)
fi

# תת-עץ מוצהר שחסר נאסף כאן ומדווח יחד עם המודולים החסרים למטה, באותה
# עצירה. ‏`if [ -d ]` לבדו העלים אותו בשקט (#1125 — הדפוס של #84: ‏usb/host
# חסר הוא מקלדת USB בלי בקר, ובנייה שיצאה 0). אין רשימת "אופציונליים":
# כל 21 העצים קיימים בקרנל דביאן 13 הרגיל (20 נמדדו 19/09;
# ‏kernel/drivers/input/mouse נוסף ב-#1204 ואומת באותה בנייה עצמה —
# חסר היה עוצר אותה, לא רק "נמדד" בעין), ומה שאין בו הוא
# קרנל שאינו מתאים לבנייה — ההודעה למטה אומרת גם את זה.
_missing_sub=""
for sub in "${MODULE_SUBDIRS[@]}"; do
    if [ -d "$MODSRC/$sub" ]; then
        mkdir -p "$ROOT/lib/modules/$KVER/$sub"
        cp -a "$MODSRC/$sub/." "$ROOT/lib/modules/$KVER/$sub/"
    else
        _missing_sub="$_missing_sub $sub"
    fi
done

# מודול מוצהר שחסר עוצר את הבנייה — פלטפורמה ומערכת קבצים כאחת.
# ‏initramfs שנבנה בלי דרייבר של פלטפורמה מוצהרת ייראה תקין לחלוטין,
# והכשל יתגלה רק מול מכונה — `no DHCP lease on any interface`, בלי רמז
# לאיזה מודול חסר. זה בדיוק מה שקרה ב-#76, ‏#77 ו-#84, בכל פעם מול
# חומרה ולא בבנייה, וב-#121 זה קרה שוב למערכות הקבצים.
# כולם נאספים לפני ההודעה, כדי שלא יתגלו אחד-אחד בשש בנייות.
_missing=""
# Intel watchdogs are optional (software recovery remains available). The
# LPC bridge registers the TCO device on older HP boards; it is not a
# module dependency of iTCO_wdt, so copy it explicitly as well.
for _mod in iTCO_wdt lpc_ich; do
    _hit=$(find "$MODSRC" -name "$_mod.ko*" | head -1)
    if [ -n "$_hit" ]; then
        _rel=${_hit#"$MODSRC"/}
        mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_rel")"
        cp -a "$_hit" "$ROOT/lib/modules/$KVER/$_rel"
    else
        echo "optional watchdog module $_mod absent (may be built-in); software fallback available" >&2
    fi
done
# #1914 (Nadav 20/10): the GUI on older and virtual display adapters too -- old
# ATI (radeon), server boards (mgag200, ast), QEMU/Bochs (bochs, cirrus-qemu),
# VMware SVGA (vmwgfx; VCLONE-2 in BIOS mode had no framebuffer at all) and the
# firmware framebuffer (simpledrm, built-in on Debian 13). Optional: one that
# is built in or absent is said, not a stop -- i915 stays the required one.
# Their firmware is not packed: radeon on a chip that needs it falls back to
# no KMS, which init then reports (mods_gpu_fallback) and the agent says.
if [ "$WITH_GUI" -eq 1 ]; then
    for _mod in radeon mgag200 ast bochs cirrus-qemu vmwgfx simpledrm; do
        _hit=$(find "$MODSRC" -name "$_mod.ko*" | head -1)
        if [ -n "$_hit" ]; then
            _rel=${_hit#"$MODSRC"/}
            mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_rel")"
            cp -a "$_hit" "$ROOT/lib/modules/$KVER/$_rel"
        else
            echo "optional display module $_mod absent (may be built-in)" >&2
        fi
    done
fi
[ -z "${BUTTON_MOD:-}" ] || REQUIRED_MODULES+=("$BUTTON_MOD"); _missing="$_missing${_btn_miss:-}"   # #1918, above
for _mod in "${REQUIRED_MODULES[@]}" "${REQUIRED_FS_MODULES[@]}"; do
    _hit=$(find "$MODSRC" -name "$_mod.ko*" | head -1)
    if [ -z "$_hit" ]; then
        _missing="$_missing $_mod"
        continue
    fi
    _rel=${_hit#"$MODSRC"/}
    if [ ! -f "$ROOT/lib/modules/$KVER/$_rel" ]; then
        mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_rel")"
        cp -a "$_hit" "$ROOT/lib/modules/$KVER/$_rel"
    fi
done
# ‏FUSE: ‏ntfs-3g רץ עליו — מדידת used_bytes ובדיקת מחיצת המערכת בקליטה
# (#1368, ‏#1369) וכתיבת שם המחשב (hostname.sh). בקרנל דביאן 13 הוא built-in
# (‏kernel/fs/fuse/fuse.ko ב-modules.builtin, נמדד 13/10) ואין .ko לארוז;
# קרנל שבו אינו built-in עוצר כאן, ולא מתגלה מול מחשב בנייה כ-used_bytes=null.
# ‏grep: ‏0 = built-in, ‏1 = אינו, ‏2 = הקובץ לא נקרא — והשניים האחרונים עוצרים.
_fuse_rc=0
grep -qx 'kernel/fs/fuse/fuse.ko' "$MODSRC/modules.builtin" || _fuse_rc=$?
case "$_fuse_rc" in
    0) ;;
    1) _missing="$_missing fuse(not-built-in)" ;;
    *) _missing="$_missing fuse(modules.builtin-unreadable)" ;;
esac
if [ -n "$_missing" ] || [ -n "$_missing_sub" ]; then
    [ -z "$_missing_sub" ] || echo "missing module trees under $MODSRC:$_missing_sub" >&2
    [ -z "$_missing" ] || echo "missing required modules:$_missing" >&2
    echo "kernel $KVER cannot serve every platform and filesystem ImageSrv" >&2
    echo "claims to support." >&2
    # ‏#904: על קרנל cloud (linux-image-cloud-amd64 — אימג' הענן של
    # דביאן) זה צפוי: הוא נבנה בלי חלק מהדרייברים ומערכות הקבצים.
    # המסלול שעבד לשרת המשני במעבדה (16/09) הוא העתקת שלושת הקבצים
    # מהשרת הראשי — לא בנייה מקומית. ההודעה אומרת זאת בשמה.
    case "$KVER" in
        *cloud*)
            echo "kernel $KVER is a cloud kernel: do not build here." >&2
            echo "Copy vmlinuz, initrd.img and initrd.img.gui from the primary" >&2
            echo "server's boot dir (/srv/imagesrv/boot) instead, or install" >&2
            echo "linux-image-amd64 and build against it (docs/server-install.md)." >&2
            ;;
    esac
    exit 1
fi

# ‏רשימת תיקיות ידנית תמיד תפספס תלות אחת עמוק יותר. ‏#76 היה PHY
# שנטען אחרי ה-MAC, ‏#77 היה usbhid בלי בקר USB — ואז התברר ש-usbcore
# עצמו תלוי ב-usb-common, שיושב בתיקייה שלישית שאיש לא חשב עליה.
# שלוש פעמים אותו באג בערב אחד, כולל פעם אחת אחרי שכבר ידענו בדיוק
# מה מחפשים.
#
# ‏`modules.dep` יודע את התשובה. במקום לנחש תיקיות, סוגרים את הגרף:
# כל מודול שהועתק גורר את התלויות שלו, עד שאין מה להוסיף. מודול חסר
# תלות הוא מודול שנכשל בטעינה בשקט, ובלי זה `N modules did not load`
# היה 69 מתוך ~180.
[ -f "$MODSRC/modules.softdep" ] || {
    echo "$MODSRC/modules.softdep is missing -- soft dependencies cannot be verified" >&2
    exit 1
}
# ‏#1464: מעבר אחד. הלולאה הקודמת הריצה `find` על כל עץ המודולים, ושלוש
# תת-מעטפות לכל קובץ, לכל שם softdep של כל מודול בכל סבב (‏~10^4 fork לשם).
# עכשיו: רשימת העץ פעם אחת, ובכל סבב awk אחד שמחשב תוכנית ("H"/"S" = להעתיק,
# "M" = softdep שלא נפתר) ולולאת shell שמעתיקה. אותה סמנטיקה בדיוק — הטסט
# tests/test_initramfs_closure_1464.py מריץ את הישנה (fixtures/) מול זו על אותם
# עצים, והתמונה משוחזרת (#1125), כך שמה שנארז הוא אותם בייטים.
# ‏softdep הוא לפעמים *יכולת* ולא קובץ: btrfs רוצה blake2b-256, וטבלת ה-alias
# ממפה אותו ל-blake2b_generic (‏`alias crypto-blake2b-256 blake2b_generic`) —
# נפתר דרך modules.alias לפני שמכריזים "חסר" (נמצא בבניית --installer, 21/09).
_closure_candidates=$(mktemp "$ROOT/module-closure.XXXXXX")
find "$MODSRC" -type f -name '*.ko*' > "$_closure_candidates"
_closure_round=0
_closure_changed=1
while [ "$_closure_changed" -eq 1 ]; do
    _closure_changed=0
    _closure_round=$((_closure_round + 1))
    _closure_staged=$(mktemp "$ROOT/module-closure.XXXXXX")
    _closure_plan=$(mktemp "$ROOT/module-closure.XXXXXX")
    (cd "$ROOT/lib/modules/$KVER" && find . -name '*.ko*' | sed 's|^\./||') > "$_closure_staged"
    _closure_alias=$MODSRC/modules.alias
    _closure_builtin=$MODSRC/modules.builtin
    _closure_have_alias=1
    _closure_have_builtin=1
    [ -f "$_closure_alias" ] || { _closure_alias=/dev/null; _closure_have_alias=0; }
    [ -f "$_closure_builtin" ] || { _closure_builtin=/dev/null; _closure_have_builtin=0; }
    awk -v src="$MODSRC" -v staged_file="$_closure_staged" \
        -v dep_file="$MODSRC/modules.dep" -v soft_file="$MODSRC/modules.softdep" \
        -v alias_file="$_closure_alias" -v builtin_file="$_closure_builtin" \
        -v candidates_file="$_closure_candidates" \
        -v have_alias="$_closure_have_alias" -v have_builtin="$_closure_have_builtin" '
        function norm(p, n) {
            n=p; sub(/^.*\//, "", n); sub(/\.ko.*$/, "", n); gsub(/-/, "_", n)
            return n
        }
        FILENAME == staged_file { staged[++staged_count]=$0; next }
        FILENAME == dep_file {
            colon=index($0, ":")
            if (colon) {
                key=substr($0, 1, colon-1); rest=substr($0, colon+1)
                count=split(rest, words)
                for (i=1; i<=count; i++) if (words[i] != "")
                    hard[key, ++hard_count[key]]=words[i]
            }
            next
        }
        FILENAME == soft_file {
            if ($1 == "softdep") {
                module=$2; gsub(/-/, "_", module)
                for (i=3; i<=NF; i++) if ($i != "pre:" && $i != "post:") {
                    value=$i; gsub(/-/, "_", value)
                    soft[module, ++soft_count[module]]=value
                }
            }
            next
        }
        FILENAME == alias_file {
            if ($1 == "alias") alias_of[$2]=alias_of[$2] " " $3
            next
        }
        FILENAME == builtin_file { builtin[norm($0)]=1; next }
        FILENAME == candidates_file {
            candidate[++candidate_count]=substr($0, length(src)+2)
            candidate_name[candidate_count]=norm($0)
            next
        }
        END {
            for (si=1; si<=staged_count; si++) {
                rel=staged[si]
                for (hi=1; hi<=hard_count[rel]; hi++) print "H " hard[rel, hi]
                module=norm(rel)
                for (sj=1; sj<=soft_count[module]; sj++) {
                    want=soft[module, sj]; found=0
                    for (ci=1; ci<=candidate_count; ci++)
                        if (candidate_name[ci] == want || index(candidate_name[ci], want "_") == 1) {
                            found=1; print "S " candidate[ci]
                        }
                    if (!found && have_alias) {
                        # Exact match here, unlike the prefix match above (as before).
                        dash=want; gsub(/_/, "-", dash)
                        alias_count=split(alias_of["crypto-" dash] alias_of[dash], alias_value, " ")
                        for (ai=1; ai<=alias_count; ai++) {
                            alias_module=alias_value[ai]; gsub(/-/, "_", alias_module)
                            for (ci=1; ci<=candidate_count; ci++)
                                if (candidate_name[ci] == alias_module) {
                                    found=1; print "S " candidate[ci]
                                }
                            if (!found && have_builtin && (alias_module in builtin)) found=1
                        }
                    }
                    if (!found && have_builtin && (want in builtin)) found=1
                    if (!found) print "M " want " " module
                }
            }
        }' "$_closure_staged" "$MODSRC/modules.dep" "$MODSRC/modules.softdep" \
        "$_closure_alias" "$_closure_builtin" "$_closure_candidates" > "$_closure_plan" || {
            echo "module closure: awk could not read the module metadata under $MODSRC" >&2
            rm -f "$_closure_staged" "$_closure_plan" "$_closure_candidates"
            exit 1
        }
    # ‏"M" נאסף בלולאה ולא נבדק ב-`grep`: קוד 2 שלו ("לא נקרא") היה מתקפל ל"אין חסר".
    _closure_missing=0
    while IFS= read -r _line; do
        _kind=${_line%% *}
        _dep=${_line#* }
        case "$_kind" in
            H) [ -f "$MODSRC/$_dep" ] || continue ;;
            S) ;;
            M) _soft=${_dep%% *}; _module=${_dep#* }
               echo "soft dependency $_soft of $_module is missing under $MODSRC" >&2
               _closure_missing=1; continue ;;
            *) echo "module closure: unexpected plan line: $_line" >&2
               _closure_missing=1; continue ;;
        esac
        if [ ! -f "$ROOT/lib/modules/$KVER/$_dep" ]; then
            mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_dep")"
            cp -a "$MODSRC/$_dep" "$ROOT/lib/modules/$KVER/$_dep"
            _closure_changed=1
        fi
    done < "$_closure_plan"
    rm -f "$_closure_staged" "$_closure_plan"
    if [ "$_closure_missing" -ne 0 ]; then
        rm -f "$_closure_candidates"
        exit 1
    fi
    # תקרה: גרף התלויות של הקרנל אינו מעגלי, אבל לולאה אינסופית בבנייה
    # גרועה מאימג' חסר.
    [ "$_closure_round" -ge 10 ] && break
done
rm -f "$_closure_candidates"
echo "module dependency closure: $(find "$ROOT/lib/modules/$KVER" -name '*.ko*' | wc -l) modules after $_closure_round rounds"
# modules.order and modules.builtin are depmod's input next to the .ko
# files; without them it indexes an incomplete tree and init reports
# "N modules did not load" with no hint why. The copy was `2>/dev/null ||
# true` -- a kernel package missing either built clean (#1125).
for _f in modules.order modules.builtin; do
    [ -f "$MODSRC/$_f" ] || { echo "$MODSRC/$_f is missing -- depmod needs it" >&2; exit 1; }
done
cp "$MODSRC"/modules.{order,builtin}* "$ROOT/lib/modules/$KVER/"
cp "$MODSRC/modules.softdep" "$ROOT/lib/modules/$KVER/"
depmod -b "$ROOT" "$KVER"

# ‏דרייברי ה-PHY חייבים להיטען לפני דרייברי ה-MAC שנתלים בהם. ‏r8169
# שעושה probe לפני ש-realtek.ko נרשם נכשל ב-EADDRNOTAVAIL (‏-49), והקרנל
# **אינו מנסה שוב**: המודול נשאר טעון וההתקן נשאר בלי דרייבר. מחשב
# Lenovo עם RTL8168 לא קיבל ממשק רשת בכלל בגלל זה (‏#76).
#
# ‏`sort -u` הוא ששבר את זה: הוא מיין אלפביתית, ו-`r8169` קודם
# ל-`realtek`. הוא גם ביטל בשקט את הכוונה של "Storage first" שורה
# מעליו. ‏`awk '!seen[$0]++'` מסיר כפילויות בלי למיין, ולכן הסדר נשמר.
_phy_mods=$(find "$ROOT/lib/modules/$KVER/kernel/drivers/net/phy" \
                 "$ROOT/lib/modules/$KVER/kernel/drivers/net/mdio" \
                 -name '*.ko*' 2>/dev/null | sed 's|.*/||; s|\.ko.*||' | sort)

{
    # Storage first, then the PHY drivers, then every copied NIC driver.
    # ‏הבקר לפני מה שמתחבר אליו, מאותו טעם כמו ה-PHY: ‏usbcore ואז
    # ה-HCDים, ורק אחר כך usb-storage ו-usbhid שנשענים עליהם.
    printf '%s\n' usbcore xhci_hcd xhci_pci ehci_hcd ehci_pci
    printf '%s\n' ohci_hcd ohci_pci uhci_hcd
    # ‏sr_mod (#1485): כונן CD/DVD מדווח ב-hello (`optical`) כדי שלעולם לא
    # ייראה כחריץ דיסק ריק. הוא יושב בעץ kernel/drivers/scsi שכבר נארז (cdrom
    # נגרר ב-modules.dep). אם לא נטען — אין /sys/module/sr_mod, והסוכן מדווח
    # ‏null ("לא נבדק"), לעולם לא [] ("אין כונן").
    # vmd before ahci/nvme (#1914): the disks behind a VMD domain appear only
    # once it is bound; a machine without VMD just fails this one, by name.
    # ata_piix/ata_generic (#1914, an old HP in the college, 20/10): SATA in
    # IDE/Legacy mode (ICH7-ICH10) is ata_piix, not ahci -- packed in
    # kernel/drivers/ata all along and never loaded. initmods.sh loads the
    # storage controllers by modalias first; these stay as the fallback.
    printf '%s\n' vmd ahci ata_piix ata_generic nvme sd_mod sr_mod uas usb-storage hv_vmbus hv_storvsc
    # Optional Intel TCO hardware recovery; dependencies follow modules.dep.
    printf '%s\n' lpc_ich iTCO_wdt
    # בקרי הדיסק של ESXi, ‏KVM ו-Xen. ‏`virtio_pci` הוא built-in, ולכן
    # האפיק כבר שם כשאלה נטענים (#78).
    printf '%s\n' vmw_pvscsi virtio_scsi virtio_blk xen-blkfront
    # ‏מקלדת לאשף השחזור — מודול חסר אינו פטאלי (issue #43).
    # ‏psmouse (#1204): עכבר PS/2 של VM/חומרה בלי בקר USB. נטען תמיד
    # ולא רק תחת --with-gui, כמו atkbd/hyperv_keyboard לצידו.
    printf '%s\n' hid hid-generic usbhid atkbd hyperv_keyboard psmouse
    # מערכות קבצים במפורש, ולא בהסתמך על טעינה-לפי-דרישה של הקרנל.
    # ‏`mount -t ext4` אמנם מבקש `fs-ext4` דרך modules.alias, אבל זו
    # שרשרת הנחות (‏depmod, ‏busybox modprobe, ‏/proc/sys/kernel/modprobe)
    # שכל חוליה בה נכשלת בשקט. כאן כישלון נספר ומדווח (#84).
    # ‏ספק ה-crc32c לפני מערכות הקבצים (#1207): ‏libcrc32c (של ext4/btrfs/xfs)
    # נכשל ב-"unknown symbol" כשאין ספק רשום, ו-busybox modprobe אינו מכבד
    # את ה-softdep. נמדד ב-QEMU 24/09: ‏xfs לבד rc=1, ‏crc32c_generic לפניו rc=0.
    printf '%s\n' crc32c_generic crc32c-intel efivarfs fat vfat nls_cp437 nls_ascii ext4 btrfs xfs
    # #1596: fanout hashes the stream through AF_ALG. sha256-generic is built
    # in, so the kernel never asks for sha256-ssse3 by itself -- it is loaded
    # here, by name. A CPU without SSSE3 refuses it and fanout gets the generic
    # one (same digest); without AF_ALG fanout hashes in-process and says so in
    # the log. Neither is fatal, so neither is in modules.required.
    printf '%s\n' af_alg algif_hash sha256-ssse3 pcspkr
    # #1918: the ACPI power button, when it is a module (BUTTON_MOD, read from
    # modules.builtin next to MODSRC above); built in = nothing to load
    [ -z "${BUTTON_MOD:-}" ] || printf '%s\n' "$BUTTON_MOD"
    [ -n "$_phy_mods" ] && printf '%s\n' "$_phy_mods"
    find "$ROOT/lib/modules/$KVER/kernel/drivers/net" -name '*.ko*' 2>/dev/null \
        | sed 's|.*/||; s|\.ko.*||' | sort
    if [ "$WITH_GUI" -eq 1 ]; then
        # ‏GPU של אחד משלושת היצרנים, ועכבר/evdev לקיוסק, ו-uinput למוניטור.
        printf '%s\n' i915 amdgpu nouveau radeon mgag200 ast bochs cirrus-qemu vmwgfx simpledrm evdev uinput   # #1914: + older/virtual
    fi
} | awk '!seen[$0]++' > "$ROOT/etc/imagesrv/modules"

# #1702: the same list, split so agent/init (lib/initmods.sh) can load by
# hardware instead of by name. `modules` stays complete and ordered -- it is
# both the old behaviour (an initramfs without these files loads all of it)
# and the fallback. phy = loaded first; nic = every other NIC driver, loaded
# only for a PCI network device that is present (or all of them if no NIC
# shows up); gpu = loaded only for a PCI display device that is present.
printf '%s\n' "$_phy_mods" | awk 'NF' > "$ROOT/etc/imagesrv/modules.phy"
find "$ROOT/lib/modules/$KVER/kernel/drivers/net" -name '*.ko*' 2>/dev/null \
    | sed 's|.*/||; s|\.ko.*||' | sort \
    | awk 'FILENAME == ARGV[1] { phy[$0]; next } !($0 in phy)' \
          "$ROOT/etc/imagesrv/modules.phy" - > "$ROOT/etc/imagesrv/modules.nic"
if [ "$WITH_GUI" -eq 1 ]; then
    printf '%s\n' i915 amdgpu nouveau radeon mgag200 ast bochs cirrus-qemu vmwgfx simpledrm \
        > "$ROOT/etc/imagesrv/modules.gpu"   # #1914: + older/virtual adapters, the fallback after modalias
fi

# ‏מודולי החובה, בנפרד: מערכות הקבצים שהמערכת נשענת עליהן בכל מכונה. אלה
# מודולי תוכנה טהורים שנטענים בכל פלטפורמה, ולכן כישלון טעינה שלהם הוא
# ‏initramfs שבור — לא חומרה נעדרת כמו דרייבר וירטואליזציה על ברזל.
# ‏agent/init מפריד לפי הרשימה הזאת: מודול חובה שנכשל נקרא בשם וכ-ERROR,
# ומודול פלטפורמה שנכשל מדווח כמצב הצפוי. בלעדיה `N modules did not load`
# קיפל את שניהם למספר אחד (#407).
printf '%s\n' "${REQUIRED_FS_MODULES[@]}" > "$ROOT/etc/imagesrv/modules.required"

# קושחה מוצהרת שחסרה עוצרת: ‏rtl_nic הוא ה-NIC של הצי (‏firmware-realtek),
# ו-i915 מצטרף עם --with-gui (‏firmware-intel-graphics בדביאן 13). ‏`if [ -d ]`
# דילג בשקט, והתחנה עלתה בלי רשת או עם מסך שחור (#1125). כולם נאספים
# לפני ההודעה, כמו המודולים.
_no_fw=""
for fw in "${FIRMWARE_DIRS[@]}"; do
    if [ -d "/lib/firmware/$fw" ]; then
        mkdir -p "$ROOT/lib/firmware/$fw"
        cp -a "/lib/firmware/$fw/." "$ROOT/lib/firmware/$fw/"
    else
        _no_fw="$_no_fw $fw"
    fi
done
if [ -n "$_no_fw" ]; then
    echo "firmware directories missing under /lib/firmware:$_no_fw" >&2
    echo "Debian 13: rtl_nic is firmware-realtek, i915 is firmware-intel-graphics." >&2
    exit 1
fi

# --radeon-legacy-firmware (#1937, the USB stick): an HP 6005 Pro (Radeon HD 4200, RS880,
# 21/10) came up with no screen -- radeon on R600..Evergreen loads its CP/RLC microcode at
# probe, and without it KMS fails: no fb0. Only those generations' files, not the whole
# radeon/ directory (SI/CIK, and amdgpu's, are other chips and megabytes), and no UVD
# (video decode -- radeon goes on without it). Declared files missing stop the build.
RADEON_LEGACY_CHIPS="R600 R700 RS780 RV610 RV620 RV630 RV635 RV670 RV710 RV730 RV740 RV770 CEDAR REDWOOD JUNIPER CYPRESS PALM SUMO SUMO2"
if [ "$RADEON_LEGACY_FW" -eq 1 ]; then
    mkdir -p "$ROOT/lib/firmware/radeon"; _rfw_n=0
    for _c in $RADEON_LEGACY_CHIPS; do
        for _f in /lib/firmware/radeon/"${_c}"_*; do
            [ -e "$_f" ] || continue
            case "${_f##*/}" in *_uvd*) continue ;; esac
            cp -a "$_f" "$ROOT/lib/firmware/radeon/"; _rfw_n=$((_rfw_n + 1))
        done
    done
    for _f in RS780_pfp RS780_me R600_rlc; do   # the RS780/RS880 set -- the machine of the field
        ls "$ROOT/lib/firmware/radeon/$_f".bin* > /dev/null 2>&1 \
            || { echo "--radeon-legacy-firmware: radeon/$_f.bin missing under /lib/firmware (Debian 13: firmware-amd-graphics, non-free-firmware)" >&2; exit 1; }
    done
    echo "radeon legacy firmware: $_rfw_n files, $(du -sk "$ROOT/lib/firmware/radeon" | cut -f1) KB" >&2
fi

# --- site/lab overlay (#1266) ---------------------------------------------------
# IMAGESRV_EXTRA_OVERLAY=<dir>: a lab or a site adds to the image what the
# product does not ship and does not know about. Two parts, both optional:
#   * the tree under <dir> is copied over the image root (owned by root),
#     last, so it can add or replace files. Start hooks go in
#     <dir>/etc/imagesrv/hooks.d/*.sh -- agent/lib/core/sitehooks.sh runs them
#     after DHCP;
#   * <dir>/.binaries names builder binaries, one per line, packed with
#     their library closure exactly like BINARIES above (a missing one stops
#     the build). The file itself is not copied into the image.
# Unset = the product image, with nothing added.
if [ -n "${EXTRA_OVERLAY:-}" ]; then
    if [ -f "$EXTRA_OVERLAY/.binaries" ]; then
        while read -r _xb; do
            case "$_xb" in ''|'#'*) continue ;; esac
            copy_bin "$_xb"
        done < "$EXTRA_OVERLAY/.binaries"
    fi
    cp -a --no-preserve=ownership "$EXTRA_OVERLAY/." "$ROOT/" \
        || { echo "IMAGESRV_EXTRA_OVERLAY: copying $EXTRA_OVERLAY failed" >&2; exit 1; }
    rm -f "$ROOT/.binaries"
    # #1412: the image says it carries an overlay; tools/iso/initrd-lab-gate.sh refuses it without --lab.
    mkdir -p "$ROOT/etc/imagesrv" && printf 'overlay: %s\n' "$EXTRA_OVERLAY" > "$ROOT/etc/imagesrv/overlay.txt" \
        || { echo "IMAGESRV_EXTRA_OVERLAY: writing the overlay marker failed" >&2; exit 1; }
    echo "overlay: $EXTRA_OVERLAY copied over the image"
fi

# --- third-party licenses (#1707) ---------------------------------------------
# Work from a snapshot of the final file set: this is after every binary,
# ldd closure, firmware directory, GUI font and site overlay has been copied.
# Files built by ImageSrv itself have no Debian owner and need no Debian notice.
# Every file that does have an owner must bring that package's copyright file;
# a missing notice is a failed build, never an image with silent omissions.
mapfile -d '' -t PACKED_FILES < <(find "$ROOT" -type f -print0)
mkdir -p "$ROOT/usr/share/doc/licenses"
for _packed in "${PACKED_FILES[@]}"; do
    _host_file="${_packed#"$ROOT"}"
    [ -e "$_host_file" ] || continue
    _host_file=$(readlink -f "$_host_file")
    _owner=$(dpkg-query -S "$_host_file" 2>/dev/null \
        | sed -n '1{s/: \/.*//;p;}') || true
    [ -n "$_owner" ] || continue
    _pkg="${_owner%%:*}"
    _copyright="/usr/share/doc/$_pkg/copyright"
    [ -f "$_copyright" ] || {
        echo "license: package $_pkg owns packed file $_host_file but has no $_copyright" >&2
        exit 1
    }
    cp -L "$_copyright" "$ROOT/usr/share/doc/licenses/$_pkg.copyright"
done

# --- pack --------------------------------------------------------------------
# Reproducible (#1125, feeding #1078 -- the initrd hash in grub.cfg): the
# same source must give the same bytes. Three sources of noise, each closed
# by name: `find` walks in inode order (LC_ALL=C sort), mtimes are the build
# time (every one clamped to SOURCE_DATE_EPOCH), and cpio stores inode and
# device numbers (--reproducible) while gzip stores a timestamp (-n).
# The epoch is the flag, the environment, or the commit the checkout is at.
# No epoch and no git is a build with no stable hash -- that stops here
# rather than falling back to `date` and printing a one-off hash that looks
# exactly like a stable one (principle 5).
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
    SOURCE_DATE_EPOCH=$(git -C "$SCRIPT_DIR" log -1 --format=%ct) \
        || { echo "SOURCE_DATE_EPOCH is unset and 'git log' failed: build from a checkout or pass --source-date-epoch" >&2; exit 1; }
fi
case "$SOURCE_DATE_EPOCH" in
    ''|*[!0-9]*) echo "SOURCE_DATE_EPOCH must be seconds since the epoch, got '$SOURCE_DATE_EPOCH'" >&2; exit 1 ;;
esac
export SOURCE_DATE_EPOCH
echo "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH"
find "$ROOT" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
(cd "$ROOT" && find . -print0 | LC_ALL=C sort -z \
    | cpio -o -H newc --null --reproducible --quiet | gzip -9n) > "$OUTPUT"

SIZE=$(du -h "$OUTPUT" | cut -f1)
echo
echo "initramfs ready: $OUTPUT ($SIZE)"
echo
echo "Next steps:"
echo "  1. Copy it, together with the matching kernel, to the HTTP root the"
echo "     installer created (clients fetch /boot/vmlinuz and /boot/initrd.img):"
if [ "$WITH_GUI" = 1 ]; then
    # --with-gui builds the kiosk initramfs. It is served next to the
    # text one under a fixed name, and the menu generator hands it only
    # to the roles that have a screen -- build and classroom (#32).
    echo "       cp $OUTPUT /srv/imagesrv/boot/initrd.img.gui"
    echo "     (--with-gui: this is the GUI initramfs. Do NOT overwrite"
    echo "      initrd.img with it -- cloning machines still need that one.)"
else
    echo "       cp $OUTPUT /srv/imagesrv/boot/initrd.img"
fi
echo "       cp /boot/vmlinuz-$KVER /srv/imagesrv/boot/vmlinuz"
echo "  2. The GRUB menu generator already points clients at those paths."
echo "  3. Updating the agent = rebuilding this file and copying it again."
echo "     No signing, no key handling."
