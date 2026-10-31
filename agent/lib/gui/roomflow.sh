# roomflow.sh -- the cloning-room round the build machine's GUI opens and
# drives (#135, spec 29). POSIX sh (busybox ash). No bashisms.
#
# The round lives on the server (server/room.py); the GUI room screen reads
# it (guistate.sh) and its buttons land here through guibridge.sh: open the
# round, wake the room, send now. Walking away is allowed: the wave finishes,
# the machines power off for the drawer swap, and the next wave opens by
# itself.
#
# Worth knowing while reading this: /api/console/room and its /start and
# /wake are `current_user` only. Any signed-in account -- admin or deploy --
# may open a room round.
#
# The text room screen (its image picker, its refresh loop and its
# renderer) was removed with the rest of the text screens (owner, 11/10:
# GUI only).

room_open() {
    # $1 = image id, $2 = how many drives the round has to produce -- both
    # from the GUI record (guibridge.sh checks their syntax, and so does
    # this: the server is the last word, not the first).
    _img=$1; _target=$2
    case "$_img" in ''|*[!A-Za-z0-9_.-]*) log "room round: bad image id"; return 1 ;; esac
    case "$_target" in
        ""|*[!0-9]*) log "room round: not a number of drives: $_target"; return 1 ;;
    esac
    [ "$_target" -ge 1 ] || { log "room round: at least one drive"; return 1; }

    # ROOM_OPEN_EXTRA: the GUI's ticked drawers (#1275) and drivers (#1269),
    # from room_open_extra.
    printf '{"image_id":"%s","target_drives":%s%s}' "$_img" "$_target" \
        "${ROOM_OPEN_EXTRA:-}" > "$RUN_DIR/room_open.json"
    _code=$(console_post "room" "$RUN_DIR/room_open.json" \
        "$RUN_DIR/room_open_resp.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The round was not opened"
        return 1
    fi
    log "cloning room round opened from the build machine: $_img x$_target"
    # #1560: its end is asked on this machine (room_end_watch below)
    _ro_id=$(json_get "$RUN_DIR/room_open_resp.json" ".id")
    case "$_ro_id" in room_*) ;; *) _ro_id="" ;; esac
    case "${_ro_id#room_}" in ''|*[!A-Za-z0-9]*) _ro_id="" ;; esac
    if [ -n "$_ro_id" ]; then printf '%s|%s\n' "$_ro_id" "$(date +%s)" > "$RUN_DIR/room.watch"
    else log "room round: no round id in the answer -- its end will not be asked"; fi
    return 0
}

room_end_watch() {
    # #1560 (Nadav 17/10): the end of a round opened here is asked like the
    # end of a capture. The kiosk sees it -- the round in room.watch is no
    # longer on the server's room -- and hands room.ended to the agent
    # (task.sh build_round_end). A round that failed or is being closed by a
    # person is not "finished": the watch is dropped, nothing asked. A room
    # that could not be read is no answer at all (principle 5): non-zero --
    # and not forever (security review 17/10: the watch also holds off the
    # idle power-off, idle.sh): no "active" for ROOM_WATCH_MAX_S drops it, aloud.
    IFS='|' read -r _rw_id _rw_seen < "$RUN_DIR/room.watch" || return 1
    case "$_rw_seen" in ''|*[!0-9]*) _rw_seen=0 ;; esac
    if gui_get room "$GUI_DIR/room-watch.json" &&
        _rw=$(jq -r --arg id "$_rw_id" 'if type != "object" or (has("round") | not) then error("bad room")
            elif (.round | type) == "object" and .round.id == $id then (.round.state // "unknown")
            else "gone" end' "$GUI_DIR/room-watch.json"); then :
    elif [ $(( $(date +%s) - _rw_seen )) -ge "${ROOM_WATCH_MAX_S:-600}" ]; then
        rm -f "$RUN_DIR/room.watch"
        log "room round $_rw_id: not confirmed for ${ROOM_WATCH_MAX_S:-600}s -- no longer watched (the idle power-off counts again)"
        return 1
    else return 1; fi
    case "$_rw" in
        active) printf '%s|%s\n' "$_rw_id" "$(date +%s)" > "$RUN_DIR/room.watch" ;;
        gone) mv "$RUN_DIR/room.watch" "$RUN_DIR/room.ended" ;;
        *) rm -f "$RUN_DIR/room.watch" ;;
    esac
}

# The GUI's `slots=<mac>@<port>,<port>/<mac>@<port>` record value (a MAC has
# colons of its own) -> the target_slots JSON array. Exit 1 on anything
# malformed, and nothing is posted: a selection that was not read is not
# "no selection" -- that one writes every drawer (#1275, principle 4/5).
# Was inline in guibridge.sh (#715); the library round uses it too.
room_slots_json() {
    case "$1" in ''|*[!0-9a-f:/,@]*) return 1 ;; esac
    printf '%s' "$1" | awk -v RS='/' -F@ '{
        gsub(/\n/, "", $2)
        if (NF != 2 || $1 == "" || $2 !~ /^[0-9]+(,[0-9]+)*$/) exit 1
        printf "%s{\"mac\":\"%s\",\"ports\":[%s]}", (n++ ? "," : "["), $1, $2 }
        END { if (!n) exit 1; printf "]" }'
}

# #1269: the GUI's `drivers=<mac>|<port>|<vendor>|<model>|...` -- four fields
# per disk that has a model; a disk with "no drivers" is simply not there.
# The state file never carries `|` inside a value (guistate.sh replaces it),
# so the fields cannot run into each other. -> the driver_models JSON array;
# exit 1 on anything malformed. The server checks the model against the
# library again and keeps its own spelling.
room_drivers_json() {
    jq -cn --arg d "$1" '$d | split("|") |
        if length == 0 or length % 4 != 0 then error("bad drivers") else
        [range(0; length; 4) as $i | .[$i:$i + 4] |
         if (.[0] | test("^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")) and (.[1] | test("^[1-9][0-9]?$"))
            and .[2] != "" and .[3] != ""
         then {mac: .[0], port: (.[1] | tonumber), vendor: .[2], model: .[3]}
         else error("bad drivers") end] end'
}

# The extra fields of a library round opened from the GUI. #1275: the ticked
# drawers go as target_slots whenever there is a selection, drivers or not --
# none ticked = empty, the round exactly as before (every fresh drawer, #695).
# #1269: driver_models only with drivers, and only on a selection: the server
# binds a model only to a disk chosen as a target (there is no "every disk").
room_open_extra() {
    _rx=''
    if [ -n "$1" ]; then
        _rs=$(room_slots_json "$1") || return 1
        _rx=",\"target_slots\":$_rs"
    fi
    if [ -n "$2" ]; then
        [ -n "$1" ] || return 1
        _rd=$(room_drivers_json "$2") || return 1
        _rx="$_rx,\"driver_models\":$_rd"
    fi
    printf '%s' "$_rx"
}

room_action() {
    # $1 = "wake" or "start". The server answers 200 or says why not, and
    # "no answer" is not "it worked". $2 (wake) = the picked cloners,
    # "<mac>,<mac>" (Nadav 17/10): only they wake. None is refused here --
    # an empty pick is never "the whole room", which has a cloner that must
    # not be woken remotely.
    if [ "$1" = wake ]; then
        case "$2" in ''|*[!0-9a-f:,]*) log "room wake: no valid cloner picked"; return 1 ;; esac
        printf '%s' "$2" | jq -R '{macs: split(",")}' > "$RUN_DIR/room_empty.json" || return 1
    else
        echo '{}' > "$RUN_DIR/room_empty.json"
    fi
    _code=$(console_post "room/$1" "$RUN_DIR/room_empty.json" \
        "$RUN_DIR/room_action.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The room did not accept '$1'"
        return 1
    fi
    log "cloning room: $1 accepted"
    [ "$1" = "wake" ] || return 0
    # The counts are the point: "0 machines" with no reason sends a
    # technician to check WoL in twelve BIOSes, when the fault is one
    # cable in the server (#74).
    log "Wake-on-LAN sent: $(json_get "$RUN_DIR/room_action.json" ".sent") machines, $(json_get "$RUN_DIR/room_action.json" ".failed") failed"
}

# #65 "power it off": one cloner the open wave shows as missing, so its drawers
# can be swapped. $1 = its MAC, $2 = its name (the GUI's confirm window,
# #1270). $3 = 1 only after the local-copy warning was confirmed. The server
# checks all of it again -- still missing, the wave still open, the name the
# machine's -- and the agent powers off on its next hello,
# after arm_wol (#587). "Requested" is not "powered off": the evidence is
# the hello that stops.
room_poweroff() {
    case "$1" in ''|*[!0-9a-f:]*) return 1 ;; esac
    case "$3" in '') _lc=false ;; 1) _lc=true ;; *) return 1 ;; esac
    jq -n --arg c "$2" --argjson lc "$_lc" '{confirm_name:$c,confirm_local_copy:$lc}' > "$RUN_DIR/room_poweroff.json" || return 1
    _code=$(console_post "room/missing/$1/poweroff" "$RUN_DIR/room_poweroff.json" \
        "$RUN_DIR/room_action.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The machine was not asked to power off"
        return 1
    fi
    log "cloning room: power-off requested for the missing cloner $1"
}

room_poweroff_all() {
    case "$2" in '') _lc=false ;; 1) _lc=true ;; *) return 1 ;; esac
    jq -n --arg c "$1" --argjson lc "$_lc" '{confirm_name:$c,confirm_local_copy:$lc}' > "$RUN_DIR/room_poweroff.json" || return 1
    _code=$(console_post "room/poweroff" "$RUN_DIR/room_poweroff.json" "$RUN_DIR/room_action.json") || return 1
    [ "$_code" = 200 ] || { console_say "$_code" "The room was not asked to power off"; return 1; }
}

room_drawer_cancel() {
    # #1386 "cancel this disk" from the build machine's distribution screen: $1 = the
    # cloner's mac, $2 = its drawer's dev, from the confirmed window. The same
    # POST as the console, with the signed-in user's session; the server checks
    # again that the drawer is writing in the live wave. 200 only.
    case "$2" in ''|*[!a-z0-9]*) return 1 ;; esac
    jq -n --arg m "$1" --arg d "$2" '{mac: $m, dev: $d}' > "$RUN_DIR/gui-action.json" || return 1
    _rc=$(console_post room/drawer/cancel "$RUN_DIR/gui-action.json" "$RUN_DIR/gui-result.json") || return 1
    [ "$_rc" = 200 ] && { log "drawer cancel asked from the GUI: $1 $2"; return 0; }
    _why=$(jq -r '.detail // empty' "$RUN_DIR/gui-result.json" 2>/dev/null)   # the server's words; none = say so
    gui_error "הביטול נדחה: ${_why:-סיבה לא ידועה} (http $_rc)" "drawer cancel refused (http $_rc)"
}
