# streamid.sh -- which stream belongs to this session (#1826).
# POSIX sh (busybox ash).
#
# The manifest every received partition is checked against is the session's
# image's own -- by its id, positive evidence -- before the first byte reaches
# a disk. A stream of another image then fails that check by name, and it can
# only do so against the right manifest. (The chunk engine's wave is named by
# the server in the same answer: group, port, key and source -- chunkgate.sh.)

session_stream() {
    # $1 = the session's image id, $2 = its manifest file, $3 = the hello answer.
    # On refusal returns 1 with STREAM_REFUSAL said.
    STREAM_REFUSAL=""
    _sid_id=$(json_get "$2" ".id")
    if [ "$_sid_id" != "$1" ]; then
        STREAM_REFUSAL="restore: manifest is not of the session's image ($_sid_id, not $1) -- nothing was written"
        return 1
    fi
}
