# guiparent.sh -- GUI lifecycle in the existing agent poll loop. POSIX sh.
#
# GUI only (owner, 11/10): there is no text menu or prompt behind the native
# GUI any more. A screen that needs a person and gets no GUI -- not installed,
# libraries missing, the process gone -- ends in gui_unavailable_local below.

#: How long the "GUI did not start" line stays on the console before the
#: reboot, so a person standing there can read it.
GUI_FAIL_SHOW_S="${GUI_FAIL_SHOW_S:-10}"
#: Owner, 17/10: a GUI that dies is started again -- this many times in one
#: boot -- and only then does the static screen (splash.sh) take its place.
GUI_RESPAWN_MAX="${GUI_RESPAWN_MAX:-3}"
#: #1726: how often the GUI's keeper (gui_keep) looks at it, in seconds.
GUI_KEEP_TICK_S="${GUI_KEEP_TICK_S:-1}"
GUI_KIOSK="${GUI_KIOSK:-/usr/bin/imagesrv-kiosk}"
GUI_STATION="${GUI_STATION:-/usr/bin/imagesrv-station-gui}"

gui_unavailable_local() {
    # $1 = which screen needed the GUI (for the journal). ASCII on purpose:
    # the Linux console has no Hebrew font and no RTL (ui.sh), so the Hebrew
    # line would reach the person as squares.
    log "native GUI unavailable for the ${1:-operator} screen -- no text fallback, booting from the local disk"
    # 11/10: the next boot goes to the local disk (BootNext), not back here
    # -- and the console hears about it (localboot.sh).
    _gu_bn=$(boot_next_local); _gu_state=${_gu_bn%%:*}; _gu_why=${_gu_bn#*:}
    log "BootNext to the local disk: $_gu_state (the Hebrew detail is in the console journal)"   # log() also prints to the ASCII tty
    gui_failed_report "${1:-operator}" "$_gu_state" "$_gu_why" || :
    # 17/10: the static screen (gui_gone) is our design; text only without it.
    if [ ! -e "$RUN_DIR/splash.on" ]; then
        ui_clear; ui_header
        echo "  The graphical interface did not start."
        if [ "$_gu_state" = set ]; then
            echo "  Booting from the local disk."
        else
            echo "  Rebooting. The next boot could NOT be pointed at the local disk:"
            echo "  (details: see the server console journal)"
        fi
    fi
    [ "${IMAGESRV_TEST:-0}" = "1" ] || sleep "$GUI_FAIL_SHOW_S"
    die_local "native GUI unavailable (${1:-operator} screen)"
}

gui_restore_authorized() {
    # The authorization recovery_gate gave the restore before 11/10, kept
    # when the text sign-in went (security review): an image name in a
    # handoff file is NOT permission to erase this disk. Fail-closed, on
    # positive evidence only:
    #   1. `.ui.require_login` must be a known answer -- anything but
    #      true/false is "the server did not say", and refuses (principle 5).
    #      Both known answers then need a session: the GUI always signs in.
    #   2. the console session of THIS GUI login -- the bridge hands its
    #      cookie jar over with the handoff ($RUN_DIR/gui/handoff.jar) --
    #      must still be live: the server answers 200 on a session-only
    #      console route. Missing jar, 401, no answer: refused.
    # The jar is single-use: a refusal deletes it here; an accepted one is
    # kept for the pull, which carries it to the server (11/10: the restore
    # is linked to this console session) and deletes it (single_restore_run).
    _gra_jar="$RUN_DIR/gui/handoff.jar"
    case "$(json_get "$RESP" ".ui.require_login")" in
        true|false) ;;
        *) rm -f "$_gra_jar"; log "restore: the server did not say whether a login is needed -- refused"; return 1 ;;
    esac
    [ -s "$_gra_jar" ] || { log "restore: no console session from the GUI -- refused"; return 1; }
    _gra_code=$(curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -b "$_gra_jar" -o "$RUN_DIR/gui/restore-auth.json" -w '%{http_code}' \
        "$SERVER/api/console/folders" 2>> "$LOG_FILE")
    [ "$_gra_code" = 200 ] && return 0
    rm -f "$_gra_jar"
    log "restore: the GUI's console session is not live (http ${_gra_code:-000}) -- refused"
    return 1
}

gui_gone() {
    # $1 = why (journal). The GUI is not coming back this boot: the static
    # screen in our design takes the display, never the bare tty (owner 17/10).
    log "$1"
    _gui_disabled=1; _gui_pid=; rm -f "$RUN_DIR/gui.pid"
    splash_draw || log "splash: not drawn -- the console text stays"
    return 1
}

gui_keep() {
    # #1726 (metal, rc5): the GUI's owner is a process of its own. gui_parent
    # ran only between hellos, and the agent spends minutes in a write, a pull,
    # a local copy or the end count -- a GUI killed there stayed dead, its last
    # frame (17%) frozen on the screen to the power-off. Here the bridge is
    # started again within a tick of its end -- an exit, a crash, or its GUI
    # process gone (SIGKILL) while the bridge is held in an action -- up to
    # GUI_RESPAWN_MAX times a boot, then the static screen. The new bridge draws
    # the screen of now: its state is read from the files (targets/, $LC/screen,
    # end.poweroff). The handoff (restore-start) ends it: that is no crash.
    _gk_n=0
    while :; do
        rm -f "$RUN_DIR/gui/station.pid"; mkdir -p "$RUN_DIR/gui"; : > "$RUN_DIR/gui/beat"   # #1946: each GUI's start grace
        "$GUI_KIOSK" >> "$LOG_FILE" 2>&1 &
        _gk_k=$!; _gk_d=0; _gk_w=$WAIT_HELPER_S
        while kill -0 "$_gk_k" 2>/dev/null; do
            sleep "$GUI_KEEP_TICK_S"
            read -r _gk_s _gk_t 2>/dev/null < "$RUN_DIR/gui/station.pid" || continue
            if kill -0 "$_gk_s" 2>/dev/null && gui_beat_fresh; then continue; fi   # no bare `! cmd` (#231)
            if kill -0 "$_gk_s" 2>/dev/null; then   # #1946: alive and silent = stopped or wedged
                log "native GUI $_gk_s: heartbeat older than ${GUI_BEAT_MAX_S}s (stopped or wedged) -- killing it"
                kill -9 "$_gk_s" 2>/dev/null
            fi
            _gk_d=$((_gk_d + 1)); [ "$_gk_d" -ge 2 ] || continue   # the bridge sees its pipe end first
            log "native GUI process $_gk_s is gone and its bridge is held -- stopping the bridge"
            # its state writer too: a bridge stopped by force never reaches gui_cleanup
            kill ${_gk_t:+"$_gk_t"} "$_gk_k" 2>/dev/null; _gk_w=1; break
        done
        wait_pid "$_gk_k" "$_gk_w" "native GUI bridge" || :
        [ ! -f "$RUN_DIR/gui/handoff" ] || return 0
        _gk_n=$((_gk_n + 1))
        [ "$_gk_n" -le "$GUI_RESPAWN_MAX" ] ||
            { gui_gone "native GUI exited again after $GUI_RESPAWN_MAX restarts -- the static screen instead"; return 1; }
        log "native GUI exited -- starting it again ($_gk_n of $GUI_RESPAWN_MAX)"
    done
}

gui_parent() {
    [ "${_gui_disabled:-0}" = 0 ] || return 1
    if [ -n "${_gui_pid:-}" ]; then
        if [ -f "$RUN_DIR/gui/handoff" ]; then
            IFS= read -r _handoff < "$RUN_DIR/gui/handoff" || return 1
            [ "$_handoff" = restore-start ] || return 1
            # The bridge stops the display and releases input before exiting.
            wait_pid "$_gui_pid" "$WAIT_HELPER_S" "native GUI handoff" || true
            _gui_pid=; _gui_disabled=1
            # #1561: the GUI is back at once, as the restore's own screen --
            # every refusal below and the write itself are shown on it.
            sr_gui_open fresh || log "restore: no GUI for the restore -- the console lines only"
            # #706: the graphical picker already chose the image and the
            # bridge checked the typed name against the server (#1073). There
            # is no second, text sign-in any more (owner, 11/10: GUI only);
            # the GUI's own console session is re-checked instead
            # (gui_restore_authorized). Without the password the pull is
            # registered only where the server waives the login; elsewhere
            # pull_open says so and the write goes on.
            _gui_img=$(sed -n '2p' "$RUN_DIR/gui/handoff")
            # Every refusal here is on the screen in Hebrew before the reboot (sr_refused,
            # srgui.sh, by its key); `|| die_local` is the gate's own backstop -- never a fall-through.
            case "$_gui_img" in ''|*[!A-Za-z0-9_.-]*) rm -f "$RUN_DIR/gui/handoff.jar"; sr_refused malformed "restore: malformed image id from the GUI" || die_local "restore: malformed image id from the GUI" ;; esac
            gui_restore_authorized || sr_refused session "restore: not authorized -- nothing was written" || die_local "restore: not authorized -- nothing was written"
            # A GUI state is up to 2 s stale: re-validate the choice
            # against fresh allowed_images before erasing (principle 5/7).
            http_get "$SERVER/api/v1/agent/state?mac=$MAC" \
                > "$RUN_DIR/gui/restore-state.json" 2>/dev/null &&
                jq -e --arg id "$_gui_img" '.allowed_images[]? | select(.id == $id)' \
                    "$RUN_DIR/gui/restore-state.json" >/dev/null \
                || { rm -f "$RUN_DIR/gui/handoff.jar"; sr_refused not-allowed "restore: image is no longer allowed for this machine" || die_local "restore: image is no longer allowed for this machine"; }
            PULL_JAR="$RUN_DIR/gui/handoff.jar"   # the pull goes under this console session
            single_restore_run "$_gui_img"; _gui_rc=$?
            # Nadav 17/10: done (0) or cancelled with the table wiped (3) -- off after
            # SR_POWEROFF_S on the card, or at once on the GUI's power-off button,
            # never a reboot. Returns only when it stayed on. 1 = failed, held.
            [ "$_gui_rc" = 1 ] || sr_end_poweroff || :
            _build_standby=1
            return 0
        fi
        if kill -0 "$_gui_pid" 2>/dev/null; then return 0; fi
        # #1726: the keeper ends on the handoff (above), or after the static screen
        wait_pid "$_gui_pid" "$WAIT_HELPER_S" "native GUI keeper" || true
        _gui_pid=; _gui_disabled=1
        [ -e "$RUN_DIR/splash.on" ] || gui_gone "native GUI keeper ended -- the static screen instead"
        return 1
    fi
    [ -x "$GUI_KIOSK" ] && [ -x "$GUI_STATION" ] || return 1
    # --help exercises the dynamic loader without taking the display. A
    # library that does not load will not load on a retry either.
    "$GUI_STATION" --help >> "$LOG_FILE" 2>&1 || { gui_gone 'native GUI libraries unavailable'; return 1; }
    GUI_SCREEN=
    [ "$D_ROLE" = classroom ] && GUI_SCREEN=class
    [ "$D_ROLE" = cloner ] && GUI_SCREEN=cloner
    export SERVER MAC IP RUN_DIR LIB_DIR GUI_SCREEN
    gui_keep &   # #1726: the GUI's owner from here on
    _gui_pid=$!
    echo "$_gui_pid" > "$RUN_DIR/gui.pid"   # log() keeps off the screen while it lives (common.sh)
    return 0
}
