# chunkfeed.sh -- B5 (#3064): HTTP BESIDE the stream, into the same writers (`chunkcast recv --feed`).
# POSIX sh (busybox ash). Needs waits.sh (wait_pid). Called from chunkrecv.sh, three places, each through `_chunk_feed`.
#
#   chunk_feed_prepare                                         before `chunkcast recv`: CHUNK_FEED_ARGS
#   chunk_feed_start <server> <image> <file> <idx> <answer>    after the server's answer to `listening`
#   chunk_feed_end <recv.out>                                  after the pass
#
# Off as shipped: CHUNK_PARALLEL=1 turns it on, and nothing here runs for any other value. Even on, it moves only on the
# server's word: the answer to `listening` carries `fetch_parallel: {"ranges": [[a, b], ...]}` when B5 (server/brain,
# parallelhttp.py) decided OPEN for this machine -- the drive is not what holds the stream back. No such word, no curl.
#
#   - one FIFO per pass ($CHUNK_DIR/feed.fifo), made BEFORE the receiver starts: chunkcast opens it read-write at its
#     start, and reads the list of the chunks ($CHUNK_DIR/feed.chunks) when the first byte arrives. The decision comes
#     after `listening`, so the list is written here, whole (a rename), and only then does curl begin;
#   - ONE curl, as chunkfetch.sh's: a transfer a range -- bytes [offset, offset + zbytes) of the partition file -- all on
#     one connection, written to the FIFO in ascending order. --fail-early: the frames after a failed range would not be the
#     chunks chunkcast expects. A curl that stands still ends by itself (speed-time), and chunkcast's feeder by --stall-s;
#   - chunkcast does the rest: every frame is read whole (also a chunk that is not needed), checked as any chunk -- index,
#     XXH64 and BLAKE3 -- and given to the writer of each drawer that lacks it, only while that writer's queue is under half
#     full. A forged chunk ends the feed (bad-chunk) and the stream goes on. Its word is the `feed ...` line of recv.out.
# What did not arrive stays missing, and the next report says so: the repair pass fetches it as before.

CHUNK_PARALLEL="${CHUNK_PARALLEL:-0}"
CHUNK_FEED_ARGS=""

_cfd_stop() {   # the curl of this pass, if any: ended, and waited for within the helpers' ceiling
    [ -f "$CHUNK_DIR/feed.pid" ] || return 0
    _cfd_p=$(cat "$CHUNK_DIR/feed.pid" 2>/dev/null); rm -f "$CHUNK_DIR/feed.pid"
    case "$_cfd_p" in ''|*[!0-9]*) return 0 ;; esac
    kill "$_cfd_p" 2>/dev/null; wait_pid "$_cfd_p" "${WAIT_HELPER_S:-10}" "the HTTP feed's curl" || :
}

chunk_feed_prepare() {
    CHUNK_FEED_ARGS=""; _cfd_stop
    [ "$CHUNK_PARALLEL" = 1 ] || return 0
    rm -f "$CHUNK_DIR/feed.fifo" "$CHUNK_DIR/feed.chunks" "$CHUNK_DIR/feed.curl"
    mkfifo "$CHUNK_DIR/feed.fifo" 2>> "$LOG_FILE" || { log "HTTP beside the stream: no FIFO -- the stream alone"; return 0; }
    CHUNK_FEED_ARGS="--feed $CHUNK_DIR/feed.fifo --feed-chunks $CHUNK_DIR/feed.chunks"
}

chunk_feed_start() {   # $1 = server, $2 = image, $3 = file, $4 = index, $5 = the answer to `listening`. 0 always: no feed is not a failure
    [ -n "$CHUNK_FEED_ARGS" ] || return 0
    jq -e '(.fetch_parallel.ranges // []) | type == "array" and length > 0' "$5" > /dev/null 2>&1 || return 0   # the server did not say
    jq -r '[.fetch_parallel.ranges[] | "\(.[0])-\(.[1])"] | join(",")' "$5" > "$CHUNK_DIR/feed.chunks.new" 2>> "$LOG_FILE" \
        && grep -qE '^[0-9]+-[0-9]+(,[0-9]+-[0-9]+)*$' "$CHUNK_DIR/feed.chunks.new" \
        || { log "HTTP beside the stream: the server's ranges were not read -- the stream alone"; rm -f "$CHUNK_DIR/feed.chunks.new"; return 0; }
    # one walk over the index (chunkfetch.sh's): a range's first chunk says where its bytes begin, its last where they end
    tr ',' '\n' < "$CHUNK_DIR/feed.chunks.new" | awk -v url="$1/api/v1/images/$2/files/$3" -v stall="${WAIT_STREAM_STALL_S:-60}" -v conn="${HTTP_TIMEOUT:-10}" '
        NR == FNR { split($0, r, "-"); lo[++n] = r[1] + 0; hi[n] = r[2] + 0; next }
        FNR == 1 { i = 1; next }
        i > n { exit }
        { k = FNR - 2
          if (k == lo[i]) from = $1
          if (k == hi[i]) {
              printf "url = \"%s\"\nrange = \"%s-%s\"\nfail\nspeed-limit = 1\nspeed-time = %s\nconnect-timeout = %s\n", \
                  url, from, $1 + $2 - 1, stall, conn
              made++; i++
              if (i <= n) {
                  print "next"
                  if (ENVIRON["TLS_CONNECT"] != "")
                      printf "cacert = \"%s\"\ncapath = \"%s\"\nconnect-to = \"%s\"\nproto-redir = \"=https\"\n", ENVIRON["TLS_CA"], ENVIRON["CURL_HOME"], ENVIRON["TLS_CONNECT"]
              } } }
        END { exit made == n ? 0 : 1 }' - "$CHUNK_IDX" > "$CHUNK_DIR/feed.curl" \
        || { log "HTTP beside the stream: a range of the server's answer is not in the index (or not ascending) -- the stream alone"
             rm -f "$CHUNK_DIR/feed.chunks.new" "$CHUNK_DIR/feed.curl"; return 0; }
    mv "$CHUNK_DIR/feed.chunks.new" "$CHUNK_DIR/feed.chunks" || return 0     # whole, before the first byte of curl
    curl -sS --fail-early -K "$CHUNK_DIR/feed.curl" > "$CHUNK_DIR/feed.fifo" 2>> "$LOG_FILE" &
    echo "$!" > "$CHUNK_DIR/feed.pid"
    log "partition $4: HTTP beside the stream -- $(tr ',' '\n' < "$CHUNK_DIR/feed.chunks" | grep -c .) ranges, one curl, into the same writers"
}

chunk_feed_end() {   # $1 = recv.out: curl ended, what chunkcast said of the feed logged, the FIFO gone
    _cfd_stop
    _cfe=$(awk '$1 == "feed" { print; f = 1 } END { exit !f }' "$1" 2>/dev/null) \
        && log "HTTP beside the stream: chunkcast said '$_cfe' (word, taken over HTTP, skipped as not needed, refused)"
    rm -f "$CHUNK_DIR/feed.fifo" "$CHUNK_DIR/feed.chunks" "$CHUNK_DIR/feed.curl"
    return 0
}
