# tools_disk.sh -- #649 domain disk: the wipe-before-handover tools.
# POSIX sh (busybox ash). Sourced by tools.sh; safe alone in tests.
#
# #1050: only the ids Nadav chose in server/tools_catalog.json live here --
# the twelve read-only inspection tools of the original module were removed,
# not hidden. The framework (tools.sh) already refuses destroy without the
# machine name; the gate below repeats it with the same name so the module
# is safe when called alone.

tools_disk_list() {
    cat <<'EOF'
disk-blkdiscard|disk|מחיקת SSD מהירה (TRIM לכל הבלוקים)|destroy|/dev/sdX
disk-hdparm-erase|disk|מחיקה מאובטחת ATA (hdparm)|destroy|/dev/sdX
disk-nvme-format|disk|פורמט NVMe עם SES=1|destroy|/dev/nvmeXnY
disk-dd-zero|disk|דריסה באפסים (dd) — גיבוי איטי|destroy|/dev/sdX
disk-wipefs-all|disk|מחיקת חתימות מערכת קבצים|destroy|/dev/sdX
EOF
}

_tools_disk_host() {
    if [ -n "${MACHINE_NAME:-}" ]; then printf '%s\n' "$MACHINE_NAME"
    else hostname 2>/dev/null || printf 'unknown\n'; fi
}

_tools_disk_confirm_ok() {
    _exp=$(_tools_disk_host)
    [ -n "$1" ] && [ "$1" = "$_exp" ]
}

_tools_disk_need() {
    command -v "$1" >/dev/null 2>&1 && return 0
    {
        printf '=== כלי חסר: %s ===\nלא נבדק — הבינארי אינו ב-PATH.\n\nמה זה אומר\n' "$1"
        printf 'אין תוצאה תקינה. חסר %s — ראה docs/tools/disk.md.\n' "$1"
    } > "$2"
    return 2
}

_tools_disk_dev_ok() {
    # #2431: a whole disk the kernel lists, by its plain name -- never a path
    # around /dev (../), a partition, loop/dm/ram/md/optical, or the clone stick.
    case "$1" in /dev/*) ;; *) return 1 ;; esac
    _tdn=${1#/dev/}
    case "$_tdn" in ''|*[!a-z0-9]*|loop*|ram*|zram*|md*|sr*|fd*|nbd*) return 1 ;; esac
    [ -d "${SYSROOT:-}/sys/block/$_tdn" ] || return 1
    # grep: 0 = the clone stick (refuse), 1 = not listed, 2 = no list (not
    # booted from the stick) -- named, not `! grep` (#231).
    if grep -qx "$_tdn" "${RUN_DIR:-/nonexistent}/usb-media.disks" 2>/dev/null; then
        return 1
    fi
    return 0
}

_tools_disk_ident() {   # $1 = /dev/<disk>: model, serial and size -- what is about to be erased
    _tdb="${SYSROOT:-}/sys/block/${1#/dev/}"
    _tds=$(cat "$_tdb/size" 2>/dev/null); case "$_tds" in ''|*[!0-9]*) _tds=0 ;; esac
    # device/serial is NVMe only: disk_serial (diskinfo.sh) reads SATA too; alone (tests), the file
    if command -v disk_serial >/dev/null 2>&1; then _tdr=$(disk_serial "${1#/dev/}"); else _tdr=$(tr -d '\r\n' < "$_tdb/device/serial" 2>/dev/null); fi
    printf 'הדיסק: %s · סידורי %s · %s בתים\n' "$(tr -d '\r\n' < "$_tdb/device/model" 2>/dev/null)" "$_tdr" "$((_tds * 512))"
}

_tools_disk_failed() {   # $1 = the step, $2 = its exit code, $3 = what it means -> rc 1, never the success words
    printf '\n%s נכשל (rc %s).\n' "$1" "$2"
    _tools_disk_means "${3:-ההתקן לא נמחק במלואו. אין לראות בו דיסק נקי.}"
    return 1
}

_tools_disk_bad_dev() {
    {
        printf '=== התקן לא חוקי ===\nהארגומנט %s אינו /dev/... תקין.\n\nמה זה אומר\n' "${1:-"(ריק)"}"
        printf 'לא הורצה פעולה. ספק /dev/sda או /dev/nvme0n1.\n'
    } > "$2"
    return 2
}

_tools_disk_means() { printf '\nמה זה אומר\n%s\n' "$1"; }

# $1=out $2=action text $3=confirm → 0 or 3
_tools_disk_destroy_gate() {
    _host=$(_tools_disk_host)
    {
        printf '=== פעולה הרסנית ===\n%s\n' "$2"
        _tools_disk_ident "$_arg"
        printf 'יימחק לצמיתות על ההתקן. אין שחזור.\nלאישור הקלד שם מכונה: %s\n' "$_host"
    } > "$1"
    if ! _tools_disk_confirm_ok "${3:-}"; then
        printf '\nסורב — confirm אינו שם המכונה.\n' >> "$1"
        _tools_disk_means 'לא בוצעה מחיקה. הקלד את שם המכונה המדויק.' >> "$1"
        return 3
    fi
    return 0
}

# Require binary + device; writes $_out on failure. Returns 2 on failure.
_tools_disk_prep() {
    _tools_disk_need "$1" "$_out" || return 2
    _tools_disk_dev_ok "$_arg" || { _tools_disk_bad_dev "$_arg" "$_out"; return 2; }
    return 0
}

tools_disk_run() {
    _id=${1:-}; _arg=${2:-}; _confirm=${3:-}
    _out="${RUN_DIR:?}/tool-${_id}.out"
    : > "$_out"

    case "$_id" in
    disk-blkdiscard)
        _tools_disk_prep blkdiscard || return 2
        _tools_disk_destroy_gate "$_out" \
            "blkdiscard על $_arg — TRIM לכל הבלוקים (SSD/NVMe)." "$_confirm" || return 3
        { printf '\n--- blkdiscard ---\n'; blkdiscard "$_arg" 2>&1 || _tools_disk_failed blkdiscard $? || return 1
          _tools_disk_means 'discard הושלם. בקרים ישנים: העדף nvme format / hdparm.'; } >> "$_out"
        return 0 ;;
    disk-hdparm-erase)
        _tools_disk_prep hdparm || return 2
        _tools_disk_destroy_gate "$_out" \
            "hdparm SECURITY ERASE על $_arg — מחיקת כל תוכן SATA." "$_confirm" || return 3
        {
            printf '\n--- security-set-pass + security-erase ---\n'
            hdparm --user-master u --security-set-pass NULL "$_arg" 2>&1 ||
                _tools_disk_failed security-set-pass $? 'לא נקבעה סיסמה ולא בוצעה מחיקה.' || return 1
            hdparm --user-master u --security-erase NULL "$_arg" 2>&1; _tde=$?
            if [ "$_tde" -eq 0 ]; then
                _tools_disk_means 'הבקר דיווח מחיקה. גשרי USB-SATA לעיתים מתעלמים.'; return 0
            fi
            # #2431: the NULL password stays after a failed erase -- the disk locks at power-off
            if hdparm --user-master u --security-disable NULL "$_arg" 2>&1; then
                _tools_disk_failed security-erase "$_tde" 'הדיסק לא נמחק. הסיסמה הוסרה — הדיסק לא יינעל.'
            else
                _tools_disk_failed security-erase "$_tde" "הדיסק לא נמחק והסיסמה NULL נשארה: הוא יינעל אחרי כיבוי. לפתיחה: hdparm --user-master u --security-unlock NULL $_arg ואז --security-disable NULL."
            fi
        } >> "$_out" ;;
    disk-nvme-format)
        _tools_disk_prep nvme || return 2
        _tools_disk_destroy_gate "$_out" \
            "nvme format --ses=1 על $_arg — User Data Erase." "$_confirm" || return 3
        { printf '\n--- nvme format --ses=1 ---\n'; nvme format "$_arg" --ses=1 2>&1 || _tools_disk_failed 'nvme format' $? || return 1
          _tools_disk_means 'SES=1 מוחק נתוני משתמש. SES=2 לא נבחר (לא תמיד נתמך).'; } >> "$_out"
        return 0 ;;
    disk-dd-zero)
        _tools_disk_prep dd || return 2
        _tools_disk_destroy_gate "$_out" \
            "dd if=/dev/zero of=$_arg — דריסת כל הסקטורים (איטי)." "$_confirm" || return 3
        # #2431: to the disk's own size, in two exact counts -- a dd that runs off the
        # end of a device ends ENOSPC (rc 1), which is evidence of nothing; conv=fsync: on the
        # disk before rc 0 (busybox dd has it -- hibernation.sh, splash.sh), not in the page cache
        _tds=$(cat "${SYSROOT:-}/sys/block/${_arg#/dev/}/size" 2>/dev/null); case "$_tds" in ''|*[!0-9]*) _tds=0 ;; esac
        { printf '\n--- dd bs=16M ---\n'; [ "$_tds" -gt 0 ] || _tools_disk_failed 'גודל הדיסק (sysfs)' 2 || return 1
          [ $((_tds / 32768)) -eq 0 ] || dd if=/dev/zero of="$_arg" bs=16M count=$((_tds / 32768)) conv=notrunc,fsync 2>&1 || _tools_disk_failed dd $? || return 1
          [ $((_tds % 32768)) -eq 0 ] || dd if=/dev/zero of="$_arg" bs=512 seek=$((_tds / 32768 * 32768)) count=$((_tds % 32768)) conv=notrunc,fsync 2>&1 || _tools_disk_failed dd $? || return 1
          _tools_disk_means 'נדרס באפסים. על SSD עדיפים blkdiscard/nvme format.'; } >> "$_out"
        return 0 ;;
    disk-wipefs-all)
        _tools_disk_prep wipefs || return 2
        _tools_disk_destroy_gate "$_out" \
            "wipefs -a על $_arg — מחיקת חתימות FS/RAID (לא כל התוכן)." "$_confirm" || return 3
        { printf '\n--- wipefs -a ---\n'; wipefs -a "$_arg" 2>&1 || _tools_disk_failed 'wipefs -a' $? || return 1
          _tools_disk_means 'חתימות הוסרו; לשכתוב מלא — dd/SE/nvme.'; } >> "$_out"
        return 0 ;;
    *)
        { printf '=== כלי לא מוכר: %s ===\n' "$_id"
          _tools_disk_means 'המזהה אינו ב-tools_disk_list.'; } > "$_out"
        return 2 ;;
    esac
}
