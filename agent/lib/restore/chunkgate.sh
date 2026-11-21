# chunkgate.sh -- which stream engine this session's wave uses (#2533, stage 3).
# POSIX sh (busybox ash).
#
# The server chooses (settings: "stream engine", experimental, off by default)
# and says so in the hello answer of a running session:
#
#   session.engine  absent | "udpcast"   today's path: udp-receiver | fanout | partclone
#                   "chunk"              chunkcast recv (chunkrecv.sh), with
#   session.chunk   {"group": "239.x.x.x", "port": N, "key": "<16 hex>", "source": "<the server's IPv4>"}
#
# The source (Nadav 14/11, #1079/#2485): chunkcast recv takes packets from that address only
# (--from); the others are dropped, counted, and told to the server (chunk_stray_json, below).
# A server that names none (older) gets today's behaviour -- every source -- and a WARNING in the
# log; one that names an unusable one is refused, as for any other parameter.
#
# Nothing here falls back. An engine this agent does not know, a chunk session
# without its three parameters, an image that is not format 2, or an initramfs
# without the receiver are each refused BY NAME before a disk is touched: a
# receiver that quietly ran udp-receiver against a chunkcast sender would wait
# out its start timeout and blame the network (principle 5; the design, 5.7:
# "an engine that fails is a round that fails, never a silent drop to the other").

chunk_gate() {
    # $1 = the hello answer, $2 = the session's manifest. Sets STREAM_MODE
    # (multicast | chunk) and, for chunk, CHUNK_GROUP / CHUNK_PORT / CHUNK_KEY /
    # CHUNK_SESSION. 1 = refused, with CHUNK_REFUSAL said.
    STREAM_MODE=multicast; CHUNK_REFUSAL=""; CHUNK_SOURCE=""
    _cg=$(json_get "$1" ".session.engine")
    case "$_cg" in
        null|udpcast) return 0 ;;
        chunk) ;;
        *) CHUNK_REFUSAL="restore: the server named a stream engine this agent does not know ('$_cg') -- nothing was written"
           return 1 ;;
    esac
    CHUNK_GROUP=$(json_get "$1" ".session.chunk.group"); CHUNK_PORT=$(json_get "$1" ".session.chunk.port")
    CHUNK_KEY=$(json_get "$1" ".session.chunk.key"); CHUNK_SESSION=$(json_get "$1" ".session.id")
    CHUNK_SOURCE=$(json_get "$1" ".session.chunk.source")
    case "$CHUNK_GROUP" in
        ''|null|*[!0-9.]*|*..*|.*|*.) _cg_bad="group '$CHUNK_GROUP'" ;;
        *.*.*.*) _cg_bad="" ;;
        *) _cg_bad="group '$CHUNK_GROUP'" ;;
    esac
    case "$CHUNK_PORT" in
        ''|null|*[!0-9]*|0*) _cg_bad="port '$CHUNK_PORT'" ;;
        *) { [ "${#CHUNK_PORT}" -le 5 ] && [ "$CHUNK_PORT" -ge 1024 ] && [ "$CHUNK_PORT" -le 65535 ]; } || _cg_bad="port '$CHUNK_PORT'" ;;
    esac
    case "$CHUNK_KEY" in
        *[!0-9a-f]*) _cg_bad="session key" ;;
        *) [ "${#CHUNK_KEY}" -eq 16 ] || _cg_bad="session key" ;;
    esac
    case "$CHUNK_SOURCE" in
        ''|null) CHUNK_SOURCE="" ;;   # not named: today's behaviour, said below
        *[!0-9.]*|*..*|.*|*.|*.*.*.*.*) _cg_bad="source '$CHUNK_SOURCE'" ;;   # chunkcast checks it again (inet_pton)
        *.*.*.*) ;;
        *) _cg_bad="source '$CHUNK_SOURCE'" ;;
    esac
    if [ -n "$_cg_bad" ]; then
        CHUNK_REFUSAL="restore: the chunk engine's $_cg_bad is not usable -- nothing was written"
        return 1
    fi
    if [ "$(json_get "$2" ".image_format")" != 2 ]; then
        CHUNK_REFUSAL="restore: the chunk engine needs a format-2 image, and this manifest is not one -- nothing was written"
        return 1
    fi
    if ! command -v chunkcast >/dev/null 2>&1; then
        CHUNK_REFUSAL="restore: the server chose the chunk engine and this initramfs has no chunkcast -- nothing was written"
        return 1
    fi
    STREAM_MODE=chunk
    if [ -n "$CHUNK_SOURCE" ]; then log "stream engine: chunk ($CHUNK_GROUP:$CHUNK_PORT from $CHUNK_SOURCE only, named by the server)"
    else log "WARNING: stream engine: chunk ($CHUNK_GROUP:$CHUNK_PORT), and the server named no source address -- packets are taken from any source"; fi
}

chunk_stray_json() {   # $1 = the control message's state -> `,"stray":N` of the pass that just ended, or nothing
    # chunkcast's `stray <N>` (packets of another source, dropped) travels in the file of `skipped` (chunkrecv.sh
    # writes it after a pass and removes it at a fetch): one lifecycle. No line = not read (no --from, an older
    # chunkcast): the field is absent, never 0 (principle 5).
    [ "$1" = ended ] && [ -s "$CHUNK_DIR/skipped" ] || return 0
    awk '$1 == "loss_shape" {printf ",\"loss_shape\":%s", $2}' "$CHUNK_DIR/skipped"
    awk '$1 == "stray" && $2 ~ /^[0-9]+$/ { v = $2 } END { if (v != "") printf ",\"stray\":%d", v }' "$CHUNK_DIR/skipped" 2>/dev/null   # chunkcast says it more than once a pass: the last is the pass's
    return 0
}
