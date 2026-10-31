# streamid.sh -- which stream belongs to this session (#1826).
# POSIX sh (busybox ash).
#
# udpcast has no image id on the wire: a receiver finds its sender by the
# portbase alone. Two senders on one VLAN with the same portbase (a primary
# and a secondary, a test server left on) and the receiver may join the wrong
# one. Two things are pinned here, both before the first byte reaches a disk:
#
# 1. The portbase is the one the server named for this session
#    (`session.portbase`, interfaces.md section 3) -- not a constant both
#    sides happen to agree on. A server from before #1826 does not send the
#    field; it sends on 9000, which is what UDPCAST_PORTBASE (restore.sh)
#    already holds.
# 2. The manifest whose sha256 every received partition is compared against
#    is the session's image's own -- by its id, positive evidence. A stream
#    from another image fails that comparison by name (restore.sh), and it
#    can only do so against the right manifest.

session_stream() {
    # $1 = the session's image id, $2 = its manifest file, $3 = the hello answer.
    # Sets UDPCAST_PORTBASE; on refusal returns 1 with STREAM_REFUSAL said.
    STREAM_REFUSAL=""
    _sid_id=$(json_get "$2" ".id")
    if [ "$_sid_id" != "$1" ]; then
        STREAM_REFUSAL="restore: manifest is not of the session's image ($_sid_id, not $1) -- nothing was written"
        return 1
    fi
    _sid_pb=$(json_get "$3" ".session.portbase")
    case "$_sid_pb" in
        null) log "session portbase: not sent (a server from before #1826) -- ${UDPCAST_PORTBASE:-9000}"
              return 0 ;;
        ''|*[!0-9]*) STREAM_REFUSAL="restore: session portbase '$_sid_pb' is not a port -- nothing was written"; return 1 ;;
    esac
    # udp-receiver takes portbase and portbase+1; below 1024 is not ours to bind.
    if [ "${#_sid_pb}" -gt 5 ] || [ "$_sid_pb" -lt 1024 ] || [ "$_sid_pb" -gt 65534 ]; then
        STREAM_REFUSAL="restore: session portbase $_sid_pb is not a port (1024-65534) -- nothing was written"
        return 1
    fi
    UDPCAST_PORTBASE=$_sid_pb
    log "session portbase: $UDPCAST_PORTBASE (named by the server)"
}
