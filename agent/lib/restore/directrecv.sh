# directrecv.sh -- who took the last partition to the end (#1952).
# POSIX sh (busybox ash). No bashisms.
#
# A drawer the server marked lost (it stopped reporting) can still be
# receiving: if it reaches the next partition first, it fills
# --min-receivers and udp-sender starts without the live drawer, which then
# joins mid-stream and gets an empty one (#1950, R2 22/10). The server
# counts such a member again only with evidence that it took the previous
# partition to the end -- and in a direct send that evidence is in OUR
# udp-sender log, not the server's. So the hello before every partition
# after the first carries it (direct_finished, interface 2).

direct_finished_ips() {
    # $1 = one partition's udp-sender log. Prints a JSON list of the
    # addresses that connected and were neither dropped nor disconnected
    # before "Transfer complete" -- the lines #1950 measured on udpcast
    # 20120424: "New connection from <ip>  (#N) ...", "Dropping client #N
    # because of timeout", "Disconnecting #N (<ip>)". A slot number is
    # reused after a disconnect, so the lines are read in order.
    [ -r "$1" ] || return 1
    awk '/Transfer complete/ { exit }
        /New connection from/ { ip = $0; sub(/.*New connection from +/, "", ip); n = ip
            sub(/ .*/, "", ip); sub(/^[^#]*#/, "", n); sub(/[^0-9].*/, "", n); s[n] = ip; next }
        /Dropping client #|Disconnecting #/ { n = $0; sub(/^[^#]*#/, "", n); sub(/[^0-9].*/, "", n); delete s[n] }
        END { printf "["; for (k in s) if (s[k] ~ /^[0-9.]+$/) printf "%s\"%s\"", (c++ ? "," : ""), s[k]; printf "]" }' "$1"
}

direct_hello() {
    # $1 = the previous partition's index ("" = none yet). send_hello with
    # direct_finished in the body (build_hello reads the fragment), then the
    # fragment is gone -- no other hello carries it. A log we cannot read is
    # "null": not checked, which the server counts as still receiving.
    _dh_f="$RUN_DIR/direct.finished"
    if [ -z "$1" ]; then rm -f "$_dh_f"
    elif _dh_l=$(direct_finished_ips "$RUN_DIR/dsend.$1.log"); then printf ',"direct_finished":%s' "$_dh_l" > "$_dh_f"
    else printf ',"direct_finished":null' > "$_dh_f"; fi
    send_hello; _dh_rc=$?
    rm -f "$_dh_f"
    return "$_dh_rc"
}

# --- #1581: a cloner asked before the first byte ----------------------------
#
# The server's own sender does not start partition 1 while a member of the
# wave waits for a person (prompt_hold.py), and stops it if the question comes
# before the first byte. In a direct send that sender is OURS, with its own
# --start-timeout 180; the server says how many wait (task.direct.operator_wait).

direct_operator_wait() {
    # $1 = a hello answer. 0 = a member waits for the operator now. A missing
    # or bad value (an older server) is 1: the stream goes as before #1581.
    case "$(json_get "$1" ".task.direct.operator_wait")" in
        ''|0|*[!0-9]*) return 1 ;;
    esac
}

direct_ask_watch() {
    # $1 = partition 1's udp-sender log, $2 = portbase, $3 = partition index.
    # Until "Starting transfer:" (or the sender's exit) it asks the server; a
    # question marks dsend.<i>.rewound and stops udp-sender -- nothing went out,
    # and its 180 s would have counted the person's time as "nobody joined".
    while [ ! -e "$RUN_DIR/dsend.$3.rc" ]; do
        sleep "${DIRECT_ASK_POLL_S:-2}"
        grep -qF "$DIRECT_TRANSFER_STARTED" "$1" 2>/dev/null && return 0
        direct_watch_hello && direct_operator_wait "$RUN_DIR/dwatch.json" || continue
        grep -qF "$DIRECT_TRANSFER_STARTED" "$1" 2>/dev/null && return 0
        : > "$RUN_DIR/dsend.$3.rewound" || return 0
        log "direct: a cloner was asked before the first byte -- stopping partition $3, sent again after the answer"
        direct_stop_sender "$2"
        return 0
    done
}

direct_watch_hello() {
    # The watch's hello: its own body and answer files -- the main shell's
    # hello.json/$RESP are not touched while it runs beside it.
    build_hello > "$RUN_DIR/dwatch.body" \
        && hello_post "$SERVER/api/v1/agent/hello" "$RUN_DIR/dwatch.body" "$RUN_DIR/dwatch.json"
}

direct_stop_sender() {
    # $1 = portbase. Our udp-sender, found in /proc like sr_stop_stream (no
    # pkill in every busybox): argv 0 (or 1 under an interpreter) is udp-sender,
    # with `--portbase $1`. Not found is said; it then ends at its own timeout.
    _ds_n=0
    for _ds_p in /proc/[0-9]*; do
        _ds_c=$( { tr '\0' '\n' < "$_ds_p/cmdline"; } 2>/dev/null | head -n 2 | tr '\n' ' ')
        case "$_ds_c" in udp-sender\ *|*/udp-sender\ *) ;; *) continue ;; esac
        { tr '\0' '\n' < "$_ds_p/cmdline"; } 2>/dev/null \
            | awk -v pb="$1" 'p == "--portbase" && $0 == pb { f = 1 } { p = $0 } END { exit !f }' || continue
        kill "${_ds_p#/proc/}" 2>/dev/null && _ds_n=$((_ds_n + 1))
    done
    [ "$_ds_n" -gt 0 ] || log "direct: udp-sender of portbase $1 was not found to stop"
}
