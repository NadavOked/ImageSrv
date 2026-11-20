# interrupt.sh -- #2528: a cloner was powered off or rebooted mid-round; the server asks and
# writes nothing without an answer (server/rounds/interrupt.py). POSIX sh (busybox ash).
#
# Nadav, 12/11: the question is shown where the round was sent from -- and on the cloner.
#
# * The cloner's own screen (interrupt_told): nobody is signed in there, so it is TOLD, not
#   asked -- one button, "הבנתי". The hello's answer names the question (.interrupt) only while
#   it is open and about this cloner; once per question and boot.
# * The build machine that opened the round (interrupt_ask_build): whoever is signed in to its
#   kiosk answers -- an admin or a deploy user. The kiosk bridge sees the question on the round
#   it watches (roomflow.sh interrupt_room_watch -> room.interrupt), the dialog has the console's
#   two answers, and the answer goes with the signed-in session to POST /api/console/room/interrupt,
#   naming the machines the dialog was about. Esc is "not now": it answers nothing.
#
# Nothing here decides anything: no GUI, a dialog that was not answered, a POST that failed --
# the question stays open on the server, which writes nothing and closes the round by its own
# clock (the console's setting). The dialog closes by itself when the server stops naming the
# question (interrupt.gone): answered elsewhere, or expired.

#: the dialog's own ceiling -- above the server's longest (240 minutes), so the server's clock,
#: not this one, ends an unanswered question.
INTERRUPT_ASK_S="${INTERRUPT_ASK_S:-14700}"

interrupt_keep() {
    # $1 = the hello answer of an attended beat (attended.sh). On the cloner: the question is
    # over when a READ answer no longer names it. An answer that could not be read says nothing.
    [ "${D_ROLE:-}" = cloner ] || return 0
    _ik=$(jq -r 'if type == "object" then (.interrupt.round_id // "none") else error("bad") end' "$1" 2>/dev/null) || return 0
    [ "$_ik" != none ] || : > "$RUN_DIR/interrupt.gone"
}

interrupt_told() {
    # The cloner's idle loop. $RESP is the hello answer just read.
    _it_id=$(jq -r 'if type == "object" then (.interrupt.round_id // "none") else error("bad") end' "$RESP" 2>/dev/null) || return 0
    case "$_it_id" in
        room_*) ;;
        *) rm -f "$RUN_DIR/interrupt.told"; return 0 ;;
    esac
    case "${_it_id#room_}" in ''|*[!A-Za-z0-9]*) return 0 ;; esac
    [ "$(cat "$RUN_DIR/interrupt.told" 2>/dev/null)" != "$_it_id" ] || return 0
    interrupt_rows "$RESP" .interrupt.disks > "$RUN_DIR/interrupt.rows" 2>/dev/null ||
        { log "round $_it_id waits for a decision about this cloner; its disks could not be listed -- not shown"; return 0; }
    rm -f "$RUN_DIR/interrupt.gone"
    log "round $_it_id waits for a decision: this cloner was powered off or rebooted mid-round"
    attended "הסבב ממתין להכרעה: המחשב כבה או אותחל באמצע הסבב" \
        gui_ask_choice interrupt self "$RUN_DIR/interrupt.rows" "$INTERRUPT_ASK_S" > /dev/null || :
    printf '%s\n' "$_it_id" > "$RUN_DIR/interrupt.told"
    return 0
}

interrupt_ask_build() {
    # The build machine's menu loop (build_console_screen). room.interrupt is the kiosk bridge's.
    [ -s "$RUN_DIR/room.interrupt" ] || return 0
    _ib_macs=$(sed -n 1p "$RUN_DIR/room.interrupt")
    case "$_ib_macs" in '['*']') ;; *) return 0 ;; esac
    case "$_ib_macs" in *[!]\[\",:0-9a-f]*) return 0 ;; esac
    [ "$_ib_macs" != "$(cat "$RUN_DIR/interrupt.asked" 2>/dev/null)" ] || return 0   # "not now" is not asked again
    sed 1d "$RUN_DIR/room.interrupt" > "$RUN_DIR/interrupt.rows" || return 0
    rm -f "$RUN_DIR/interrupt.gone"
    _ib=$(attended "הסבב ממתין להכרעה: מחשב שיכפול כבה או אותחל באמצע הסבב" \
        gui_ask_choice interrupt cloner "$RUN_DIR/interrupt.rows" "$INTERRUPT_ASK_S") || _ib=""
    printf '%s\n' "$_ib_macs" > "$RUN_DIR/interrupt.asked"
    case "$_ib" in
        rewrite|close) ;;
        *) log "the round's question was not answered here (${_ib:-no answer}) -- it stays open on the server"; return 0 ;;
    esac
    printf '{"answer":"%s","machines":%s}' "$_ib" "$_ib_macs" > "$RUN_DIR/interrupt.json" || return 0
    _ib_code=$(console_post room/interrupt "$RUN_DIR/interrupt.json" "$RUN_DIR/interrupt.resp") || _ib_code=000
    case "$_ib_code" in
        200) log "the round's question was answered here: $_ib" ;;
        409) log "the round's question was already answered elsewhere (http 409)" ;;
        *) rm -f "$RUN_DIR/interrupt.asked"   # the answer did not reach the server: asked again
           log "the answer '$_ib' did not reach the server (http $_ib_code) -- the question stays open" ;;
    esac
    return 0
}
