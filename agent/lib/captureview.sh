# captureview.sh -- #2213: Fast Startup is dropped on a RAM view, never on
# the source. POSIX sh (busybox ash). lcsnap.sh owns the views and their
# budget; ntfsunhib.sh owns the drop and its clean read-back.

capture_ntfs_gate() {
    # $1 disk, $2 index, $3 node, $4 gate rc, $5 reason. Keep the old
    # dirty/unchecked refusals. rc 1 includes probe 15: only positive
    # hibernation (14) may proceed to a view, never a dirty-only volume.
    CAPTURE_NTFS_ERROR="$5"; CAPTURE_NTFS_CODE=source_disk
    [ -n "$CAPTURE_NTFS_ERROR" ] || [ "$4" = 0 ] || CAPTURE_NTFS_ERROR="שערי המקור לא רצו (capturegate.sh) — לא נקלט"
    [ "$4" != 0 ] || { [ -z "$5" ]; return; }
    [ "$4" = 1 ] || return 1
    CAPTURE_NTFS_CODE=source_ntfs
    _cv_state=$(ntfs_state "$3")
    case "$_cv_state" in
        hibernated) ;;
        dirty|clean) return 1 ;;
        *) CAPTURE_NTFS_ERROR="מחיצה $2: לא הצלחנו לבדוק אם Windows בה כובה עד הסוף (${_cv_state#unchecked:})"; CAPTURE_NTFS_CODE=source_disk; return 1 ;;
    esac
    echo "$2" >> "$RUN_DIR/capture.unhib"
}

capture_prepare_views() {
    # Shrink keeps its existing measurement and operator choice. If it makes
    # views, capture_snap adds every hibernated partition to that same plan.
    # Plain/no shrink still needs the full-size hibernated views.
    shrink_before_capture "$1" "$2" "$3" || return 1
    [ -s "$RUN_DIR/capture.unhib" ] || return 0
    [ ! -f "$RUN_DIR/src_unhib" ] || return 0
    : > "$RUN_DIR/shrink.plan" || return 1
    capture_snap "$1" "$2"
}

capture_snap() {
    # Same entry as _shrink_view: $1 disk, $2 parts, shrink.plan may be empty.
    # All planned views are budgeted together before any drop/resize.
    LC_ERROR=""; LC_SNAP_RUN="$RUN_DIR/capsnap"
    mkdir -p "$LC_SNAP_RUN" && cp "$2" "$LC_SNAP_RUN/parts.txt" &&
        awk -F'|' -v OFS='|' -v hib="$RUN_DIR/capture.unhib" -v plan="$RUN_DIR/shrink.plan" '
            FILENAME == hib { h[$1] = 1; next }
            FILENAME == plan { p[$1] = $0; next }
            $1 in p { print p[$1]; next }
            $1 in h { b = sprintf("%.0f", $5 * 512); print $0, b, b }
        ' "$RUN_DIR/capture.unhib" "$RUN_DIR/shrink.plan" "$2" > "$LC_SNAP_RUN/shrink.plan" || {
            SHRINK_ERROR="לא הצלחנו להכין את העותק בזיכרון — המקור לא נכתב"; return 1; }
    lc_snap_shrink "$1" capture; _cv_rc=$?
    if [ "$_cv_rc" != 0 ]; then
        SHRINK_ERROR="${LC_ERROR:-$LC_SNAP_WHY}"; return 1   # never direct send's optional-shrink fallback
    fi
    # The flag outlives teardown, and is shared by the manifest and report.
    if [ ! -s "$LC_SNAP_RUN/src_unhib" ]; then
        SHRINK_ERROR="לא הצלחנו לרשום שההשהיה בוטלה בעותק — המקור לא נכתב"; return 1
    fi
    while IFS='|' read -r _cv_idx _cv_rest; do
        _cv_node=$(cat "$LC_SNAP_RUN/snap/$_cv_idx.node" 2>/dev/null)
        case "$_cv_node" in "$DEVROOT/mapper/lc-src-$_cv_idx"|"$DEVROOT/mapper/lc-cut-$_cv_idx")
            [ ! -e "$_cv_node" ] || { _snap_valid "lc-src-$_cv_idx" && continue; } ;;
        esac
        SHRINK_ERROR="העותק של מחיצה $_cv_idx לא נקרא בחזרה כפי שנוצר — הפרטים ביומן"; return 1
    done < "$LC_SNAP_RUN/shrink.plan"
    cp "$LC_SNAP_RUN/src_unhib" "$RUN_DIR/src_unhib" && cp "$LC_SNAP_RUN/parts.txt" "$2" || {
        SHRINK_ERROR="לא הצלחנו לרשום שההשהיה בוטלה בעותק — המקור לא נכתב"; return 1; }
}

capture_unhib_note() {
    # Existing local-clone wording; the task's error field carries warnings
    # even on success. Reapply after direct send initializes its second pass.
    case "$(cat "$RUN_DIR/src_unhib" 2>/dev/null)" in
        unhibernated) _cv_note=$LC_NTFS_UNHIB ;;
        unhibernated_logfile) _cv_note=$LC_NTFS_UNHIB_LOGFILE ;;
        *) [ ! -e "$RUN_DIR/src_unhib" ]; return ;;
    esac
    _cv_existing=""
    [ ! -f "$RUN_DIR/targets/$1/error" ] || _cv_existing=$(cat "$RUN_DIR/targets/$1/error") || return 1
    case "$_cv_existing" in *"$_cv_note"*) return 0 ;; esac
    printf '%s\n' "${_cv_existing:+$_cv_existing · }$_cv_note" > "$RUN_DIR/targets/$1/error" || return 1
    log "WARNING: $1: done, orange -- $_cv_note"
}

capture_unhib_json() {
    case "$(cat "$RUN_DIR/src_unhib" 2>/dev/null)" in
        unhibernated|unhibernated_logfile) printf ',"src_unhib":"%s"' "$(cat "$RUN_DIR/src_unhib")" ;;
    esac
}
