# directserve.sh -- single chunks of this machine's disk, without reading the partition again (#3209).
# POSIX sh (busybox ash). No bashisms.
#
# directchunk.sh sends a partition by reading all of it. Two things need less:
#
#   direct_chunk_targeted   a REPAIR pass. The server's list names the chunks; `chunkcast send
#                           --device` reads each from its own place on the disk (through the bit
#                           map of the head frame read 1 kept), checks it against read 1's index and
#                           compresses it as chunkcut did. A chunk missing near the end of a 250GB
#                           partition costs one chunk's read, not the partition's.
#   direct_serve_start      the wave's frame servers, one a partition (`chunkcast serve`). The image
#                           server holds no file of a direct send, so a receiver's HTTP fetch -- a
#                           drawer pushed back in, a machine that reported after its partition was
#                           closed -- is relayed by the server to these. They run until the round
#                           closes, and answer only a request that carries the round's token, which
#                           the server put in the hello and sends in the header of the fetch it relays.
#
# Neither weakens what a receiver believes: every chunk is still checked there against the index
# the manifest signs. A targeted repair that does not go out is SAID, and the caller reads the
# whole partition instead -- the pass is never reported sent on its word.

direct_chunk_targeted() {
    # $1 task, $2 partition index, $3 its node; the order's own values are direct_chunk_pass's
    # (_dp_seq, _dp_pass, _dp_to, _dp_key, _dp_kbps, _dp_wkbps, _dp_list, _dp_w). 0 = every listed
    # chunk went out, read from its own place and checked. 1 = it did not (said).
    _dt_x="$RUN_DIR/idx.$2"; _dt_h=""
    [ "$(awk 'NR == 1 { print $3 }' "$_dt_x" 2>/dev/null)" != partclone ] || _dt_h="$_dt_x.head"
    { [ -s "$_dt_x" ] && { [ -z "$_dt_h" ] || [ -s "$_dt_h" ]; }; } \
        || { log "partition $2: read 1's index or head frame is not here -- the repair reads the whole partition"; return 1; }
    rm -f "$_dp_w.out" "$_dp_w.ended" "$_dp_w.send.pid" "$RUN_DIR/dchunk.abort"
    log "partition $2: chunk pass $_dp_pass (order $_dp_seq), a repair read from the chunks' own places"
    # shellcheck disable=SC2086 # the optional pairs are word lists
    chunkcast send --device "$3" ${_dt_h:+--head "$_dt_h"} --level "$CAPTURE_LEVEL" --to "$_dp_to" \
        --session "$_dp_key" --part "$2" --pass "$_dp_pass" --index "$_dt_x" --kbps "$_dp_kbps" \
        --write-kbps "$_dp_wkbps" ${IFACE:+--iface "$IFACE"} --chunks-file "$_dp_list" \
        < /dev/null > "$_dp_w.out" 2>> "$LOG_FILE" &
    _dt_pid=$!; echo "$_dt_pid" > "$_dp_w.send.pid"
    ( _direct_chunk_beat "$1" "$_dp_seq" "$_dp_w.ended" ) < /dev/null > /dev/null 2>&1 &
    _dt_bpid=$!; _dt_still=0; _dt_seen=""; _dt_kill=0
    # chunkcast says "sent N" once a second while it sends: a pass that says nothing for the stall
    # ceiling, or one the server no longer waits for, is stopped by its own pid.
    while kill -0 "$_dt_pid" 2>/dev/null; do
        sleep 1
        _dt_now=$(wc -c 2>/dev/null < "$_dp_w.out")
        if [ "$_dt_now" != "$_dt_seen" ]; then _dt_seen="$_dt_now"; _dt_still=0; else _dt_still=$((_dt_still + 1)); fi
        if [ -e "$RUN_DIR/dchunk.abort" ] || [ "$_dt_still" -ge "$WAIT_STREAM_STALL_S" ]; then
            _dt_kill=$((_dt_kill + 1)); [ "$_dt_kill" -le 10 ] || break
            if [ "$_dt_kill" -gt 5 ]; then kill -9 "$_dt_pid" 2>/dev/null; else kill "$_dt_pid" 2>/dev/null; fi
        fi
    done
    _dt_rc="$WAIT_TIMED_OUT"
    if ! kill -0 "$_dt_pid" 2>/dev/null; then
        wait "$_dt_pid" 2>/dev/null   # wait-bounded: after-kill-0-gone -- the loop above left once it was gone; this collects its exit code
        _dt_rc=$?
    fi
    : > "$_dp_w.ended"; { kill "$_dt_bpid"; wait "$_dt_bpid"; } 2>/dev/null
    if [ "$_dt_rc" -eq 0 ] && grep -q '^done ' "$_dp_w.out" 2>/dev/null; then return 0; fi
    log "partition $2: the targeted repair did not go out (chunkcast rc=$_dt_rc) -- reading the whole partition instead"
    return 1
}

direct_serve_start() {
    # $1 = disk. The plan is $RUN_DIR/direct.plan; the port of each partition is in the hello that said the
    # wave runs ($RESP, `serve_ports`: index -> port, in the order the server sends them -- #3871). One
    # `chunkcast serve` a streamed partition, on the port the server named for it: nothing is computed here.
    # A server that named no ports (an older one) gets none: completions over HTTP then fail there,
    # by name, as before #3209.
    : > "$RUN_DIR/dserve.pids"
    # One flat object, {"<index>": port, ...}: blanks out (jq -r prints it indented), then read by key below.
    _ds_map=$(json_get "$RESP" ".task.direct.chunk.serve_ports" | tr -d ' \t\r\n')
    case "$_ds_map" in
        '{'*'}') ;;
        *) log "direct: the server named no frame-server ports -- single chunks are not served over HTTP"; return 0 ;;
    esac
    # Who may ask is the round's token, not an address (a name is not resolved here, and a server with two
    # cards leaves from the one it likes): no token, no frame server -- completions over HTTP then fail at
    # the server by name. The token goes to chunkcast in a file that only this process reads, never in argv
    # (ps shows it) and never in the log.
    _ds_tok="$RUN_DIR/dserve.token"; rm -f "$_ds_tok"
    ( umask 077; json_get "$RESP" ".task.direct.chunk.serve_token" > "$_ds_tok" ) 2> /dev/null
    case "$(cat "$_ds_tok" 2> /dev/null)" in ''|null) rm -f "$_ds_tok"; log "direct: the server sent no token for the frame servers -- single chunks are not served over HTTP"; return 0 ;; esac
    # the image server's address, when it is a number, narrows it further; it is never the only condition
    _ds_ip=${TLS_IP:-${SERVER#*://}}; _ds_ip=${_ds_ip%%[:/]*}
    case "$_ds_ip" in ''|*[!0-9.]*) _ds_ip="" ;; esac
    while IFS='|' read -r _ds_i _ds_g _ds_r _ds_fs _ds_st _ds_sz _ds_f _ds_rest <&3; do
        case "$_ds_f" in ''|null) continue ;; esac
        _ds_port=$(printf '%s' "$_ds_map" | sed -n "s/.*[{,]\"$_ds_i\":\([0-9][0-9]*\)[,}].*/\1/p")
        case "$_ds_port" in
            ''|null|0|*[!0-9]*) log "direct: the server named no port for partition $_ds_i -- its single chunks are not served over HTTP"; continue ;;
        esac
        _ds_x="$RUN_DIR/idx.$_ds_i"; _ds_h=""
        [ "$(awk 'NR == 1 { print $3 }' "$_ds_x" 2>/dev/null)" != partclone ] || _ds_h="$_ds_x.head"
        # shellcheck disable=SC2086 # the optional pairs are word lists
        chunkcast serve --listen "0.0.0.0:$_ds_port" --token-file "$_ds_tok" ${_ds_ip:+--allow "$_ds_ip"} --index "$_ds_x" \
            --device "$(lc_src_node "$1" "$_ds_i")" ${_ds_h:+--head "$_ds_h"} --level "$CAPTURE_LEVEL" \
            < /dev/null > "$RUN_DIR/dserve.$_ds_i.out" 2>> "$LOG_FILE" &
        echo "$!" >> "$RUN_DIR/dserve.pids"
    done 3< "$RUN_DIR/direct.plan"
}

direct_serve_stop() {
    # The frame servers, by the pids they were started with. Nothing is left listening on this
    # machine's disk once the round is over.
    [ -e "$RUN_DIR/dserve.pids" ] || return 0
    while read -r _ds_p; do
        case "$_ds_p" in ''|*[!0-9]*) continue ;; esac
        kill "$_ds_p" 2>/dev/null || continue
        # gone is the evidence, not "the signal was sent": up to 5 s, and then it is killed outright and said
        _ds_n=0
        while [ "$_ds_n" -lt 50 ] && kill -0 "$_ds_p" 2>/dev/null; do sleep 0.1 2>/dev/null || sleep 1; _ds_n=$((_ds_n + 1)); done
        if kill -0 "$_ds_p" 2>/dev/null; then kill -9 "$_ds_p" 2>/dev/null; log "direct: a frame server (pid $_ds_p) did not stop at SIGTERM -- killed"; fi
    done < "$RUN_DIR/dserve.pids"
    rm -f "$RUN_DIR/dserve.pids" "$RUN_DIR/dserve.token"
}
