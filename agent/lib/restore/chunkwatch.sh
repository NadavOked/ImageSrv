# chunkwatch.sh -- a drawer that dies inside a chunk pass is out in seconds (#2533).
# POSIX sh (busybox ash).
#
# Metal, 13/11 (cloner 2, PR #3063, run H): drawer 3 was pulled at 41% of a
# pass. chunkcast failed the target within seconds ("the flush to the disk
# failed") and said so in its state file -- and nobody read it: the verdicts
# were taken when the pass ended, six minutes later. All that time the drawer
# said "writing" and nothing was heard, and the server, never told, named it
# "did not keep up with the stream".
#
# So a pass (and a fetch) is watched as drawers.sh watches a udpcast stream, in
# the background beside _chunk_run's loop -- a disk that is dying can hold the
# evidence it is asked for (fail_written_target), and the loop that feeds the
# watchdog is not made to wait for that. Every tick:
#
#   the kernel   the disk left sysfs, sysfs says offline, or the kernel failed a
#                WRITE (drawer_dead_mark, verdict.sh -- drawer_watch's evidence,
#                words and tune: "pulled" is heard as it happens);
#   chunkcast    any target it failed itself -- a write error, a failed flush, a
#                disk that hung past its ceiling -- by that reason, and heard
#                (_chunk_verdicts).
#
# The kernel is asked first, and chunkcast's word is the one it had BEFORE the
# kernel was asked: a pulled drawer is then named "pulled", and never by the
# flush that failed because of it (the server reads "write_error" before
# "removed", ata_cause.py). Nothing is cut and the receiver is not
# stopped: there is no fifo here, a target has a queue of its own (chunkcast.c),
# and chunkcast drops it on its failed write or flush -- the neighbours' chunks
# never wait for it. A drawer failed here stays failed: pushed back in, it is
# not handed to the next pass or to a fetch (_chunk_live), whatever chunkcast
# still says of its node -- until drawerback.sh finds it by its serial (#3108).
#
# The same watch tells the outage tune (stream_beep_tick, roundbeep.sh) when a
# partition is mid-receive, in the two files drawers.sh keeps for it: bytes.raw
# holds a byte once THIS pass made a chunk durable, and source.rc is there
# once the receiver ended. Without them a cable pulled in a chunk pass was
# never heard. A fetch is HTTP, with curl's own ceiling: not judged.
# "This pass made a chunk durable" = a target chunkcast still writes holds more
# chunks than the state the partition's last pass LEFT (p<n>.base, kept when a
# watch ends; none = the first pass): not than a count read when the watch
# began -- the receiver runs before the watch does -- and not a sum over all
# targets -- one that failed keeps its chunks in the old state and has no line
# in the new one (review of #3080).
#
# A drawer is failed without waiting for what failing it does: the evidence and
# the GPT wipe (fail_written_target) ask the dying disk itself, with no ceiling,
# and a wipe held by one disk must not leave the next drawer unwatched.
# A state that is there and cannot be read is not "nothing arrived" (principle
# 5): it is said in the log, once a pass, and while it lasts the pass is not
# called mid-receive -- an outage tune on a stream nobody could look at would
# send the operator to a cable that is in.

chunk_watch() {   # $1 = the pid of the receiver or the fetch, $2 = partition index, $3.. = disks. A subshell of its own (chunk_watch_start)
    _cw_pid="$1"; _cw_idx="$2"; shift 2; _cw_seen=" "; _cw_k="$CHUNK_DIR/dmesg.chunkwatch"; _cw_blind=0
    for _cw_d in "$@"; do [ ! -e "$SYSROOT/sys/block/$_cw_d" ] || _cw_seen="$_cw_seen$_cw_d "; done
    _cw_deaf=0; CHUNK_FAIL=_chunk_fail_now; _cw_base="${CHUNK_STATE%.state}.base"
    _cw_live="$CHUNK_STATE"; CHUNK_STATE="$_cw_k.state"   # in here chunkcast is judged by a copy: its word as it stood BEFORE the kernel is asked
    # `watching` is _chunk_run's: it leaves a process a disk holds for ever (D state), and so does this
    while kill -0 "$_cw_pid" 2>/dev/null && [ -e "$CHUNK_DIR/watching" ]; do
        _nap "$_cw_pid"
        _cw_read=1; : > "$CHUNK_STATE"   # no state yet = no verdict yet, never "ok"
        [ ! -e "$_cw_live" ] || cp "$_cw_live" "$CHUNK_STATE" 2>> "$LOG_FILE" || _cw_read=0   # one that is there and was not copied is not "no state"
        if ! dmesg > "$_cw_k" 2>/dev/null; then
            : > "$_cw_k"   # not "no errors": said once, and sysfs is still watched
            [ "$_cw_blind" = 1 ] || log "WARNING: dmesg unreadable -- kernel write errors on drawers are not watched"
            _cw_blind=1
        fi
        for _cw_d in $(_chunk_live "$@"); do
            case "$_cw_seen" in *" $_cw_d "*) _cw_s=1 ;; *) _cw_s=0 ;; esac
            drawer_dead_mark "$_cw_d" "$_cw_s" "$_cw_k" || continue
            _chunk_fail_now "$_cw_d" "מחיצה $_cw_idx: $DRAWER_WHY"
        done
        if command -v drawer_left_mark > /dev/null 2>&1; then for _cw_d in "$@"; do drawer_left_mark "$_cw_d" || :; done; fi   # #3270: failed on refused writes, gone from sysfs since
        _chunk_verdicts "$_cw_idx" "$@"
        # #3336: a drawer that stands still is said in seconds, as drawer_watch says it on a udpcast stream (chunkstall.sh); a state that was not read judges nothing
        if [ "$_cw_read" = 1 ] && command -v chunk_stall_tick > /dev/null; then chunk_stall_tick "$_cw_idx" "$_cw_live" "$@"; fi
        if [ -z "${CHUNK_TELL:-}" ] && [ -z "${CHUNK_BG:-}" ] && [ ! -s "$RUN_DIR/bytes.raw" ]; then   # CHUNK_BG: a completion (drawerback.sh) is not the round's stream
            # awk's 1 is an answer ("not yet"), not a failure; anything else that is not 0 is the check itself
            _cw_rc=2; [ "$_cw_read" != 1 ] || { _chunk_arrived "$CHUNK_STATE" "$_cw_base"; _cw_rc=$?; }
            if [ "$_cw_rc" = 0 ]; then echo 1 > "$RUN_DIR/bytes.raw"
            elif [ "$_cw_rc" != 1 ] && [ "$_cw_deaf" = 0 ]; then
                _cw_deaf=1; log "WARNING: partition $_cw_idx: chunkcast's state was not read -- whether the stream arrived is not known, and a network outage is not heard while this lasts"
            fi
        fi
    done
    if command -v chunk_stall_end > /dev/null; then chunk_stall_end "$@"; fi   # #3336: the pass ended: nothing stands any more
    rm -f "$_cw_k" "$CHUNK_STATE"
}

_chunk_arrived() {   # $1 = the state now, $2 = the state the partition's last pass left (no file = nothing yet)
    # 0 = a target chunkcast still writes holds more chunks than it did. 1 = not yet. 2 and up = it was not read.
    [ ! -e "$2" ] || [ -r "$2" ] || return 2
    awk -v was="$2" 'BEGIN { while ((getline l < was) > 0) { split(l, f, " "); had[f[1]] = f[3] } }
        NR > 1 && $2 == "ok" && $3 + 0 > had[$1] + 0 { more = 1 } END { exit more ? 0 : 1 }' "$1" 2>> "$LOG_FILE"
}

_chunk_fail_now() {   # $1 = disk, $2 = why: failed now, by name; what failing it does to the disk is not waited for
    fail_written_target "$1" "$2" &
    _fn=0   # the state is the first thing it writes: seen here before the next tick, so that the drawer is not failed twice
    until case "$(cat "$RUN_DIR/targets/$1/state" 2>/dev/null)" in failed|cancelled) : ;; *) false ;; esac || [ "$_fn" -ge 20 ]; do
        poll_tick; _fn=$((_fn + _tk))
    done
}

chunk_watch_start() {   # $1 = pid, $2 = partition index, $3.. = disks
    : > "$CHUNK_DIR/watching"; [ -n "${CHUNK_BG:-}" ] || rm -f "$RUN_DIR/bytes.raw" "$RUN_DIR/source.rc"   # nothing of this pass has arrived yet
    chunk_watch "$@" &
    CHUNK_WATCH_PID=$!
}

chunk_watch_end() {   # the watch ends with what it follows; one that a disk holds is not waited for past the helpers' ceiling
    rm -f "$CHUNK_DIR/watching"; [ -n "${CHUNK_BG:-}" ] || echo 0 > "$RUN_DIR/source.rc"   # the receiver ended: what stands still now is not an outage
    # what this pass left is what the next one is measured against; not kept = the older one stays, and that is said
    [ ! -e "$CHUNK_STATE" ] || cp "$CHUNK_STATE" "${CHUNK_STATE%.state}.base" 2>> "$LOG_FILE"         || log "WARNING: chunkcast's state was not kept for the next pass -- its outage tune may be early"
    wait_pid "$CHUNK_WATCH_PID" "$WAIT_HELPER_S" "מעקב המגירות של מנוע החתיכות" || :
}
