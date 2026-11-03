# timing.sh -- the console's wait and power-off times, applied by the agent
# (#2056, #2059; interfaces.md: lost_wait_seconds / idle_poweroff_seconds /
# end_poweroff_seconds in the hello answer, applied_timing in the hello).
# POSIX sh (busybox ash).
#
# Every hello answer of a registered machine carries the three numbers. Each
# one that is a whole number in its range (the twins of RANGES in
# server/agent/timing.py -- a test compares) is applied, and the next hello
# says so in applied_timing -- that one and nothing else (principle 5): a value
# that did not come, is out of range or was not applied is absent there. Not
# applied = the agent's own constant (waits.sh, idle.sh) -- never 0, never
# "forever" -- with a log line when that changes, not one per hello.
#
#   lost_wait_seconds     -> the stream stall ceiling WAIT_STREAM_STALL_S and
#                            udp-receiver's --receive-timeout (UDPCAST_STALL_TIMEOUT,
#                            restore.sh). The server waits that long for a silent
#                            cloner before it calls it lost; the agent must not give
#                            up first, so its ceiling is the server's value +
#                            TIMING_STALL_MARGIN_S, never below its own (300).
#                            120 = 300 - 180: the margin the two defaults always
#                            had, so the default changes nothing. The watchdog
#                            lease (600) is no limit here: wait_progress feeds it
#                            on every sample, still counter or not.
#   idle_poweroff_seconds -> IDLE_POWEROFF_S (idle.sh: idle_check)
#   end_poweroff_seconds  -> END_POWEROFF_S  (idle.sh: end_countdown)
#
# A variable the environment set (the tests' short ceilings) wins and is not
# reported -- the server's value was not applied. That is read HERE, at load,
# so imagesrv-agent sources this file before waits.sh, restore.sh and idle.sh
# fill the variables in (a test holds the order). Left alone on purpose: the
# local clone's LC_STALL_S (lccopy.sh) and WAIT_DRAWER_S -- a disk-to-disk
# copy and a drawer's drain; no server waits on either.

TIMING_ENV="${WAIT_STREAM_STALL_S:+lost }${UDPCAST_STALL_TIMEOUT:+lost }${IDLE_POWEROFF_S:+idle }${END_POWEROFF_S:+end }"
TIMING_STALL_MARGIN_S=120
TIMING_APPLIED=""   # "<field>":N,... -- what the last answer applied
TIMING_SAID=""      # the last log line, so it is written once per change

_timing_int() {
    # $1 = value, $2/$3 = the range. The rule of timing.parse: digits only, no
    # leading 0 (ash reads 020 as octal), at most 5 digits, inside the range.
    case "$1" in ''|0*|*[!0-9]*|??????*) return 1 ;; esac
    [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

_timing_pick() {
    # $1 = the field, $2 = the answer, $3/$4 = its range, $5 = its name in
    # TIMING_ENV. Sets _tp to the server's value when it is applied ("" = not)
    # and adds it to TIMING_APPLIED; appends the field's state to _tp_line.
    _tp=""; _tp_v=$(json_get "$2" ".$1")
    case " $TIMING_ENV " in
        *" $5 "*) _tp_why="set in the environment" ;;
        *) if [ "$_tp_v" = null ]; then _tp_why="not in the answer"
           elif _timing_int "$_tp_v" "$3" "$4"; then _tp=$_tp_v; _tp_why=""
           else _tp_why="'$_tp_v' is not $3-$4"; fi ;;
    esac
    if [ -n "$_tp" ]; then
        TIMING_APPLIED="${TIMING_APPLIED:+$TIMING_APPLIED,}\"$1\":$_tp"
        _tp_line="$_tp_line $1=$_tp;"
    else
        _tp_line="$_tp_line $1: the agent's own ($_tp_why);"
    fi
}

timing_take() {
    # $1 = the hello answer (read_answer, every answer). The agent's own values
    # are what the variables hold before the first one.
    : "${TIMING_OWN_STALL:=$WAIT_STREAM_STALL_S}" "${TIMING_OWN_IDLE:=$IDLE_POWEROFF_S}" "${TIMING_OWN_END:=$END_POWEROFF_S}"
    TIMING_APPLIED=""; _tp_line=""
    _timing_pick lost_wait_seconds "$1" 180 1800 lost
    _tm_stall=$TIMING_OWN_STALL
    if [ -n "$_tp" ] && [ $((_tp + TIMING_STALL_MARGIN_S)) -gt "$_tm_stall" ]; then
        _tm_stall=$((_tp + TIMING_STALL_MARGIN_S))
    fi
    case " $TIMING_ENV " in
        *" lost "*) ;;
        *) WAIT_STREAM_STALL_S=$_tm_stall; UDPCAST_STALL_TIMEOUT=$_tm_stall ;;
    esac
    _timing_pick idle_poweroff_seconds "$1" 60 3600 idle
    IDLE_POWEROFF_S=${_tp:-$TIMING_OWN_IDLE}
    _timing_pick end_poweroff_seconds "$1" 10 300 end
    END_POWEROFF_S=${_tp:-$TIMING_OWN_END}
    _tp_line="timing:$_tp_line stall ceiling ${WAIT_STREAM_STALL_S}s"
    [ "$_tp_line" = "$TIMING_SAID" ] && return 0
    TIMING_SAID=$_tp_line
    log "$_tp_line"
}

timing_json() {
    # build_hello's fragment: what the last answer applied. {} before the first
    # answer and when nothing was -- present, so the server's record is replaced.
    printf ',"applied_timing":{%s}' "$TIMING_APPLIED"
}
