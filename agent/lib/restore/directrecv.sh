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
