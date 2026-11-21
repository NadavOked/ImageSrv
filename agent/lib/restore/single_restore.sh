# single_restore.sh -- the erase+write of ONE library image onto the internal
# disk, behind the graphical restore screen (#706). POSIX sh.
#
# The GUI chooses the image and the bridge checks the typed machine name; the
# caller (guiparent.sh) re-validates the choice against FRESH allowed_images,
# then this writes. It never reads stdin and never reboots (the caller
# decides). The text recovery wizard that shared it was removed (owner, 11/10:
# GUI only).

single_restore_run() {
    # $1 = image id, already chosen and confirmed on the GUI. The caller
    # re-validates the (possibly stale) choice against fresh allowed_images
    # before calling this. A deleted image is caught here by the manifest
    # fetch (principle 5). 0 = written, 1 = failed (held), 3 = cancelled on
    # the GUI and the disk left empty (#1561).
    [ -n "$1" ] || sr_refused no-image "restore: no image id" || die_local "restore: no image id"
    http_get "$SERVER/api/v1/images/$1/manifest" "${PULL_JAR:-}" > "$RUN_DIR/sr-manifest.json" \
        || sr_refused manifest "restore: manifest fetch failed" || die_local "restore: manifest fetch failed"
    _sr_disk=$(pick_internal_disk); _sr_pk=$?   # #1807: 2 = more than one internal disk
    [ "$_sr_pk" -ne 2 ] || sr_refused many-disks "more than one internal disk" || die_local "more than one internal disk"
    [ "$_sr_pk" -eq 0 ] || sr_refused no-disk "no internal disk found" || die_local "no internal disk found"
    # #1732: a red disk (SMART fail) is never a target -- refused before a byte, whatever the GUI sent
    # #1742: a read-only disk is the same red, refused with its own reason
    if smart_red "$_sr_disk"; then   # the probe first: the reason is read from its cache
        _sr_why="failed SMART"; _sr_key=smart-red
        [ "$(smart_field "$_sr_disk" 2)" != "readonly" ] || { _sr_why="is read-only"; _sr_key="readonly"; }
        [ "$(smart_field "$_sr_disk" 2)" != "f1_observed" ] || { _sr_why="was seen stopping the BIOS on F1"; _sr_key="f1-observed"; }   # #1858
        sr_refused "$_sr_key" "restore: $_sr_disk $_sr_why -- never written (#1732)" || die_local "restore: $_sr_disk $_sr_why"
    fi
    _sr_total=$(json_get "$RUN_DIR/sr-manifest.json" ".total_compressed_bytes")
    job_reset   # #1916: nothing of the last job rides on this one's first report
    target_init "$_sr_disk" "$_sr_total"
    # The progress on the GUI the handoff brought up (srgui.sh), before the
    # pull opens: the image, the disk, then the partitions as they go.
    sr_gui_start "$RUN_DIR/sr-manifest.json" "$_sr_disk" || log "restore: no GUI for the progress -- the console lines only"
    # #1898 (Nadav 22/10): the split of the room is shown and approved first, as on the cloner.
    sr_share_gate "$_sr_disk" "$RUN_DIR/sr-manifest.json"; _sr_sq=$?
    [ "$_sr_sq" -ne 2 ] || sr_refused share-plan "restore: the split of the room did not compute (#1898)" || die_local "restore: the split of the room did not compute"
    [ "$_sr_sq" -eq 0 ] || sr_refused share "restore: the split of the room was not approved (#1898)" || die_local "restore: the split of the room was not approved"

    # The server is told before the first byte moves (a pull nobody can watch is
    # useless, #63). #2657: the pull is also the permission -- the server serves
    # an image's files only inside an open round or pull of that image -- so a
    # pull that did not open stops here, before the disk is touched (rule 5),
    # and not after the table was wiped, at the stream.
    #
    # No password: the operator signed in on the GUI, whose password never
    # leaves the kiosk's sign-in process. Instead the pull carries that
    # console session ($PULL_JAR, the cookie jar the bridge handed over), and
    # the server accepts it as the authorization (11/10) -- the restore is
    # linked to the console user and followed there. The jar serves the
    # manifest above and this pull, and is removed right after.
    pull_open "$SERVER" "$MAC" "$1" "" ""; _sr_po=$?
    [ -z "${PULL_JAR:-}" ] || rm -f "$PULL_JAR"
    [ "$_sr_po" -eq 0 ] || sr_refused pull "restore: the server did not open the pull -- nothing was written (#2657)" || die_local "restore: the server did not open the pull"
    # #2657: an id in the answer is not yet evidence the pull is open for us -- the same gate as a round (opengate.sh)
    session_open_gate "$PULL_SESSION" "$MAC" "$SERVER" || sr_refused pull "restore: $OPEN_GATE_WHY (#2657)" || die_local "restore: $OPEN_GATE_WHY"
    progress_loop "$PULL_SESSION" "$MAC" "$SERVER" &
    _sr_ppid=$!
    # #1561 (Nadav 17/10): "ביטול השחזור" on the GUI stops the stream (srgui.sh);
    # a cancel, whatever the write's own end, leaves an empty disk -- 3.
    sr_cancel_watch "$1" "$_sr_disk" & _sr_cw=$!
    WRITEMARK_HOLD=1   # #1877: the mark comes off after the drivers, below
    run_restore "unicast" "$_sr_disk" "$SERVER" "$1" "$RUN_DIR/sr-manifest.json"; _sr_rc=$?
    WRITEMARK_HOLD=0
    kill "$_sr_cw" 2>/dev/null; wait "$_sr_cw" 2>/dev/null
    if sr_cancel_requested; then
        sr_cancelled "$_sr_disk" "$_sr_ppid" && return 3
        ui_error_hold "restore cancelled, but the partition table was not wiped" hold_beat
        return 1
    fi
    if [ "$_sr_rc" -eq 0 ]; then
        # #3906: the name first, as the class station (imagesrv-agent) -- never fatal, never silent.
        sr_name_machine "$_sr_disk"
        # #720: driver staging before the closing report -- its outcome rides
        # in that report. Never fatal; a Linux image is skipped inside.
        stage_drivers "$_sr_disk" "$RUN_DIR/sr-manifest.json"
        writemark_release_done "$_sr_disk" || { _sr_rc=1; echo failed > "$RUN_DIR/state"; }   # #1877: after the last write; a mark that stays = failed
    fi
    if [ "$_sr_rc" -eq 0 ]; then
        echo "done" > "$RUN_DIR/state"
        pull_close "$_sr_ppid" "$PULL_SESSION" "$MAC" "$SERVER"
        sr_gui_done
        return 0
    fi
    # A failed restore stays visible until a person clears it (#133/#108); the
    # reporter keeps running when pull_open succeeded so the console sees it.
    single_restore_failed_screen "$_sr_disk" || log "restore failed -- no GUI for the failure screen, the console line only"
    ui_error_hold "restore did not complete" hold_beat
    return 1
}

sr_name_machine() {
    # $1 = disk. #3906 (metal 20/11): this restore wrote no name at all -- a
    # Linux image came up as the machine it was captured from, and no line
    # said so. The name is this machine's own, as the server holds it
    # (machines.suffix): `name` of the /agent/state the bridge read just
    # before the write (guiparent.sh). A pull has no session prefix
    # (PULL_PREFIX, sessions.py), so nothing is composed. The same
    # write_hostname as every path: Windows in the hive (a hive not synced
    # is written, with a note -- #3167), Linux in /etc/hostname.
    rm -f "$RUN_DIR/gui/restore.warn"
    _sn=$(jq -r '.name // empty | strings' "$RUN_DIR/gui/restore-state.json" 2>> "$LOG_FILE"); _sn_q=$?
    [ "$_sn_q" -eq 0 ] || { sr_name_said "$1" "השם בתשובת השרת לא נקרא"; return 0; }
    [ -n "$_sn" ] || { sr_name_said "$1" "השרת לא שלח שם למחשב הזה"; return 0; }
    assert_target "$1" || return 0   # #2434: it failed by name; the mark stays
    _sn_r=$(write_hostname "$1" "$RUN_DIR/sr-manifest.json" "$_sn"); _sn_rc=$?
    printf '%s\n' "$_sn_r" > "$RUN_DIR/hostname.json"
    log "hostname: $_sn_r"
    if [ "$_sn_rc" -eq 0 ]; then
        _sn_n=$(json_get "$RUN_DIR/hostname.json" ".note // empty")
        [ -z "$_sn_n" ] || { printf '%s\n' "$_sn_n" > "$RUN_DIR/targets/$1/note"; sr_name_card "$_sn_n"; }   # #3167: a note, not an error
        return 0
    fi
    _sn_e=$(json_get "$RUN_DIR/hostname.json" ".error")
    { [ -n "$_sn_e" ] && [ "$_sn_e" != null ]; } || _sn_e="סיבה לא ידועה (rc=$_sn_rc)"
    sr_name_said "$1" "$_sn_e"
}

sr_name_said() {
    # $1 = disk, $2 = why. Principle 5, as name_this_machine (#856): the
    # restore is done, but "no name" is not "all fine" -- the target stays
    # done with the reason in its error (the report, the console), in the
    # log, and on the success card.
    _sd_why="שם המחשב לא נכתב: $2"
    log "hostname: $_sd_why"
    sr_name_card "$_sd_why" "שם המחשב לא נכתב — הסיבה ביומן ובקונסולה"
    [ -s "$RUN_DIR/targets/$1/error" ] && _sd_why="$(cat "$RUN_DIR/targets/$1/error") | $_sd_why"
    target_set "$1" "done" "$_sd_why"
}

sr_name_card() {
    # $1 = the words for the success card (srgui.sh puts them before the
    # count), $2 = shorter ones when they do not fit. sub= is 200 bytes in
    # the GUI and the label and the count take up to ~90: 100 here, never
    # cut mid-letter.
    _sc_t=$1; [ "$(printf '%s · ' "$_sc_t" | wc -c)" -le 100 ] || _sc_t=${2:-}
    [ -n "$_sc_t" ] || return 0
    mkdir -p "$RUN_DIR/gui" && printf '%s · ' "$_sc_t" > "$RUN_DIR/gui/restore.warn" || log "restore: the name's line was not put on the card"
}

sr_share_gate() {
    # $1 = disk, $2 = manifest. #1898 (Nadav 22/10): a shrunk partition BEFORE
    # the one that grows (C: before the linux root) goes back to its source
    # size only when the operator approved the split -- here as on the cloner
    # (drawergate.sh drawer_share_gate): share_plan says what expand_last will
    # write, the question (kind share) shows it on the progress GUI, and
    # "split" writes targets/<disk>/share.ok, which expand_last and disk_fits
    # read. 0 = nothing to ask, or approved. 1 = not approved: "skip", no
    # answer, no GUI. 2 = the split did not compute. 1 and 2 write nothing.
    _sq_rows=$(share_plan "$1" "$2") || return 2
    [ -n "$_sq_rows" ] || return 0
    printf '%s\n' "$_sq_rows" | awk 'NF { print "0|" $0 }' > "$RUN_DIR/share.rows" || return 2   # port 0: "הדיסק"
    _sq_a=$(sr_ask share "$1" "$RUN_DIR/share.rows")
    log "restore: share question (#1898) on $1 -- ${_sq_a:-no answer}"
    [ "$_sq_a" = split ] && : > "$RUN_DIR/targets/$1/share.ok"
}

sr_ask() {
    # $1 kind, $2 subject, $3 rows: gui_ask_choice (askgui.sh) on the progress
    # GUI. Its answer comes on its stdout (restore.out), like restore-cancel;
    # no bridge runs here to turn it into ask-decision (gui_ask_dispatch), so
    # this watcher does -- gui_ask_choice still checks the nonce and the
    # answer. PRINTS the answer; runs inside $( ).
    GUI_DIR="$RUN_DIR/gui"
    ( while :; do
          _sa=$(grep -E '^ask-[a-z]+[|][0-9]+-[0-9]+$' "$GUI_DIR/restore.out" 2>/dev/null | tail -n 1)
          [ -z "$_sa" ] || [ -s "$GUI_DIR/ask-decision" ] || { _sa_a=${_sa%%|*}; printf '%s|%s\n' "${_sa#*|}" "${_sa_a#ask-}" > "$GUI_DIR/ask-decision"; }
          sleep 1
      done ) > /dev/null & _sa_w=$!   # not on $( )'s pipe: it is killed below
    _gui_pid=${SR_GUI_PID:-}; gui_ask_choice "$1" "$2" "$3" "${WAIT_SMART_GUI_S:-1800}"; _sa_rc=$?
    kill "$_sa_w" 2>/dev/null; wait "$_sa_w" 2>/dev/null
    return "$_sa_rc"
}

single_restore_failed_screen() {
    # $1 = disk. #1545 (metal 17/10): the bridge released the display for the
    # write (guiparent.sh), so a failure was English on the bare tty -- no
    # Hebrew font there (ui.sh) -- and never said why. The GUI comes back as
    # a display-only message screen with the target's own reason -- on the
    # progress GUI itself when it is still up (srgui.sh sr_gui_message); its
    # clicks go nowhere, and the tty hold under it keeps the heartbeat.
    _sw=$(head -n 1 "$RUN_DIR/targets/$1/error" 2>/dev/null)
    sr_gui_message "השחזור נכשל — המחשב נשאר דלוק ומדווח לשרת" "$_sw" || return 1
    log "restore failed -- shown on the GUI: $_sw"
}

pick_internal_disk() {
    # The ONE internal disk (disk_internal: no USB enclosure, no SD card -- #1807).
    # The write after this erases it: 1 = none, 2 = more than one -- a refusal,
    # never the first of several. Lived in recovery.sh until the text wizard went (11/10).
    _pk=""
    for _n in $(list_disks); do disk_internal "$_n" && _pk="$_pk $_n"; done
    # shellcheck disable=SC2086 # the disk list is a word list on purpose
    set -- $_pk
    [ "$#" -eq 1 ] && { echo "$1"; return 0; }
    [ "$#" -eq 0 ] && return 1
    log "more than one internal disk ($*) -- not guessing which one to erase (#1807)"
    return 2
}
