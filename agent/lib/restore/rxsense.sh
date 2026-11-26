# rxsense.sh -- what the chunk engine's receiver and the HTTP completion say about a round, for the brain
# (#3064: S41 the receiver's counters per pass, S59 the HTTP fetches). POSIX sh (busybox ash).
#
# Until now only `abandoned` (and `stray`) left chunkcast's pass (chunkloss.sh): the rest of its counters reached the
# log, after the pass. `chunkcast recv` now also prints one `rxstats k=v ...` line at the end of each pass; a pass
# without it (an older binary) is counted in `passes` but not in `read` -- the sums are of the passes that said, and the
# server sees both numbers. A counter chunkcast could not know (`stray` without --from) is `-` and makes its sum null.
#
#   rx_sense_pass OUT   a pass ended: its rxstats line joins the round's sums (senses/<session>/rx.sum), and is logged.
#   ss_rx_json DIR      {"passes","read",<counters>}; null while no pass ended.
#   rx_http_begin       a fetch starts (chunkfetch.sh)         rx_http_note RC   the fetch ended; its curl rc is in fetch.rc
#   ss_http_json DIR    {"fetches","ranges","req_bytes","taken","secs","curl_rc","curl_failed"}; null without a fetch.
#
# Files are under $RUN_DIR/senses/$CHUNK_SESSION/ -- the session a pass belongs to, so a late pass of a closed round can
# never join the next one's sums (sensecore.sh removes the other sessions' directories).

RX_KEYS="pkts foreign stray bad_pkt dup evicted busy bad_chunk good skipped conflict version"

_rx_dir() {
    case "${CHUNK_SESSION:-}" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
    mkdir -p "$RUN_DIR/senses/$CHUNK_SESSION" 2>/dev/null && printf '%s' "$RUN_DIR/senses/$CHUNK_SESSION"
}

rx_sense_pass() {
    # $1 = chunkcast recv's stdout of the pass that ended
    _rx_d=$(_rx_dir) || return 0
    _rx_in="$_rx_d/rx.sum"; [ -f "$_rx_in" ] || _rx_in=/dev/null
    _rx_l=$(sed -n 's/^rxstats //p' "$1" 2>/dev/null | tail -n 1)
    if [ -n "$_rx_l" ]; then log "chunk pass rx: $_rx_l"; else log "chunk pass rx: this chunkcast did not say its counters (an older binary)"; fi
    awk -v line="$_rx_l" -v keys="$RX_KEYS" '
        BEGIN { n = split(line, kv, " "); for (i = 1; i <= n; i++) { split(kv[i], p, "="); if (p[1] != "") v[p[1]] = p[2] }
                nk = split(keys, k, " ") }
        { old[$1] = $2 }
        END { had = (n > 0)
              print "passes", old["passes"] + 1; print "read", old["read"] + (had ? 1 : 0)
              for (i = 1; i <= nk; i++) { key = k[i]
                  if (!had) { if (key in old) print key, old[key]; continue }
                  nv = (key in v) ? v[key] : "-"
                  if (nv !~ /^[0-9]+$/ || old[key] == "-") print key, "-"; else printf "%s %.0f\n", key, old[key] + nv } }' \
        "$_rx_in" > "$_rx_d/rx.sum.new" 2>> "$LOG_FILE" && [ -s "$_rx_d/rx.sum.new" ] && mv "$_rx_d/rx.sum.new" "$_rx_d/rx.sum" ||
        log "chunk pass rx: the counters were not added to the round's sums"
    return 0
}

ss_rx_json() {
    [ -s "$1/rx.sum" ] || { printf 'null'; return 0; }
    awk '{ v[$1] = $2 } END { if (!NR) { print "null"; exit }
        printf "{\"passes\":%d,\"read\":%d", v["passes"], v["read"]
        n = split("pkts foreign stray bad_pkt dup evicted busy bad_chunk good skipped conflict version", k, " ")
        for (i = 1; i <= n; i++) if (v["read"] > 0) printf ",\"%s\":%s", k[i], ((k[i] in v) && v[k[i]] != "-") ? v[k[i]] : "null"
        printf "}" }' 2>/dev/null < "$1/rx.sum" || printf 'null'
}

rx_http_begin() { RX_HTTP_T0=$(cut -d' ' -f1 "${SYSROOT:-}/proc/uptime" 2>/dev/null); }

rx_http_note() {
    # $1 = apply's exit code. One fetch: the ranges asked for and their bytes (from curl's own config), the chunks apply
    # took, the seconds, curl's exit code. What was not read stays "-" -- the sums say null, not 0.
    _rh_d=$(_rx_dir) || return 0
    _rh_n=$(tr ',' '\n' 2>/dev/null < "$CHUNK_DIR/fetch.ranges" | grep -c .)
    _rh_b=$(awk -F'"' '/^range = / { split($2, r, "-"); s += r[2] - r[1] + 1 } END { if (NR) printf "%.0f", s }' "$CHUNK_DIR/fetch.curl" 2>/dev/null)
    _rh_t=$(_chunk_taken 2>/dev/null)
    _rh_e=$(cut -d' ' -f1 "${SYSROOT:-}/proc/uptime" 2>/dev/null)
    _rh_s=$(awk -v a="${RX_HTTP_T0:-}" -v b="$_rh_e" 'BEGIN { if (a ~ /^[0-9.]+$/ && b ~ /^[0-9.]+$/ && b + 0 >= a + 0) printf "%.0f", b - a }')
    _rh_c=$(cat "$CHUNK_DIR/fetch.rc" 2>/dev/null)
    case "$_rh_c" in ''|*[!0-9]*) _rh_c=- ;; esac
    case "$_rh_b" in ''|*[!0-9]*) _rh_b=- ;; esac
    case "$_rh_t" in ''|*[!0-9]*) _rh_t=- ;; esac
    case "$_rh_s" in ''|*[!0-9]*) _rh_s=- ;; esac
    _rh_in="$_rh_d/http.sum"; [ -f "$_rh_in" ] || _rh_in=/dev/null
    log "chunk fetch http: $_rh_n ranges, ${_rh_b} bytes asked, ${_rh_t} chunks taken, ${_rh_s}s, curl rc=$_rh_c, apply rc=$1"
    awk -v r="$_rh_n" -v b="$_rh_b" -v t="$_rh_t" -v s="$_rh_s" -v c="$_rh_c" '
        { old[$1] = $2 }
        function add(key, x) { if (x == "-" || old[key] == "-") print key, "-"; else printf "%s %.0f\n", key, old[key] + x }
        END { print "fetches", old["fetches"] + 1
              add("ranges", r); add("req_bytes", b); add("taken", t); add("secs", s)
              print "curl_rc", c
              print "curl_failed", old["curl_failed"] + ((c != "-" && c != 0) ? 1 : 0) }' \
        "$_rh_in" > "$_rh_d/http.sum.new" 2>> "$LOG_FILE" && [ -s "$_rh_d/http.sum.new" ] && mv "$_rh_d/http.sum.new" "$_rh_d/http.sum" ||
        log "chunk fetch http: the fetch was not added to the round's sums"
    return 0
}

ss_http_json() {
    [ -s "$1/http.sum" ] || { printf 'null'; return 0; }
    awk 'function v(k) { return (k in a && a[k] != "-") ? a[k] : "null" }
         { a[$1] = $2 }
         END { if (!NR) { print "null"; exit }
             printf "{\"fetches\":%d,\"ranges\":%s,\"req_bytes\":%s,\"taken\":%s,\"secs\":%s,\"curl_rc\":%s,\"curl_failed\":%d}",
                a["fetches"], v("ranges"), v("req_bytes"), v("taken"), v("secs"), v("curl_rc"), a["curl_failed"] }' 2>/dev/null < "$1/http.sum" || printf 'null'
}
