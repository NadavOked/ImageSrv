# linuxdrivers.sh -- can the restored Linux's initramfs find the disk it was
# just written to? (#1331 T8; interfaces.md section 17, step 1L). POSIX sh
# (busybox ash).
#
# Linux has the drivers in-tree (AHCI, NVMe) -- what breaks when a disk moves
# between models is the initramfs: one built "host-only" (dracut's default on
# Fedora/RHEL/SUSE, MODULES=dep in initramfs-tools) carries only the modules
# of the machine it was built on, and the kernel never finds its root -- the
# Linux 0x7B. The evidence is the initramfs itself, not the config files that
# should have produced it: the bus module of the disk we wrote (nvme* ->
# nvme.ko, sd* -> ahci.ko) is in its file list, or it is built into the
# kernel (modules.builtin), or the disk will not boot on another bus.
#
# Read-only, report-only: nothing on the disk is changed, and nothing from
# the agent's own kernel is ever put on it (#1331, Nadav 17/10). The fix
# (a rebuild in a chroot) is not built yet.
#
# Sets $_dr_linux to the JSON object that rides in drivers.json as "linux".
# Never fatal, never silent (עיקרון 5): "could not check" is "failed" or
# "unsupported" by name -- never "generic".

linux_bus_module() {
    # $1 = disk name. "<module>|<its path under lib/modules/<kver>>" (rc 1: none).
    case "$1" in
        nvme*) echo "nvme|kernel/drivers/nvme/host/nvme.ko" ;;
        sd*)   echo "ahci|kernel/drivers/ata/ahci.ko" ;;
        *)     return 1 ;;
    esac
}

_linux_result() {
    # $1 = state, $2 = error/reason (optional). Uses $_ld_mod/$_ld_kver/$_ld_initrd/$_ld_os.
    _lr="{\"state\":\"$1\""
    for _lk in module:_ld_mod kver:_ld_kver initrd:_ld_initrd os:_ld_os; do
        eval "_lv=\${${_lk#*:}:-}"
        [ -n "$_lv" ] && _lr="$_lr,\"${_lk%%:*}\":\"$(json_escape "$_lv")\""
    done
    if [ -n "${2:-}" ]; then
        _lrk="error"; [ "$1" = "skipped" ] && _lrk="reason"
        _lr="$_lr,\"$_lrk\":\"$(json_escape "$2")\""
    fi
    _dr_linux="$_lr}"
    log "linux initramfs${DR_PORT:+ (drawer $DR_PORT)}: $_dr_linux"
}

_hex8() {
    # $1 = 8 hex digits from a newc header. The number, or rc 1.
    case "$1" in ''|*[!0-9a-fA-F]*) return 1 ;; esac
    echo $((0x$1))
}

_cpio_walk() {
    # $1 = file, $2 = offset of an uncompressed newc archive. Prints the member
    # names and leaves the offset right after its trailer in $_cw_end.
    _cw=$2
    while :; do
        _cwh=$(tail -c +$((_cw + 1)) "$1" | head -c 110)
        case "$_cwh" in 07070[12]*) ;; *) _ld_why="כותרת cpio פגומה בהיסט $_cw"; return 1 ;; esac
        _cwf=$(_hex8 "$(printf %s "$_cwh" | cut -c55-62)") || { _ld_why="גודל קובץ פגום בהיסט $_cw"; return 1; }
        _cwn=$(_hex8 "$(printf %s "$_cwh" | cut -c95-102)") || { _ld_why="אורך שם פגום בהיסט $_cw"; return 1; }
        [ "$_cwn" -ge 1 ] || { _ld_why="אורך שם פגום בהיסט $_cw"; return 1; }
        _cwname=$(tail -c +$((_cw + 111)) "$1" | head -c $((_cwn - 1)))
        _cw=$(( (_cw + 110 + _cwn + 3) / 4 * 4 ))
        [ "$_cwname" = "TRAILER!!!" ] && break
        printf '%s\n' "$_cwname"
        _cw=$(( _cw + (_cwf + 3) / 4 * 4 ))
    done
    _cw_end=$_cw
}

_initrd_list() {
    # $1 = initrd. Every member name of every archive in it -- the early
    # uncompressed ones (microcode) walked header by header, the compressed
    # one listed through its decompressor. Returns 1 with $_ld_why.
    _il=0
    _ilsize=$(wc -c < "$1") || { _ld_why="לא הצלחנו לקרוא את $_ld_initrd"; return 1; }
    _ilsize=$((_ilsize))
    while [ "$_il" -lt "$_ilsize" ]; do
        _ilm=$(od -A n -t x1 -N 6 -j "$_il" "$1" | tr -d ' \n')
        case "$_ilm" in
            303730373031*|303730373032*)
                _cpio_walk "$1" "$_il" || return 1
                # the kernel skips zero padding between archives; so do we
                _il=$(od -A d -t x1 -v -j "$_cw_end" -N 65536 "$1" \
                    | awk '{ for (i = 2; i <= NF; i++) if ($i != "00") { print $1 + i - 2; exit } }')
                [ -n "$_il" ] && continue
                [ $((_cw_end + 65536)) -ge "$_ilsize" ] && return 0
                _ld_why="ריפוד של יותר מ-64KB אחרי ארכיון בהיסט $_cw_end"; return 1 ;;
            28b52ffd*)     _ilcat="zstd -dc" ;;
            1f8b*)         _ilcat="zcat" ;;
            fd377a585a00*) _ilcat="xzcat" ;;
            425a68*)       _ilcat="bzcat" ;;
            02214c18*)     _ilcat="lz4cat" ;;
            *) _ld_why="פורמט initrd לא מזוהה בהיסט $_il ($_ilm)"; return 1 ;;
        esac
        command -v "${_ilcat%% *}" > /dev/null 2>&1 || { _ld_why="אין בסוכן ${_ilcat%% *} לפריסת ה-initrd"; return 1; }
        # cpio's exit is the evidence: it reaches the trailer only through a
        # complete stream. The decompressor may still complain about padding.
        tail -c +$((_il + 1)) "$1" | $_ilcat 2>> "$LOG_FILE" | cpio -t 2>> "$LOG_FILE" \
            || { _ld_why="פריסת ה-initrd נכשלה (${_ilcat%% *})"; return 1; }
        return 0
    done
}

_linux_kernel_ok() {
    # $1 = root, $2 = kver. rc 0 = the bus module is in its initrd or built
    # in; 1 = neither (host-only); 2 = could not check ($_ld_why); 3 = no initrd.
    _ld_initrd=""
    for _ki in "boot/initrd.img-$2" "boot/initramfs-$2.img"; do
        [ -f "$1/$_ki" ] && { _ld_initrd="/$_ki"; break; }
    done
    _kb="$_ld_moddir/$2/modules.builtin"
    if [ -f "$_kb" ]; then
        grep -qxF "$_ld_path" "$_kb"; _kbrc=$?
        [ "$_kbrc" -eq 0 ] && return 0
        [ "$_kbrc" -eq 1 ] || { _ld_why="קריאת modules.builtin נכשלה (rc=$_kbrc)"; return 2; }
    fi
    [ -n "$_ld_initrd" ] || return 3
    _initrd_list "$1$_ld_initrd" > "$RUN_DIR/initrd.list" || return 2
    # "usr/lib/modules/<kver>/kernel/..." or "lib/modules/...", maybe compressed
    awk -v p="/$_ld_path" '{ sub(/\.(xz|gz|zst)$/, ""); n = length($0) - length(p)
        if (n >= 0 && substr($0, n + 1) == p) { f = 1; exit } } END { exit !f }' \
        "$RUN_DIR/initrd.list"; _klrc=$?
    [ "$_klrc" -eq 0 ] && return 0
    [ "$_klrc" -eq 1 ] || { _ld_why="חיפוש ברשימת ה-initrd נכשל (rc=$_klrc)"; return 2; }
    [ -f "$_kb" ] || { _ld_why="אין modules.builtin לקרנל $2 -- לא ידוע אם $_ld_mod מובנה"; return 2; }
    return 1
}

_linux_check_root() {
    # $1 = the mounted root. Every kernel that has a vmlinuz in /boot is
    # checked; the first one that fails decides.
    # Read, never sourced: the file is the image's. usr/lib first -- an
    # absolute /etc/os-release symlink would resolve into the agent's own root,
    # and so would an absolute /lib (#1331: never the agent's modules).
    _ld_os=$(cat "$1/usr/lib/os-release" "$1/etc/os-release" 2>/dev/null \
        | sed -n 's/^PRETTY_NAME=//p' | head -n 1 | tr -d "\"'")
    _ld_moddir="$1/usr/lib/modules"
    [ -d "$_ld_moddir" ] && [ ! -L "$1/usr/lib" ] || _ld_moddir="$1/lib/modules"
    _ld_n=0
    for _kv in "$1"/boot/vmlinuz-*; do
        [ -f "$_kv" ] || continue
        _ld_kver=${_kv##*/vmlinuz-}
        _ld_n=$((_ld_n + 1))
        _linux_kernel_ok "$1" "$_ld_kver"
        case $? in
            0) ;;
            1) _linux_result hostonly "ב-initramfs אין $_ld_mod -- הוא נבנה למכונה המקורית בלבד"; return ;;
            3) _linux_result unsupported "אין initrd לקרנל $_ld_kver"; return ;;
            *) _linux_result failed "$_ld_why"; return ;;
        esac
    done
    if [ "$_ld_n" -eq 0 ]; then
        _ld_kver=""
        if grep -qE '^[^#]*[[:space:]]/boot[[:space:]]' "$1/etc/fstab" 2>/dev/null; then
            _linux_result unsupported "/boot במחיצה נפרדת -- לא נבדק"
        else
            _linux_result unsupported "לא נמצא קרנל ב-/boot"
        fi
        return
    fi
    _linux_result generic
}

linux_initramfs_check() {
    # $1 = disk, $2 = manifest, $3 = verified plan. Always returns 0.
    _dr_linux=""; _ld_mod=""; _ld_kver=""; _ld_initrd=""; _ld_os=""; _ld_why=""
    _ld_bus=$(linux_bus_module "$1") || { _linux_result skipped "unknown_bus"; return 0; }
    _ld_mod=${_ld_bus%%|*}; _ld_path=${_ld_bus#*|}
    _ld_root=$(_mount_linux "$1" "$2" "$3")
    case $? in
        0) ;;
        2) _linux_result failed "אין תיקיית /etc במחיצת הלינוקס"; return 0 ;;
        3|4) _linux_result failed "לא ברור איזה תת-כרך btrfs הוא השורש"; return 0 ;;
        *) _linux_result failed "לא הצלחנו לעגן את מחיצת הלינוקס"; return 0 ;;
    esac
    _linux_check_root "$_ld_root"
    _umount_checked "$RUN_DIR/linux" || _linux_result failed "לא הצלחנו לנתק את מחיצת הלינוקס"
    return 0
}
