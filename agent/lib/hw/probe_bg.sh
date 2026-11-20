# probe_bg.sh -- keep the full hardware probe off the first hello. POSIX sh.

PROBE_MAX_S="${PROBE_MAX_S:-35}"

_pb_say() { printf '[%s] probe_bg: %s\n' "$(date -u +%H:%M:%S 2>/dev/null || echo --:--:--)" "$*"; }   # to the job's own stdout = $_pb_lf
_pb_up() { _probe_up 2>/dev/null || date +%s; }   # /proc/uptime, as the probe's own seconds (#2852): `date` is aligned mid-probe

_probe_background_start() {
    # $1 = cache path, $2 = sample epoch. The marker makes an in-flight job
    # observable; capped_run owns the hard ceiling and kills by PID, not name.
    # #3524: the job's output goes to $RUN_DIR/probe_bg.log (start, ceiling kill, end with rc and
    # seconds) -- it went to /dev/null. No RUN_DIR, or a file that cannot be opened = /dev/null, as before.
    _pb_pc=$1; _pb_now=$2
    _pb_lf=/dev/null
    if [ -n "${RUN_DIR:-}" ] && { : >> "$RUN_DIR/probe_bg.log"; } 2>/dev/null; then _pb_lf="$RUN_DIR/probe_bg.log"; fi
    printf '%s\n' "$_pb_now" > "$_pb_pc.running" || return 1
    (
        bg_detach_fds   # #3548 (#3524)
        trap 'rm -f "$_pb_pc.running"' EXIT
        _pb_t0=$(_pb_up); _pb_say "start, ceiling ${PROBE_MAX_S}s"; log "gap: hardware probe start"
        capped_run "$PROBE_MAX_S" "$_pb_pc.work" _probe_fresh; _pb_rc=$?
        _pb_say "end rc=$_pb_rc timed_out=$CAPPED_TIMED_OUT after $(( $(_pb_up) - _pb_t0 ))s"
        log "gap: hardware probe end rc=$_pb_rc timed_out=$CAPPED_TIMED_OUT after $(( $(_pb_up) - _pb_t0 ))s"
        if [ "$_pb_rc" = 0 ]; then
            [ -s "$_pb_pc.work" ] || { log "hardware probe produced no result"; exit 1; }
            mv "$_pb_pc.work" "$_pb_pc" || exit 1
            printf '%s' "$_pb_now" > "$_pb_pc.at" || exit 1
        elif [ "$CAPPED_TIMED_OUT" = 1 ]; then
            _pb_say "killed at ${PROBE_MAX_S}s ceiling"
            log "hardware probe killed at ${PROBE_MAX_S}s ceiling; next hello remains unchecked"
            rm -f "$_pb_pc.work"
        else
            log "hardware probe failed; next hello remains unchecked"
            rm -f "$_pb_pc.work"
        fi
    ) >> "$_pb_lf" 2>&1 < /dev/null &
}

# Prints ,"probe":{...} when a completed sample exists. On the first call it
# starts the slow probe and returns immediately, so mac + disks can be sent.
probe_json() {
    _pc="${RUN_DIR:-/run/imagesrv}/probe.json"; _now=$(date +%s 2>/dev/null) || _now=0
    [ -s "$_pc" ] && [ -s "$_pc.at" ] && [ $((_now - $(cat "$_pc.at"))) -lt "${PROBE_TTL:-600}" ] && { cat "$_pc"; return; }
    [ -d "${_pc%/*}" ] || { _probe_fresh; return; }   # host/unit use: no hidden background job
    if [ -s "$_pc.running" ]; then
        _pb_started=$(cat "$_pc.running" 2>/dev/null)
        case "$_pb_started" in ''|*[!0-9]*) _pb_started=0 ;; esac
        if [ $((_now - _pb_started)) -le $((PROBE_MAX_S + 1)) ]; then return 0; fi
        log "stale hardware-probe marker expired; restarting" >&2   # #3637: not stdout -- a $( ) around hello is its body
        rm -f "$_pc.running" "$_pc.work"
    fi
    _probe_background_start "$_pc" "$_now" || log "hardware probe could not start" >&2
}
