# rcvbuf.sh -- udp-receiver's socket receive buffer: asked for, read back,
# reported (#2994, #2555). POSIX sh (busybox ash).
#
# udp-receiver (udpcast 20120424, the Debian 13 package) sets no SO_RCVBUF on
# Linux unless --rcvbuf is given (udp-receiver.c: requestedBufSize = 0), so its
# socket gets net.core.rmem_default -- 212992 bytes on a stock kernel. The
# kernel charges a datagram at its truesize, not its 1456 bytes of payload, so
# that is a few milliseconds of a 600 Mbit/s stream. On 12/11 cloner 2 (HP
# 8100, two SSD drawers) dropped 3.94-3.96% of every wave in its own kernel
# (RcvbufErrors 532,847 ~ the sender's 529,282 re-xmits, #2028) -- the loss
# that held the learned rate at 600m. Whether a bigger buffer removes it is
# for the metal to say (#2994); this file only gives the stream the buffer.
#
# Each multicast stream asks for UDPCAST_RCVBUF bytes. The kernel grants
# min(asked, rmem_max) * 2 (socket(7)). rmem_max is raised to the ask once per
# boot -- never lowered -- and read back before every stream: the read is the
# evidence, not the write (principle 5). A lower rmem_max set after that (by
# hand, an A/B on the metal) is honoured and reported, never undone.
#
# What the socket got rides in the round report beside the drops it explains
# (udp_drops.rcvbuf_asked / rcvbuf_bytes, interfaces.md): the smallest grant
# over this restore's streams, null when one of them was not read. Absent =
# no stream asked. UDPCAST_RCVBUF=0 asks for nothing: the agent before #2994.

UDPCAST_RCVBUF="${UDPCAST_RCVBUF:-16777216}"

_rcvbuf_int() { case "$1" in ''|*[!0-9]*) return 1 ;; esac; [ "${#1}" -le 12 ]; }

rcvbuf_check() {
    # Before each udp-receiver (stream_source). Sets RCVBUF_ASK; empty = no --rcvbuf.
    RCVBUF_ASK=""
    if ! _rcvbuf_int "$UDPCAST_RCVBUF" || [ "$UDPCAST_RCVBUF" -eq 0 ]; then
        log "udp rcvbuf: not asked (UDPCAST_RCVBUF=$UDPCAST_RCVBUF) -- the kernel default"
        return 0
    fi
    _rb_f="${SYSROOT:-}/proc/sys/net/core/rmem_max"
    if [ ! -e "$RUN_DIR/rcvbuf.raised" ]; then
        : > "$RUN_DIR/rcvbuf.raised"
        _rb_m=$(cat "$_rb_f" 2>/dev/null) || _rb_m=""
        if _rcvbuf_int "$_rb_m" && [ "$_rb_m" -lt "$UDPCAST_RCVBUF" ]; then
            { echo "$UDPCAST_RCVBUF" > "$_rb_f"; } 2>/dev/null   # not the evidence: the read below is
        fi
    fi
    RCVBUF_ASK=$UDPCAST_RCVBUF
    _rb_m=$(cat "$_rb_f" 2>/dev/null) || _rb_m=""
    if ! _rcvbuf_int "$_rb_m"; then
        _rb_g=null
        log "WARNING: udp rcvbuf: asked $RCVBUF_ASK, rmem_max not read -- what the socket got is unknown"
    elif [ "$_rb_m" -lt "$RCVBUF_ASK" ]; then
        _rb_g=$((_rb_m * 2))
        log "WARNING: udp rcvbuf: asked $RCVBUF_ASK, cut to rmem_max $_rb_m -- the socket gets $_rb_g bytes"
    else
        _rb_g=$((RCVBUF_ASK * 2))
        log "udp rcvbuf: asked $RCVBUF_ASK, the socket gets $_rb_g bytes (rmem_max $_rb_m)"
    fi
    _rb_p=$(cat "$RUN_DIR/rcvbuf.min" 2>/dev/null) || _rb_p=""
    if [ "$_rb_p" = null ]; then _rb_g=null
    elif _rcvbuf_int "$_rb_p" && [ "$_rb_g" != null ] && [ "$_rb_p" -lt "$_rb_g" ]; then _rb_g=$_rb_p
    fi
    echo "$_rb_g" > "$RUN_DIR/rcvbuf.min"
}

rcvbuf_frag() {
    # The udp_drops keys (leading comma); nothing until a stream of this restore asked.
    _rb_g=$(cat "$RUN_DIR/rcvbuf.min" 2>/dev/null) || return 0
    _rcvbuf_int "$_rb_g" || _rb_g=null
    printf ',"rcvbuf_asked":%s,"rcvbuf_bytes":%s' "$UDPCAST_RCVBUF" "$_rb_g"
}
