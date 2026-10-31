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
    case "$1" in
        ''|*[!/a-zA-Z0-9_.-]*) return 1 ;;
        /dev/[a-zA-Z]*) return 0 ;;
        *) return 1 ;;
    esac
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
        { printf '\n--- blkdiscard ---\n'; blkdiscard "$_arg" 2>&1
          _tools_disk_means 'discard הושלם. בקרים ישנים: העדף nvme format / hdparm.'; } >> "$_out"
        return 0 ;;
    disk-hdparm-erase)
        _tools_disk_prep hdparm || return 2
        _tools_disk_destroy_gate "$_out" \
            "hdparm SECURITY ERASE על $_arg — מחיקת כל תוכן SATA." "$_confirm" || return 3
        {
            printf '\n--- security-set-pass + security-erase ---\n'
            hdparm --user-master u --security-set-pass NULL "$_arg" 2>&1
            hdparm --user-master u --security-erase NULL "$_arg" 2>&1
            _tools_disk_means 'בלי שגיאה=הבקר דיווח מחיקה. גשרי USB-SATA לעיתים מתעלמים.'
        } >> "$_out"; return 0 ;;
    disk-nvme-format)
        _tools_disk_prep nvme || return 2
        _tools_disk_destroy_gate "$_out" \
            "nvme format --ses=1 על $_arg — User Data Erase." "$_confirm" || return 3
        { printf '\n--- nvme format --ses=1 ---\n'; nvme format "$_arg" --ses=1 2>&1
          _tools_disk_means 'SES=1 מוחק נתוני משתמש. SES=2 לא נבחר (לא תמיד נתמך).'; } >> "$_out"
        return 0 ;;
    disk-dd-zero)
        _tools_disk_prep dd || return 2
        _tools_disk_destroy_gate "$_out" \
            "dd if=/dev/zero of=$_arg — דריסת כל הסקטורים (איטי)." "$_confirm" || return 3
        { printf '\n--- dd bs=16M ---\n'; dd if=/dev/zero of="$_arg" bs=16M 2>&1
          _tools_disk_means 'נדרס באפסים. על SSD עדיפים blkdiscard/nvme format.'; } >> "$_out"
        return 0 ;;
    disk-wipefs-all)
        _tools_disk_prep wipefs || return 2
        _tools_disk_destroy_gate "$_out" \
            "wipefs -a על $_arg — מחיקת חתימות FS/RAID (לא כל התוכן)." "$_confirm" || return 3
        { printf '\n--- wipefs -a ---\n'; wipefs -a "$_arg" 2>&1
          _tools_disk_means 'חתימות הוסרו; לשכתוב מלא — dd/SE/nvme.'; } >> "$_out"
        return 0 ;;
    *)
        { printf '=== כלי לא מוכר: %s ===\n' "$_id"
          _tools_disk_means 'המזהה אינו ב-tools_disk_list.'; } > "$_out"
        return 2 ;;
    esac
}
