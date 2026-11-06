# hostname_linux.sh -- the Linux side of write_hostname (hostname.sh,
# interfaces.md section 5). POSIX sh (busybox ash).
#
# /etc/hostname is replaced and the 127.0.1.1 line of /etc/hosts is
# rewritten, which is what every Debian/Ubuntu-family installer does.
#
# Split from hostname.sh when #89/#107 would have pushed it past the 300-line
# wall of tests/sizelimit.py (#539 is the precedent: split, don't squeeze).

HOSTNAME_METHOD_LINUX="etc-hostname"

_mount_linux() {
    # $1 = disk, $2 = manifest, $3 = an already verified plan (optional).
    # Echoes the restored system's root (the directory holding etc/), mounted
    # at $RUN_DIR/linux or below it. Fails with a return code that names why:
    #   1 = could not mount, 2 = mounted but no /etc,
    #   3 = btrfs: no subvolume holds /etc, 4 = btrfs: several do.
    if [ -n "${3:-}" ]; then
        _mount_plan="$3"
    else
        _mount_plan="$RUN_DIR/mount-linux.plan"
        manifest_plan "$2" > "$_mount_plan" || return 1
    fi
    # ‏#1899: השורש שהקליטה סימנה ("system": true) — לא "הראשונה", שהיא /boot
    # כשהוא נפרד. מניפסט בלי סימון (לפני #1899): כמו קודם, הראשונה.
    # רק מבין השורות של התוכנית שהתקבלה: תוכנית של מחיצה אחת (‏#1979,
    # ‏_write_hostname_linux) מעגנת את המחיצה הזו — לא את המסומנת הראשונה.
    _idx=$(awk -F'|' -v m=" $(_linux_marked "$2" | tr '\n' ' ')" \
        '$3 == "linux" && index(m, " " $1 " ") { print $1; exit }' "$_mount_plan") || return 1
    [ -n "$_idx" ] || _idx=$(awk -F'|' '$3 == "linux" { print $1; exit }' "$_mount_plan") || return 1
    [ -n "$_idx" ] || { log "no linux partition in the manifest" >&2; return 1; }
    _fs=$(awk -F'|' -v i="$_idx" '$1 == i { print $4 }' "$_mount_plan") || return 1
    _node=$(partition_node "$1" "$_idx")
    _mnt="$RUN_DIR/linux"
    mkdir -p "$_mnt"
    mount -t "$_fs" "$_node" "$_mnt" >> "$LOG_FILE" 2>&1 || { log "could not mount $_node" >&2; return 1; }
    [ -d "$_mnt/etc" ] && { echo "$_mnt"; return 0; }
    if [ "$_fs" != "btrfs" ]; then
        umount "$_mnt" >> "$LOG_FILE" 2>&1
        log "no /etc on $_node" >&2
        return 2
    fi
    # #89: the mount above got the DEFAULT subvolume -- the root itself on
    # openSUSE (snapper sets it). Ubuntu ("@"), Fedora ("root") and hand
    # installs leave the default at the top level and keep the root one level
    # down, under a name nobody can guess. So the top level is mounted and the
    # root is the one child that holds etc/ -- found, not guessed. Zero or
    # several is a named failure, never a pick (עיקרון 5).
    umount "$_mnt" >> "$LOG_FILE" 2>&1
    mount -t btrfs -o subvolid=5 "$_node" "$_mnt" >> "$LOG_FILE" 2>&1 \
        || { log "could not mount the btrfs top level of $_node" >&2; return 1; }
    _root=""; _n=0
    for _d in "$_mnt"/*/etc; do
        [ -d "$_d" ] && { _root="${_d%/etc}"; _n=$((_n + 1)); }
    done
    if [ "$_n" -eq 1 ]; then
        log "btrfs $_node: the root is subvolume $(basename "$_root")" >&2
        echo "$_root"
        return 0
    fi
    umount "$_mnt" >> "$LOG_FILE" 2>&1
    log "btrfs $_node: $_n top-level subvolumes hold /etc -- cannot tell which is the root" >&2
    [ "$_n" -eq 0 ] && return 3
    return 4
}

_write_linux_files() {
    # $1 = the restored system's /etc, $2 = name. Replaces /etc/hostname and
    # the 127.0.1.1 line the installer wrote in /etc/hosts (added if absent).
    printf '%s\n' "$2" > "$1/hostname" || return 1
    if [ -f "$1/hosts" ]; then
        awk -v n="$2" 'BEGIN { done = 0 }
            /^127\.0\.1\.1[ \t]/ { print "127.0.1.1\t" n; done = 1; next }
            { print }
            END { if (!done) print "127.0.1.1\t" n }' "$1/hosts" > "$1/hosts.new" \
            && mv "$1/hosts.new" "$1/hosts" || return 1
    else
        printf '127.0.0.1\tlocalhost\n127.0.1.1\t%s\n' "$2" > "$1/hosts" || return 1
    fi
}

_linux_marked() {
    # $1 = manifest. The index of every linux root the capture marked
    # ("system": true, #1899) -- every one: a disk with two distributions
    # carries two marks. Empty for a manifest from before the mark.
    jq -r '.partitions[] | select(.role == "linux" and .system == true) | .index' "$1" 2>> "$LOG_FILE"
}

_write_hostname_linux() {
    # $1 = disk, $2 = manifest, $3 = name, $4 = verified plan (optional).
    # Emits the section 5 result. Every linux root on the disk is named --
    # until now only the first marked one was, and a second distribution came
    # up with the image's name: #107's failure between two Linuxes. A root
    # that failed is named by its partition, and the others are written all
    # the same (עיקרון 5). One root, or none marked: the single path below.
    _wl_roots=$(_linux_marked "$2")
    case "$_wl_roots" in
        *[!0-9]*) ;;   # more than one index (a newline between them)
        *) _write_linux_root "$1" "$2" "$3" "${4:-}"; return $? ;;
    esac
    _wl_plan="${4:-$RUN_DIR/hostname-linux.plan}"
    if [ -z "${4:-}" ] && ! manifest_plan "$2" > "$_wl_plan"; then
        printf '{"ok":false,"error":"לא הצלחנו לקרוא את תוכנית המחיצות","code":"manifest_plan_failed"}\n'
        return 1
    fi
    _wl_bad=""; _wl_code=""
    for _wl_i in $_wl_roots; do
        awk -F'|' -v i="$_wl_i" '$1 == i' "$_wl_plan" > "$_wl_plan.one"
        _wl_r=$(_write_linux_root "$1" "$2" "$3" "$_wl_plan.one") && continue
        [ -n "$_wl_code" ] || _wl_code=$(printf '%s' "$_wl_r" | sed -n 's/.*"code":"\([^"]*\)".*/\1/p')
        _wl_bad="${_wl_bad:+$_wl_bad; }מחיצה $_wl_i: $(printf '%s' "$_wl_r" | sed -n 's/.*"error":"\([^"]*\)".*/\1/p')"
    done
    if [ -n "$_wl_bad" ]; then
        printf '{"ok":false,"error":"%s","code":"%s"}\n' "$_wl_bad" "${_wl_code:-hostname_write_failed}"
        return 1
    fi
    printf '{"ok":true,"hostname":"%s","method":"%s"}\n' "$3" "$HOSTNAME_METHOD_LINUX"
}

_write_linux_root() {
    # $1 = disk, $2 = manifest, $3 = name, $4 = verified plan (optional).
    # Names the one root _mount_linux finds in the plan; emits the section 5 result.
    _root=$(_mount_linux "$1" "$2" "${4:-}")
    case $? in
        0) ;;
        2) printf '{"ok":false,"error":"אין תיקיית /etc במחיצת הלינוקס","code":"no_etc"}\n'; return 1 ;;
        3) printf '{"ok":false,"error":"אף תת-כרך btrfs אינו מחזיק את /etc","code":"no_root_subvolume"}\n'; return 1 ;;
        4) printf '{"ok":false,"error":"כמה תתי-כרכי btrfs מחזיקים את /etc","code":"ambiguous_root_subvolume"}\n'; return 1 ;;
        *) printf '{"ok":false,"error":"לא הצלחנו לעגן את מחיצת הלינוקס","code":"mount_failed"}\n'; return 1 ;;
    esac
    log "writing hostname $3 into $_root/etc/hostname" >&2
    _write_linux_files "$_root/etc" "$3"
    _rc=$?
    # The mount point, not $_root: with a btrfs subvolume the root is a
    # directory under the top-level mount.
    _umount_checked "$RUN_DIR/linux"
    _umrc=$?
    if [ "$_rc" -ne 0 ]; then
        printf '{"ok":false,"error":"לא הצלחנו לכתוב את /etc/hostname","code":"hostname_write_failed"}\n'
        return 1
    fi
    if [ "$_umrc" -ne 0 ]; then
        printf '{"ok":false,"error":"לא הצלחנו לנתק את המחיצה אחרי כתיבת /etc/hostname","code":"umount_failed"}\n'
        return 1
    fi
    printf '{"ok":true,"hostname":"%s","method":"%s"}\n' "$3" "$HOSTNAME_METHOD_LINUX"
}
