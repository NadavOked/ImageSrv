# netwatch.sh -- what the distribution port hears: IGMP queries, multicast nobody asked this port for,
# traffic that is not ImageSrv's (#3064: N4, N6, N8). POSIX sh (busybox ash). Needs netprobe.sh (_np_iface).
#
# Hello carries ,"netwatch":{...} (a sibling of probe, like netprobe), in the three states of principle 5:
#   measured   the output of imagesrv-netwatch, plus age_s = seconds since the sample ended
#   {"pending":true}   the first sample is still running
#   {"error":"..."}    the tool tried and could not listen (rc 3), or died
# and nothing at all when the binary is absent (an old image): the server stores that as "not reported".
#
# The sample (NETWATCH_S seconds -- longer than the 125 s a querier waits between queries, or "no query heard"
# would mean nothing) runs in the BACKGROUND and only while the cloner idles: the same gate as
# cable_live_check (no round open, no task). The moment the cloner leaves idle, netwatch_stop kills it and the
# half-finished sample is dropped -- a window that overlaps a wave this machine joined would read as a flood.
# Promiscuous (--promisc) is the point: without it the NIC's own filter hides the multicast the switch flooded.

NETWATCH="${NETWATCH:-imagesrv-netwatch}"; NETWATCH_S="${NETWATCH_S:-140}"; NETWATCH_GAP="${NETWATCH_GAP:-30}"
NETWATCH_GRACE="${NETWATCH_GRACE:-30}"   # seconds past its own window before the tool is killed and the sample is an error (wait_pid, waits.sh)

_nw_have() { command -v "$NETWATCH" >/dev/null 2>&1; }

_nw_running() { [ -s "$RUN_DIR/netwatch.pid" ] && kill -0 "$(cat "$RUN_DIR/netwatch.pid")" 2>/dev/null; }

# $1 = the tool's exit code. The work file is the cache only when it holds the tool's own JSON line.
_nw_finish() {
    _nw_w="$RUN_DIR/netwatch.work"
    case "$1" in
        0|3) [ -s "$_nw_w" ] || printf '{"error":"imagesrv-netwatch rc %s: הכלי לא הדפיס דבר"}\n' "$1" > "$_nw_w" ;;
        1[3-9][0-9]|2[0-9][0-9]) rm -f "$_nw_w"; return 0 ;;   # killed (netwatch_stop): no sample, keep the last one
        "$WAIT_TIMED_OUT") printf '{"error":"imagesrv-netwatch לא הסתיים תוך %s שניות"}\n' "$((NETWATCH_S + NETWATCH_GRACE))" > "$_nw_w" ;;
        *) printf '{"error":"imagesrv-netwatch rc %s"}\n' "${1:-?}" > "$_nw_w" ;;   # an rc nobody wrote down is not a measurement either
    esac
    mv "$_nw_w" "$RUN_DIR/netwatch.json" && date +%s > "$RUN_DIR/netwatch.json.at"
}

netwatch_idle_start() {
    [ "${D_ROLE:-}" = cloner ] && [ "${D_SESSION_STATE:-}" != open ] && [ "${D_TASK:-null}" = null ] || return 0
    [ -d "$RUN_DIR" ] && _nw_have || return 0
    _nw_running && return 0
    _nw_now=$(date +%s 2>/dev/null) || return 0
    _nw_at=$(cat "$RUN_DIR/netwatch.json.at" 2>/dev/null)
    case "$_nw_at" in ''|*[!0-9]*) _nw_at=0 ;; esac
    [ $((_nw_now - _nw_at)) -ge "$NETWATCH_GAP" ] || return 0
    _nw_if=$(_np_iface) || return 0
    (
        rm -f "$RUN_DIR/netwatch.rc"
        (   # the tool's parent writes down how it ended; wait_pid, which bounds the wait below, cannot say
            "$NETWATCH" "$_nw_if" "$NETWATCH_S" --promisc > "$RUN_DIR/netwatch.work" 2>/dev/null < /dev/null &
            echo "$!" > "$RUN_DIR/netwatch.pid"; wait "$!"; echo "$?" > "$RUN_DIR/netwatch.rc"
        ) &
        if wait_pid "$!" "$((NETWATCH_S + NETWATCH_GRACE))" "imagesrv-netwatch"; then
            _nw_rc=$(cat "$RUN_DIR/netwatch.rc" 2>/dev/null) || _nw_rc=   # no rc written down = not a measurement (_nw_finish)
        else
            _nw_rc=$WAIT_TIMED_OUT; netwatch_stop   # past the ceiling the parent is gone (kill -9); the tool is stopped by its own pid
        fi
        rm -f "$RUN_DIR/netwatch.pid"
        _nw_finish "$_nw_rc"
    ) > /dev/null 2>&1 < /dev/null &
}

netwatch_stop() {
    _nw_running && kill "$(cat "$RUN_DIR/netwatch.pid")" 2>/dev/null
    return 0
}

# ,"netwatch":{...}  -- only for a cloner (a build machine never samples, and "pending" would be a lie).
netwatch_json() {
    [ "${D_ROLE:-}" = cloner ] && [ -d "$RUN_DIR" ] && _nw_have || return 0
    if [ ! -s "$RUN_DIR/netwatch.json" ]; then printf ',"netwatch":{"pending":true}'; return 0; fi
    _nw_now=$(date +%s 2>/dev/null) || _nw_now=0
    _nw_at=$(cat "$RUN_DIR/netwatch.json.at" 2>/dev/null)
    case "$_nw_at" in ''|*[!0-9]*) _nw_at=$_nw_now ;; esac
    _nw_age=$((_nw_now - _nw_at)); [ "$_nw_age" -ge 0 ] || _nw_age=0
    printf ',"netwatch":%s' "$(tr -d '\n' < "$RUN_DIR/netwatch.json" | sed "s/^{/{\"age_s\":$_nw_age,/")"
}
