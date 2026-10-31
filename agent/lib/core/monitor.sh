# monitor.sh -- supervised RFB monitor, behind its own kernel-line gate.
# POSIX sh (busybox ash).

MONITOR_PORT="${MONITOR_PORT:-5900}"
MONITOR_BIN="${MONITOR_BIN:-/usr/bin/imagesrv-monitor}"
# The framebuffer device; guibridge.sh tests the same one (#835).
FB_DEV="${FB_DEV:-/dev/fb0}"

# #839/#1077: the session secret. 5900 listens on the distribution address,
# so the RFB handshake is the gate -- and the secret behind it is drawn
# HERE, on the machine, reported in hello, and rewritten by imagesrv-monitor
# at every successful authentication (#1837). Direction matters: hello is
# unauthenticated, so a secret the server handed out in its answer could be
# fetched by anyone posting this machine's MAC. The reverse flow only lets
# a rogue hello overwrite the row (monitor DoS until the next real hello),
# never learn the secret. Only the server's proxy answers the RFB challenge
# with HMAC(secret, challenge); the browser never sees it.
_monitor_secret_file() {
    printf '%s' "${MONITOR_SECRET_FILE:-${RUN_DIR:-/run/imagesrv}/monitor.secret}"
}

# imagesrv.server is http://IP:port or IP:port. Peer-check is IPv4 only
# (the monitor binds IPv4). Missing/unparseable = do not start (fail closed).
_monitor_server_ip() {
    _s=${IMAGESRV_SERVER:-}
    case "$_s" in
        http://*)  _s=${_s#http://} ;;
        https://*) _s=${_s#https://} ;;
    esac
    _s=${_s%%/*}
    case "$_s" in
        *:*) _s=${_s%%:*} ;;
    esac
    case "$_s" in
        *.*.*.*) printf '%s' "$_s" ;;
        *) return 1 ;;
    esac
}

monitor_secret() {
    # Prints the 32-hex secret, drawing it on first use. Anything short of
    # 32 hex chars -- od missing, urandom short -- is no secret at all:
    # nothing is written, nothing is printed, and the monitor stays down.
    _msf=$(_monitor_secret_file)
    if [ ! -s "$_msf" ]; then
        _ms=$(dd if=/dev/urandom bs=16 count=1 2>/dev/null \
              | od -An -tx1 | tr -d ' \n')
        case "$_ms" in *[!0-9a-f]*|'') _ms= ;; esac
        [ "${#_ms}" -eq 32 ] || _ms=
        [ -n "$_ms" ] || { log "monitor: could not draw a boot secret" >&2; return 1; }   # #876: נלכד ב-$( )
        ( umask 077; printf '%s\n' "$_ms" > "$_msf.tmp" ) &&
            mv "$_msf.tmp" "$_msf" || return 1
    fi
    _ms=$(tr -d '\n' < "$_msf")
    case "$_ms" in *[!0-9a-f]*|'') return 1 ;; esac
    [ "${#_ms}" -eq 32 ] || return 1
    printf '%s' "$_ms"
}

_monitor_spawn() {
    setsid sh -c '
        trap "" HUP
        while :; do
            "$@" </dev/null
            rc=$?
            echo "monitor: helper exited (rc=$rc); restarting in 5s" >&2
            sleep 5 || echo "monitor: restart sleep interrupted" >&2
        done
    ' sh "$@" </dev/null >> "$LOG_FILE" 2>&1 &
    _monitor_pid=$!
    echo "$_monitor_pid" > "$RUN_DIR/monitor.pid"   # #1849: the waiter spawns in a subshell
}

# #1849: a supervisor is up -- spawned here or by the background waiter.
_monitor_alive() {
    [ -n "${_monitor_pid:-}" ] && kill -0 "$_monitor_pid" 2>/dev/null && return 0
    read -r _mpf < "$RUN_DIR/monitor.pid" 2>/dev/null &&
        [ -n "$_mpf" ] && kill -0 "$_mpf" 2>/dev/null
}

# #1849: the main loop calls monitor_start once per hello round, and a round
# whose answer is `restore`/`task` blocks in do_restore/do_task until the
# work ends -- before the GUI it starts there has created fb.mem. So the
# waiting is handed to one background waiter: it retries _monitor_launch
# every second, up to MONITOR_FB_WAIT_S (0 = no waiter), and its pid sits in
# a marker file so the main loop neither starts a second one nor spawns a
# second supervisor beside it. No network, no hello: it only looks at a file.
_monitor_waiter() {
    _mw="$RUN_DIR/monitor.waiter"
    _mw_cap=${MONITOR_FB_WAIT_S:-300}
    case "$_mw_cap" in ''|*[!0-9]*) _mw_cap=300 ;; esac
    [ "$_mw_cap" -gt 0 ] || return 0
    (
        _mw_t=0
        while [ "$_mw_t" -lt "$_mw_cap" ]; do
            sleep 1 || break
            _mw_t=$((_mw_t + 1))
            _monitor_launch && { rm -f "$_mw"; exit 0; }
        done
        log "monitor: no frame to serve ${_mw_cap}s after the first try; the next hello round tries again"
        rm -f "$_mw"
    ) </dev/null >/dev/null 2>&1 &
    echo "$!" > "$_mw"
    log "monitor: waiting in the background for the GUI's frame (up to ${_mw_cap}s)"
}

monitor_start() {
    [ "${IMAGESRV_MONITOR:-0}" = 1 ] || return 0
    _monitor_alive && return 0
    read -r _mwp < "$RUN_DIR/monitor.waiter" 2>/dev/null &&
        [ -n "$_mwp" ] && kill -0 "$_mwp" 2>/dev/null && return 1   # #1849: it is waiting

    case "${D_ROLE:-}" in
        build|cloner)   # #1454: a cloner has a keyboard and mouse too (owner 16/10)
            _monitor_input=--input
            ;;
        classroom)
            # Deliberate future gate: classroom monitoring is not built.
            return 0
            ;;
        *)
            return 0
            ;;
    esac

    [ -n "${IP:-}" ] || {
        log "monitor: management IP unavailable; not starting"
        return 1
    }
    [ -x "$MONITOR_BIN" ] || {
        log "monitor: binary absent from image"
        return 1
    }
    # #839: no secret file, no monitor. build_hello draws it before the first
    # hello; if that failed, an unauthenticated 5900 is the wrong fallback.
    _msf=$(_monitor_secret_file)
    [ -s "$_msf" ] || {
        log "monitor: no boot secret at $_msf; not starting"
        return 1
    }
    _allow=$(_monitor_server_ip) || {
        log "monitor: cannot parse server IP from IMAGESRV_SERVER=${IMAGESRV_SERVER:-}; not starting"
        return 1
    }
    _monitor_launch && return 0
    _monitor_waiter
    return 1
}

# The frame to serve and the spawn; 1 = nothing to serve yet (monitor_start).
_monitor_launch() {
    # #835: no framebuffer device (no display attached -- cloner 2): the GUI
    # draws into $RUN_DIR/fb.mem and the monitor serves that file. Until the
    # GUI has created it there is nothing to serve; the poll loop -- and the
    # #1849 waiter -- call back here, so wait quietly rather than restart-loop
    # a FATAL.
    # #1475: with a screen too, the GUI mirrors every frame into the same
    # file (--fb-mirror, guibridge.sh): RAM, plus a seqlock that says when
    # nothing changed. Serving /dev/fb0 itself is a full read of
    # write-combined video memory on every tick -- 60% of a hardware thread
    # on cloner 2 during a local clone (owner, 16/10). So fb.mem wins
    # whenever it is there; /dev/fb0 only when no mirror has appeared within
    # MONITOR_MIRROR_WAIT_S (a GUI that never started), and the log says so.
    _monitor_fb=
    if [ -s "$RUN_DIR/fb.mem" ]; then
        _monitor_fb="--fb $RUN_DIR/fb.mem"
    elif [ ! -c "$FB_DEV" ]; then
        [ "${_monitor_wait_logged:-0}" = 1 ] ||
            log "monitor: no $FB_DEV; waiting for the GUI's fb.mem"
        _monitor_wait_logged=1
        return 1
    else
        _mnow=$(date +%s)
        case "$_mnow" in
            ''|*[!0-9]*) _mnow= ;;
        esac
        [ -n "${_monitor_mirror_since:-}" ] || _monitor_mirror_since=$_mnow
        if [ -n "$_mnow" ] && [ -n "$_monitor_mirror_since" ] &&
           [ $((_mnow - _monitor_mirror_since)) -lt "${MONITOR_MIRROR_WAIT_S:-60}" ]; then
            [ "${_monitor_wait_logged:-0}" = 1 ] ||
                log "monitor: waiting for the GUI's frame mirror $RUN_DIR/fb.mem"
            _monitor_wait_logged=1
            return 1
        fi
        log "monitor: no frame mirror from the GUI; serving $FB_DEV directly" \
            "(a full video-memory read every tick)"
    fi

    # #1475: the clone path wins the CPU -- the monitor is nice 10. No nice
    # is said, not silently skipped.
    _monitor_nice="nice -n 10"
    command -v nice >/dev/null 2>&1 || {
        _monitor_nice=
        log "monitor: no nice; running at normal priority"
    }

    # shellcheck disable=SC2086 # optional nice/--input/--fb are intentionally words.
    _monitor_spawn $_monitor_nice "$MONITOR_BIN" --bind "$IP" --port "$MONITOR_PORT" \
        --fps 10 --secret-file "$_msf" --allow-from "$_allow" \
        $_monitor_input $_monitor_fb
    log "monitor: supervisor started on $IP:$MONITOR_PORT" \
        "$([ -n "$_monitor_input" ] && echo '(input enabled)' ||
            echo '(view only)')"
}
