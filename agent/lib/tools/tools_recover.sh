# tools_recover.sh -- read-only catalog inspection (#1104). POSIX sh.

tools_recover_list() {
    cat <<'EOF'
windows-ntfs-cat|windows|קריאת קובץ מתוך NTFS|ro|/dev/sdX1:/path/to/file
ntfs-recover-scan|windows|סריקת קבצים שנמחקו מ-NTFS|ro|/dev/sdX1
unsquashfs|disk|בדיקת כותרת קובץ SquashFS|ro|/path/to/image.squashfs
bsdtar|disk|רשימת קבצים בארכיון|ro|/path/to/archive
EOF
}

_tr_device() {
    case "$1" in /dev/*) ;; *) return 2 ;; esac
    _trname=${1#/dev/}
    case "$_trname" in ''|*[!a-zA-Z0-9]*) return 2 ;; esac
    [ -d "${SYSROOT:-}/sys/class/block/$_trname" ] || return 2
}

_tr_file() {
    case "$1" in /*) [ -f "$1" ] && [ -r "$1" ] && return 0 ;; esac
    printf 'An explicit readable file path is required.\n'
    return 2
}

# windows-ntfs-cat holds the whole file in RAM (RUN_DIR is a ramdisk) and the
# kiosk may have 2GB. So: a size ceiling (TOOLS_CAT_MAX bytes, default 32MB)
# and a time limit (TOOLS_CAT_SECS, default 60). Refused up front when free
# memory cannot hold 4x the ceiling; ulimit -f stops the writer at the
# ceiling; a file at or over it is a FAILURE, never a shortened success
# (principle 5). ntfscat cannot report a size beforehand, so the real size is
# judged after the read. ulimit -f counts 512-byte blocks in dash and busybox
# ash, 1024 in non-POSIX bash: the post-read size check is the rule, ulimit
# only keeps RAM bounded.
_tr_cat() {
    _tcmax=${TOOLS_CAT_MAX:-33554432}; _tcsecs=${TOOLS_CAT_SECS:-60}
    case "$_tcmax$_tcsecs" in ''|*[!0-9]*) printf 'סירוב: תקרת הקריאה אינה מספר תקין.\n'; return 2 ;; esac
    [ "$_tcmax" -gt 0 ] && [ "$_tcsecs" -gt 0 ] || { printf 'סירוב: תקרת הקריאה אינה מספר תקין.\n'; return 2; }
    command -v capped_run >/dev/null 2>&1 || { printf 'סירוב: אין מגבלת זמן לקריאה; הקובץ לא נקרא.\n'; return 2; }
    _tckb=$(awk '/^MemAvailable:/ { print $2; exit }' "${SYSROOT:-}/proc/meminfo" 2>/dev/null)
    case "$_tckb" in ''|*[!0-9]*) printf 'סירוב: לא ניתן לדעת כמה זיכרון פנוי; הקובץ לא נקרא.\n'; return 2 ;; esac
    [ $((_tckb * 1024)) -ge $((_tcmax * 4)) ] || {
        printf 'סירוב: אין מספיק זיכרון פנוי לקריאה (נדרשים %s MB, פנויים %s MB); הקובץ לא נקרא.\n' \
            $((_tcmax * 4 / 1048576)) $((_tckb / 1024)); return 2; }
    _tcdata="$RUN_DIR/tool-windows-ntfs-cat.data"; _tcerr="$RUN_DIR/tool-windows-ntfs-cat.err"
    rm -f "$_tcdata" "$_tcerr"
    capped_run "$_tcsecs" "$_tcdata" \
        sh -c 'ulimit -f "$1" || exit 125; _e=$2; shift 2; exec ntfscat "$@" 2>"$_e"' \
        sh $(((_tcmax + 511) / 512)) "$_tcerr" "$1" "$2"
    _tcrc=$?
    if [ "$CAPPED_TIMED_OUT" = 1 ]; then
        rm -f "$_tcdata" "$_tcerr"
        printf 'סירוב: הקריאה לא הסתיימה תוך %s שניות ונעצרה; הקובץ לא נקרא, אין כאן תוצאה.\n' "$_tcsecs"
        return 2
    fi
    _tcsz=$(wc -c < "$_tcdata" 2>/dev/null | tr -d ' ')
    case "$_tcsz" in ''|*[!0-9]*)
        rm -f "$_tcdata" "$_tcerr"; printf 'סירוב: לא ניתן למדוד את הקובץ שנקרא.\n'; return 2 ;; esac
    if [ "$_tcsz" -ge "$_tcmax" ]; then
        rm -f "$_tcdata" "$_tcerr"
        printf 'סירוב: הקובץ גדול מהתקרה (%s MB) ולכן נקרא רק בחלקו; הוא לא מוצג כי חלקי אינו הקובץ. זה כישלון.\n' \
            $((_tcmax / 1048576))
        return 2
    fi
    if [ "$_tcrc" -ne 0 ]; then
        cat "$_tcerr" 2>/dev/null; rm -f "$_tcdata" "$_tcerr"; return "$_tcrc"
    fi
    cat "$_tcdata"; _tcrc=$?
    rm -f "$_tcdata" "$_tcerr"; return "$_tcrc"
}

_tr_run() {
    case "$1" in
        windows-ntfs-cat)
            _trdev=${2%%:*}; _trpath=${2#*:}
            _tr_device "$_trdev" || { printf 'An explicit NTFS device is required.\n'; return 2; }
            case "$_trpath" in /*) ;; *) printf 'Use /dev/partition:/path/to/file.\n'; return 2 ;; esac
            [ "$2" != "$_trdev" ] || return 2
            _tr_cat "$_trdev" "$_trpath" ;;
        ntfs-recover-scan)
            _tr_device "$2" || { printf 'An explicit NTFS device is required.\n'; return 2; }
            ntfsundelete --scan "$2" </dev/null ;;
        unsquashfs)
            _tr_file "$2" || return 2
            unsquashfs -s "$2" </dev/null ;;
        bsdtar)
            _tr_file "$2" || return 2
            bsdtar -tf "$2" </dev/null ;;
    esac
}

tools_recover_run() {
    _id=${1:-}; _arg=${2:-}
    case "$_id" in
        windows-ntfs-cat) _trbin=ntfscat ;;
        ntfs-recover-scan) _trbin=ntfsundelete ;;
        unsquashfs|bsdtar) _trbin=$_id ;;
        *) return 4 ;;
    esac
    {
        command -v "$_trbin" >/dev/null 2>&1 || { printf 'Missing tool: %s\n' "$_trbin"; return 2; }
        _tr_run "$_id" "$_arg"; _trrc=$?
        if [ "$_trrc" -ne 0 ]; then
            printf '\nRead failed or could not run (rc %s).\n' "$_trrc"; return 2
        fi
        printf '\nמה זה אומר: הקריאה הסתיימה. לא בוצע שחזור או תיקון.\n'
    } > "${RUN_DIR:?}/tool-$_id.out" 2>&1
}
