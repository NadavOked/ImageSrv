# shrinkgui.sh -- the capture's shrink question on the native GUI (#928).
# POSIX sh. The same handshake as clonergui.sh's SMART bridge: the agent
# publishes one atomic request and waits for a decision bound to its nonce.
#
#   $GUI_DIR/shrink-request   nonce|count, then idx|old bytes|new bytes per row
#   $GUI_DIR/shrink-decision  nonce|continue|plain|cancel  (written by the bridge)
#
# guistate.sh copies the request into the state file (shrink_prompt= and
# shrink_partition=); the GUI draws the dialog and emits shrink-<answer>|nonce.
# Nothing here ever answers "continue" by itself: a dead GUI, a malformed
# plan, a broken handshake or no answer within WAIT_SHRINK_GUI_S all return
# non-zero, and shrink_ask turns that into a cancelled capture (principle 5).

#: The most rows the GUI draws (MAX_SHRINK_PARTS in native-gui/src/ui.h).
#: A plan with more is refused, not drawn in part.
SHRINK_GUI_MAX_ROWS=16

gui_shrink_clear() {
    rm -f "$GUI_DIR/shrink-request" "$GUI_DIR/shrink-decision" \
        "$GUI_DIR/shrink-request.next" "$GUI_DIR/shrink-decision.next"
}

gui_shrink_choice() {
    # $1 = shrink plan (idx|guid|uguid|start|size|cur bytes|new bytes).
    # stdout = continue / plain / cancel; non-zero = the GUI could not answer.
    [ -n "${_gui_pid:-}" ] && kill -0 "$_gui_pid" 2>/dev/null || return 1
    case "${WAIT_SHRINK_GUI_S:-}" in ''|*[!0-9]*) return 1 ;; esac
    GUI_DIR=${GUI_DIR:-$RUN_DIR/gui}
    gui_shrink_clear || return 1
    # The nonce is file-backed and monotonic: this runs inside `$( )`, and a
    # variable would not survive to the next capture.
    _sg_seq=0
    if [ -f "$GUI_DIR/shrink-seq" ]; then
        read -r _sg_seq < "$GUI_DIR/shrink-seq" || return 1
    fi
    case "$_sg_seq" in ''|*[!0-9]*) return 1 ;; esac
    _sg_seq=$((_sg_seq + 1))
    printf '%s\n' "$_sg_seq" > "$GUI_DIR/shrink-seq" || return 1
    _sg_nonce="$_sg_seq-0"
    # Validate the complete plan before publishing, never silently omit a row.
    # #1171: a direct send asks the same question in its own words (|direct).
    _sg_kind=""; [ "${CAPTURE_SINK:-upload}" = discard ] && _sg_kind="|direct"
    awk -F'|' -v nonce="$_sg_nonce" -v max="$SHRINK_GUI_MAX_ROWS" -v kind="$_sg_kind" '
        NF != 7 || $1 !~ /^[0-9]+$/ || $6 !~ /^[0-9]+$/ || $7 !~ /^[0-9]+$/ ||
        $1 < 1 || $6 + 0 <= $7 + 0 || $7 + 0 <= 0 || seen[$1]++ { bad = 1; exit }
        { rows[++n] = $1 "|" $6 "|" $7 }
        END { if (bad || n < 1 || n > max) exit 1
              print nonce "|" n kind; for (i = 1; i <= n; i++) print rows[i] }
    ' "$1" > "$GUI_DIR/shrink-request.next" || { gui_shrink_clear; return 1; }
    mv "$GUI_DIR/shrink-request.next" "$GUI_DIR/shrink-request" || { gui_shrink_clear; return 1; }
    _sg_waited=0
    while kill -0 "$_gui_pid" 2>/dev/null && [ -e "$GUI_DIR/shrink-request" ]; do   # #1726: a bridge that died took the question with it
        attended_power_check
        if [ -s "$GUI_DIR/shrink-decision" ]; then
            IFS='|' read -r _sg_answer_nonce _sg_answer < "$GUI_DIR/shrink-decision" || {
                gui_shrink_clear; return 1;
            }
            if [ "$_sg_answer_nonce" = "$_sg_nonce" ]; then
                case "$_sg_answer" in
                    continue|plain|cancel)
                        gui_shrink_clear || return 1
                        printf '%s\n' "$_sg_answer"; return 0 ;;
                esac
            fi
            # A stale or malformed decision is dropped; the wait goes on.
            rm -f "$GUI_DIR/shrink-decision" || { gui_shrink_clear; return 1; }
        fi
        if [ "$_sg_waited" -ge "$WAIT_SHRINK_GUI_S" ]; then
            printf 'native GUI: no shrink answer in %ss\n' "$WAIT_SHRINK_GUI_S" >&2
            break
        fi
        sleep 1
        _sg_waited=$((_sg_waited + 1))
    done
    gui_shrink_clear
    return 1
}

gui_shrink_dispatch() {
    # `shrink-<answer>|<nonce>` from the build GUI. This is a pending local
    # question, not a new request: like SMART it needs no console session,
    # and it is accepted only for the nonce of the request on screen.
    [ -z "${GUI_SCREEN:-}" ] || return 1
    case "$token" in *'|'*) ;; *) return 1 ;; esac
    _sg_action=${token%%|*}; _sg_click=${token#*|}
    case "$_sg_action" in shrink-continue|shrink-plain|shrink-cancel) ;; *) return 1 ;; esac
    case "$_sg_click" in ''|*[!0-9-]*) return 1 ;; esac
    [ -s "$GUI_DIR/shrink-request" ] || return 1
    IFS='|' read -r _sg_active _ < "$GUI_DIR/shrink-request" || return 1
    [ -n "$_sg_active" ] && [ "$_sg_click" = "$_sg_active" ] || return 1
    printf '%s|%s\n' "$_sg_active" "${_sg_action#shrink-}" \
        > "$GUI_DIR/shrink-decision.next" &&
        mv "$GUI_DIR/shrink-decision.next" "$GUI_DIR/shrink-decision"
}
