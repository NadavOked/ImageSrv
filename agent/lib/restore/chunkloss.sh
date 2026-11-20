# chunkloss.sh -- what this machine's receiver lost in a chunk round, in the progress report (#3064, G6/G7).
# POSIX sh (busybox ash).
#
# A udpcast partition adds its RcvbufErrors to `udp_drops` once it ends (waits.sh). A chunk pass is
# minutes long, and the server's network budget (server/brain/budget.py, K7) halves the HTTP downloads
# on a loss *while* it runs -- so here `udp_drops` is refreshed during the pass (chunkrecv.sh calls
# chunk_loss_tell once a second), and the progress report (progress.sh) carries what is there:
#
#   rcvbuf, inerrors -- /proc/net/snmp now, minus when the pass began (udp_drops, waits.sh)
#   abandoned        -- chunks chunkcast gave up half-received in this pass: its last "abandoned N" line
#
# each plus what this restore's earlier passes lost (udp_drops.sum as waits.sh keeps it, and
# udp_drops.abandoned). A counter that was not read is null for the whole sum, never 0 (principle 5):
# a partial sum would read as "the receiver is clean". A chunkcast that never says "abandoned" (an older
# binary) is "not read", not "nothing abandoned".
#
# And two facts that explain a loss (G7, S42/S43), neither a sum:
#
#   rcvbuf_bytes -- the SO_RCVBUF the kernel gave the receiver's socket (chunkcast's "rcvbuf N" line, the
#                   getsockopt read back after the ask): the smallest over this restore's passes, null when
#                   one pass did not say. Asked 16MB, given 416KB is what drops chunks.
#   pass_word    -- how the latest pass ended (chunkcast's "pass <word>": complete, ended, stalled,
#                   no-stream...), null while a pass runs or when chunkcast died without saying -- so the
#                   server tells "finished" from "stuck", not just "ended".

chunk_loss_mark() {   # a receiving pass begins: the kernel's counters now
    CHUNK_LOSS0=$(udp_drops) || CHUNK_LOSS0=""
    if command -v wave_sense_mark >/dev/null 2>&1; then wave_sense_mark; fi   # #3041: the machine's own senses, S46-S50 (wavesense.sh)
}

_chunk_loss_pass() {   # $1 = chunkcast's stdout -> "<rcvbuf> <inerrors> <abandoned> <granted> <word>" of this pass so far ("null" = not read)
    _cl_ab=$(sed -n 's/^abandoned \([0-9][0-9]*\)$/\1/p' "$1" 2>/dev/null | tail -n 1)
    [ -n "$_cl_ab" ] || _cl_ab=null
    _cl_gr=$(sed -n 's/^rcvbuf \([0-9][0-9]*\)$/\1/p' "$1" 2>/dev/null | tail -n 1)
    [ -n "$_cl_gr" ] || _cl_gr=null
    _cl_w=$(sed -n 's/^pass \([a-z][a-z0-9-]*\)$/\1/p' "$1" 2>/dev/null | tail -n 1)
    [ -n "$_cl_w" ] || _cl_w=null
    _cl_now=$(udp_drops) || _cl_now=""
    if [ -n "${CHUNK_LOSS0:-}" ] && [ -n "$_cl_now" ]; then
        # shellcheck disable=SC2086 # two counters each: word splitting is the point
        set -- $CHUNK_LOSS0 $_cl_now
        echo "$(($3 - $1)) $(($4 - $2)) $_cl_ab $_cl_gr $_cl_w"
    else
        echo "null null $_cl_ab $_cl_gr $_cl_w"
    fi
}

_chunk_loss_add() {   # $1 = a sum so far, $2 = this pass's -> their sum; null on either side is null
    case "$1 $2" in *null*) echo null ;; *) echo $(($1 + $2)) ;; esac
}

_chunk_loss_min() {   # $1 = the smallest grant so far ("none" = no pass yet), $2 = this pass's -> the smallest; null on either side is null
    case "$1:$2" in
        none:*) echo "$2" ;;
        *null*) echo null ;;
        *) if [ "$1" -le "$2" ]; then echo "$1"; else echo "$2"; fi ;;
    esac
}

_chunk_loss_sums() {   # -> "<rcvbuf> <inerrors> <abandoned> <granted>" of this restore's ended passes
    _cs_ri=$(cat "$RUN_DIR/udp_drops.sum" 2>/dev/null) || _cs_ri="0 0"
    _cs_a=$(cat "$RUN_DIR/udp_drops.abandoned" 2>/dev/null) || _cs_a=0
    _cs_g=$(cat "$RUN_DIR/udp_drops.rcvbuf" 2>/dev/null) || _cs_g=none
    echo "$_cs_ri $_cs_a $_cs_g"
}

_chunk_loss_json() {   # $1..$5 = rcvbuf inerrors abandoned rcvbuf_bytes pass_word -> udp_drops.json, by a rename (the reporter reads it at any moment)
    case "$5" in null) _cj_w=null ;; *) _cj_w="\"$5\"" ;; esac
    printf '{"rcvbuf":%s,"inerrors":%s,"abandoned":%s,"rcvbuf_bytes":%s,"pass_word":%s}\n' "$1" "$2" "$3" "$4" "$_cj_w" > "$RUN_DIR/udp_drops.json.new" \
        && mv "$RUN_DIR/udp_drops.json.new" "$RUN_DIR/udp_drops.json"
}

chunk_loss_tell() {   # $1 = chunkcast's stdout: during a pass, the restore's sums plus this pass so far
    # shellcheck disable=SC2046 # the numbers and the word of each
    set -- $(_chunk_loss_sums) $(_chunk_loss_pass "$1")
    _chunk_loss_json "$(_chunk_loss_add "$1" "$5")" "$(_chunk_loss_add "$2" "$6")" "$(_chunk_loss_add "$3" "$7")" \
        "$(_chunk_loss_min "$4" "$8")" "$9"
    if command -v wave_sense_tick >/dev/null 2>&1; then wave_sense_tick; fi   # #3041: a window of S46-S50 closes every WAVE_SENSE_S (wavesense.sh)
}

chunk_loss_end() {   # $1 = chunkcast's stdout: the pass ended -- its counts join the restore's sums
    if command -v rx_sense_pass >/dev/null 2>&1; then rx_sense_pass "$1"; fi   # #3064 S41: every counter of the pass, not only these (rxsense.sh)
    # shellcheck disable=SC2046 # the numbers and the word of each
    set -- $(_chunk_loss_sums) $(_chunk_loss_pass "$1")
    log "chunk pass: udp drops on this machine rcvbuf=$5 inerrors=$6, chunks abandoned half-received=$7, receive buffer granted=$8, ended: $9"
    set -- "$(_chunk_loss_add "$1" "$5")" "$(_chunk_loss_add "$2" "$6")" "$(_chunk_loss_add "$3" "$7")" "$(_chunk_loss_min "$4" "$8")" "$9"
    echo "$1 $2" > "$RUN_DIR/udp_drops.sum"; echo "$3" > "$RUN_DIR/udp_drops.abandoned"; echo "$4" > "$RUN_DIR/udp_drops.rcvbuf"
    _chunk_loss_json "$1" "$2" "$3" "$4" "$5"; CHUNK_LOSS0=""
}
