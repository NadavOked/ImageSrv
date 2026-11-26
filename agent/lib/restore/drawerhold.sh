# drawerhold.sh -- a cloner whose LAST live drawer was pulled waits for it, for a while (#3140).
# POSIX sh (busybox ash).
#
# VM lab 14/11 (room_75941455, MC4): one drawer failed the write check and the other, the last, was pulled and
# pushed back 22 seconds later. The machine left the round 10 seconds after the pull -- chunkrecv.sh found no
# drawer alive and returned -- and the drawer came back to nobody: its completion (#3112, drawerback.sh) never ran.
# A cloner with a second live drawer saw the same pull and the same return end whole. Whether a pulled drawer was
# forgiven depended on the drawer NEXT to it.
#
# Now, when no drawer is alive and one was PULLED (`pulled`, verdict.sh -- the one failure that can undo itself: not a
# write error, not a disk that is not the one chosen, not a completion that already failed), the machine stays in the
# round for DRAWER_HOLD_S. The ceiling is the server's: lost_wait_seconds, applied as every wait of timing.sh -- the
# server says how long it waits for a machine, as for one that went silent. Meanwhile:
#
#   - the server is told once, as at the end of any pass, that every target here is failed. The wave does not wait for
#     a machine with nothing more to say, and does not call it silent either;
#   - the watchdog is fed and the close of the round is heard, as in every wait of chunkrecv.sh;
#   - drawerback.sh looks for the drawer by its serial, every CHUNK_ASK_POLL_S. Found, it completes in the
#     BACKGROUND, as in #3112. There is no neighbour to carry the stream, so the machine waits here for that
#     completion to end -- and joins the next partition level with the round, like any drawer (drawer_back before).
#
# Not back in time, or back as another disk: the machine fails as it did before -- by the ceiling now, and by name.

_dh_pulled() {   # $@ = disks. 0 = one is out because it was PULLED, and drawer_back may still find it (the same tests as _db_found)
    for _dh_p in "$@"; do
        _dh_t="$RUN_DIR/targets/$_dh_p"
        [ "$(cat "$_dh_t/state" 2>/dev/null)" = failed ] && [ -f "$_dh_t/pulled" ] || continue
        [ ! -f "$_dh_t/foreign" ] && [ ! -f "$_dh_t/back_failed" ] || continue
        _dh_k=$(cat "$_dh_t/bound" 2>/dev/null) || continue
        _dh_k=${_dh_k%|*}
        case "$_dh_k" in '|'*|*'|') continue ;; esac   # no serial or no size: nothing to know it by
        return 0
    done
    return 1
}

_dh_finish() {   # $@ = disks: waits for each completion in the background to end (not for it to be read, only for its process)
    for _dh_f in "$@"; do
        read -r _dh_pid _ 2>/dev/null < "$RUN_DIR/targets/$_dh_f/catchup" || continue
        while kill -0 "$_dh_pid" 2>/dev/null; do
            _chunk_beat; round_closed && return 1; sleep 1
        done
    done
    return 0
}

drawer_hold() {   # $1 = partition index, $2 = pass, $3 = server, $4 = image, $5.. = the round's disks (every one out).
    # 0 = a drawer is back and was completed here: _disks is the round's list. 1 = nothing to wait for, or nothing came back in time
    _dh_idx="$1"; _dh_pass="$2"; _dh_srv="$3"; _dh_img="$4"; shift 4
    [ "${STREAM_MODE:-}" = chunk ] && _dh_pulled "$@" || return 1
    _dh_list="$*"; _dh_max="${DRAWER_HOLD_S:-180}"
    log "partition $_dh_idx: no drawer is left and one was pulled -- the machine stays in the round up to ${_dh_max}s for it"
    if _chunk_tell ended "$_dh_pass" "$_dh_idx" "$@"; then
        [ "$(json_get "$CHUNK_DIR/answer.json" ".action")" != closed ] || return 1   # the round is over: nothing to wait for
    else
        log "WARNING: the server did not take the failed targets of partition $_dh_idx -- waiting for the drawer all the same"
    fi
    _dh_t0=$(date +%s)
    while :; do
        _chunk_beat
        round_closed && return 1
        # shellcheck disable=SC2086 # a word list; drawer_back leaves the list with a renamed drawer in _disks
        if drawer_back in "$_dh_idx" "$_dh_srv" "$_dh_img" $_dh_list; then _dh_list="$_disks"; fi
        if _any_alive "$_dh_list"; then
            # shellcheck disable=SC2086
            _dh_finish $_dh_list || return 1
            _any_alive "$_dh_list" || return 1   # its completion failed: by name, and not tried again
            log "partition $_dh_idx: a pulled drawer is back and completed -- the machine is in the round again"
            return 0
        fi
        # shellcheck disable=SC2086
        _dh_pulled $_dh_list || { log "partition $_dh_idx: no pulled drawer that can come back is left"; return 1; }
        [ $(( $(date +%s) - _dh_t0 )) -lt "$_dh_max" ] || { log "partition $_dh_idx: no drawer came back in ${_dh_max}s -- the machine leaves the round"; return 1; }
        sleep "$CHUNK_ASK_POLL_S"
    done
}
