# chunkfetch.sh -- what ONE machine lacks, over HTTP (#2533): one curl, one chunkcast apply.
# POSIX sh (busybox ash).
#
# The server's answer to a machine that alone lacks chunks is "fetch" with their
# ranges (chunkrecv.sh). A scattered repair is tens of thousands of ranges, and a
# curl, a TCP connection, a walk over the whole index, a state load, an fsync and
# a state write for EACH of them was the whole cost of it. So:
#
#   - ONE walk over the index turns the ranges into curl's config: a transfer a
#     range -- bytes [offset, offset + zbytes) of the partition file (format-spec
#     8) -- all of them in ONE curl, which keeps its connection between them;
#   - ONE `chunkcast apply --chunks-file` reads the frames as they come, checks
#     each against the index before it writes, and flushes and saves its state the
#     way recv does: every few seconds, never a chunk at a time;
#   - the pair is watched as a receiver is (_chunk_run): the watchdog is fed, the
#     drawers' counters and ledger files follow the state file, a drawer that
#     stops taking writes fails alone -- and the server is told "fetching" as
#     chunks become durable. A long download is then not taken for silence, and a
#     plan made meanwhile does not ask again for what is already here.
#
# curl stops at the first range that fails (--fail-early): the frames after it
# would not be the chunks apply expects. What did not arrive stays missing; the
# next report says so, and the server counts the attempt.
CHUNK_FETCH_TELL_S="${CHUNK_FETCH_TELL_S:-20}"   # a "fetching" report at most this often, and only when something is new -- the first one at once

_chunk_taken() {   # how many chunks `chunkcast apply` has TAKEN from the stream, flushed or not (#3149): its own count, as it wrote it. Nothing there, or not a number = nothing said
    _ctk=$(cat "$CHUNK_STATE.taken" 2>/dev/null); case "$_ctk" in *[!0-9]*) _ctk="" ;; esac; printf '%s' "$_ctk"
}

_chunk_fetch_tell() {   # $1 = pass, $2 = index, $3.. = disks: "alive, fetching" -- with how many chunks are durable on its nodes now
    # #3112 (the VM lab, 13/11: 2,751 ranges, and the round closed as "silent" after 6.7 minutes): the count read in
    # apply's own state file is evidence of its own, and the server takes a count that grew as a download alive
    # (`fetched`, chunksend.py). Not read = not sent: never a 0 that the server would take for "nothing moved".
    CHUNK_FETCHED=$(awk 'NR > 1 && $2 == "ok" { s += $3 } END { print s + 0 }' "$CHUNK_STATE" 2>> "$LOG_FILE") || CHUNK_FETCHED=""
    case "$CHUNK_FETCHED" in *[!0-9]*) CHUNK_FETCHED="" ;; esac
    # #3149 (the VM lab, 14/11: 2,569 chunks taken from the stream in 342 seconds, four state writes -- the first report came after 340, the server's
    # ceiling was 330): what a slow flush holds back from the state file is not held back from this count. Proof of life only; durable is `fetched`.
    CHUNK_RECEIVED=$(_chunk_taken)
    log "chunk fetch: partition $2 (pass $1): ${CHUNK_FETCHED:-?} chunks durable on the drawers -- told to the server (${CHUNK_RECEIVED:-?} taken from the stream)"
    _chunk_tell fetching "$@"; _cft=$?; CHUNK_FETCHED=""; CHUNK_RECEIVED=""
    # #2023: "hold" / "fail" in the answer (brain_drawer_hold = on): the drawer completes in the background / fails by name (chunkhold.sh)
    if [ "$_cft" = 0 ] && command -v chunk_hold_take > /dev/null; then chunk_hold_take "$@"; fi   # (not `|| ! cmd ||`: a bare negation is #231's)
    return "$_cft"
}

_chunk_fetch() {   # $1 = server, $2 = image, $3 = file, $4 = index, $5 = pass, $6 = the disks (a word list), $7.. = nodes
    _cf_url="$1/api/v1/images/$2/files/$3"; _cf_idx="$4"; _cf_pass="$5"; _cf_disks="$6"; shift 6
    jq -r '[.ranges[] | "\(.[0])-\(.[1])"] | join(",")' "${CHUNK_ANSWER:-$CHUNK_DIR/answer.json}" > "$CHUNK_DIR/fetch.ranges" 2>> "$LOG_FILE" || return 1
    grep -q '^[0-9]' "$CHUNK_DIR/fetch.ranges" || { log "chunk fetch: the server's answer names no ranges"; return 1; }
    # one walk over the index: a range's first chunk says where its bytes begin, its last where they end
    tr ',' '\n' < "$CHUNK_DIR/fetch.ranges" | awk -v url="$_cf_url" -v stall="$WAIT_STREAM_STALL_S" -v conn="$HTTP_TIMEOUT" '
        NR == FNR { split($0, r, "-"); lo[++n] = r[1] + 0; hi[n] = r[2] + 0; next }
        FNR == 1 { i = 1; next }
        i > n { exit }
        { k = FNR - 2
          if (k == lo[i]) from = $1
          if (k == hi[i]) {
              printf "url = \"%s\"\nrange = \"%s-%s\"\nfail\nspeed-limit = 1\nspeed-time = %s\nconnect-timeout = %s\n", \
                  url, from, $1 + $2 - 1, stall, conn
              made++; i++
              if (i <= n) print "next" } }
        END { exit made == n ? 0 : 1 }' - "$CHUNK_IDX" > "$CHUNK_DIR/fetch.curl" \
        || { log "chunk fetch: a range of the server's answer is not in the index"; return 1; }
    if command -v rx_http_begin >/dev/null 2>&1; then rx_http_begin; fi   # #3064 S59: the fetch's clock (rxsense.sh)
    rm -f "$CHUNK_DIR/fetch.rc" "$CHUNK_STATE.taken"; : > "$CHUNK_DIR/apply.out"   # taken: this fetch's, not an earlier one's -- a number left over would be proof of life of nothing
    # shellcheck disable=SC2086 # the image arguments are a word list on purpose
    { curl -sS --fail-early -K "$CHUNK_DIR/fetch.curl" 2>> "$LOG_FILE"; echo "$?" > "$CHUNK_DIR/fetch.rc"; } \
        | chunkcast apply --part "$_cf_idx" $CHUNK_IMAGE_ARGS --state "$CHUNK_STATE" \
            --chunks-file "$CHUNK_DIR/fetch.ranges" "$@" > "$CHUNK_DIR/apply.out" 2>> "$LOG_FILE" &
    _cf_pid=$!; echo "$_cf_pid" > "$CHUNK_DIR/apply.pid"   # #3112: "cancel this disk" stops a completion in the background by it (drawer_catchup_stop, drawerback.sh)
    # shellcheck disable=SC2086 # the disks are a word list on purpose
    CHUNK_OUT="$CHUNK_DIR/apply.out" CHUNK_TELL="$_cf_pass" _chunk_run "ההורדה של מחיצה $_cf_idx (מעבר $_cf_pass)" "$_cf_pid" "$_cf_idx" $_cf_disks; _cf_rc=$?
    rm -f "$CHUNK_DIR/apply.pid"   # a pid kept past the process is another process's
    # what did not arrive or did not match stays missing: the next report says so, and the server counts the attempt
    log "chunk fetch: $(tr ',' '\n' < "$CHUNK_DIR/fetch.ranges" | grep -c .) ranges: curl rc=$(cat "$CHUNK_DIR/fetch.rc" 2>/dev/null || echo ?), apply rc=$_cf_rc"
    if command -v rx_http_note >/dev/null 2>&1; then rx_http_note "$_cf_rc"; fi   # #3064 S59: ranges, bytes, seconds, curl rc -> the report (rxsense.sh; always 0)
}
