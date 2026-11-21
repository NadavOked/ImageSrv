# chunkstall.sh -- a drawer that stands still inside a chunk pass is said within seconds (#3336, of #2988).
# POSIX sh (busybox ash).
#
# #2988 (Nadav 12/11): a pulled disk or a pulled cable left the drawer on "writing" with its last speed. The
# record that says otherwise (targets/<dev>/stall, stall.sh) is written by one function, drawer_stall_tick,
# and the only one who called it was drawer_watch -- which does not run in a chunk pass. So in a chunk round
# the line "waiting for the disk" / "the network was cut" never appeared, on the cloner's screen or in the console.
#
# chunkwatch.sh ticks it now, once a tick, with what a chunk pass can say of itself. stall.sh decides nothing
# here either: the verdict stays with chunkcast's own ceilings, and this file only says what is already so.
#
#   the drawer's counter   the compressed bytes that are DURABLE on it (chunkrecv.sh `_chunk_counters`:
#                          feed.bytes). `bytes.raw` is here a mark of "a chunk arrived", not a counter.
#   the stream's counter   a receive pass: the UDP datagrams this machine was handed (/proc/net/snmp
#                          InDatagrams), counted only in ticks that brought CHUNK_STALL_PKTS or more -- a
#                          stray broadcast is not the stream. A fetch: the chunks apply has TAKEN from the
#                          HTTP stream (`<state>.taken`, #3149) -- durable stands still on a slow flush.
#                          Not read = nothing to judge by: stall.sh then names only a pulled cable.
#   the ceilings           the network's is chunkcast's --stall-s (and curl's speed-time in a fetch): both
#                          WAIT_STREAM_STALL_S. The disk's is chunkcast's own CHUNKCAST_WRITE_STALL_MS, not
#                          fanout's: a limit that is not the one that applies is what #3035 was.
#   when it is shown       CHUNK_STALL_SHOW_S, 6: three of chunkcast's flushes (--sync-ms 2000), because the
#                          durable counter moves once a flush and 3 s would name a healthy drive that is
#                          between two of them.
#   not judged             a drawer that holds every chunk of the partition (nothing is due to it, whatever
#                          the stream does for the others), a tick whose state could not be read (principle 5:
#                          it is not "nothing arrived"), and a completion in the background (drawerback.sh).
#
# One pass is one measurement: the records are cleared when the watch ends, so the first chunk of the next
# pass is not a stall and a repair pass does not inherit the last one's clock.

# #3533: three flushes of whatever --sync-ms the pass runs with (CHUNK_SYNC_MS, chunkrecv.sh passes it on); not a number = chunkcast refuses it
case "${CHUNK_SYNC_MS:-2000}" in ''|*[!0-9]*) _cs_sync=2000 ;; *) _cs_sync=${CHUNK_SYNC_MS:-2000} ;; esac
CHUNK_STALL_SHOW_S="${CHUNK_STALL_SHOW_S:-$((_cs_sync * 3 / 1000))}"
CHUNK_STALL_PKTS="${CHUNK_STALL_PKTS:-20}"

chunk_stall_src() {   # the stream's counter on stdout; nothing = not read. Runs in a $( ): what it keeps, it keeps in files
    if [ -n "${CHUNK_TELL:-}" ]; then cat "$CHUNK_STALL_LIVE.taken" 2>/dev/null; return 0; fi
    _cs_n=$(awk '$1 == "Udp:" { if (!h) { for (i = 2; i <= NF; i++) c[$i] = i; h = 1; next }
        if (c["InDatagrams"] && $c["InDatagrams"] ~ /^[0-9]+$/) { print $c["InDatagrams"]; ok = 1 } exit } END { exit !ok }' \
        "${SYSROOT:-}/proc/net/snmp" 2>/dev/null) || return 0
    _cs_p=; _cs_a=; [ ! -r "$RUN_DIR/stall.dg" ] || read -r _cs_p _cs_a < "$RUN_DIR/stall.dg"   # <datagrams at the last tick> <the stream's count>
    case "$_cs_p:$_cs_a" in *[!0-9:]*|:*|*:) _cs_p=$_cs_n; _cs_a=0 ;; esac
    [ "$_cs_n" -ge "$_cs_p" ] || _cs_p=$_cs_n   # a counter that went back: counted from here
    [ $((_cs_n - _cs_p)) -lt "$CHUNK_STALL_PKTS" ] || _cs_a=$((_cs_a + _cs_n - _cs_p))
    echo "$_cs_n $_cs_a" > "$RUN_DIR/stall.dg"; echo "$_cs_a"
}

_chunk_stall_whole() {   # $1 = dev. 0 = it holds every chunk of this partition. An index that cannot be read proves nothing: not judged either
    _cz_t=$(awk 'NR == 1 { print $9 }' "$CHUNK_IDX" 2>/dev/null); [ -n "$_cz_t" ] || return 0
    _cz_h=$(_chunk_line "$(partition_node "$1" "$CHUNK_STALL_IDX")" | awk '$2 == "ok" { print $3 }')
    [ "$_cz_h" = "$_cz_t" ]
}

chunk_stall_tick() {   # $1 = partition index, $2 = chunkcast's own state file, $3.. = disks. chunkwatch.sh, once a tick
    [ -z "${CHUNK_BG:-}" ] || return 0
    command -v drawer_stall_tick > /dev/null || return 0
    CHUNK_STALL_IDX="$1"; CHUNK_STALL_LIVE="$2"; shift 2
    STALL_SHOW_S=$CHUNK_STALL_SHOW_S; STALL_SRC_FN=chunk_stall_src; STALL_WHOLE_FN=_chunk_stall_whole; STALL_NET_LIMIT_S=$WAIT_STREAM_STALL_S
    _ks=${CHUNKCAST_WRITE_STALL_MS:-120000}
    case "$_ks" in ''|*[!0-9]*) STALL_DISK_LIMIT_S=- ;; *) STALL_DISK_LIMIT_S=$((_ks / 1000)) ;; esac
    drawer_stall_tick "$(_chunk_live "$@")"
}

chunk_stall_end() {   # $@ = disks. The pass's watch ended: nothing stands any more, and the next pass is measured from its own first chunk
    [ -z "${CHUNK_BG:-}" ] || return 0
    command -v drawer_stall_clear > /dev/null || return 0
    drawer_stall_clear "$*"; rm -f "$RUN_DIR/stall.dg"
}
