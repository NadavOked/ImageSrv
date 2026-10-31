#!/bin/sh
# Record the latest hello attempt separately from its response.  A blank code
# means transport failed before an HTTP response; it must not look like code 0.

#: #1447: how far (seconds) the clock may be from the server's before it is set.
CLOCK_SLACK_S="${CLOCK_SLACK_S:-2}"

clock_from_server() {
    # #1447: the machine has no NTP, and its RTC drifts or has a dead battery
    # (cloner 2 ran 13 min ahead on metal 16/10). The hello reply carries the
    # server's epoch in "now"; set the clock from it and log the correction.
    # No "now" (an older server) = nothing to compare. A value that is not a
    # number, or a date -s that fails, is logged and the clock stays as it was:
    # the hello itself is not failed for it. $1 = the reply JSON.
    _cs_now=$(json_get "$1" ".now")
    [ "$_cs_now" = "null" ] && return 0
    case "$_cs_now" in ''|*[!0-9]*) log "clock: server time '$_cs_now' is not a number; clock not set"; return 0 ;; esac
    _cs_here=$(date +%s) || { log "clock: cannot read the local clock; not set"; return 0; }
    _cs_off=$((_cs_here - _cs_now))
    [ "$_cs_off" -gt "$CLOCK_SLACK_S" ] || [ "$_cs_off" -lt "-$CLOCK_SLACK_S" ] || return 0
    if date -s "@$_cs_now" > /dev/null; then
        log "clock: set from the server, was ${_cs_off}s off (local $_cs_here, server $_cs_now)"
    else
        log "clock: date -s failed; the clock stays ${_cs_off}s off the server"
    fi
}

hello_post() {
    # $1 = URL, $2 = request JSON, $3 = response JSON.
    _hp_code=$(curl -sS -f --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -o "$3.next" -w '%{http_code}' -H "Content-Type: application/json" \
        --data-binary "@$2" "$1")
    _hp_rc=$?
    case "$_hp_code" in 000|*[!0-9]*|"") _hp_code="" ;; esac
    # #1447: before hello.state is stamped, so its time is the corrected one.
    [ "$_hp_rc" -eq 0 ] && clock_from_server "$3.next"
    printf '%s|%s\n' "$(date +%s)" "$_hp_code" > "$RUN_DIR/hello.state.next" || return 1
    mv "$RUN_DIR/hello.state.next" "$RUN_DIR/hello.state" || return 1
    if [ "$_hp_rc" -ne 0 ]; then rm -f "$3.next"; return "$_hp_rc"; fi
    mv "$3.next" "$3"
}
