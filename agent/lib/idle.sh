# idle.sh -- idle power-off for a machine that waits for nobody (#434). POSIX sh.
#
# Three states that looked the same from the outside:
#   waiting for a person  -> this clock runs   -> power off
#   working               -> poll_sleep is never called: no clock
#   failed, message shown -> ui_error_hold never returns: no clock
# The clock is one file, the epoch of the last activity; poll_sleep asks
# idle_check once per beat. Activity: a changed hello answer, an open round
# for this group (a wave about to start is not idle), a key press in the
# native GUI (the kiosk writes idle.touch), or the stay_on file. Never on a
# failure screen -- the hold loops never reach poll_sleep, by construction.
# IDLE_POWEROFF_S=0 disables; the warning shows IDLE_WARN_S seconds first.

IDLE_POWEROFF_S="${IDLE_POWEROFF_S:-300}"
IDLE_WARN_S="${IDLE_WARN_S:-30}"

idle_reset() { date +%s > "$RUN_DIR/idle.since"; rm -f "$RUN_DIR/idle.warn"; }

idle_touched() {
    # The kiosk touches idle.touch on a key press; not older than the clock =
    # activity (same second counts -- the safe direction is staying on).
    # `find -newer` and not `-nt`: -nt is undefined in POSIX sh (SC3013).
    # find prints idle.since only when it is strictly newer than the touch;
    # a find that fails prints nothing = "touched" = stay on, the safe side.
    [ -f "$RUN_DIR/idle.touch" ] || return 1
    [ -z "$(find "$RUN_DIR/idle.since" -newer "$RUN_DIR/idle.touch" 2>/dev/null)" ]
}

idle_exempt() {
    [ "$IDLE_POWEROFF_S" -eq 0 ] 2>/dev/null && return 0
    [ -f "$RUN_DIR/stay_on" ] && return 0            # a screen that must stay lit
    [ "${D_SESSION_STATE:-}" = "open" ] && return 0  # a wave is about to start
    [ "${D_TASK:-null}" != "null" ] && return 0       # work was just handed to us
    idle_round_shown && return 0                     # #1560: a round this machine opened or shows
    return 1
}

idle_round_shown() {
    # #1560 (metal 17/10, twice): the build machine powered off mid-round,
    # 91% on its screen -- only keys and the mouse counted. Not idle: a round
    # opened from this GUI that is still active (roomflow.sh room.watch), or
    # an active round / class session the kiosk is drawing now -- its read
    # newer than the clock (it rewrites it every beat; a kiosk that died
    # stops, and the clock runs again). A read that fails is no round.
    [ -f "$RUN_DIR/room.watch" ] && return 0
    _ir_g="${GUI_DIR:-$RUN_DIR/gui}"
    case "$(cat "$_ir_g/mode" 2>/dev/null)" in
        room|direct) _ir_f="$_ir_g/room.json"; _ir_q='.round.state == "active"' ;;
        classes) _ir_f="$_ir_g/session-shown.json"; _ir_q='.session.state == "open" or .session.state == "running"' ;;
        *) return 1 ;;
    esac
    [ -n "$(find "$_ir_f" -newer "$RUN_DIR/idle.since" 2>/dev/null)" ] &&
        jq -e "$_ir_q" "$_ir_f" >/dev/null 2>&1
}

idle_poweroff() {
    safe_poweroff "idle: no activity for ${IDLE_POWEROFF_S}s" || { idle_reset; return 1; }
}

power_check() {
    # #980: the console's "power off the room" rides the hello answer
    # (power_action) at the same rate -- no new polling. Same rule as the
    # idle power-off: only after the wake-on-LAN read-back (#587), or the room
    # could not be woken again. The server hands it out once.
    # #977 section 4: "reboot" from the machine drawer, same channel. No
    # arm_wol: a rebooted machine comes back by itself.
    case "$(json_get "$RESP" ".power_action")" in
        poweroff) ;;
        reboot) log "power: the console asked to reboot -- rebooting"; sync; reboot -f; return ;;
        *) return 0 ;;
    esac
    safe_poweroff "power: the console asked to power off"
}

idle_check() {
    power_check || return 1
    [ -f "$RUN_DIR/idle.since" ] || { idle_reset; return 0; }
    if idle_exempt || idle_touched || [ "${POLL_MARK_CHANGED:-0}" = 1 ]; then idle_reset; return 0; fi
    _now=$(date +%s); _since=$(cat "$RUN_DIR/idle.since" 2>/dev/null || echo "$_now")
    _idle=$((_now - _since))
    [ "$_idle" -ge "$IDLE_POWEROFF_S" ] || return 0
    # the warning: the GUI reads idle.warn (guistate.sh -> idle_poweroff_at=)
    # and any key press cancels it through idle.touch
    _at=$((_now + IDLE_WARN_S)); rm -f "$RUN_DIR/idle.now"; printf '%s|%s\n' "$_at" "$IDLE_POWEROFF_S" > "$RUN_DIR/idle.warn"   # mockup 10: the window says how long
    log "idle: ${_idle}s without activity -- power-off in ${IDLE_WARN_S}s unless a key is pressed"
    _n=0
    while [ "$_n" -lt "$IDLE_WARN_S" ]; do
        sleep 1; _n=$((_n + 1))
        if [ -f "$RUN_DIR/idle.now" ]; then rm -f "$RUN_DIR/idle.now"; log "idle: 'כבה עכשיו' pressed"; break; fi   # mockup 10: Enter
        if idle_touched || [ -f "$RUN_DIR/stay_on" ]; then log "idle: cancelled by activity"; idle_reset; return 0; fi
    done
    idle_poweroff
}

# --- the end of a task (Nadav, 17/10, #1560; 22/10, #2024) ---------------------
# A cloner after any of its tasks -- local clone, a round, restore from the
# server, a direct send it received: no question. Every target clean
# (end_problems): the machine powers off in END_POWEROFF_S, or at once on the
# GUI's "כבה עכשיו"; the power-off is finish_and_stop -> safe_poweroff (WoL
# armed first, #587). The build machine's ends ask instead (task.sh).
# #2024: a target with a problem -- red, orange, or a result that cannot be
# read (principle 5) -- holds the results on the screen, no count and no
# ceiling (it replaces END_HOLD_S), until "כבה" (end.now), the local result
# screen's own button (END_ABORT_FILE), or the console's power-off when a beat
# runs (the local clone's: attended_keep). The price, accepted by Nadav: a
# cloner left on does not join the next round by itself. No hello, as the count
# had none: a hello would make it "seen" to a new wave, and the server's re-wake
# would then neither wake nor fail the round by name (#1721). #2031: a light
# "held" report instead (hold.sh held_beat_start) -- the console's "דלוק — מחכה
# ללחיצה" and its "כבה", which comes back in that report's answer.
#   $RUN_DIR/end.poweroff  <epoch>|<kind>|<done>|<targets>|<held 0/1>; every GUI
#                          screen draws it (gui_hello_state). held 1 = epoch 0, no limit
#   $RUN_DIR/end.problems  <disk>|<r|o>|<reason> a problem target (red/orange), drawn highlighted
#   $RUN_DIR/end.now       the button: the bridge writes it on end-poweroff
#   $RUN_DIR/end.hold      an older screen's "stop the count" (end-hold): the hold, the same
END_POWEROFF_S="${END_POWEROFF_S:-20}"

end_tally() {
    # $@ = target disks. _et_ok = how many ended "done", _et_n = how many.
    _et_ok=0; _et_n=0
    for _et_d in "$@"; do
        _et_n=$((_et_n + 1))
        [ "$(cat "$RUN_DIR/targets/$_et_d/state" 2>/dev/null)" != "done" ] || _et_ok=$((_et_ok + 1))
    done
}

_end_why() { _ep_r="${_ep_r:+$_ep_r · }$2"; [ "$1" = o ] || _ep_c=r; }
end_problem() {
    # $1 = a target disk. Prints "ok" -- a clean done, read -- or "<r|o>|<reason>".
    # Clean = done, no warning (error), read back verified|sampled (#1805), not a
    # slow write check (#1366), drivers not failed (#1331). A file that is there
    # and cannot be read is not "no warning".
    _ep_t="$RUN_DIR/targets/$1"; _ep_r=; _ep_c=o
    _ep_s=$(cat "$_ep_t/state" 2>/dev/null) || _ep_s=
    _ep_e=; [ ! -e "$_ep_t/error" ] || _ep_e=$(cat "$_ep_t/error" 2>/dev/null) || _ep_e="תוצאת הדיסק לא נקראה"
    case "$_ep_s" in
        done) ;;
        '') _end_why r "מצב הדיסק לא נקרא — לא ידוע אם נכתב" ;;
        skipped) _end_why r "${_ep_e:-הדיסק לא נכתב}" ;;
        *) _end_why r "${_ep_e:-הכתיבה לא הסתיימה ($_ep_s)}" ;;
    esac
    if [ "$_ep_s" = "done" ]; then
        [ -z "$_ep_e" ] || _end_why o "$_ep_e"
        case "$(cat "$_ep_t/readback" 2>/dev/null)" in
            verified|sampled) ;;
            unverified) _ep_x=$(cat "$_ep_t/readback_reason" 2>/dev/null); _end_why o "${_ep_x:-מה שנכתב לא נבדק מול הדיסק}" ;;
            *) _end_why o "האימות אחרי הכתיבה לא נרשם — לא ידוע אם מה שנכתב תקין" ;;
        esac
    fi
    if [ "$(cat "$_ep_t/write_check_state" 2>/dev/null)" = slow ]; then
        _ep_x=$(cat "$_ep_t/write_check_reason" 2>/dev/null); _end_why r "${_ep_x:-הדיסק כותב לאט}"
    fi
    if [ -e "$_ep_t/drivers.json" ]; then
        _ep_v=$(jq -r '[(.state // ""), (.boot.state // ""), (.linux.state // "")] | join("|")' "$_ep_t/drivers.json" 2>/dev/null) || _ep_v="?||"
        case "$_ep_v" in "?||") _end_why o "תוצאת הדרייברים לא נקראה" ;; esac
        case "$_ep_v" in *"|failed|"*) _end_why r "דרייבר האחסון לא הוגדר לעלייה — הדיסק עלול לא לעלות" ;; esac
        case "$_ep_v" in failed\|*) _end_why o "דרייברים לא הונחו — המחשב יעלה בלי הדרייברים של הדגם" ;; esac
        case "$_ep_v" in *\|hostonly|*\|unsupported|*\|failed) _end_why o "לינוקס: לא ידוע אם הדיסק יעלה על בקר אחר" ;; esac
    fi
    if [ -z "$_ep_r" ]; then echo ok; else printf '%s|%s\n' "$_ep_c" "$_ep_r"; fi
}

end_problems() {
    # $@ = the run's targets. Writes end.problems; 0 = a problem, or no target
    # named at all (an end with nothing read is no evidence). A write that fails holds.
    {
        [ "$#" -gt 0 ] || echo "|r|לא נמצאו יעדים לבדיקה — לא ידוע מה נכתב"
        for _ep_d in "$@"; do
            _ep_l=$(end_problem "$_ep_d" | tr '\r\n' '  '); _ep_l=${_ep_l% }
            case "$_ep_l" in   # a | inside a reason would cut the GUI's record: it becomes /
                ok) ;;
                [ro]\|?*) printf '%s|%s|%s\n' "$_ep_d" "${_ep_l%%|*}" "$(printf '%s' "${_ep_l#?|}" | tr '|' '/')" ;;
                *) printf '%s|r|תוצאת הדיסק לא נקראה\n' "$_ep_d" ;;
            esac
        done
    } > "$RUN_DIR/end.problems" || return 0
    [ -s "$RUN_DIR/end.problems" ]
}

end_record() {
    # $1 = kind, $2/$3 = done/targets. Counting: the epoch of the power-off; held: 0.
    _er_at=0; [ "$_ec_held" = 1 ] || _er_at=$(( $(date +%s) + END_POWEROFF_S ))
    printf '%s|%s|%s|%s|%s\n' "$_er_at" "$1" "$2" "$3" "$_ec_held" > "$RUN_DIR/end.poweroff"
}

end_clear() {
    if command -v held_beat_stop >/dev/null 2>&1; then held_beat_stop; fi
    rm -f "$RUN_DIR/end.poweroff" "$RUN_DIR/end.now" "$RUN_DIR/end.hold" "$RUN_DIR/end.problems"
}

end_countdown() {
    # $1 = kind (drawers|partial|localclone|failed), $2 = what ended (journal),
    # $3/$4 = targets written / targets ("" = not said), $5.. = the target disks,
    # whose results decide (#2024; none = a problem). Returns 1 when the
    # machine stayed on (the screen says why), 2 when END_ABORT_FILE came.
    # No kiosk: it runs all the same.
    _ec_k=$1; _ec_w=$2; _ec_d=${3:-}; _ec_t=${4:-}
    if [ "$#" -ge 4 ]; then shift 4; else set --; fi
    rm -f "$RUN_DIR/end.now" "$RUN_DIR/end.hold"
    _ec_held=0; case "$_ec_k" in partial|failed) _ec_held=1 ;; esac   # the caller's own count says so
    end_problems "$@" && _ec_held=1
    end_record "$_ec_k" "$_ec_d" "$_ec_t"
    if [ "$_ec_held" = 1 ]; then
        log "$_ec_w -- a disk has a problem: the results stay on the screen until 'כבה', no count (#2024): $(tr '\n' ';' < "$RUN_DIR/end.problems" 2>/dev/null)"
    else log "$_ec_w -- powering off in ${END_POWEROFF_S}s, or at once on 'כבה עכשיו'"; fi
    _ec_n=0   # counted like idle_check's warning; the screen counts to the epoch
    while [ "$_ec_held" = 1 ] || [ "$_ec_n" -lt "$END_POWEROFF_S" ]; do
        if [ -f "$RUN_DIR/end.now" ]; then log "$_ec_w -- 'כבה עכשיו' pressed"; break; fi
        # a request of the screen's own (END_ABORT_FILE, lcwait.sh: the local clone's
        # end.req) is the person's: the count gives way to it, its caller acts on it
        if [ -n "${END_ABORT_FILE:-}" ] && [ -s "$END_ABORT_FILE" ]; then
            log "$_ec_w -- the screen's own request came: the count stops"
            end_clear; return 2
        fi
        if [ "$_ec_held" = 0 ] && [ -f "$RUN_DIR/end.hold" ]; then
            _ec_held=1; end_record "$_ec_k" "$_ec_d" "$_ec_t"; log "$_ec_w -- the count was stopped: the results stay until 'כבה'"
        fi
        # held: said to the console (#2031, hold.sh); its power-off or reboot, kept by that beat or a running one (attended_keep)
        if [ "$_ec_held" = 1 ]; then
            if command -v held_beat_start >/dev/null 2>&1; then held_beat_start; fi
            if command -v attended_power_check >/dev/null 2>&1; then attended_power_check; fi
        fi
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        sleep 1; _ec_n=$((_ec_n + 1))
    done
    end_clear
    finish_and_stop && return 0
    # safe_poweroff refused (#1483): the countdown is gone and the reason stays
    # on every screen (power.fail, gui_hello_state) -- a WoL refusal too.
    [ "${POWEROFF_WHY:-}" != wol ] ||
        echo "המחשב לא כובה: Wake-on-LAN לא נדרך בכרטיס הרשת, ובלעדיו אי אפשר להדליק אותו מרחוק. כבו אותו בכפתור ההפעלה." > "$RUN_DIR/power.fail"
    log "$_ec_w -- the machine stayed on (${POWEROFF_WHY:-unknown})"
    return 1
}

end_failed_round() {
    # $1 = the line, $2 = session id, $3 = the reporter's pid. A cloner round
    # (a room round, "restore from the server" (lcpull.sh), a direct send it
    # received) where no drawer completed. Metal 17/10: cloner 2 stayed on after such a failure; its
    # hold beat does not join a wave and WoL wakes nothing that is on, so the
    # next round went on without it. Now the failure is reported -- the
    # console keeps it -- and shown. #2024 (Nadav 22/10): a failed round is a
    # problem, so the results stay until "כבה" -- no count. Metal 17/10
    # (room_72308383, link down mid-stream): an unconfirmed report was
    # hold_unheard, which never returns. The report goes on behind the
    # results; a wave that wrote nothing fails the round on the server.
    if ! report_final "$3" "$2" "$MAC" "$SERVER"; then
        log "the failure report was not confirmed -- reporting on while the results are on the screen"
        progress_loop "$2" "$MAC" "$SERVER" &
    fi
    agent_log_send "$2"   # #1882: why it failed, kept on the server (best effort)
    # shellcheck disable=SC2086 # the disk list is a word list on purpose; #2024: _all = the drawers skipped red too, each with its reason
    end_tally ${_all:-$_disks}
    # shellcheck disable=SC2086
    end_countdown failed "$1" "$_et_ok" "$_et_n" ${_all:-$_disks} || ui_error_hold "$1" hold_beat
    return 1
}
