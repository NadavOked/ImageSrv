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

UNKNOWN_POLL_S="${UNKNOWN_POLL_S:-1}"   # #1802: how often the screen's F2 is looked for

unknown_autoboot_s() {
    # The console's "מחשב לא רשום: עלייה אוטומטית מהדיסק" (the hello answer's
    # unregistered_autoboot_seconds, 0-600; 0 = off, the default). UEFI ONLY
    # (Nadav 04/11): on the deploy network a BIOS machine is a cloner, and its
    # disk is a drawer of payload (#323, #345) -- never an automatic boot there.
    # Missing (an old server) or bad = 0 and a log line: doubt does not boot a disk.
    _ua=$(json_get "${RESP:-/nonexistent}" ".unregistered_autoboot_seconds" 2>/dev/null)
    case "$_ua" in
        0|[1-9]|[1-9][0-9]|[1-5][0-9][0-9]|600) ;;
        *) log "unknown: unregistered_autoboot_seconds '${_ua:-unread}' is not 0-600 -- off" >&2; echo 0; return ;;
    esac
    if [ "$_ua" -gt 0 ] && [ "$(detect_firmware)" != uefi ]; then
        log "unknown: Legacy BIOS -- never an automatic boot (#345); the setting ${_ua}s is for UEFI" >&2
        _ua=0
    fi
    echo "$_ua"
}

ui_unknown_records() {
    # $1 = mac, $2 = seconds to the automatic boot (0 = none). The mockup's
    # case 8 (failure-screen.html, "unknown"): not a failure -- tone inf, no
    # hold. #1802 (Nadav 04/11): it stays; its one button is F2 = restart
    # (fail_btn reboot), and fail_at only when the setting counts down (UEFI).
    # The MAC in capitals: the GUI draws a value of capitals and digits mono, LTR.
    _uu_mac=$(printf '%s' "$1" | tr 'a-f' 'A-F')
    printf 'fail=inf|0|המחשב הזה לא רשום|הקונסולה קיבלה התראה על מחשב חדש|\n'
    if [ "$2" -gt 0 ]; then
        printf 'fail_why=השרת לא מכיר את המחשב הזה, ולכן אין לו משימה. הוא יעלה כרגיל מהדיסק המקומי.\n'
        printf 'fail_step=אין צורך לעשות דבר — האתחול ימשיך בעוד %s שניות.\n' "$2"
    else
        printf 'fail_why=השרת לא מכיר את המחשב הזה, ולכן אין לו משימה.\n'
    fi
    printf 'fail_step=כדי לצרף את המחשב לשרת: רשמו אותו בקונסולה לפי כתובת ה-MAC.\n'
    printf 'fail_btn=reboot|F2 — הפעלה מחדש\n'
    printf 'fail_ctx=כתובת MAC|%s\nfail_ctx=כתובת IP|%s\nfail_ctx=מצב|לא רשום בשרת\n' "$_uu_mac" "${IP:-?}"
    [ "$2" -gt 0 ] && printf 'fail_ctx=אתחול|מהדיסק המקומי\n'
    printf 'fail_code=[agent] decide=unknown mac=%s\n' "$1"
    printf 'fail_code=[agent] ui_unknown: MAC not registered\n'
    printf 'fail_code=mac %s · agent %s\n' "$1" "${AGENT_VERSION:-unknown}"
    [ "$2" -gt 0 ] && printf 'fail_at=%s\n' "$(( $(date +%s) + $2 ))"
    return 0
}

ui_unknown() {
    # $1 = mac. #1802: an unregistered machine on the deploy network reaches
    # the agent only to be told so. Nadav 04/11: it stays on the screen. F2
    # (the GUI's fail.action "reboot") only restarts it: the firmware boots
    # from the network again and the machine is back on this screen -- that is
    # the intent; we do not own the boot order and write no firmware (no
    # BootNext). The one way to the disk is the console's setting, UEFI only
    # (unknown_autoboot_s): after N s a reboot, and the server, which served
    # this screen inside its window, answers UEFI with the local disk
    # (server/agent/unknown_screen.py). Not die_local: this is not a failure.
    # Returns only if reboot failed.
    _uu_auto=$(unknown_autoboot_s); _uu_end=0
    [ "$_uu_auto" -gt 0 ] && _uu_end=$(( $(date +%s) + _uu_auto ))
    _uu_act="${GUI_DIR:-$RUN_DIR/gui}/fail.action"
    rm -f "$_uu_act"
    if command -v fail_show >/dev/null 2>&1 && mkdir -p "$RUN_DIR/gui" &&
        ui_unknown_records "$1" "$_uu_auto" > "$RUN_DIR/fail.state.next" &&
        mv "$RUN_DIR/fail.state.next" "$RUN_DIR/fail.state"; then
        fail_show || :   # no GUI binary: the tty lines are the screen
    fi
    ui_clear
    ui_header
    echo "  This computer is not registered ($1)."
    echo "  The console has been notified."
    echo
    [ "$_uu_auto" -gt 0 ] && echo "  Booting from the local disk in ${_uu_auto}s."
    log "unknown: $1 is not registered -- on the screen; F2 restarts (automatic boot: ${_uu_auto}s, 0 = off)"
    while :; do
        _uu_why=""
        if mv "$_uu_act" "$RUN_DIR/fail.action.taken" 2>/dev/null; then
            case "$(head -n 1 "$RUN_DIR/fail.action.taken" | tr -d '\r')" in
                reboot) _uu_why="F2" ;;
                *) log "unknown: '$(head -n 1 "$RUN_DIR/fail.action.taken")' is not this screen's" ;;
            esac
        fi
        [ -z "$_uu_why" ] && [ "$_uu_end" -gt 0 ] && [ "$(date +%s)" -ge "$_uu_end" ] && _uu_why="${_uu_auto}s"
        if [ -n "$_uu_why" ]; then
            log "unknown: $_uu_why -- restarting; the server decides where the next boot goes"
            if [ "${IMAGESRV_TEST:-0}" = "1" ]; then
                echo "TEST-REBOOT: MAC not registered ($_uu_why)"
                exit 86
            fi
            sync
            reboot -f
            return 1
        fi
        sleep "$UNKNOWN_POLL_S"
    done
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
