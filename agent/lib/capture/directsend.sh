# directsend.sh -- the build machine as the source of a wave (#715): the task.
# POSIX sh (busybox ash). No bashisms.
#
# "Deploy from this disk": the server stays the only orchestrator (session,
# receiver count, start, progress, console); what changes is the SOURCE. This
# machine sends its own disk, read-only, and nothing of it is written to the
# server but the manifest. The wave is the chunk engine's (directchunk.sh,
# #3161), the only one (#3173): a task the server did not hand to it is refused
# by name before a byte is sent.
#
# Two reads of the disk, on purpose. The receivers compare every partition to
# the manifest they fetched BEFORE the stream, so the manifest must exist before
# the first packet. Read 1 is capture_disk with the upload sink swapped for a bit
# bucket: same gates (GPT only, hibernation #651, BitLocker #671), same manifest,
# no byte leaves the machine. Every pass after it reads the disk again
# (directchunk.sh) and is compared to read 1.

_direct_failed() {
    # $1 = disk, $2 = reason. Same road as _capture_failed (#106): the
    # reason lands in the target's error field, which is what the console
    # shows; the log alone is tmpfs and gone at the next boot.
    log "direct send failed on $1: $2"
    target_set "$1" "failed" "$2"
    echo "failed" > "$RUN_DIR/state"
}

direct_manifest_put() {
    # $1 = task id, $2 = manifest path. The manifest -- and only the
    # manifest -- goes to the server, so the receivers can fetch it as
    # /api/v1/images/<image_id>/manifest. The bytes never do (Nadav 12/09).
    curl -sfS -X PUT -H "Content-Type: application/json" \
        -H "X-Imagesrv-Task-Token: ${TASK_TOKEN:-}" \
        --data-binary "@$2" "$SERVER/api/v1/direct/$1/manifest" >> "$LOG_FILE" 2>&1
}

direct_wait_running() {
    # $1 = task id. Polls hello until the server says the wave is running --
    # the selected drawers cover the target, or somebody pressed "send now".
    # 0 = send. 1 = the task is gone or the wave closed: not ours to decide,
    # and a source that starts anyway broadcasts to nobody (#438).
    # No ceiling on the wait itself -- the room has no timer, a person
    # stands there (spec 29) -- but a server that stops answering is
    # counted the way the main loop counts it, and 30 misses is a failure.
    _dw_miss=0
    while :; do
        if send_hello; then
            _dw_miss=0
            [ "$(json_get "$RESP" ".task.id")" = "$1" ] \
                || { log "direct: task $1 is no longer ours -- not sending"; return 1; }
            case "$(json_get "$RESP" ".task.direct.session_state")" in
                running) direct_operator_wait "$RESP" || return 0 ;;   # #1581: not while a cloner waits for a person
                closed|failed) log "direct: the wave closed before it started"; return 1 ;;   # #3161: failed = refused by name before a byte (the round carries the reason)
            esac
            ui_clear; ui_header
            echo "  Waiting for the cloning machines to join the wave."
            echo "  Joined so far: $(json_get "$RESP" ".task.direct.receivers")"
            echo "  Sending starts from the screen or the console ('send now')."
        else
            _dw_miss=$((_dw_miss + 1))
            [ "$_dw_miss" -ge 30 ] && { log "direct: server unreachable while waiting"; return 1; }
        fi
        sleep "${DIRECT_POLL_S:-2}"
    done
}

# --- #1581: a cloner asked before the first byte ----------------------------
# The wave does not start while a member waits for a person (prompt_hold.py);
# the server says how many wait (task.direct.operator_wait).

direct_operator_wait() {
    # $1 = a hello answer. 0 = a member waits for the operator now. A missing
    # or bad value (an older server) is 1: the wave goes as before #1581.
    case "$(json_get "$1" ".task.direct.operator_wait")" in
        ''|0|*[!0-9]*) return 1 ;;
    esac
}

direct_send_run() {
    # $1 = task id, $2 = disk, $3 = its serial at ordering (#1955; ""/null = none). 0 only when the server said
    # every partition is whole on the drawers still alive (direct_chunk_run).
    echo "capturing" > "$RUN_DIR/state"   # #3942: the smallest drawer chosen -- pass 1 shrinks to it, or refuses before a byte is read
    _dr_tb=$(json_get "$RESP" ".task.direct.target_bytes"); _dr_tn=$(json_get "$RESP" ".task.direct.target_name"); [ "$_dr_tn" != null ] || _dr_tn=""
    [ "$_dr_tb" != null ] || { _dr_tb=""; log "direct send: the smallest target drawer's size is unknown${_dr_tn:+ ($_dr_tn)} -- not checked here, each drawer checks its own fit (#3942)"; }
    CAPTURE_SINK=discard CAPTURE_TARGET_BYTES="$_dr_tb" CAPTURE_TARGET_NAME="$_dr_tn" capture_disk "$1" "$2" "${3:-}" || return 1   # #1955: #1901's identity gate, before the first datagram
    _dr_manifest="$RUN_DIR/new-manifest.json"
    direct_chunk_offer "$1" "$2" "$_dr_manifest" || return 1   # #3161: the chunk indexes and heads, before the manifest that signs them (directchunk.sh)
    direct_manifest_put "$1" "$_dr_manifest" || { _direct_failed "$2" "השרת לא קיבל את המניפסט החי"; return 1; }
    echo "waiting" > "$RUN_DIR/state"
    direct_wait_running "$1" || { _direct_failed "$2" "הגל לא התחיל"; return 1; }
    # #3173: the chunk engine is the only one -- a wave the server did not hand to it is refused by name, nothing sent
    [ "$(json_get "$RESP" ".task.direct.engine")" = chunk ] || { _direct_failed "$2" "השרת לא הציע את מנוע החתיכות להפצה הישירה -- לא נשלח דבר"; return 1; }
    direct_chunk_run "$1" "$2" "$_dr_manifest"   # directchunk.sh
}

direct_send_task() {
    # $1 = task id; the answer that carried the task is in $RESP. Mirrors
    # do_task for a capture: both roads out end at report_final (#127), and
    # "the server never heard" holds instead of powering off.
    _dt_disk=$(json_disk "$RESP" ".task.disk") || log "direct send: disk name '$(json_get "$RESP" ".task.disk")' refused (#1635)"
    ui_clear; ui_header
    echo "  Deploying /dev/$_dt_disk directly to the cloning machines."
    echo "  Source disk: read only -- nothing is written to it."
    echo "  Do not power off."
    progress_loop "" "$MAC" "$SERVER" "$1" &
    _dt_ppid=$!
    [ -n "$_dt_disk" ] && direct_send_run "$1" "$_dt_disk" "$(json_get "$RESP" ".task.disk_serial")"; _dt_rc=$?
    shrink_restore_source "$_dt_disk" || :   # #1171: the tail's RAM view, after both reads; not released = a warning in error
    [ "$_dt_rc" != 0 ] || capture_unhib_note "$_dt_disk" || { _direct_failed "$_dt_disk" "לא הצלחנו לרשום שההשהיה בוטלה בעותק — המקור לא נכתב"; _dt_rc=1; }
    if [ "$_dt_rc" -eq 0 ]; then
        target_set "$_dt_disk" "done"   # #2885: the source's own state too -- target_init left it waiting, both engines
        echo "done" > "$RUN_DIR/state"
        report_final "$_dt_ppid" "" "$MAC" "$SERVER" "$1" \
            || { hold_unheard "" "$1"; return 1; }
        # #1560 (Nadav 17/10): asked like the end of a capture -- not a silent
        # 20 s and a power-off. The build menu keeps the GUI up; none = power off.
        build_end_ask send-end "ההפצה הישירה הושלמה: לכבות או לחזור לתפריט" "direct send complete" \
            "Direct deployment complete. The drawers were written."
        return 0
    fi
    echo "failed" > "$RUN_DIR/state"
    report_final "$_dt_ppid" "" "$MAC" "$SERVER" "$1" \
        || { hold_unheard "" "$1" \
             "direct deployment did not complete, and the server was not told"; return 1; }
    HOLD_YIELD_TASK="$1"   # #1575: the source was only read -- a new task ends the hold
    ui_error_hold "direct deployment did not complete" hold_beat
    return 1
}
