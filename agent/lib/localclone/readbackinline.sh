# readbackinline.sh -- #2023: local clone's concurrent full read-back.
# POSIX sh (busybox ash). Loaded with readback.sh/readbacklc.sh.
# Source digests come from the incoming -a0 stream in each target's writer;
# lccopy's existing whole-stream XXH64 compares that stream to the source's.
# No success is applied until lc_judge accepted all the copy evidence too.
# Repair before failing (Nadav 22/10, fanout-inline.h): a chunk read back wrong is
# rewritten from the source and read again, up to RB_INLINE_TRIES times --
# matched = "repaired" (orange, never "verified"); still wrong = the drawer is
# stopped mid-copy and failed by partition (rb_inline_judge), the others go on.

RB_INLINE_TRIES=2   # fanout-inline.h IR_TRIES

rb_inline_begin() {
    mkdir -p "$LC/run/inline" || return 1
    rm -f "$LC/run/inline/"*.result "$LC/run/inline/"*.source "$LC/run/inline/"*.repaired "$LC/run/inline/selected."* || return 1
    # rb_mode (readback.sh) says sample|full and reads inline as full; only here is it inline.
    case "$(jq -r '.readback_mode // "sample"' "$RUN_DIR/response.json" 2>/dev/null)" in
        inline) echo inline ;; *) rb_mode "$RUN_DIR/response.json" ;;
    esac > "$LC/run/inline/mode"
}

rb_inline_rotational() { cat "${SYSROOT:-}/sys/block/$1/queue/rotational" 2>/dev/null; }

rb_inline_select() {
    # $1 = partition, $2 = fs, $3 = source disk, $4.. = targets.
    # One selection for the partition: a rotational sibling makes the entire
    # partition use the existing full pass (including its one source read).
    LC_INLINE=0; LC_INLINE_WAIT=$WAIT_DRAWER_S
    [ "$(cat "$LC/run/inline/mode" 2>/dev/null)" = inline ] || return 0
    _ri_i=$1; _ri_fs=$2; shift 2; _ri_why=""
    LC_INLINE_SRC=$(lc_src_node "$1" "$_ri_i")   # a repair reads the source again (read-only, #1466)
    case "$_ri_fs" in ntfs|vfat|fat|fat32|fat16|ext2|ext3|ext4) ;;
        *) _ri_why="unsupported partition type '$_ri_fs' (raw/other)" ;;
    esac
    for _ri_disk in "$@"; do
        case "$(rb_inline_rotational "$_ri_disk")" in
            0) ;;
            1) _ri_why="rotational=1 on $_ri_disk" ;;
            *) _ri_why="rotational status unknown on $_ri_disk" ;;
        esac
    done
    LC_INLINE_LAG=${LC_INLINE_LAG_BYTES:-536870912}
    # A small explicit lag is useful on loop devices. On hardware the operator
    # must set it ABOVE the drive's cache; never silently lower the default.
    case "$LC_INLINE_LAG" in ''|*[!0-9]*) _ri_why="invalid LC_INLINE_LAG_BYTES" ;;
        *) if [ "${#LC_INLINE_LAG}" -gt 13 ] || [ "$LC_INLINE_LAG" -lt 4194304 ] || [ "$LC_INLINE_LAG" -gt 1099511627776 ]; then
               _ri_why="LC_INLINE_LAG_BYTES outside 4MiB..1TiB"
           fi ;;
    esac
    if [ -n "$_ri_why" ]; then
        log "readback: inline partition $_ri_i: falling back to after-copy full readback: $_ri_why"
        return 0
    fi
    # A selected partition MUST produce a result for every surviving target.
    # Missing/empty evidence cannot silently turn into a successful empty pass.
    if ! echo "$_ri_i" > "$LC/run/inline/selected.$_ri_i"; then
        log "readback: inline partition $_ri_i: falling back to after-copy full readback: cannot record selection"
        return 0
    fi
    LC_INLINE=1; LC_INLINE_WAIT=$WAIT_READBACK_S
    log "readback: inline partition $_ri_i: lag $LC_INLINE_LAG bytes, full check with O_DIRECT"
}

rb_inline_restore() {
    # $1 target, $2 index, $3 partition node. stdin is the relay FIFO.
    fanout --inline-write "$3" "$LC_INLINE_SRC" "$LC/run/inline/$1.$2.result" "$LC_INLINE_LAG" "$WAIT_READBACK_S" \
        > "$LC/run/inline/$1.$2.source" 2>> "$LOG_FILE"
}

rb_inline_judge() {
    # $1 target, $2 index: lc_judge's first word. A chunk still wrong after its
    # rewrites fails the target here, by partition, before any later partition
    # is written to it. 0 = failed here; 1 = nothing to say (not inline, not failed).
    [ -f "$LC/run/inline/selected.$2" ] || return 1
    _rj_r=$(cat "$LC/run/inline/$1.$2.result" 2>/dev/null) || return 1
    case "$_rj_r" in failed\|*) ;; *) return 1 ;; esac
    fail_written_target "$1" "מחיצה $2: $READBACK_FAIL (חתיכה ${_rj_r#*|} של 4MiB לא תאמה למקור גם אחרי $RB_INLINE_TRIES כתיבות מחדש)"
    return 0
}

rb_inline_repaired() {
    # $1 target. 0 + the orange line when the reader rewrote chunks that then read back right.
    [ -s "$LC/run/inline/$1.repaired" ] || return 1
    awk '{ n += $2; p = p (p == "" ? "" : ", ") "מחיצה " $1 ": " $2 }
         END { printf "%d חתיכות נכתבו מחדש ותוקנו — מומלץ לבדוק את הדיסק (%s)", n, p }' "$LC/run/inline/$1.repaired"
}

rb_inline_collect() {
    # RB_ALL still names ALL non-swap partitions. Consume the inline results,
    # then leave only the fallback partitions for _rb_raw's usual full pass.
    : > "$RB_ALL.after" || return 1
    while IFS='|' read -r _ri_i _ri_fs _ri_b <&4; do
        if [ ! -f "$LC/run/inline/selected.$_ri_i" ]; then
            printf '%s|%s|%s\n' "$_ri_i" "$_ri_fs" "$_ri_b" >> "$RB_ALL.after" || return 1
            continue
        fi
        for _ri_t in "$@"; do
            case "$(cat "$RUN_DIR/targets/$_ri_t/state" 2>/dev/null)" in failed|cancelled) continue ;; esac
            _ri_result=$(cat "$LC/run/inline/$_ri_t.$_ri_i.result" 2>/dev/null) || _ri_result=""
            _ri_why=${_ri_result#*|}; [ -n "$_ri_result" ] || _ri_why="לא נרשמה תוצאה"
            case "$_ri_result" in
                ok) ;;
                repaired\|*) echo "$_ri_i $_ri_why" >> "$LC/run/inline/$_ri_t.repaired" || return 1 ;;
                failed\|*) _rb_apply "$_ri_t" "failed|מחיצה $_ri_i: $READBACK_FAIL (חתיכה $_ri_why של 4MiB)" ;;
                *) _rb_apply "$_ri_t" "unchecked|מחיצה $_ri_i: הקריאה במקביל לא הושלמה ($_ri_why)" ;;
            esac
        done
    done 4< "$RB_ALL"
    mv "$RB_ALL.after" "$RB_ALL"
}
