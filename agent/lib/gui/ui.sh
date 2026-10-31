# ui.sh -- the classroom screens. Plain text on the console: the Linux
# console cannot render RTL text, so agent screens are English by design
# (image names from the manifest are shown as-is).
# POSIX sh (busybox ash).

#: #1946: a native GUI whose heartbeat ($RUN_DIR/gui/beat, main.c) is older than this
#: is stopped or wedged -- killed and started again. 90, not 30: the GUI's login
#: waits for console_signin (curl --max-time 10 --retry 3, up to ~47 s) in its loop.
GUI_BEAT_MAX_S="${GUI_BEAT_MAX_S:-90}"

gui_beat_fresh() {
    # Here and not in guiparent.sh: both keepers read it (gui_keep, srgui.sh sr_gui_open).
    # 0 = beat within GUI_BEAT_MAX_S; 1 = older, or no beat file (the keepers
    # create it at each start: the start's grace, the first paint takes over).
    _gb_m=$(stat -c %Y "$RUN_DIR/gui/beat" 2>/dev/null) || return 1
    [ $(( $(date +%s) - _gb_m )) -le "$GUI_BEAT_MAX_S" ]
}

ui_clear() {
    [ "${IMAGESRV_TEST:-0}" = "1" ] || clear 2>/dev/null || printf '\033c'
}

ui_header() {
    echo "==============================================="
    echo "  ImageSrv"
    echo "==============================================="
    echo
}

ui_waiting_draw() {
    # $1 prefix, $2 joined, $3 expected, $4 starts_in_seconds
    ui_clear
    ui_header
    echo "  A deployment round is open: $1"
    echo
    echo "  Machines joined:  $2 / $3"
    if [ -n "$4" ] && [ "$4" != "null" ]; then
        echo "  Starting in:      ${4}s (or when everyone joins)"
    fi
    echo
    echo "  This machine is registered. Nothing to do -- do not"
    echo "  turn the computer off."
}

UNKNOWN_SHOW_S="${UNKNOWN_SHOW_S:-10}"   # #1802: how long "not registered" stays up

ui_unknown_records() {
    # $1 = mac. The mockup's case 8 (failure-screen.html, "unknown"): not a
    # failure -- tone inf, no hold, the reboot counted down (fail_at). The MAC
    # in capitals: the GUI draws a value of capitals and digits mono, LTR.
    _uu_mac=$(printf '%s' "$1" | tr 'a-f' 'A-F')
    printf 'fail=inf|0|המחשב הזה לא רשום|הקונסולה קיבלה התראה על מחשב חדש|\n'
    printf 'fail_why=השרת לא מכיר את המחשב הזה, ולכן אין לו משימה. הוא יעלה כרגיל מהדיסק המקומי.\n'
    printf 'fail_step=אין צורך לעשות דבר — האתחול ימשיך בעוד %s שניות.\n' "$UNKNOWN_SHOW_S"
    printf 'fail_step=כדי לצרף את המחשב לשרת: רשמו אותו בקונסולה לפי כתובת ה-MAC.\n'
    printf 'fail_ctx=כתובת MAC|%s\nfail_ctx=כתובת IP|%s\n' "$_uu_mac" "${IP:-?}"
    printf 'fail_ctx=מצב|לא רשום בשרת\nfail_ctx=אתחול|מהדיסק המקומי\n'
    printf 'fail_code=[agent] decide=unknown mac=%s\n' "$1"
    printf 'fail_code=[agent] ui_unknown: MAC not registered\n'
    printf 'fail_code=mac %s · agent %s\n' "$1" "${AGENT_VERSION:-unknown}"
    printf 'fail_at=%s\n' "$(( $(date +%s) + UNKNOWN_SHOW_S ))"
}

ui_unknown() {
    # $1 = mac. #1802 (owner 19/10): an unregistered machine on the deploy
    # network reaches the agent only to be told so -- the screen, then the
    # local disk. Not die_local: this is not a failure, and its red screen
    # would replace this one. The reboot lands on the disk because the server
    # served this screen once (server/unknown_screen.py) and the next menu for
    # this MAC is the local disk. Nothing here writes the firmware of a
    # machine we do not know (no BootNext). Returns only if reboot failed.
    case "$UNKNOWN_SHOW_S" in ''|*[!0-9]*) UNKNOWN_SHOW_S=10 ;; esac
    [ "$UNKNOWN_SHOW_S" -le 60 ] || UNKNOWN_SHOW_S=10   # a cap: never held here
    if command -v fail_show >/dev/null 2>&1 && mkdir -p "$RUN_DIR/gui" &&
        ui_unknown_records "$1" > "$RUN_DIR/fail.state.next" &&
        mv "$RUN_DIR/fail.state.next" "$RUN_DIR/fail.state"; then
        fail_show || :   # no GUI binary: the tty lines below are the screen
    fi
    ui_clear
    ui_header
    echo "  This computer is not registered ($1)."
    echo "  The console has been notified."
    echo
    echo "  Booting from the local disk in ${UNKNOWN_SHOW_S}s."
    log "unknown: $1 is not registered -- ${UNKNOWN_SHOW_S}s on screen, then the local disk"
    sleep "$UNKNOWN_SHOW_S"
    if [ "${IMAGESRV_TEST:-0}" = "1" ]; then
        echo "TEST-REBOOT: MAC not registered"
        exit 86
    fi
    sync
    reboot -f
}

ui_error_hold() {
    # $1 = message. $2 = optional heartbeat command.
    #
    # A failed write leaves a broken disk -- do not reboot into it. Stay
    # powered so the console sees 'failed' and IT can act.
    #
    # "Stay powered" only means something if the server keeps hearing us:
    # `last_seen` is written by hello alone, so a silent hold made the
    # console draw the machine as "off" while it sat lit with an error on
    # its screen (#64). The heartbeat is a NON-joining hello -- it refreshes
    # `last_seen` without volunteering the machine for the next wave.
    #
    # The loop itself is hold_watch in hold.sh: this screen was drawn once and
    # then ran the heartbeat as `"$2" > /dev/null 2>&1 || true`, which threw
    # away the very evidence the heartbeat exists to produce (#109).
    HOLD_PROMPT="FAILED: $1"   # #906: rides on every hold beat (hold.sh)
    # #1569 (owner 17/10): the same line as one Hebrew screen on the GUI
    # (failscreen.sh); the tty text below stays behind it, for the journal.
    command -v fail_screen >/dev/null 2>&1 && fail_screen hold "$1" || :
    ui_clear
    ui_header
    echo "  FAILED: $1"
    echo
    echo "  Leave the computer on and contact IT."
    if [ "${IMAGESRV_TEST:-0}" = "1" ]; then
        echo "TEST-HOLD"
        return
    fi
    hold_watch "$2"
}
