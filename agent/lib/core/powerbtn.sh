# powerbtn.sh -- #1918 (Nadav 20/10): the machine's power button. POSIX sh.
#
# A short press did nothing: Linux turns the ACPI button into an input event
# (button.ko -> "Power Button", KEY_POWER) and nobody listened. Now the native
# GUI reads it with the keyboard (input.c) and leaves a request file next to
# its state file, as the local clone's screens do (lcgui.sh) -- the screen
# decides nothing:
#
#   $RUN_DIR/gui/power.press    the button was pressed
#   $RUN_DIR/gui/power.answer   nonce|stop or nonce|keep -- the dialog's answer
#
# and power_watch, a loop of the agent's own (it holds safe_poweroff -- the
# USB stick's too), decides:
#
# * nothing is writing: off, by the same road as "כבה את המחשב" (safe_poweroff:
#   sync, Wake-on-LAN where it can be armed, poweroff -f);
# * a write is running: $RUN_DIR/power.ask = nonce|kind|can_stop, which every
#   state file carries as power_ask= (guistate.sh gui_hello_state, srgui.sh),
#   and the screen asks. The write goes on until an answer. "עצור וכבה" stops
#   it by the stop that already exists for it -- the local clone's stop.req,
#   a round drawer's cancel (cancel.sh), the restore's cancel (srgui.sh), a
#   capture's cancel (the console's "ביטול הקליטה", POST tasks/<id>/cancel,
#   with the GUI's admin session -- Nadav 20/10) -- and the machine powers off
#   once nothing writes. A write that has no stop of its own (the read-back,
#   the source's restore after a shrink, a classroom's restore, a direct send,
#   a capture with no admin signed in on the GUI) is not stopped here:
#   can_stop=0, and the screen offers "המשך" only.
#
# A long press stays the firmware's: off at once, nothing here sees it.

POWER_TICK_S="${POWER_TICK_S:-1}"
#: from "עצור וכבה" to nothing writing; past it the machine stays on, aloud.
#: A stop during a shrink first puts the source back (lccopy.sh): minutes.
POWER_STOP_S="${POWER_STOP_S:-1800}"
POWER_ROUND_END_S="${POWER_ROUND_END_S:-60}"   # a stopped round's own end count, before off from here
POWER_WHO="מכפתור ההפעלה של המחשב"   # who asked, as cancel.sh says it

power_writing() {
    # stdout = kind|can_stop of the write running now; 1 = nothing writes.
    # A state file that is there and cannot be read is not "idle": the
    # question is asked (principle 5) -- never a power-off over a write.
    _pw_ph=$(cat "${LC:-$RUN_DIR/lc}/phase" 2>/dev/null)
    case "$_pw_ph" in
        copying|shrinking) echo "localclone|1"; return 0 ;;
        verifying|restoring) echo "localclone|0"; return 0 ;;
    esac
    # A cloner's drawers are a round (cancel.sh); elsewhere a target is a
    # restore, stoppable only on the build machine's restore screen (srgui.sh).
    _pw_k=restore; _pw_c=0
    if [ "${D_ROLE:-}" = cloner ]; then _pw_k=round; _pw_c=1
    elif [ -s "$RUN_DIR/gui/restore.head" ]; then _pw_c=1; fi
    _pw_v=""
    for _pw_f in "$RUN_DIR"/targets/*/state; do
        [ -f "$_pw_f" ] || continue                 # no match leaves the pattern itself
        _pw_s=$(cat "$_pw_f") || { echo "unknown|0"; return 0; }
        case "$_pw_s" in
            writing) echo "$_pw_k|$_pw_c"; return 0 ;;
            verifying) _pw_v="$_pw_k|0" ;;
        esac
    done
    [ -z "$_pw_v" ] || { echo "$_pw_v"; return 0; }
    if [ -f "$RUN_DIR/state" ]; then
        _pw_s=$(cat "$RUN_DIR/state") || { echo "unknown|0"; return 0; }
        case "$_pw_s" in
            capturing|waiting|sending)   # waiting: the direct send's, its source still shrunk (directsend.sh)
                if power_capture_task >/dev/null; then echo "capture|1"; else echo "capture|0"; fi; return 0 ;;
            writing|verifying|naming|staging) echo "$_pw_k|0"; return 0 ;;   # before or after the targets' own state
        esac
    fi
    return 1
}

power_capture_task() {
    # stdout = the capture task's id when the console's own cancel can be
    # asked from here: a capture (a direct send has another), and the GUI's
    # session of an admin -- what POST /api/console/tasks/<id>/cancel needs.
    [ "$(json_get "$RUN_DIR/response.json" .task.type)" = capture ] || return 1
    _pc_id=$(json_get "$RUN_DIR/response.json" .task.id)
    case "$_pc_id" in ''|null|*[!A-Za-z0-9_-]*) return 1 ;; esac
    [ -s "${GUI_DIR:-$RUN_DIR/gui}/console.jar" ] || return 1
    [ "$(json_get "$RUN_DIR/console_login.json" .role)" = admin ] || return 1
    echo "$_pc_id"
}

power_off_now() {
    # $1 = why (journal). safe_poweroff returns only when the machine stayed
    # on; the reason is said on every screen (power.fail, gui_hello_state).
    log "power button: $1 -- powering off"
    safe_poweroff "power button" && return 0
    [ -s "$RUN_DIR/power.fail" ] ||
        echo "המחשב לא כובה: Wake-on-LAN לא נדרך בכרטיס הרשת, ובלעדיו אי אפשר להדליק אותו מרחוק." > "$RUN_DIR/power.fail"
    log "power button: the machine stayed on (${POWEROFF_WHY:-unknown})"
    return 1
}

power_press() {
    # The button was pressed. A question already on the screen stays as it is.
    if ! _pp_w=$(power_writing); then
        rm -f "$RUN_DIR/power.ask"
        power_off_now "pressed, nothing is writing"; return $?
    fi
    [ -s "$RUN_DIR/power.ask" ] && return 0
    [ ! -f "$RUN_DIR/power.stop" ] || { log "power button: pressed again -- the stop is already under way"; return 0; }
    _pp_n=$(( $(cat "$RUN_DIR/power.seq" 2>/dev/null || echo 0) + 1 ))
    echo "$_pp_n" > "$RUN_DIR/power.seq"
    printf '%s|%s\n' "$_pp_n" "$_pp_w" > "$RUN_DIR/power.ask.next" &&
        mv "$RUN_DIR/power.ask.next" "$RUN_DIR/power.ask" || return 1
    log "power button: pressed during a ${_pp_w%%|*} -- asked on the screen; the write goes on"
}

power_answer() {
    # $1 = the answer file. Only for the question on the screen now (its nonce).
    IFS='|' read -r _pa_n _pa_a < "$1" || return 1
    IFS='|' read -r _pa_q _pa_k _pa_s < "$RUN_DIR/power.ask" 2>/dev/null ||
        { log "power button: an answer with no question on the screen -- dropped"; return 0; }
    [ -n "$_pa_n" ] && [ "$_pa_n" = "$_pa_q" ] || { log "power button: a stale answer ($_pa_n, the question is $_pa_q) -- dropped"; return 0; }
    case "$_pa_a" in
        keep) rm -f "$RUN_DIR/power.ask"; log "power button: continue -- the $_pa_k goes on" ;;
        stop)
            [ "$_pa_s" = 1 ] || { log "power button: 'stop' for a $_pa_k that has no stop -- refused"; return 1; }
            rm -f "$RUN_DIR/power.ask"
            # #1918 review: the stop names the write it is for (the restore's
            # run id, srgui.sh) -- a file left from another write stops nothing
            _pa_id=""; [ "$_pa_k" != restore ] || _pa_id=$(cat "$RUN_DIR/gui/restore.id" 2>/dev/null)
            echo "$(date +%s)|$_pa_k|$_pa_id" > "$RUN_DIR/power.stop"; _pt_quiet=""
            log "power button: stop and power off -- stopping the $_pa_k, then off"
            power_stop "$_pa_k" || rm -f "$RUN_DIR/power.stop" ;;
        *) log "power button: an answer this agent does not know ($_pa_a) -- dropped"; return 1 ;;
    esac
}

power_stop() {
    # $1 = kind. The stop that already exists for it, never a new one.
    case "$1" in
        localclone)   # the screen's own "עצור" (lcgui.sh lc-stop): copying or shrinking only
            _ps_lc=${LC:-$RUN_DIR/lc}
            case "$(cat "$_ps_lc/phase" 2>/dev/null)" in
                copying|shrinking) printf 'stop\n' > "$_ps_lc/stop.req.next" && mv "$_ps_lc/stop.req.next" "$_ps_lc/stop.req" ;;
            esac ;;
        round)        # "בטל דיסק זה" for every drawer still writing (cancel.sh)
            for _ps_f in "$RUN_DIR"/targets/*/state; do
                [ "$(cat "$_ps_f" 2>/dev/null)" = writing ] || continue
                _ps_d=${_ps_f%/state}; _ps_d=${_ps_d##*/}
                if cancel_request "$_ps_d" "$POWER_WHO"; then log "$_ps_d: cancel asked $POWER_WHO"; fi
            done ;;
        restore) : ;;  # srgui.sh: sr_cancel_watch reads power.stop as "ביטול השחזור", sr_end_poweroff as "כבה עכשיו"
        capture)      # the console's "ביטול הקליטה": the server ends the upload (409), the
                      # capture fails, the source is put back (_capture_failed), then off
            _ps_id=$(power_capture_task) || { log "power button: the capture cannot be cancelled from here"; return 1; }
            CONSOLE_JAR="${GUI_DIR:-$RUN_DIR/gui}/console.jar"
            printf '{}\n' > "$RUN_DIR/power.cancel.json"
            _ps_c=$(console_post "tasks/$_ps_id/cancel" "$RUN_DIR/power.cancel.json" "$RUN_DIR/power.cancel.out")
            [ "$_ps_c" = 200 ] && { log "power button: capture $_ps_id cancelled on the server"; return 0; }
            log "power button: the server did not cancel capture $_ps_id (http ${_ps_c:-000}) -- the capture goes on"
            echo "המחשב לא כובה: השרת לא ביטל את הקליטה (http ${_ps_c:-000}). הקליטה ממשיכה." > "$RUN_DIR/power.fail"
            return 1 ;;
    esac
}

power_stop_for_restore() {
    # $1 = this restore's run id (srgui.sh restore.id). 0 = "עצור וכבה" was
    # asked for THIS restore -- never for one before it in the same boot.
    [ -n "${1:-}" ] && [ -f "$RUN_DIR/power.stop" ] || return 1
    IFS='|' read -r _ps_at _ps_k _ps_id < "$RUN_DIR/power.stop" || return 1
    [ "$_ps_k" = restore ] && [ "$_ps_id" = "$1" ]
}

power_stop_wait() {
    # After "עצור וכבה": off once nothing writes; past POWER_STOP_S, on, aloud.
    # A restore is not powered off from here: its cancel reports and then
    # wipes the table after the write has ended, and its own end (srgui.sh
    # sr_end_poweroff) takes power.stop as "כבה עכשיו" -- or holds the
    # machine on when the wipe failed.
    IFS='|' read -r _pt_at _pt_k _pt_id < "$RUN_DIR/power.stop" || return 1
    if [ "$_pt_k" = restore ]; then   # its restore still runs: srgui.sh's to act on
        [ "$_pt_id" != "$(cat "$RUN_DIR/gui/restore.id" 2>/dev/null)" ] || [ -s "$RUN_DIR/gui/restore.final" ] || return 0
        rm -f "$RUN_DIR/power.stop"; log "power button: the stop was for a restore that is over ($_pt_id) -- dropped"; return 0
    fi
    if power_writing >/dev/null; then
        _pt_quiet=""
        [ $(( $(date +%s) - ${_pt_at:-0} )) -ge "$POWER_STOP_S" ] || return 0
        rm -f "$RUN_DIR/power.stop"
        log "power button: the $_pt_k did not stop within ${POWER_STOP_S}s -- not powering off"
        echo "המחשב לא כובה: הכתיבה לא נעצרה תוך $((POWER_STOP_S / 60)) דקות. הפרטים ביומן הסוכן." > "$RUN_DIR/power.fail"
        return 1
    fi
    # A round reports its end and counts to the power-off (idle.sh
    # end_countdown): its "כבה עכשיו" is taken, so the report is not cut.
    # No count within POWER_ROUND_END_S: off from here.
    if [ "$_pt_k" = round ]; then
        if [ -s "$RUN_DIR/end.poweroff" ]; then
            : > "$RUN_DIR/end.now"; rm -f "$RUN_DIR/power.stop"
            log "power button: the round stopped -- its end count powers off now"; return 0
        fi
        _pt_quiet=${_pt_quiet:-$(date +%s)}
        [ $(( $(date +%s) - _pt_quiet )) -ge "$POWER_ROUND_END_S" ] || return 0
    fi
    rm -f "$RUN_DIR/power.stop"    # once: off, or on and said why (power.fail)
    power_off_now "the $_pt_k stopped"
}

power_tick() {
    _pk_g=${GUI_DIR:-$RUN_DIR/gui}
    if mv "$_pk_g/power.press" "$RUN_DIR/power.press.taken" 2>/dev/null; then power_press; fi
    if mv "$_pk_g/power.answer" "$RUN_DIR/power.answer.taken" 2>/dev/null; then
        power_answer "$RUN_DIR/power.answer.taken"
    fi
    if [ -f "$RUN_DIR/power.stop" ]; then power_stop_wait; fi
    # the write ended by itself while the question was up: no question any more
    if [ -f "$RUN_DIR/power.ask" ] && ! power_writing >/dev/null; then
        rm -f "$RUN_DIR/power.ask"; log "power button: the write ended -- the question is gone"
    fi
    return 0
}

power_watch() {
    while :; do power_tick; sleep "$POWER_TICK_S"; done
}

power_watch_start() {
    # Once a boot, from the agent once its role is known (imagesrv-agent after
    # the first answer, usbclone.sh). A press from before it is not a question.
    [ -n "${_pwr_pid:-}" ] && kill -0 "$_pwr_pid" 2>/dev/null && return 0
    rm -f "${GUI_DIR:-$RUN_DIR/gui}/power.press" "${GUI_DIR:-$RUN_DIR/gui}/power.answer" \
        "$RUN_DIR/power.ask" "$RUN_DIR/power.stop"
    power_watch > /dev/null 2>&1 & _pwr_pid=$!
    log "power button: listening ($RUN_DIR/gui/power.press, from the GUI)"
}
