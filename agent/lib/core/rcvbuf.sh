# rcvbuf.sh -- the kernel's ceiling on a UDP socket's receive buffer, raised
# for the chunk receiver (#2994, #3072). POSIX sh (busybox ash).
#
# The kernel grants a socket min(asked, rmem_max) * 2 (socket(7)), and a stock
# kernel's rmem_max is 212992 bytes: a few milliseconds of a 600 Mbit/s stream.
# chunkcast recv asks for its own buffer (CC_RCVBUF, chunkcast.c) and says what
# the kernel gave it ("rcvbuf N", chunkloss.sh). This file only lifts the
# ceiling: rmem_max is raised to RECV_RCVBUF once per boot -- never lowered --
# and read back before every pass: the read is the evidence, not the write
# (principle 5). A lower rmem_max set after that (by hand, an A/B on the metal)
# is honoured and said, never undone. RECV_RCVBUF=0 raises nothing.

RECV_RCVBUF="${RECV_RCVBUF:-16777216}"

_rcvbuf_int() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; [ "${#1}" -le 12 ]; }

rcvbuf_check() {
    # Before each chunk pass (chunkrecv.sh). Logs what rmem_max reads back.
    if ! _rcvbuf_int "$RECV_RCVBUF" || [ "$RECV_RCVBUF" -eq 0 ]; then
        log "udp rcvbuf: not raised (RECV_RCVBUF=$RECV_RCVBUF) -- the kernel default"
        return 0
    fi
    _rb_f="${SYSROOT:-}/proc/sys/net/core/rmem_max"
    if [ ! -e "$RUN_DIR/rcvbuf.raised" ]; then
        : > "$RUN_DIR/rcvbuf.raised"
        _rb_m=$(cat "$_rb_f" 2>/dev/null) || _rb_m=""
        if _rcvbuf_int "$_rb_m" && [ "$_rb_m" -lt "$RECV_RCVBUF" ]; then
            { echo "$RECV_RCVBUF" > "$_rb_f"; } 2>/dev/null   # not the evidence: the read below is
        fi
    fi
    _rb_m=$(cat "$_rb_f" 2>/dev/null) || _rb_m=""
    if ! _rcvbuf_int "$_rb_m"; then
        log "WARNING: udp rcvbuf: rmem_max not read -- what the receiver's socket can get is unknown"
    elif [ "$_rb_m" -lt "$RECV_RCVBUF" ]; then
        log "WARNING: udp rcvbuf: rmem_max $_rb_m, below $RECV_RCVBUF -- the receiver's socket is cut to $((_rb_m * 2)) bytes"
    else
        log "udp rcvbuf: rmem_max $_rb_m (raised to at least $RECV_RCVBUF)"
    fi
}
