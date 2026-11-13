# tools_boot.sh -- UEFI/boot toolbox (#649 domain 5). POSIX sh (busybox ash).
#
# #1050: only esp-fsck-repair -- the one id of this domain that Nadav chose in
# server/tools_catalog.json -- remains. The other eight (NVRAM entries,
# Secure Boot state, memtest, ESP inventory) were removed, not hidden: the
# read-only ones go to the machine card (#1049), BootNext/orphans to #433.

tools_boot_list() {
    cat <<'EOF'
esp-fsck-repair|boot|תיקון ESP ב-fsck.vfat|rw|מחיצת ESP
EOF
}

_tb_say() { printf '\nמה זה אומר:\n%s\n' "$1"; }
_tb_confirm() {
    _tbc=${MACHINE_NAME:-$(hostname 2>/dev/null || true)}
    [ -n "$_tbc" ] && [ "$1" = "$_tbc" ]
}
_tb_need() {
    command -v "$1" >/dev/null 2>&1 && return 0
    printf '=== כלי חסר: %s ===\nהבינארי אינו ב-PATH.\nלא נבדק — אין תוצאה תקינה.\n' "$1"
    _tb_say "הכלי חסר. זה אינו תקין ואינו כבוי — פשוט לא נבדק."
    return 2
}

_tb_part_ok() {
    # #2486: like _tools_disk_dev_ok (#2431), for a partition -- its plain
    # name (never ../), listed in /sys/class/block, on exactly one disk the
    # kernel lists that is not loop/md/... and not the clone stick.
    case "${1:-}" in /dev/*) ;; *) return 1 ;; esac
    _tbp=${1#/dev/}
    case "$_tbp" in ''|*[!a-z0-9]*) return 1 ;; esac
    [ -e "${SYSROOT:-}/sys/class/block/$_tbp" ] || return 1
    _tbd=""
    for _tbx in "${SYSROOT:-}"/sys/block/*/"$_tbp"; do
        [ -d "$_tbx" ] || continue
        [ -z "$_tbd" ] || return 1
        _tbd=${_tbx%/*}; _tbd=${_tbd##*/}
    done
    case "$_tbd" in ''|loop*|ram*|zram*|md*|sr*|fd*|nbd*|dm-*) return 1 ;; esac
    _tbl="${RUN_DIR:-/nonexistent}/usb-media.disks"
    [ -e "$_tbl" ] || return 0   # not booted from the stick: no list
    # grep: 0 = the stick (refuse), 1 = not listed, else the list did not read -- refused (#231)
    grep -qx "$_tbd" "$_tbl"; [ $? -eq 1 ]
}

_tb_esp_fsck_repair() {
    _tb_need fsck.vfat || return 2
    _tb_part_ok "${1:-}" || { echo "מחיצת ESP לא חוקית: ${1:-"(ריק)"} — נדרשת מחיצה (/dev/sda1) שהקרנל מכיר, לא מקל השיכפול"; return 1; }
    _tb_confirm "${2:-}" || {
        echo "=== תיקון ESP ==="; echo "יעד: $1"; echo "פעולה: fsck.vfat -a"
        echo "הקלד שם מכונה."; _tb_say "בלי אישור לא כותבים."; return 3
    }
    echo "=== fsck.vfat -a $1 ==="; fsck.vfat -a "$1" 2>&1; _rc=$?
    _tb_say "קוד $_rc (0=נקי, 1=תוקן). אחרי תיקון — בדיקת ESP שוב."; return $_rc
}

tools_boot_run() {
    _id=$1; _arg=${2:-}; _cnf=${3:-}
    mkdir -p "$RUN_DIR" || return 1
    _out="$RUN_DIR/tool-$_id.out"
    _tmp="$_out.next"
    _rc=0
    case "$_id" in
        esp-fsck-repair)    _tb_esp_fsck_repair "$_arg" "$_cnf" >"$_tmp" 2>&1; _rc=$? ;;
        *)
            printf 'boot: מזהה לא מוכר: %s\n\nמה זה אומר:\nלא ברשימה.\n' "$_id" >"$_tmp"
            _rc=2
            ;;
    esac
    mv "$_tmp" "$_out" || cp "$_tmp" "$_out" || return 1
    return "$_rc"
}
