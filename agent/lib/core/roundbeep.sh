# roundbeep.sh -- the PC speaker of the cloner and the build machine (#1967, #2067).
# POSIX sh (busybox ash).
#
# Nadav 24/10: five tunes (mockup 08-beep-2067, table 1), on the cloner and on
# the build machine. The setting `cloner_beep` (hello ui.cloner_beep) turns
# both off; missing = on. A tune never blocks or slows a write: it plays in
# the background, one at a time.
#
# Debian's busybox has no `beep` applet (1:1.37.0-6+b9, testrunner 22/10) and
# the initramfs has no udev, so nothing called `beep` exists there. pcspkr.ko
# (REQUIRED_MODULES) registers an input device named "PC Speaker"; a tone is
# one input_event written to its evdev node: EV_SND (0x12) / SND_TONE (0x02)
# with the frequency, and 0 stops it. struct input_event on x86_64 = 16 bytes
# of time (ignored on write) + u16 type + u16 code + s32 value, little-endian.
# No speaker / a write that fails = a log line; the screen still shows it.

SYSFS_INPUT="${SYSFS_INPUT:-${SYSROOT:-}/sys/class/input}"

round_beep_enabled() {
    [ "$(json_get "${RESP:-$RUN_DIR/response.json}" ".ui.cloner_beep" 2>/dev/null)" != false ]
}

round_beep_dev() {
    for _rb_n in "$SYSFS_INPUT"/event*; do
        [ "$(cat "$_rb_n/device/name" 2>/dev/null)" = "PC Speaker" ] || continue
        echo "${DEVROOT:-/dev}/input/${_rb_n##*/}"; return 0
    done
    return 1
}

_rb_tone() {
    # $1 = node, $2 = Hz (0 = silence). One input_event, 24 bytes.
    printf '\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\022\000\002\000'"\\$(printf %o $(($2 % 256)))\\$(printf %o $(($2 / 256 % 256)))"'\000\000' >> "$1"
}

_rb_beep() {
    # $1 = node, $2 = Hz, $3 = length (seconds, sleep's syntax).
    _rb_tone "$1" "$2" && sleep "$3"; _rb_rc=$?
    _rb_tone "$1" 0 && [ "$_rb_rc" = 0 ]
}

_rb_unheard() { log "beep $1: $2 -- not heard"; }

_rb_alive() {
    # $1 = pid. Gone or a zombie = not alive. Builtins only.
    [ -r "/proc/$1/stat" ] && { read -r _rb_x _rb_x _rb_st _rb_x < "/proc/$1/stat"; } 2>/dev/null && [ "$_rb_st" != Z ]
}

_rb_unlock() {
    # The tone off first: a tune cut mid-tone must not leave the speaker on.
    [ -z "${_rb_cut:-}" ] || _rb_tone "$_rb_cut" 0 2>/dev/null
    _rb_o=; [ ! -r "$_rb_lockdir/pid" ] || read -r _rb_o < "$_rb_lockdir/pid"
    [ "$_rb_o" != "$_rb_me" ] || rm -rf "$_rb_lockdir"   # a lock broken and taken since is not ours
}

_rb_lock() {
    # One tune at a time: mkdir is atomic; the directory holds its owner's pid.
    # A waiter never waits forever (#2067; background waiters expire): an owner
    # that is gone -- kill -9 leaves the directory -- is broken at once, and
    # after BEEP_LOCK_WAIT_S the lock is broken anyway. Two tunes over each
    # other beat a speaker silenced until the next boot (principle 5).
    _rb_lockdir="$RUN_DIR/beep.lock"; _rb_w=0; _rb_missing=0; _rb_me=; _rb_cut=
    _rb_cap=${BEEP_LOCK_WAIT_S:-30}; case "$_rb_cap" in ''|*[!0-9]*) _rb_cap=30 ;; esac
    until mkdir "$_rb_lockdir" 2>/dev/null; do
        if [ ! -d "$_rb_lockdir" ]; then
            # The owner may have unlocked after mkdir failed; retry briefly.
            _rb_missing=$((_rb_missing + 1))
            [ "$_rb_missing" -lt 3 ] || { _rb_unheard "$1" 'cannot lock the speaker'; return 1; }
            sleep .05; continue
        fi
        _rb_missing=0
        _rb_o=; [ ! -r "$_rb_lockdir/pid" ] || read -r _rb_o < "$_rb_lockdir/pid"
        if [ -n "$_rb_o" ] && ! _rb_alive "$_rb_o"; then
            # Another waiter may have broken it and a new owner taken it.
            _rb_check=; { read -r _rb_check < "$_rb_lockdir/pid"; } 2>/dev/null || :
            if [ "$_rb_check" = "$_rb_o" ]; then
                rm -rf "$_rb_lockdir"
                log "beep $1: the speaker's lock outlived its owner ($_rb_o) -- broken"; continue
            fi
        fi
        _rb_w=$((_rb_w + 1))
        if [ "$_rb_w" -gt $((_rb_cap * 20)) ]; then
            log "beep $1: the speaker stayed locked ${_rb_cap} s -- lock broken"; rm -rf "$_rb_lockdir"; _rb_w=0; continue
        fi
        sleep .05
    done
    # This (sub)shell's own pid -- $$ is the parent's. A redirection on a builtin, no fork.
    { read -r _rb_me _rb_x < /proc/self/stat; } 2>/dev/null || _rb_me=
    [ -z "$_rb_me" ] || printf '%s\n' "$_rb_me" > "$_rb_lockdir/pid" || :
    trap _rb_unlock 0
    trap 'exit 0' HUP INT TERM
}

_rb_play() {
    # Caller owns the lock. Always 0: a speaker is never a reason to stop.
    _rb_kind=$1
    # The kiosk is a process of its own: no D_ROLE there, the hello says it.
    case "${D_ROLE:-$(json_get "$RUN_DIR/response.json" .role 2>/dev/null)}" in
        cloner|build) ;;
        *) _rb_unheard "$1" 'not a cloner or a build machine'; return 0 ;;
    esac
    round_beep_enabled || { _rb_unheard "$1" 'off in the console settings'; return 0; }
    _rb_d=$(round_beep_dev) || { _rb_unheard "$1" 'no PC speaker (pcspkr)'; return 0; }
    # One table: Hz, seconds on, silence after (BEEPS in the mockup).
    case "$1" in
        success) set -- 523 .1 .02 659 .1 .02 784 .1 .02 1047 .28 0 ;;
        failure) set -- 880 .3 0 587 .3 0 880 .3 0 587 .3 0 880 .3 0 587 .3 0 ;;
        netlost) set -- 880 .22 .04 660 .22 .04 494 .45 0 ;;
        netback) set -- 900 .15 .06 1400 .15 0 ;;
        pulled) set -- 440 .08 .12 440 .08 .12 440 .08 .12 440 .08 0 ;;
        *) _rb_unheard "$1" 'unknown tune'; return 0 ;;
    esac
    # A repeat of the same notice within 5 s is one notice; a failure is never skipped.
    case "$_rb_kind" in
        netlost|netback|pulled)
            _rb_now=$(date +%s); _rb_last=0
            [ ! -r "$RUN_DIR/beep.$_rb_kind" ] || read -r _rb_last < "$RUN_DIR/beep.$_rb_kind"
            case "$_rb_last" in ''|*[!0-9]*) _rb_last=0 ;; esac
            [ $((_rb_now - _rb_last)) -ge 5 ] || { _rb_unheard "$_rb_kind" 'repeat within 5 s'; return 0; }
            printf '%s\n' "$_rb_now" > "$RUN_DIR/beep.$_rb_kind" || log 'beep: repeat timestamp not saved' ;;
    esac
    _rb_cut=$_rb_d   # until the last tone is off
    while [ "$#" -gt 0 ]; do
        if ! { _rb_beep "$_rb_d" "$1" "$2" && { [ "$3" = 0 ] || sleep "$3"; }; } 2>/dev/null; then
            _rb_unheard "$_rb_kind" "writing to $_rb_d failed"; return 0
        fi
        shift 3
    done
    _rb_cut=
}

event_beep() {
    # $1 = success | failure | netlost | netback | pulled. Returns at once.
    ( _rb_lock "$1" && _rb_play "$1" ) < /dev/null &
    return 0
}

round_beep() { event_beep "$1"; }

round_beep_result() {
    # $@ = every target of the job, the ones skipped before writing too:
    # all done = success; partial or none = failure (Nadav 24/10).
    end_tally "$@"
    if [ "$_et_n" -gt 0 ] && [ "$_et_ok" = "$_et_n" ]; then event_beep success; else event_beep failure; fi
}

_rb_stream_age() {
    # Sets _rb_age = seconds since the newest movement of any writing drawer
    # (moved.<dev>, #2055), or "" = no stream to judge: the machine is not in
    # its stream phase (drawers.sh: $RUN_DIR/state), a local copy, or no
    # partition mid-receive -- the write check, TRIM, the server's start wait
    # and the gap between partitions are not an outage. Builtin reads; `date`
    # only while a drawer streams.
    _rb_age=; _rb_s=; _rb_latest=
    [ ! -r "$RUN_DIR/state" ] || read -r _rb_s < "$RUN_DIR/state"
    [ "$_rb_s" = writing ] || return 0
    _rb_s=; [ ! -r "${LC:-$RUN_DIR/lc}/phase" ] || read -r _rb_s < "${LC:-$RUN_DIR/lc}/phase"
    case "$_rb_s" in copying|shrinking|verifying) return 0 ;; esac
    # Only a partition mid-receive (#2067, Grok): fanout's -P counter holds a
    # byte and the receiver has not ended (drawers.sh clears both as each
    # partition starts). The wait for the next partition has its own ceiling
    # and message (waits.sh); a drawer draining after EOF is no outage.
    if [ ! -s "$RUN_DIR/bytes.raw" ] || [ -e "$RUN_DIR/source.rc" ]; then
        [ ! -e "$RUN_DIR/beep.rx" ] || rm -f "$RUN_DIR/beep.rx"; return 0
    fi
    for _rb_t in "$RUN_DIR"/targets/*; do
        _rb_s=; [ ! -r "$_rb_t/state" ] || read -r _rb_s < "$_rb_t/state"
        [ "$_rb_s" = writing ] && [ -r "${GUI_DIR:-$RUN_DIR/gui}/moved.${_rb_t##*/}" ] || continue
        _rb_m=; read -r _rb_m _rb_x < "${GUI_DIR:-$RUN_DIR/gui}/moved.${_rb_t##*/}"
        case "$_rb_m" in ''|*[!0-9]*) continue ;; esac
        [ "${_rb_latest:-0}" -ge "$_rb_m" ] || _rb_latest=$_rb_m
    done
    [ -n "$_rb_latest" ] || return 0
    _rb_now=$(date +%s); case "$_rb_now" in ''|*[!0-9]*) return 0 ;; esac
    # Never older than this receive: a drawer's last move may be the previous
    # partition's for a tick after the new bytes came (beep.rx = first sample).
    _rb_rx=; [ ! -r "$RUN_DIR/beep.rx" ] || read -r _rb_rx < "$RUN_DIR/beep.rx"
    case "$_rb_rx" in
        ''|*[!0-9]*) _rb_rx=0; if echo "$_rb_now" > "$RUN_DIR/beep.rx"; then _rb_rx=$_rb_now; else log 'beep: receive start not saved'; fi ;;
    esac
    [ "$_rb_latest" -ge "$_rb_rx" ] || _rb_latest=$_rb_rx
    _rb_age=$((_rb_now - _rb_latest))
}

stream_beep_tick() {
    # The kiosk's state loop, once a second (clonergui.sh). $1 = an explicit
    # age in seconds; none = sampled. The stream stood still NETLOST_AFTER_S
    # (30) = netlost, once an outage; it moves again = netback, once.
    # In-process and synchronous (one caller): only a tune forks.
    if [ "$#" -gt 0 ]; then _rb_age=$1; else _rb_stream_age; fi
    case "$_rb_age" in ''|*[!0-9]*) [ ! -e "$RUN_DIR/beep.net" ] || rm -f "$RUN_DIR/beep.net"; return 0 ;; esac
    _rb_limit=${NETLOST_AFTER_S:-30}; case "$_rb_limit" in ''|*[!0-9]*) _rb_limit=30 ;; esac
    _rb_net=up; [ ! -r "$RUN_DIR/beep.net" ] || read -r _rb_net < "$RUN_DIR/beep.net"
    if [ "$_rb_net" = lost ] && [ "$_rb_age" -lt "$_rb_limit" ]; then
        event_beep netback; echo up > "$RUN_DIR/beep.net" || log 'beep: stream state not saved'
    elif [ "$_rb_net" != lost ] && [ "$_rb_age" -ge "$_rb_limit" ]; then
        event_beep netlost; echo lost > "$RUN_DIR/beep.net" || log 'beep: stream state not saved'
    fi
    return 0
}
