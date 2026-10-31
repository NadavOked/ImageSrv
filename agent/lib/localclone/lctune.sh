# lctune.sh -- 18/10: the local clone toward the SATA II ceiling (~280MB/s;
# measured 17/10: 224MB/s, direct read of the source 267). The knobs a metal
# measurement turns, one per run (principle 5c), for the copy only, restored
# after. POSIX sh (busybox ash).
#
# Read from "$LC/tune" -- KEY=<digits> lines, written by hand over SSH in the
# lab before lc-start. No file = exactly the product. An unknown key or a value
# that is not a number is refused aloud, never half-applied; a knob the kernel
# did not take is logged as not applied (principle 5: "set" and "did not set"
# are two states). Nothing here can make a target "ok": LC_EXP_DIRECT drops the
# xxh64 evidence and fails, by name, every target it wrote.
#
#   LC_SRC_Z            partclone -c buffer (-z), bytes. 524288 (measured 15/10)
#   LC_SRC_RA_KB        the source disk's read-ahead (sysfs read_ahead_kb): a
#                       kernel setting, nothing is written to the disk
#   LC_DIRTY_BG_BYTES   vm.dirty_background_bytes: write-back starts sooner.
#                       DEFAULT 64MB, also with no tune file: metal 18/10, cloner 2,
#                       MZ7LN256 -> 870, 44.18GB, copy_s 188/188/187 without,
#                       183/183/182 with (#1749). The write to the target is the
#                       limit there (partclone -b alone: 190). 0 = leave the kernel's.
#   FANOUT_PIPE_BYTES   the pipes fanout widens (src, feed, relay); fanout.c
#   FANOUT_CHUNK        fanout's read size; fanout.c
#   LC_EXP_DIRECT       1 = partclone -b source -> target: no fanout, no hash,
#                       the tool's own ceiling. One target; no stop button.

LC_TUNE_KEYS="LC_SRC_Z LC_SRC_RA_KB LC_DIRTY_BG_BYTES FANOUT_PIPE_BYTES FANOUT_CHUNK LC_EXP_DIRECT"
LC_SRC_Z_DEFAULT=524288
LC_DIRTY_BG_BYTES_DEFAULT=67108864
LC_SRC_Z="$LC_SRC_Z_DEFAULT"

_tune_put() {
    # $1 = file, $2 = value. 0 only when the value reads back. A test run with
    # no SYSROOT never writes the host's kernel (IMAGESRV_TEST, as poweroff).
    [ "${IMAGESRV_TEST:-0}" != 1 ] || [ -n "${SYSROOT:-}" ] || return 1
    { printf '%s\n' "$2" > "$1"; } 2>/dev/null && [ "$(cat "$1" 2>/dev/null)" = "$2" ]
}

lc_tune_begin() {
    # $1 = source disk, $2.. = targets. Sets LC_TUNE (what was applied, for the log).
    _tu_src="$1"; shift
    LC_SRC_Z="$LC_SRC_Z_DEFAULT"; LC_SRC_RA_KB=""; LC_DIRTY_BG_BYTES=$LC_DIRTY_BG_BYTES_DEFAULT; LC_EXP_DIRECT=0
    unset FANOUT_PIPE_BYTES FANOUT_CHUNK
    LC_TUNE=""; rm -f "$LC/run/tune.ra" "$LC/run/tune.dirty"
    for _tu_t in "$@"; do rm -f "$RUN_DIR/targets/$_tu_t/lc_exp"; done
    [ -f "$LC/tune" ] && while IFS='=' read -r _tu_k _tu_v; do
        case "$_tu_k" in ''|'#'*) continue ;; esac
        case " $LC_TUNE_KEYS " in *" $_tu_k "*) ;; *) log "local clone tune: unknown key '$_tu_k' -- refused"; continue ;; esac
        case "$_tu_v" in ''|*[!0-9]*) log "local clone tune: $_tu_k='$_tu_v' is not a number -- refused"; continue ;; esac
        case "$_tu_k" in
            LC_SRC_Z) LC_SRC_Z=$_tu_v ;;
            LC_SRC_RA_KB) LC_SRC_RA_KB=$_tu_v ;;
            LC_DIRTY_BG_BYTES) LC_DIRTY_BG_BYTES=$_tu_v ;;
            FANOUT_PIPE_BYTES) FANOUT_PIPE_BYTES=$_tu_v; export FANOUT_PIPE_BYTES ;;
            FANOUT_CHUNK) FANOUT_CHUNK=$_tu_v; export FANOUT_CHUNK ;;
            LC_EXP_DIRECT)
                # shellcheck disable=SC2034 # read by lc_stream (lccopy.sh)
                LC_EXP_DIRECT=$_tu_v ;;
        esac
        LC_TUNE="$LC_TUNE${LC_TUNE:+ }$_tu_k=$_tu_v"
    done < "$LC/tune"
    _tu_ra="${SYSROOT:-}/sys/block/$_tu_src/queue/read_ahead_kb"
    if [ -n "$LC_SRC_RA_KB" ]; then
        _tu_old=$(cat "$_tu_ra" 2>/dev/null)
        if [ -n "$_tu_old" ] && _tune_put "$_tu_ra" "$LC_SRC_RA_KB"; then echo "$_tu_old" > "$LC/run/tune.ra"
        else LC_TUNE="$LC_TUNE (LC_SRC_RA_KB not applied)"; fi
    fi
    _tu_vm="${SYSROOT:-}/proc/sys/vm"
    case " $LC_TUNE " in *" LC_DIRTY_BG_BYTES="*) ;; *) LC_TUNE="$LC_TUNE${LC_TUNE:+ }LC_DIRTY_BG_BYTES=$LC_DIRTY_BG_BYTES (default)" ;; esac
    if [ "$LC_DIRTY_BG_BYTES" != 0 ]; then
        _tu_old="$(cat "$_tu_vm/dirty_background_bytes" 2>/dev/null)|$(cat "$_tu_vm/dirty_background_ratio" 2>/dev/null)"
        if _tune_put "$_tu_vm/dirty_background_bytes" "$LC_DIRTY_BG_BYTES"; then echo "$_tu_old" > "$LC/run/tune.dirty"
        else LC_TUNE="$LC_TUNE (LC_DIRTY_BG_BYTES not applied)"; fi
    fi
    log "local clone tune: ${LC_TUNE:-none}"; printf '%s\n' "$LC_TUNE" > "$LC/run/tune.applied"
}

lc_tune_end() {
    # $1.. = targets. The kernel settings back as they were; a target written
    # without evidence (LC_EXP_DIRECT) fails here, after every partition.
    if [ -f "$LC/run/tune.ra" ]; then
        _tune_put "${SYSROOT:-}/sys/block/$_tu_src/queue/read_ahead_kb" "$(cat "$LC/run/tune.ra")" \
            || log "local clone tune: the source read-ahead was not restored"
    fi
    if [ -f "$LC/run/tune.dirty" ]; then
        IFS='|' read -r _tu_b _tu_r < "$LC/run/tune.dirty"
        if [ "${_tu_b:-0}" != 0 ]; then _tune_put "${SYSROOT:-}/proc/sys/vm/dirty_background_bytes" "$_tu_b"
        else _tune_put "${SYSROOT:-}/proc/sys/vm/dirty_background_ratio" "$_tu_r"; fi \
            || log "local clone tune: vm.dirty_background was not restored"
    fi
    unset FANOUT_PIPE_BYTES FANOUT_CHUNK
    for _tu_t in "$@"; do
        [ -f "$RUN_DIR/targets/$_tu_t/lc_exp" ] || continue
        fail_written_target "$_tu_t" "ניסוי (partclone -b, בלי fanout): מחיצות $(tr '\n' ' ' < "$RUN_DIR/targets/$_tu_t/lc_exp")נכתבו בלי xxh64 — אין ראיה, היעד אינו מאומת"
    done
}

lc_exp_direct() {
    # $1 = idx, $2 = fs, $3 = source, $4.. = targets. LC_EXP_DIRECT=1 only: the
    # partition device to device, the seconds in the log. 0 = go on.
    _xd_i="$1"; _xd_pcl=$(partclone_for_fs "$2"); _xd_src="$3"; shift 3
    _xd_o=""; [ "$_xd_pcl" = partclone.dd ] || _xd_o="-b -z $LC_SRC_Z"
    [ "$_xd_pcl" = partclone.ntfs ] && _xd_o="$_xd_o -I"
    echo "$_xd_i|copy" > "$LC/run/cur"
    for _xd_t in "$@"; do
        _xd_d="$RUN_DIR/targets/$_xd_t"
        [ "$(cat "$_xd_d/state")" = failed ] && continue
        rm -f "$_xd_d/exp.rc"; _xd_t0=$(date +%s)
        # shellcheck disable=SC2086 # the option list is a word list on purpose
        ( "$_xd_pcl" $_xd_o -s "$(lc_src_node "$_xd_src" "$_xd_i")" -O "$(partition_node "$_xd_t" "$_xd_i")" \
              -L "$_xd_d/partclone.log" 2>> "$LOG_FILE"; echo "$?" > "$_xd_d/exp.rc" ) &
        wait_pid "$!" "$WAIT_DRAWER_S" "partclone -b $_xd_t (partition $_xd_i)"
        _xd_rc=$(cat "$_xd_d/exp.rc" 2>/dev/null)
        log "local clone tune: partclone -b partition $_xd_i -> $_xd_t rc=${_xd_rc:-none} in $(( $(date +%s) - _xd_t0 )) s"
        if [ "$_xd_rc" = 0 ]; then echo "$_xd_i" >> "$_xd_d/lc_exp"
        else fail_written_target "$_xd_t" "מחיצה $_xd_i: partclone -b נכשל (rc=${_xd_rc:-לא נרשם})"; fi
    done
    echo "$_xd_i|done" > "$LC/run/cur"
    _any_alive "$*"
}
