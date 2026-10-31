# task.sh -- do_task: the answer was a task (decide = task) -- a capture, or
# a direct send (#715). POSIX sh (busybox ash).
# פוצל מ-imagesrv-agent ב-#1217 לפני קיר 300 השורות, בלי שינוי התנהגות. הלולאה
# הראשית ושני מסלולי השחזור נשארו שם; המשתנים שהפונקציה קוראת (‏RESP, ‏SERVER,
# ‏MAC) נקבעים שם לפני הלולאה, והיא נקראת רק ממנה.

do_task() {
    _type=$(json_get "$RESP" ".task.type")
    _tid=$(json_get "$RESP" ".task.id")
    rm -f "$RUN_DIR/power.stop"   # #1918: a power button's stop is for the write it was asked in, never this one
    # ‏#530: האסימון מגיע בתשובת ה-hello, ורק למכונה שה-MAC שלה תואם,
    # וכל כתיבה על המשימה דורשת אותו בחזרה. **בלי `export`**: שני
    # אתרי השימוש ב-`capture.sh` הם הרחבת מעטפת בקובץ שנטען ב-`.`,
    # ותת-מעטפת יורשת משתנה גם בלי ייצוא. אין שם `sh -c`.
    TASK_TOKEN=$(json_get "$RESP" ".task.token")
    # ‏#1916 (ברזל 20/10): הדיווח הראשון יוצא לפני ש-capture_disk כותב משהו
    # משלו. בלי הניקוי הוא נשא את `failed` של הקליטה הקודמת, והשרת סגר את
    # החדשה תוך 3 שניות. לפני ה-case: גם הפצה ישירה (`done` ישן = סגירה כמוצלחת).
    job_reset
    case "$_type" in
        capture) ;;
        direct_send) direct_send_task "$_tid"; return $? ;;   # #715
        # A task type this build does not know is not a reason to guess.
        *) log "unknown task type '$_type' -- ignoring it"; sleep 5; return 0 ;;
    esac

    _disk=$(json_get "$RESP" ".task.disk")
    ui_clear; ui_header
    echo "  Capturing $(json_get "$RESP" ".task.name")"
    echo "  Source disk: /dev/$_disk"
    echo
    echo "  Reading used blocks only. Do not power off."

    progress_loop "" "$MAC" "$SERVER" "$_tid" &
    _ppid=$!
    # The shrink question is a GUI dialog only (#928; owner 11/10: no text
    # screens). A capture ordered from the web console can land before the
    # GUI was ever started -- start it now. No GUI = the question cannot be
    # asked, and shrink_ask cancels the capture (nothing shrinks without a yes).
    gui_parent || log "capture: native GUI unavailable -- a shrink question will cancel the capture"

    # Both roads out of here end at report_final (#127). `sleep 4; kill` was
    # the same gamble #101 took out of the round and the drawers, and on this
    # path it is the worst of the three: the next step is `poweroff -f` with
    # the drive about to be pulled out, so a capture the server never heard
    # about stays open for ever against a machine that is off.
    if capture_disk "$_tid" "$_disk" "$(json_get "$RESP" ".task.disk_serial")"; then   # #1901: the disk chosen, not its name
        if upload_manifest "$_tid" "$RUN_DIR/new-manifest.json"; then
            echo "done" > "$RUN_DIR/state"
            report_final "$_ppid" "" "$MAC" "$SERVER" "$_tid" \
                || { hold_unheard "" "$_tid"; return 1; }
            # ‏#409: "power off, or back to the menu?" -- a GUI dialog since 11/10.
            build_end_ask capture-end "הקליטה הושלמה: לכבות או לחזור לתפריט" "capture complete" \
                "Capture complete. The server is verifying the image before it enters the library."
            return 0
        fi
        log "manifest upload failed"
    fi
    echo "failed" > "$RUN_DIR/state"
    report_final "$_ppid" "" "$MAC" "$SERVER" "$_tid" \
        || { hold_unheard "" "$_tid" \
             "capture did not complete, and the server was not told"; return 1; }
    HOLD_YIELD_TASK="$_tid"   # #1575: the source was only read -- a new task ends the hold
    ui_error_hold "capture did not complete" hold_beat
    return 1
}

build_end_ask() {
    # $1 = kind (capture-end|send-end|round-end), $2 = the question for the
    # console (attended, #906), $3 = what completed (journal), $4 = the line
    # when the machine stays on. #1560 (Nadav 17/10): the build machine's
    # ends ask -- a capture (#409), a direct send, a round opened here --
    # in the GUI dialog (askgui.sh). No GUI, or no answer within
    # CAPTURE_END_ASK_S: power off -- the machine someone walked away from is
    # off, as before #409: 20 s (Nadav 17/10). Metal 17/10: "20" was fixed
    # text, and read as "it powers off by itself" -- the subject is now the
    # epoch of the power-off, and the dialog counts down to it each second.
    # Returns 0 on "back to the menu", or after a power-off that did not
    # take; a power-off that took never returns.
    _be_s=${CAPTURE_END_ASK_S:-20}
    case "$(attended "$2" gui_ask_choice "$1" "$(( $(date +%s) + _be_s ))" "" "$_be_s")" in
        menu) log "$3 -- back to the menu (operator's choice)"; _build_standby=0; return 0 ;;
        poweroff) log "$3 -- powering off (operator's choice)" ;;
        *) log "$3 -- powering off" ;;
    esac
    [ "${IMAGESRV_TEST:-0}" = "1" ] && exit 0
    finish_and_stop
    # #1222: finish_and_stop returns only when the machine stayed on (#1483: WoL, or
    # poweroff, #587). The task is done and reported; this is not the failure path.
    task_done_stays_on "$4"
    return 0
}

build_round_end() {
    # #1560: the kiosk saw a round opened from this GUI end (roomflow.sh
    # room_end_watch). Asked once, from the build menu's loop.
    [ -f "$RUN_DIR/room.ended" ] || return 0
    rm -f "$RUN_DIR/room.ended"
    build_end_ask round-end "הסבב הסתיים: לכבות או לחזור לתפריט" "room round complete" \
        "The round is complete."
}

task_done_stays_on() {
    # $1 = what completed. A finished task on a machine that could not be
    # armed for Wake-on-LAN: say so on the screen and go back to the loop.
    # _build_standby keeps this screen up: build_console_screen does not boot
    # the machine to its local disk for want of a GUI after a finished task.
    # #1483: the reason safe_poweroff gave. Not always Wake-on-LAN: a poweroff
    # that came back (HP-BUILD 16/10) was logged "wol not armed" a line after
    # "wol: eth0 armed" -- two contradicting lines, and the real one unsaid.
    if [ "${POWEROFF_WHY:-}" = poweroff ]; then
        log "task complete -- the power-off did not take, staying powered on"
        _td_why="The power-off command returned and this computer is still on: power it off by hand."
    else
        log "task complete -- wol not armed, staying powered on"
        _td_why="Wake-on-LAN could not be armed, so this computer stays on."
    fi
    ui_clear; ui_header
    echo "  $1"
    echo "  $_td_why"
    _build_standby=1
}
