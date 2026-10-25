# identify.sh -- "איזו מגירה זו?": a drawer's activity LED blinks from reads (#1966).
# POSIX sh (busybox ash).
#
# Nadav 22/10: a button by each disk of the cloner's standby screen; the disk
# is READ for ~15 s and its drawer's LED shows which one it is -- no port
# mapping needed. Never while that disk is written.
#
# The GUI drops $GUI_DIR/identify-request (the device name, renamed whole);
# identify_watch takes it with mv. Reads only (dd to /dev/null), and every
# chunk starts at a new offset: the same 16 MiB again would come from the page
# cache and the LED would stay dark. The watcher is started only from the
# cloner's idle branch (wait_poll), and every write path stops it first --
# do_restore_drawers and lc_session -- and WAITS for it (identify.stop, the
# ceiling IDENTIFY_STOP_S), so no read is left running under the first write.

IDENTIFY_SECONDS="${IDENTIFY_SECONDS:-15}"
IDENTIFY_STOP_S="${IDENTIFY_STOP_S:-10}"

identify_writing() {
    # 0 = something writes, or may: this disk's target, the machine, a local copy.
    [ "$(cat "$RUN_DIR/targets/$1/state" 2>/dev/null)" = writing ] && return 0
    [ "$(cat "$RUN_DIR/state" 2>/dev/null)" = writing ] && return 0
    command -v lc_copy_busy >/dev/null 2>&1 && lc_copy_busy && return 0
    return 1
}

identify_disk() {
    # $1 = disk name. 0 = read for IDENTIFY_SECONDS or stopped by identify.stop.
    case " $(list_drawers | tr '\n' ' ') " in *" $1 "*) ;; *) log "identify: $1 is not a drawer -- refused"; return 1 ;; esac
    _id_mib=$(( $(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null || echo 0) / 2048 ))
    [ "$_id_mib" -ge 16 ] || { log "identify: the size of $1 is not readable -- refused"; return 1; }
    log "identify: reading $1 for ${IDENTIFY_SECONDS}s"
    _id_end=$(($(date +%s) + IDENTIFY_SECONDS)); _id_at=0
    while [ "$(date +%s)" -lt "$_id_end" ]; do
        [ -e "$RUN_DIR/identify.stop" ] && { log "identify: $1 stopped -- a write is starting"; return 0; }
        identify_writing "$1" && { log "identify: $1 is being written -- stopped"; return 1; }
        dd if="$DEVROOT/$1" of=/dev/null bs=1048576 count=16 skip="$_id_at" 2>> "$LOG_FILE" \
            || { log "identify: reading $1 failed"; return 1; }
        _id_at=$((_id_at + 16)); [ $((_id_at + 16)) -le "$_id_mib" ] || _id_at=0
    done
}

identify_watch() {
    while [ ! -e "$RUN_DIR/identify.stop" ]; do
        if mv "$GUI_DIR/identify-request" "$RUN_DIR/identify.taken" 2>/dev/null; then
            _id_req=$(tr -d '\r\n' < "$RUN_DIR/identify.taken")
            identify_disk "$_id_req" || :
        fi
        sleep 1
    done
}

identify_watch_start() {
    [ "$D_ROLE" = cloner ] || return 0
    [ -n "${_identify_watch_pid:-}" ] && kill -0 "$_identify_watch_pid" 2>/dev/null && return 0
    rm -f "$RUN_DIR/identify.stop"
    identify_watch < /dev/null & _identify_watch_pid=$!
}

identify_watch_stop() {
    # Returns when no identification read runs any more (or past the ceiling: logged, killed).
    : > "$RUN_DIR/identify.stop"; rm -f "$GUI_DIR/identify-request"
    [ -n "${_identify_watch_pid:-}" ] || return 0
    wait_pid "$_identify_watch_pid" "$IDENTIFY_STOP_S" "identify (drawer LED read)" || :
    _identify_watch_pid=
}
