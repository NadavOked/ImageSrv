# watchdog.sh -- cloner dead-man; POSIX sh, independent of progress reporting.
# Only explicit control-loop progress renews the lease. /proc/uptime is
# monotonic: DHCP/NTP/wall-clock corrections cannot expire a healthy lease.
# NOT a guard for a power-off that hangs: iTCO_wdt calls watchdog_stop_on_reboot(),
# so the kernel stops the hardware timer on SYS_POWER_OFF, before device_shutdown()
# (v6.12 drivers/watchdog/watchdog_core.c:158-176). The lease needs a live kernel (#1563).
watchdog_now() {
    IFS=' ' read -r _wd_up _wd_rest < "${WATCHDOG_UPTIME:-/proc/uptime}" || return 1
    _wd_up=${_wd_up%%.*}
    case "$_wd_up" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s\n' "$_wd_up"
}

watchdog_beat() {
    [ "${WATCHDOG_ACTIVE:-0}" = 1 ] || return 0
    _wd_stamp=$(watchdog_now) &&
        printf '%s\n' "$_wd_stamp" > "$RUN_DIR/watchdog.beat.next" &&
        mv -f "$RUN_DIR/watchdog.beat.next" "$RUN_DIR/watchdog.beat" && return 0
    echo 'watchdog: heartbeat write failed' >&2
    return 1
}

watchdog_expired() {
    # Bad/missing reads do not manufacture freshness. Retain the last
    # observed good heartbeat, so transient errors do not kill recovery.
    if IFS= read -r _wd_read < "$RUN_DIR/watchdog.beat"; then
        case "$_wd_read" in ''|*[!0-9]*) echo 'watchdog: invalid heartbeat' >&2 ;;
            *) if [ "$_wd_read" -le "$1" ] && [ "$_wd_read" -ge "$_wd_last" ]; then
                   _wd_last=$_wd_read
               fi ;;
        esac
    else
        echo 'watchdog: cannot read heartbeat' >&2
    fi
    [ "$(( $1 - _wd_last ))" -ge 600 ]
}

watchdog_reboot() {
    echo "watchdog: ${1:-no agent progress for 600s}; rebooting" >&2
    while :; do
        reboot -f
        echo 'watchdog: reboot returned; retrying, hardware NOT fed' >&2
        sleep 5 || echo 'watchdog: reboot retry sleep interrupted' >&2
    done
}

watchdog_loop() {
    # $1 hardware/software. FD 3 stays OPEN on expiry: never magic-close
    # or exit, either of which can disable a hardware watchdog.
    echo "watchdog: $1 recovery armed; lease 600s" >&2
    _wd_clock_fail=0
    while :; do
        if _wd_now=$(watchdog_now); then
            _wd_clock_fail=0
            if watchdog_expired "$_wd_now"; then
                watchdog_reboot
            fi
        else
            echo 'watchdog: monotonic clock unreadable; not feeding hardware' >&2
            # A broken clock cannot disable the software fallback forever.
            # Count only completed sleeps, never interrupted ones.
            if sleep 1; then _wd_clock_fail=$((_wd_clock_fail + 1)); fi
            [ "$_wd_clock_fail" -lt 600 ] ||
                watchdog_reboot 'monotonic clock unavailable for 600 completed sleeps'
            continue
        fi
        if [ "$1" = hardware ]; then
            printf '.' >&3 || echo 'watchdog: hardware keepalive failed' >&2
        fi
        sleep 1 || echo 'watchdog: sleep interrupted' >&2
    done
}

watchdog_supervise() {
    set +e
    trap '' HUP
    _wd_last=$(watchdog_now) || { echo 'watchdog: no monotonic clock' >&2; return 1; }
    # Read the driver's actual timeout before opening/arming it. A one
    # second pet interval needs margin; never assume the driver's default.
    _wd_device=${WATCHDOG_DEVICE:-/dev/watchdog}
    _wd_timeout=''
    if [ -c "$_wd_device" ]; then
        if IFS= read -r _wd_timeout < /sys/class/watchdog/watchdog0/timeout; then
            case "$_wd_timeout" in ''|*[!0-9]*) _wd_timeout=0 ;; esac
            if [ "$_wd_timeout" -ge 5 ]; then
                watchdog_loop hardware 3> "$_wd_device"
                echo 'watchdog: hardware open/loop failed; software fallback' >&2
            else
                echo 'watchdog: unsafe hardware timeout; software fallback' >&2
            fi
        else
            echo 'watchdog: hardware timeout unknown; software fallback' >&2
        fi
    else
        echo 'watchdog: no hardware device; software fallback' >&2
    fi
    watchdog_loop software
}

watchdog_start() {
    # Recovery menus deliberately wait for human input. Classroom/build
    # machines are outside this headless-cloner recovery policy.
    [ "${D_ROLE:-}" = cloner ] && [ "${D_SCHEMA:-}" = 1 ] &&
        [ "${D_KNOWN:-}" = true ] && [ "${D_MODE:-}" != recovery ] || return 0
    [ "${IMAGECTL_TEST:-0}" != 1 ] || return 0
    if [ -n "${_wd_supervisor:-}" ]; then
        kill -0 "$_wd_supervisor" && return 0
        echo 'watchdog: supervisor died; restarting' >&2
    fi
    WATCHDOG_ACTIVE=1
    watchdog_beat || return 1
    setsid sh -c '. "$1/watchdog.sh"; RUN_DIR=$2; watchdog_supervise' \
        sh "$LIB_DIR" "$RUN_DIR" </dev/null >> "$LOG_FILE" 2>&1 &
    _wd_supervisor=$!
}

agent_lock() {
    # #1929 (M3, 20/10): one agent writes the drawers, never two. Nothing in
    # the code starts a second one -- /init runs it once, in the foreground,
    # and reboots when it exits; the watchdog only reboots -- so this is the
    # lock against whatever does. $RUN_DIR/agent.pid is created with
    # noclobber (atomic). A pid file whose process is gone, or is not an
    # agent, is a dead agent's: taken over, aloud. 1 = another agent is alive.
    _al_f="$RUN_DIR/agent.pid"
    ( set -C; echo "$$" > "$_al_f" ) 2>/dev/null && return 0
    _al_p=$(cat "$_al_f" 2>/dev/null)
    case "$_al_p" in
        ''|*[!0-9]*) ;;
        "$$") return 0 ;;
        *) if kill -0 "$_al_p" 2>/dev/null &&
                   tr '\0' ' ' < "${SYSROOT:-}/proc/$_al_p/cmdline" 2>/dev/null | grep -q imagectl-agent; then
               echo "agent: another agent (pid $_al_p) is running -- this one ($$) stops before it touches anything" >&2
               return 1
           fi ;;
    esac
    echo "agent: the pid file named '$_al_p', which is not a live agent -- taken over by $$" >&2
    rm -f "$_al_f" && ( set -C; echo "$$" > "$_al_f" ) 2>/dev/null
}
