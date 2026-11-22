# tooldest.sh -- #2804: the devices of the toolbox -- where a tool that
# produces files saves them, the source it reads, the partitions the GUI's
# picker offers, and whether a disk is being written right now. POSIX sh
# (busybox ash). tools_load sources it; named like toolbins.sh -- tools_load
# globs tools_*.sh as domain modules, and this is not one.
#
# Nadav 31/10: what a tool produces goes to the USB stick in the portable
# edition, to the server (this machine's folder) in the regular one. Rules:
#   * never on the disk it reads (the source) -- checked by the mount, not
#     assumed from the medium;
#   * free space is read before the run; after it, a destination with less
#     than TOOLS_DEST_FULL_KB left is a run that stopped on a full disk;
#   * no destination = the ids that need one are not listed at all (missing,
#     not grey), named once on stderr by tools_list.

TOOLS_DEST_IDS='scalpel foremost extundelete magicrescue ddrescue-image safecopy'
# A carve's output cannot be known before it runs. The floor only refuses a
# destination that cannot hold a handful of recovered files (16 MB = half the
# regular stick's data partition, DATA_MB=32 in tools/build_usb_clone.sh);
# the after-run check below is what tells a complete carve from a cut one.
TOOLS_DEST_MIN_KB=${TOOLS_DEST_MIN_KB:-16384}
TOOLS_DEST_FULL_KB=${TOOLS_DEST_FULL_KB:-1024}

tooldest_needs() {   # $1 = id -> 0 when the tool writes files to a destination
    case " $TOOLS_DEST_IDS " in *" $1 "*) return 0 ;; esac
    return 1
}

# --- the destination ------------------------------------------------------------

tooldest_root() {
    # -> TD_ROOT, TD_WHERE (Hebrew); rc 0 = there is one, rc 2 = there is
    # not, and TD_WHY says why. One function per medium: the server's channel,
    # when Nadav picks it, is _tooldest_server and nothing else.
    TD_ROOT=""; TD_WHERE=""; TD_WHY=""
    if [ "${IMAGESRV_MODE:-}" = usbclone ]; then _tooldest_usb; else _tooldest_server; fi
}

_tooldest_usb() {
    # usbclone.sh mounts IMGSRV_DATA (FAT, 32 MB on the regular stick) at
    # $USB_DATA_DIR and writes its answer to $RUN_DIR/usb.data.
    TD_WHERE='בדיסק-און-קי שהמחשב עלה ממנו'
    _tdu=$(cat "$RUN_DIR/usb.data" 2>/dev/null)
    [ "$_tdu" = mounted ] || { TD_WHY="אין בדיסק-און-קי מחיצת נתונים מחוברת לכתיבה (${_tdu:-לא נקרא})"; return 2; }
    # ASCII on purpose: the data partition is mounted vfat without a UTF-8 option.
    TD_ROOT="${USB_DATA_DIR:-$RUN_DIR/usbdata}/recovered"
}

_tooldest_server() {
    # No channel yet (measured 11/11): the initramfs has curl only -- no NFS,
    # CIFS or sshfs -- and a carve writes a tree, ddrescue writes at random
    # offsets. Which service the server opens for it is Nadav's call.
    TD_WHERE='בשרת, בתיקייה של המחשב הזה'
    TD_WHY='אין עדיין ערוץ שמירה לשרת'
    return 2
}

tooldest_mount() {
    # $1 = path -> TD_MDISK (the whole disk under the longest mount point that
    # holds $1) and TD_MFS. rc 1 = not on a block device we can name
    # (tmpfs, overlay, unreadable /proc/mounts): never a destination.
    TD_MDISK=""; TD_MFS=""
    _tdm=$(awk -v p="$1" '
        { m = $2; if (p == m || m == "/" || index(p, m "/") == 1) if (length(m) > best) { best = length(m); d = $1; f = $3 } }
        END { if (best) print d, f }' "${SYSROOT:-}/proc/mounts" 2>/dev/null) || return 1
    # shellcheck disable=SC2086 # "device fstype": two words, split on purpose
    set -- $_tdm
    [ $# -eq 2 ] || return 1
    _tdmd=${1#"${SYSROOT:-}"}; TD_MFS=$2
    case "$_tdmd" in /dev/*) _tdmd=${_tdmd#/dev/} ;; *) return 1 ;; esac
    TD_MDISK=$(tooldest_disk_of "$_tdmd") || return 1
}

tooldest_disk_of() {   # $1 = block name (sdb1 or sdb) -> its whole disk, or rc 1
    case "$1" in ''|*[!a-z0-9]*) return 1 ;; esac
    [ -d "${SYSROOT:-}/sys/block/$1" ] && { printf '%s\n' "$1"; return 0; }
    for _tdo in "${SYSROOT:-}"/sys/block/*/"$1"; do
        [ -d "$_tdo" ] || continue
        _tdo=${_tdo%/*}; printf '%s\n' "${_tdo##*/}"; return 0
    done
    return 1
}

tooldest_free_kb() {   # $1 = path -> free KB on its filesystem; rc 1 = not read
    _tdf=$(df -Pk "$1" 2>/dev/null | awk 'NR == 2 { print $4 }')
    case "$_tdf" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$_tdf"
}

tooldest_prepare() {
    # $1 = sub-path under the destination root, $2 = KB the run needs.
    # TD_SRC_DISK must be set (tooldest_source). Prints the destination line
    # the GUI shows (first line of the tool's output) and sets TD_DIR /
    # TD_FREE_KB. rc 2 + one Hebrew line = no run.
    tooldest_root || { printf 'סירוב: %s. הכלי לא הורץ ושום דבר לא נשמר.\n' "$TD_WHY"; return 2; }
    TD_DIR="$TD_ROOT/$1"
    tooldest_mount "$TD_DIR" || { printf 'סירוב: לא ניתן לקבוע על איזה דיסק נמצא היעד (%s). הכלי לא הורץ.\n' "$TD_ROOT"; return 2; }
    [ -n "${TD_SRC_DISK:-}" ] && [ "$TD_MDISK" != "$TD_SRC_DISK" ] ||
        { printf 'סירוב: היעד נמצא על הדיסק שממנו קוראים (%s). כתיבה אליו הייתה דורסת את מה שמשחזרים.\n' "${TD_SRC_DISK:-?}"; return 2; }
    mkdir -p "$TD_DIR" 2>/dev/null && [ -d "$TD_DIR" ] && [ -w "$TD_DIR" ] ||
        { printf 'סירוב: לא ניתן ליצור את תיקיית היעד %s. הכלי לא הורץ.\n' "$TD_DIR"; return 2; }
    TD_FREE_KB=$(tooldest_free_kb "$TD_DIR") || { printf 'סירוב: לא ניתן לקרוא כמה מקום פנוי ביעד. הכלי לא הורץ.\n'; return 2; }
    [ "$TD_FREE_KB" -ge "$2" ] ||
        { printf 'סירוב: אין מספיק מקום %s — פנויים %s MB, נדרשים %s MB. הכלי לא הורץ.\n' "$TD_WHERE" $((TD_FREE_KB / 1024)) $(($2 / 1024)); return 2; }
    printf 'יעד השמירה: %s · %s · פנוי: %s MB\n' "$TD_WHERE" "$TD_DIR" $((TD_FREE_KB / 1024))
}

tooldest_filled() {   # rc 0 = the destination is (almost) full after the run
    _tdl=$(tooldest_free_kb "$TD_DIR") || return 0   # unreadable = cannot say it did not fill
    [ "$_tdl" -lt "$TOOLS_DEST_FULL_KB" ]
}

tooldest_stamp() { date +%Y-%m-%d_%H%M%S; }

# --- the source -------------------------------------------------------------------

tooldest_source() {
    # $1 = /dev/<name>, $2 = disk | part | any. -> TD_SRC (/dev/..), TD_SRC_DISK
    # (the whole disk's name), TD_SRC_BYTES. rc 2 + a Hebrew line on refusal:
    # not a plain kernel name, not that kind, the clone stick, or no size.
    TD_SRC=""; TD_SRC_DISK=""; TD_SRC_BYTES=""
    case "$1" in /dev/*) ;; *) printf 'סירוב: נדרש התקן מקור מפורש (/dev/...), התקבל "%s".\n' "$1"; return 2 ;; esac
    _tds=${1#/dev/}
    case "$_tds" in ''|*[!a-z0-9]*|loop*|ram*|zram*|md*|sr*|fd*|nbd*|dm*)
        printf 'סירוב: %s אינו דיסק או מחיצה שאפשר לקרוא מהם.\n' "$1"; return 2 ;; esac
    TD_SRC_DISK=$(tooldest_disk_of "$_tds") || { printf 'סירוב: ההתקן %s לא נמצא.\n' "$1"; return 2; }
    case "$2:$([ "$TD_SRC_DISK" = "$_tds" ] && echo disk || echo part)" in
        disk:part) printf 'סירוב: הכלי קורא דיסק שלם, ו-%s היא מחיצה.\n' "$1"; return 2 ;;
        part:disk) printf 'סירוב: הכלי קורא מחיצה, ו-%s הוא דיסק שלם.\n' "$1"; return 2 ;;
    esac
    # grep: 0 = the clone stick (refuse), 1 = not it, 2 = no list (#231, as tools_disk.sh)
    if grep -qx "$TD_SRC_DISK" "$RUN_DIR/usb-media.disks" 2>/dev/null; then
        printf 'סירוב: %s הוא הדיסק-און-קי שהמחשב עלה ממנו.\n' "$1"; return 2
    fi
    if [ -d "${SYSROOT:-}/sys/block/$_tds" ]; then _tdz="${SYSROOT:-}/sys/block/$_tds/size"
    else _tdz="${SYSROOT:-}/sys/block/$TD_SRC_DISK/$_tds/size"; fi
    _tdz=$(cat "$_tdz" 2>/dev/null)
    case "$_tdz" in ''|0|*[!0-9]*) printf 'סירוב: לא ניתן לקרוא את גודל %s.\n' "$1"; return 2 ;; esac
    TD_SRC=$1; TD_SRC_BYTES=$((_tdz * 512))
}

tooldest_serial() {   # $1 = disk name -> a file-safe serial, or rc 1
    if command -v disk_serial >/dev/null 2>&1; then _tdr=$(disk_serial "$1")
    else _tdr=$(cat "${SYSROOT:-}/sys/block/$1/device/serial" 2>/dev/null); fi
    _tdr=$(printf '%s' "$_tdr" | tr -cd 'A-Za-z0-9_.-')
    [ -n "$_tdr" ] || return 1
    printf '%s\n' "$_tdr"
}

# --- the GUI's partition picker (#2804, interfaces.md §23) ----------------------------

tools_gui_parts() {
    # $GUI_DIR/tool-parts: one line per partition, dev|disk|size_bytes|fstype|label,
    # read from sysfs (lsblk empty is not "no partitions", principle 5a); fstype
    # "?" = blkid could not tell, "" = blkid read it and found no filesystem.
    # The clone stick is never offered. No file = not read; empty = no partitions.
    [ -d "${SYSROOT:-}/sys/block" ] || { rm -f "$GUI_DIR/tool-parts"; return 1; }
    for _tpd in "${SYSROOT:-}"/sys/block/*; do
        _tpn=${_tpd##*/}
        case "$_tpn" in sd*|nvme*|hd*|vd*|mmcblk*) ;; *) continue ;; esac
        grep -qx "$_tpn" "$RUN_DIR/usb-media.disks" 2>/dev/null && continue
        for _tpp in "$_tpd/$_tpn"*; do
            [ -f "$_tpp/partition" ] || continue
            _tpq=${_tpp##*/}; _tpz=$(cat "$_tpp/size" 2>/dev/null); case "$_tpz" in ''|*[!0-9]*) _tpz=0 ;; esac
            _tpf=$(blkid -s TYPE -o value "/dev/$_tpq" 2>/dev/null); _tpr=$?
            case "$_tpr" in 0) ;; 2) _tpf='' ;; *) _tpf='?' ;; esac
            _tpl=$(blkid -s LABEL -o value "/dev/$_tpq" 2>/dev/null | tr '|\r\n' '   ')
            printf '/dev/%s|/dev/%s|%s|%s|%s\n' "$_tpq" "$_tpn" $((_tpz * 512)) "$_tpf" "$_tpl"
        done
    done > "$GUI_DIR/tool-parts.next" && mv "$GUI_DIR/tool-parts.next" "$GUI_DIR/tool-parts"
}

# --- "the button is off while writing" (mockup 14) -------------------------------------

tools_busy() {
    # rc 0 = a disk of this machine is being written: a local clone copying /
    # shrinking / verifying, or a round's drawer in any state but a final
    # one. A state that cannot be read counts as writing (fail closed).
    case "$(cat "${LC:-$RUN_DIR/lc}/phase" 2>/dev/null)" in copying|shrinking|verifying) return 0 ;; esac
    for _tbs in "$RUN_DIR"/targets/*/state; do
        [ -e "$_tbs" ] || continue
        case "$(cat "$_tbs" 2>/dev/null)" in done|failed|skipped|cancelled) ;; *) return 0 ;; esac
    done
    return 1
}
