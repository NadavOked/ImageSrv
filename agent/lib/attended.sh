# attended.sh -- hello while the agent waits for a person (#906).
#
# smart_choice (and the cloner screen behind it) blocks until the operator
# answers. Nothing else sends hello in that time: `last_seen` freezes, the
# console draws the machine as not seen, and the monitor -- the one thing
# the operator needs to read the question from his desk -- is refused
# (403) past ONLINE_SECONDS. Measured 16/09 on cloner 2.
#
# The wait itself is not shortened (Nadav: cloning is always with a person).
# The machine only keeps saying, at the hold beat's rate, "I am here, and I
# am waiting for you: <question>" -- interfaces.md section 2, `waiting_for`
# and `prompt`. A hello without the fields clears the prompt on the server.
#
# POSIX sh (busybox ash): no `read -t`, no job control. The beat is a
# background subshell started before the wait and killed after it. Its
# only children are `curl` (bounded by HTTP_TIMEOUT) and `sleep`; there is
# no stream under it to orphan. Every descriptor of the subshell goes to
# /dev/null: the caller runs inside `$( )`, and an inherited stdout would
# keep that capture open until the last `sleep` ended.

#: Same rate as HOLD_BEAT_S -- the other wait-for-a-person -- and never
#: faster than the idle poll ladder's first rung (poll.sh).
ATTENDED_BEAT_S="${ATTENDED_BEAT_S:-10}"
#: #989 item 2: the beat while a SMART question is on the GUI.
ATTENDED_ASK_BEAT_S="${ATTENDED_ASK_BEAT_S:-3}"

attended_hello() {
    # $1 = the question on the screen ("" = plain heartbeat, as hold_beat
    # always sent). Non-joining, like hold_beat: a waiting machine does not
    # ask into a wave. The answer is discarded -- the main loop's
    # response.json must not change under a running task.
    _ah_body=$(build_hello false) || return 1
    if [ -n "${1:-}" ]; then
        _ah_body="${_ah_body%\}},\"waiting_for\":\"operator\",\"prompt\":\"$(json_escape "$1")\"}"
    fi
    # #989 item 2: a SMART question on the cloner's GUI (smart-request) is also
    # offered to the console: `ask`. Without a live request -- a text menu,
    # nothing here would read the answer -- there is no `ask`.
    _ah_req="${GUI_DIR:-/nonexistent}/smart-request"
    _ah_nonce=""
    if [ -s "$_ah_req" ]; then
        IFS='|' read -r _ah_nonce _ah_port _ah_verdict _ah_reason _ < "$_ah_req" || _ah_nonce=""   # 16/10: model|serial follow
        case "$_ah_nonce$_ah_port" in *[!0-9-]*|'') _ah_nonce="" ;; esac
    fi
    if [ -n "$_ah_nonce" ]; then
        _ah_body="${_ah_body%\}},\"ask\":{\"nonce\":\"$_ah_nonce\",\"port\":${_ah_port:-0},\"verdict\":\"$(json_escape "$_ah_verdict")\",\"reason\":\"$(json_escape "$_ah_reason")\"}}"
    fi
    # #1367 (Nadav 14/10): a local copy writing on this machine is said, so the
    # console warns before a power-off that would leave the targets broken.
    if [ -f "${LC:-/nonexistent}/screen" ] && lc_copy_busy; then
        _ah_body="${_ah_body%\}},\"local_clone\":\"copying\"}"
    fi
    printf '%s' "$_ah_body" > "$RUN_DIR/attended.json" || return 1
    http_post_json "$SERVER/api/v1/agent/hello" "$RUN_DIR/attended.json" > "$RUN_DIR/attended.resp" || return 1
    attended_keep "$RUN_DIR/attended.resp"
    if command -v lc_report_resend >/dev/null; then lc_report_resend; fi   # #1913: the server answers again
    [ -n "$_ah_nonce" ] || return 0
    # The console answered: hand it to the GUI wait loop as if the button was
    # pressed -- same file, same nonce binding (clonergui.sh gui_smart_choice).
    # A local click already there wins; the claim in gui_smart_choice settles it.
    _ah_ans=$(smart_ask_answer "$RUN_DIR/attended.resp" "$_ah_nonce") || return 0
    [ ! -e "${GUI_DIR}/smart-decision" ] || return 0
    printf '%s|%s\n' "$_ah_nonce" "$_ah_ans" > "${GUI_DIR}/smart-decision.next" &&
        mv "${GUI_DIR}/smart-decision.next" "${GUI_DIR}/smart-decision"
}

attended_keep() {
    # #1367: $1 = the beat's answer. Two things in it outlive the beat:
    # * power_action (#980) is handed out ONCE -- this answer is the only one
    #   that will carry it. Kept whole in power.resp for whoever waits to act
    #   on it (localclone.sh); the waits that do not read it lose nothing they
    #   did not lose before.
    # * the round's state, for the screen in front of the person (lcgui.sh):
    #   attended.round, "" when it cannot be read -- never "none" by default.
    case "$(jq -r '.power_action // empty' "$1" 2>/dev/null)" in
        poweroff|reboot) cp "$1" "$RUN_DIR/power.resp.next" && mv "$RUN_DIR/power.resp.next" "$RUN_DIR/power.resp"
            # a copy blocks the screen's loop: a power request the operator
            # CONFIRMED over it is carried out from here (localclone.sh)
            if [ "$(jq -r '.power_confirmed // false' "$1" 2>/dev/null)" = true ] && lc_copy_busy; then
                lc_power_during_copy
            fi ;;
    esac
    _ak_s=$(jq -er '.session.state // "none"' "$1" 2>/dev/null) || _ak_s=""
    printf '%s\n' "$_ak_s" > "$RUN_DIR/attended.round.next" && mv "$RUN_DIR/attended.round.next" "$RUN_DIR/attended.round"
}

attended_power_check() {
    [ -s "$RUN_DIR/power.resp" ] || return 0
    mv "$RUN_DIR/power.resp" "$RUN_DIR/power.taken" 2>/dev/null || return 0
    _ap_resp="${RESP:-}"; RESP="$RUN_DIR/power.taken"
    power_check || log "power: request received during an attended wait but not executed"
    RESP="$_ap_resp"
}

smart_ask_answer() {
    # $1 = a hello response, $2 = the nonce asked. Prints the answer the
    # server says won for exactly that question; non-zero when there is none.
    jq -er --arg n "$2" '.ask_answer | select(.nonce == $n) | .answer
        | select(. == "replace" or . == "rescue" or . == "skip")' "$1" 2>/dev/null
}

attended_claim() {
    # $1 = nonce, $2 = what the person at the machine chose. The server settles
    # who answered first (console or here) and its reply carries the WINNING
    # answer: that is what gets done. No reply -- the server is down or the
    # reply is unreadable -- the local choice stands, said out loud on stderr:
    # a cloner does not stop because the server did (principle 1), but this is
    # not "the server agreed".
    _ac_body=$(build_hello false) &&
        _ac_body="${_ac_body%\}},\"ask_answered\":{\"nonce\":\"$1\",\"answer\":\"$2\"}}" &&
        printf '%s' "$_ac_body" > "$RUN_DIR/attended-claim.json" &&
        http_post_json "$SERVER/api/v1/agent/hello" "$RUN_DIR/attended-claim.json" \
            > "$RUN_DIR/attended-claim.resp" &&
        _ac_win=$(smart_ask_answer "$RUN_DIR/attended-claim.resp" "$1") &&
        { printf '%s\n' "$_ac_win"; return 0; }
    log "smart: the server did not confirm the answer -- keeping the local choice ($2)" >&2
    printf '%s\n' "$2"
}

#: The questions being waited on, innermost first, one per line -- a
#: prompt is one screen line. `attended` keeps it; nobody else reads it.
ATTENDED_NL='
'

attended_secs() {
    # $1 = the variable's name, $2 = its value, $3 = the default. Prints the
    # beat in whole seconds. The loop below counts seconds with `[ -lt ]` and
    # `%`: on "0.2" `[ -lt ]` is not false but an error (rc=2), so the loop
    # never slept and the beat became a hello storm (#1333); "0" did the
    # same, and a bad ask beat killed the beat at its first `%`. Anything but
    # a positive whole number is said on stderr (stdout may be the caller's
    # `$( )`), and the default -- the production rate -- is used.
    case "$2" in
        ''|0*|*[!0-9]*)
            log "attended: $1='$2' is not a whole number of seconds -- using $3" >&2
            echo "$3" ;;
        *) echo "$2" ;;
    esac
}

attended_start() {
    # $1 = the question. A failed beat is not retried faster and not logged
    # per beat: the screen the person is reading must not scroll.
    #
    # One beat at a time (#912): a question asked inside another one -- the
    # sign-in inside a menu, say -- replaces the running beat instead of
    # joining it. Two beats would alternate their prompts on the server, and
    # the one that started first would outlive both stops as an orphan
    # (measured 16/09: 6 hellos for 0.7 s of waiting, 3 more after the end).
    attended_stop
    _at_beat=$(attended_secs ATTENDED_BEAT_S "$ATTENDED_BEAT_S" 10)
    _at_ask=$(attended_secs ATTENDED_ASK_BEAT_S "$ATTENDED_ASK_BEAT_S" 3)
    _at_agent=$$   # the agent's own shell, also from inside a $( ) (POSIX $$)
    (
        while :; do
            # #1929 (M3): a beat whose agent is gone (kill -9) stops. Orphaned
            # (ppid 1), it went on saying hello and feeding the watchdog's
            # lease for a dead agent -- the machine looked alive, and the
            # watchdog never rebooted it.
            kill -0 "$_at_agent" 2>/dev/null || exit 0
            if command -v watchdog_beat >/dev/null; then watchdog_beat; fi
            attended_hello "$1" || :
            # A SMART question on the GUI is answerable from the console
            # (#989 item 2), so its beat is faster: the answer is on screen
            # within ATTENDED_ASK_BEAT_S of the click, not 10 s.
            _at_t=0
            while [ "$_at_t" -lt "$_at_beat" ]; do
                sleep 1; _at_t=$((_at_t + 1))
                [ -s "${GUI_DIR:-/nonexistent}/smart-request" ] &&
                    [ $((_at_t % _at_ask)) = 0 ] && break
            done
        done
    ) < /dev/null > /dev/null 2>&1 &
    ATTENDED_PID=$!
}

attended_stop() {
    [ -n "${ATTENDED_PID:-}" ] || return 0
    kill "$ATTENDED_PID" 2>/dev/null
    ATTENDED_PID=""
}

attended() {
    # $1 = the question; $2.. = the command that waits for the answer.
    # Its stdout and exit code pass through untouched. When the command
    # returns, the beat of the question this one was asked inside -- if
    # any -- resumes: the person is back at that screen.
    ATTENDED_STACK="$1$ATTENDED_NL${ATTENDED_STACK:-}"
    attended_start "$1"; shift
    "$@"; _at_rc=$?
    attended_stop
    ATTENDED_STACK="${ATTENDED_STACK#*"$ATTENDED_NL"}"
    [ -z "$ATTENDED_STACK" ] || attended_start "${ATTENDED_STACK%%"$ATTENDED_NL"*}"
    return "$_at_rc"
}
