# chunkloss.sh -- what this machine's receiver lost in a chunk round, in the progress report (#3064, G6/G7; #3168).
# POSIX sh (busybox ash).
#
# The kernel counts this machine's UDP drops (udp_drops, waits.sh). A chunk pass is
# minutes long, and the server's network budget (server/brain/budget.py, K7) halves the HTTP downloads
# on a loss *while* it runs -- so here `udp_drops` is refreshed during the pass (chunkrecv.sh calls
# chunk_loss_tell once a second), and the progress report (progress.sh) carries what is there:
#
#   rcvbuf, inerrors -- /proc/net/snmp now, minus when the pass began (udp_drops, waits.sh)
#   abandoned        -- chunks chunkcast gave up half-received in this pass: its last "abandoned N" line
#   softnet          -- /proc/net/softnet_stat, the "dropped" column summed over the CPUs: a CPU's input queue was
#                       full before UDP ever saw the packet (#3168)
#   nic              -- `ethtool -S $IFACE`, the receive-side drop counters the card keeps (NIC_DROP_NAMES): the
#                       card or its ring dropped the frame before the kernel did (#3168)
#
# each plus what this restore's earlier passes lost (udp_drops.sum as waits.sh keeps it, udp_drops.abandoned
# and udp_drops.net). A counter that was not read is null for the whole sum, never 0 (principle 5): a
# partial sum would read as "the receiver is clean". A chunkcast that never says "abandoned" (an older
# binary) is "not read", not "nothing abandoned". The same for the card: no ethtool, no $IFACE, a driver that
# answers with none of the names we know, or a counter that went DOWN (the card was reset) is "not read",
# not "the card dropped nothing".
#
# And two facts that explain a loss (G7, S42/S43), neither a sum:
#
#   rcvbuf_bytes -- the SO_RCVBUF the kernel gave the receiver's socket (chunkcast's "rcvbuf N" line, the
#                   getsockopt read back after the ask): the smallest over this restore's passes, null when
#                   one pass did not say. Asked 16MB, given 416KB is what drops chunks.
#   pass_word    -- how the latest pass ended (chunkcast's "pass <word>": complete, ended, stalled,
#                   no-stream...), null while a pass runs or when chunkcast died without saying -- so the
#                   server tells "finished" from "stuck", not just "ended".

#: the receive-side counters of `ethtool -S` that mean "this card dropped a frame". Drivers name them differently;
#: a name that is not here is not counted -- and a card with none of them is "not read", never 0.
NIC_DROP_NAMES="rx_dropped rx_missed_errors rx_fifo_errors rx_over_errors rx_no_buffer_count rx_discards rx_out_of_buffer"

softnet_drops() {   # the sum over the CPUs of column 2 of softnet_stat (hex); rc 1 = not read -- never 0
    [ -r "${SYSROOT:-}/proc/net/softnet_stat" ] || return 1
    _sd_sum=0; _sd_n=0
    while read -r _sd_a _sd_b _sd_rest; do
        case "$_sd_b" in ''|*[!0-9a-fA-F]*) return 1 ;; esac
        _sd_sum=$((_sd_sum + 0x$_sd_b)); _sd_n=$((_sd_n + 1))
    done < "${SYSROOT:-}/proc/net/softnet_stat"
    [ "$_sd_n" -gt 0 ] || return 1
    echo "$_sd_sum"
}

nic_drops() {   # $1 = the interface -> the sum of its NIC_DROP_NAMES counters; rc 1 = not read -- never 0
    [ -n "$1" ] || return 1
    _nd_out=$("${ETHTOOL:-ethtool}" -S "$1" 2>/dev/null) || return 1
    printf '%s\n' "$_nd_out" | sed -n 's/^[[:space:]]*\([A-Za-z0-9_.-]*\): *\([0-9][0-9]*\)$/\1 \2/p' | {
        _nd_sum=0; _nd_found=0
        while read -r _nd_k _nd_v; do
            case " $NIC_DROP_NAMES " in *" $_nd_k "*) _nd_sum=$((_nd_sum + _nd_v)); _nd_found=1 ;; esac
        done
        [ "$_nd_found" = 1 ] && echo "$_nd_sum"
    }
}

chunk_loss_mark() {   # a receiving pass begins: the kernel's and the card's counters now
    CHUNK_LOSS0=$(udp_drops) || CHUNK_LOSS0=""
    CHUNK_SOFT0=$(softnet_drops) || CHUNK_SOFT0=""
    CHUNK_NIC0=$(nic_drops "${IFACE:-}") || CHUNK_NIC0=""
    if command -v wave_sense_mark >/dev/null 2>&1; then wave_sense_mark; fi   # #3041: the machine's own senses, S46-S50 (wavesense.sh)
}

_chunk_loss_delta() {   # $1 = at the start, $2 = now ("" = not read) -> the difference; a counter that went down was reset = null
    if [ -n "$1" ] && [ -n "$2" ] && [ "$2" -ge "$1" ]; then echo $(($2 - $1)); else echo null; fi
}

_chunk_loss_pass() {   # $1 = chunkcast's stdout -> "<rcvbuf> <inerrors> <abandoned> <softnet> <nic> <granted> <word>" of this pass so far ("null" = not read)
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
        _cl_rb="$(($3 - $1)) $(($4 - $2))"
    else
        _cl_rb="null null"
    fi
    _cl_sn=$(softnet_drops) || _cl_sn=""; _cl_nc=$(nic_drops "${IFACE:-}") || _cl_nc=""
    echo "$_cl_rb $_cl_ab $(_chunk_loss_delta "${CHUNK_SOFT0:-}" "$_cl_sn") $(_chunk_loss_delta "${CHUNK_NIC0:-}" "$_cl_nc") $_cl_gr $_cl_w"
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

_chunk_loss_sums() {   # -> "<rcvbuf> <inerrors> <abandoned> <softnet> <nic> <granted>" of this restore's ended passes
    _cs_ri=$(cat "$RUN_DIR/udp_drops.sum" 2>/dev/null) || _cs_ri="0 0"
    _cs_a=$(cat "$RUN_DIR/udp_drops.abandoned" 2>/dev/null) || _cs_a=0
    _cs_n=$(cat "$RUN_DIR/udp_drops.net" 2>/dev/null) || _cs_n="0 0"
    _cs_g=$(cat "$RUN_DIR/udp_drops.rcvbuf" 2>/dev/null) || _cs_g=none
    echo "$_cs_ri $_cs_a $_cs_n $_cs_g"
}

_chunk_loss_json() {   # $1..$7 = rcvbuf inerrors abandoned softnet nic rcvbuf_bytes pass_word -> udp_drops.json, by a rename (the reporter reads it at any moment)
    case "$7" in null) _cj_w=null ;; *) _cj_w="\"$7\"" ;; esac
    printf '{"rcvbuf":%s,"inerrors":%s,"abandoned":%s,"softnet":%s,"nic":%s,"rcvbuf_bytes":%s,"pass_word":%s}\n' "$1" "$2" "$3" "$4" "$5" "$6" "$_cj_w" > "$RUN_DIR/udp_drops.json.new" \
        && mv "$RUN_DIR/udp_drops.json.new" "$RUN_DIR/udp_drops.json"
}

chunk_loss_tell() {   # $1 = chunkcast's stdout: during a pass, the restore's sums plus this pass so far
    # shellcheck disable=SC2046 # the numbers and the word of each
    set -- $(_chunk_loss_sums) $(_chunk_loss_pass "$1")
    _chunk_loss_json "$(_chunk_loss_add "$1" "$7")" "$(_chunk_loss_add "$2" "$8")" "$(_chunk_loss_add "$3" "$9")" \
        "$(_chunk_loss_add "$4" "${10}")" "$(_chunk_loss_add "$5" "${11}")" "$(_chunk_loss_min "$6" "${12}")" "${13}"
    if command -v wave_sense_tick >/dev/null 2>&1; then wave_sense_tick; fi   # #3041: a window of S46-S50 closes every WAVE_SENSE_S (wavesense.sh)
}

chunk_loss_end() {   # $1 = chunkcast's stdout: the pass ended -- its counts join the restore's sums
    if command -v rx_sense_pass >/dev/null 2>&1; then rx_sense_pass "$1"; fi   # #3064 S41: every counter of the pass, not only these (rxsense.sh)
    # shellcheck disable=SC2046 # the numbers and the word of each
    set -- $(_chunk_loss_sums) $(_chunk_loss_pass "$1")
    log "chunk pass: udp drops on this machine rcvbuf=$7 inerrors=$8, chunks abandoned half-received=$9, softnet=${10}, nic=${11}, receive buffer granted=${12}, ended: ${13}"
    set -- "$(_chunk_loss_add "$1" "$7")" "$(_chunk_loss_add "$2" "$8")" "$(_chunk_loss_add "$3" "$9")" "$(_chunk_loss_add "$4" "${10}")" \
        "$(_chunk_loss_add "$5" "${11}")" "$(_chunk_loss_min "$6" "${12}")" "${13}"
    echo "$1 $2" > "$RUN_DIR/udp_drops.sum"; echo "$3" > "$RUN_DIR/udp_drops.abandoned"; echo "$4 $5" > "$RUN_DIR/udp_drops.net"
    echo "$6" > "$RUN_DIR/udp_drops.rcvbuf"
    _chunk_loss_json "$1" "$2" "$3" "$4" "$5" "$6" "$7"; CHUNK_LOSS0=""; CHUNK_SOFT0=""; CHUNK_NIC0=""
}
