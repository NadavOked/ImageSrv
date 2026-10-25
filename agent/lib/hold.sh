# hold.sh -- what a machine that has stopped does while it waits for a person.
#
# A hold is not a pause before another attempt. The machine has stopped, and it
# will do nothing more (#64): it does not reboot, it does not retry the write,
# it does not power off. The one thing left for it to do is to keep saying that
# it is here -- `last_seen` on the server is written by hello and by nothing
# else, so a hold that goes quiet is drawn on the console as a machine that is
# OFF. That is the worst sentence this system can say to a technician: "off"
# sends him after a power cable, while the machine stands lit with the error
# message he actually came to read.
#
# The screens live in ui.sh; what the machine keeps *saying* lives here, so the
# two can be read apart. Both directions of the pairing are runtime calls, and
# neither file needs the other loaded first.
#
# POSIX sh (busybox ash). No bashisms.

HOLD_BEAT_S="${HOLD_BEAT_S:-10}"

hold_beat() {
    # The heartbeat of a machine stopped on an error: it says "I am here"
    # without asking to join a wave. The *answer* is thrown away -- this
    # machine does not act on anything any more -- but the exit code is not.
    # It is the only evidence that anyone heard, and it is what hold_watch
    # below is built to read.
    #
    # #906: the beat also carries the FAILED line (HOLD_PROMPT, set by
    # ui_error_hold) as `prompt`, so the console names what the machine is
    # waiting on instead of only that it is alive. attended.sh builds it.
    attended_hello "${HOLD_PROMPT:-}"
}

hold_unheard() {
    # $1 = session id (empty for a capture task), $2 = task id (optional),
    # $3 = the line for the screen (optional).
    #
    # The work ended and the server never said it heard. Rule 1 sends every
    # *unclear* state to the local disk; this one is not unclear -- we know the
    # work ended and we know the server does not, and a boot from here is
    # answered by GRUB with the same running session and the same restore, to
    # the boot guard's budget (#75). A capture is worse still: the next step
    # there is `poweroff -f` with the drive about to be pulled, and a task left
    # open against a machine that is off is a state no operator can read (#127).
    #
    # So it holds, named, with the reporter back up to keep saying the closing
    # word if the server returns, and with the heartbeat so the console keeps
    # drawing the machine as awake while it says it.
    progress_loop "$1" "$MAC" "$SERVER" "${2:-}" &
    ui_error_hold \
        "${3:-the work finished but the server never confirmed it}" hold_beat
}

hold_new_task() {
    # #1575 (metal 17/10): a build machine whose capture or direct send failed
    # held here for ever, and the task its own GUI ordered next ("קליטה
    # נוספת", tsk_d58c) stayed pending -- the beat threw the answer away. Its
    # source disk was only read: nothing is broken to guard. HOLD_YIELD_TASK =
    # the failed task (set by those two callers only); a hello answer (the
    # beat's, attended.resp) with ANOTHER task ends the hold, and the main
    # loop's next hello takes it. 0 = yield. Same task, no task: hold.
    [ -n "${HOLD_YIELD_TASK:-}" ] || return 1
    _hy=$(json_get "$RUN_DIR/attended.resp" ".task.id" 2>/dev/null)
    case "$_hy" in ""|null|"$HOLD_YIELD_TASK") return 1 ;; esac
    log "hold ended: the server gave a new task ($_hy) after $HOLD_YIELD_TASK failed"
    HOLD_YIELD_TASK=""; HOLD_PROMPT=""
}

# --- #2031: the end-of-task hold (idle.sh end_countdown, #2024), said ---------
# A cloner holding its results until "כבה" sends no hello: a "seen" cloner
# would join no wave, yet stop #1721's re-wake from failing the round by name.
# Without a word, though, the console drew it "not connected". So a light
# report on the progress path, with no session and no task --
# {"mac","state":"held","problems":[{disk,level,reason}]} from end.problems --
# every HELD_BEAT_S (the idle poll's ceiling, POLL_MAX). The server keeps it
# as a sign of life (progress_seen), never as last_seen, and answers with the
# console's power_action: kept in power.resp, as attended_keep does, for the
# hold loop's attended_power_check. A failed report is not retried faster.
HELD_BEAT_S="${HELD_BEAT_S:-15}"

held_report() {
    [ -n "${SERVER:-}" ] && [ -n "${MAC:-}" ] || return 1
    { cat "$RUN_DIR/end.problems" 2>/dev/null || :; } | jq -Rn --arg mac "$MAC" \
        '{mac: $mac, state: "held", problems: [inputs | split("|") | select(length >= 3)
          | {disk: .[0], level: .[1], reason: (.[2:] | join("|"))}]}' > "$RUN_DIR/held.json" || return 1
    http_post_json "$SERVER/api/v1/agent/progress" "$RUN_DIR/held.json" > "$RUN_DIR/held.resp" || return 1
    case "$(jq -r '.power_action // empty' "$RUN_DIR/held.resp" 2>/dev/null)" in
        poweroff|reboot) cp "$RUN_DIR/held.resp" "$RUN_DIR/power.resp.next" && mv "$RUN_DIR/power.resp.next" "$RUN_DIR/power.resp" ;;
    esac
}

held_beat_start() {
    # One beat, started once per hold; every descriptor to /dev/null (attended_start).
    [ -z "${HELD_PID:-}" ] || return 0
    _hb_s=$(attended_secs HELD_BEAT_S "$HELD_BEAT_S" 15); _hb_agent=$$
    (
        while kill -0 "$_hb_agent" 2>/dev/null; do   # #1929: no orphan beat for a dead agent
            held_report || :
            sleep "$_hb_s"
        done
    ) < /dev/null > /dev/null 2>&1 &
    HELD_PID=$!
}

held_beat_stop() { [ -n "${HELD_PID:-}" ] || return 0; kill "$HELD_PID" 2>/dev/null; HELD_PID=""; }

hold_watch() {
    # $1 = the heartbeat command, or "" for a hold whose caller deliberately
    # left its progress loop running instead. Returns only through
    # hold_new_task (#1575); every other hold never returns.
    #
    # Until #109 the beat ran as `"$2" > /dev/null 2>&1 || true` -- stdout,
    # stderr and the exit code thrown away in one line. That is the shape rule
    # 5 names: the heartbeat is not a report that will be repeated in two
    # seconds (`progress_loop` earns its `|| true` that way, and keeps it), it
    # is the single piece of evidence that this machine is alive. When it stops
    # arriving, `last_seen` goes stale past AWAKE_SECONDS and the console draws
    # the machine as off -- the exact symptom #64 closed -- while nothing, on
    # the screen or in the journal, said so.
    #
    # What changes on failure is what the machine *says*, and nothing else:
    #
    #   * The loop is never broken. A hold that stopped beating is a machine
    #     that is neither visible nor recoverable when the link returns.
    #   * No backoff, and no ceiling to grow through. Six requests a minute is
    #     not load, and a beat that has slowed down is a machine that stays
    #     invisible for minutes after the network comes back.
    #   * One line per *transition* -- lost, and heard again -- never one per
    #     beat. This screen scrolls and never clears: a note every ten seconds
    #     would push the FAILED line off the top, and that line is the one the
    #     technician walked over to read.
    #   * What curl said is kept, not sent to /dev/null: "connection refused"
    #     and "could not resolve host" send a technician to two different
    #     places. It is held in a file that each beat overwrites and copied
    #     into the journal at the transition -- the reason for an outage is
    #     one sentence, not the same sentence six times a minute.
    _beat_fails=0
    _beat_out="$RUN_DIR/beat.err"
    while :; do
        attended_power_check
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        if [ -n "${1:-}" ]; then
            if "$1" > "$_beat_out" 2>&1; then
                hold_new_task && return 0   # #1575: the one way out
                if [ "$_beat_fails" -gt 0 ]; then
                    log "the server is answering again" \
                        "(after $_beat_fails missed heartbeats)"
                    echo "  The server can see this machine again."
                    _beat_fails=0
                fi
            else
                _beat_fails=$((_beat_fails + 1))
                if [ "$_beat_fails" = "1" ]; then
                    log "WARNING: the heartbeat is not getting through --" \
                        "the console will show this machine as off." \
                        "$(tr '\n' ' ' < "$_beat_out")"
                    echo
                    echo "  NOTE: no contact with the server since" \
                        "$(date -u +%H:%M:%S 2>/dev/null || echo '--:--:--')."
                    echo "        The console cannot see this machine, so it"
                    echo "        draws it as OFF -- check the network, not"
                    echo "        the power. The message above still stands."
                fi
            fi
        fi
        _hw_t=0   # #2068: the screen's buttons, every second between two beats
        while [ "$_hw_t" -lt "$HOLD_BEAT_S" ]; do
            hold_action; _hw_a=$?
            [ "$_hw_a" = 0 ] && return 0; [ "$_hw_a" = 2 ] && break
            sleep 1; _hw_t=$((_hw_t + 1))
        done
    done
}

# --- #2068 (Nadav 24/10): the failure screen's buttons --------------------------
# "A failure screen does with what can be done without going to another screen."
# The GUI leaves fail.action (its first line, the action) next to its state file,
# as power.press (powerbtn.sh); the agent decides. Its result, when it has one to
# say, is one more line on the same screen (fail_note) -- never silence.
fail_note() {
    [ -s "$RUN_DIR/fail.state" ] || return 0
    printf 'fail_step=%s
' "$1" >> "$RUN_DIR/fail.state"
}

hold_action() {
    # 0 = leave the hold (standby), 2 = beat now, 1 = nothing / stay.
    mv "${GUI_DIR:-$RUN_DIR/gui}/fail.action" "$RUN_DIR/fail.action.taken" 2>/dev/null || return 1
    _ha=$(head -n 1 "$RUN_DIR/fail.action.taken" | tr -d '
')
    log "failure screen: '$_ha' pressed"
    case "$_ha" in
        off) safe_poweroff "failure screen: power off" ||
                 fail_note "המחשב לא כובה: Wake-on-LAN לא נדרך בכרטיס הרשת. כבו אותו בכפתור ההפעלה." ;;
        retry|reboot) sync; reboot -f ;;
        beat) return 2 ;;
        standby) [ "${D_ROLE:-}" = cloner ] || { log "failure screen: 'standby' is a cloner's only"; return 1; }
              rm -f "$RUN_DIR/fail.state"; HOLD_PROMPT=""; return 0 ;;
        smart) _ha_d=$(for _ha_t in "$RUN_DIR"/targets/*/; do [ -d "$_ha_t" ] && { _ha_t=${_ha_t%/}; echo "${_ha_t##*/}"; break; }; done)
               if [ -z "$_ha_d" ] || ! command -v smart_probe >/dev/null 2>&1; then fail_note "SMART של דיסק המקור לא נבדק."
               else _ha_v=$(smart_probe "$_ha_d"); case "$_ha_v" in
                   ok) fail_note "SMART של דיסק המקור תקין." ;;
                   *) fail_note "דיסק המקור: $(smart_why_he "$_ha_v" "$(smart_field "$_ha_d" 2)")." ;; esac; fi ;;
        windows) _ha_b=$(boot_next_local 2>/dev/null); case "$_ha_b" in
                     set:*) log "failure screen: BootNext $_ha_b -- rebooting to Windows"; sync; reboot -f ;;
                     *) fail_note "אי אפשר לבחור מכאן את Windows (${_ha_b#*:}). כבו והדליקו, ובחרו בדיסק המקומי." ;; esac ;;
        *) log "failure screen: an action this agent does not know ($_ha)" ;;
    esac
    return 1
}
