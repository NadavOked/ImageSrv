# probe_bg.sh -- keep the full hardware probe off the first hello. POSIX sh.

PROBE_MAX_S="${PROBE_MAX_S:-35}"

_probe_background_start() {
    # $1 = cache path, $2 = sample epoch. The marker makes an in-flight job
    # observable; capped_run owns the hard ceiling and kills by PID, not name.
    _pb_pc=$1; _pb_now=$2
    printf '%s\n' "$_pb_now" > "$_pb_pc.running" || return 1
    (
        trap 'rm -f "$_pb_pc.running"' EXIT
        if capped_run "$PROBE_MAX_S" "$_pb_pc.work" _probe_fresh; then
            [ -s "$_pb_pc.work" ] || { log "hardware probe produced no result"; exit 1; }
            mv "$_pb_pc.work" "$_pb_pc" || exit 1
            printf '%s' "$_pb_now" > "$_pb_pc.at" || exit 1
        elif [ "$CAPPED_TIMED_OUT" = 1 ]; then
            log "hardware probe killed at ${PROBE_MAX_S}s ceiling; next hello remains unchecked"
            rm -f "$_pb_pc.work"
        else
            log "hardware probe failed; next hello remains unchecked"
            rm -f "$_pb_pc.work"
        fi
    ) >/dev/null 2>&1 < /dev/null &
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
        log "stale hardware-probe marker expired; restarting"
        rm -f "$_pc.running" "$_pc.work"
    fi
    _probe_background_start "$_pc" "$_now" || log "hardware probe could not start"
}
