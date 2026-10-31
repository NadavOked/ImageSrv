# guibridge.sh -- native GUI process and record dispatcher. POSIX sh.
# gui_main runs inside imagesrv-kiosk. imagesrv-agent also sources this file
# (function defs only, inert there) so the load gate (#84) sees every lib.
gui_error() {
    printf 'native-gui bridge: %s\n' "${2:-$1}" >&2   # $1 Hebrew for the screen, $2 English for the log
    printf 'toast=%s\nform_error=%s\nroom_error=%s\nclass_error=%s\n' \
        "$1" "$1" "$1" "$1" > "$GUI_DIR/error.next" &&
        mv "$GUI_DIR/error.next" "$GUI_DIR/error"
    return 1
}

gui_role() {
    # 17/09 (#947): the role comes from the login answer console_signin just
    # saved -- `/api/console/me` is a management route and stays off the
    # agent/kiosk ports (Astra's list); login already returns {username,role}.
    [ -s "$RUN_DIR/console_login.json" ] || return 1
    GUI_ROLE=$(jq -er '.role | select(. == "admin" or . == "deploy")' \
        "$RUN_DIR/console_login.json") || return 1
}

gui_auth() {
    rm -f "$CONSOLE_JAR" "$RUN_DIR/console_login.json" || return 1
    IFS= read -r RECOVERY_USER && IFS= read -r RECOVERY_PASS || return 1
    [ -n "$SERVER" ] && [ -n "$RECOVERY_USER" ] && [ -n "$RECOVERY_PASS" ] || return 1
    # stdout: the role, then the saved theme (#1499); never persist the password.
    if console_signin >&2 && gui_role; then
        unset RECOVERY_PASS
        printf '%s\n%s\n' "$GUI_ROLE" "$(gui_saved_theme)"
        return 0
    fi
    unset RECOVERY_PASS
    rm -f "$CONSOLE_JAR" "$RUN_DIR/console_login.json"   # a stale role must not pass gui_role
    gui_error 'הכניסה נדחתה או שלא ניתן היה לאמת אותה' 'Login was refused or could not be verified' >&2
}

gui_capture() {
    [ "$GUI_ROLE" = admin ] || return 1
    build_name_ok "$name" || return 1
    # Check against fresh server inventory, not a GUI-supplied device path.
    http_get "$SERVER/api/v1/agent/state?mac=$MAC" > "$RUN_DIR/gui-inventory.json" || return 1
    jq -e --arg dev "$dev" '.known == true and .role == "build" and
        any(.disks[]; .dev == $dev and .removable == false)' \
        "$RUN_DIR/gui-inventory.json" >/dev/null || return 1
    case "$folder_new" in
        yes)
            build_new_folder "$folder" >/dev/null || return 1
            ;;
        no) ;;
        *) return 1 ;;
    esac
    build_capture_post "$name" "${dev#/dev/}" "$folder" "$desc"
}

gui_dispatch() {
    # The cloner's SMART choice needs no login: a cloning machine is headless
    # and never signs in. Handle it before gui_role so the role check (which
    # would fail with no session) does not swallow the decision. Only on the
    # cloner screen, and only the three valid tokens.
    case "$token" in
        shrink-*) gui_shrink_dispatch; return $? ;;   ask-*) gui_ask_dispatch; return $? ;;   # 11/10: askgui.sh
        end-poweroff) [ -s "$RUN_DIR/end.poweroff" ] && : > "$RUN_DIR/end.now"; return $? ;;
        idle-poweroff) [ -s "$RUN_DIR/idle.warn" ] && : > "$RUN_DIR/idle.now"; return $? ;;
        choice-back) [ "${GUI_SCREEN:-}" = cloner ] && [ ! -f "${LC:-$RUN_DIR/lc}/screen" ] && [ "${D_SESSION_STATE:-}" != open ] && mkdir -p "${LC:-$RUN_DIR/lc}" && echo "0|2" > "${LC:-$RUN_DIR/lc}/boot" && echo boot > "${LC:-$RUN_DIR/lc}/screen"; return $? ;;   # #2079 (mockup 02): back to the boot choice, no count (localclone.sh)   # mockup 10: "כבה עכשיו" in the idle window (idle.sh)   # #1560: "כבה עכשיו" (idle.sh), no session needed
        end-hold) case "$(cut -d'|' -f2 "$RUN_DIR/end.poweroff" 2>/dev/null)" in partial|failed) : > "$RUN_DIR/end.hold" ;; *) return 1 ;; esac ;;   # #1560: read the results
        logout) rm -f "$CONSOLE_JAR" "$RUN_DIR/console_login.json"; return $? ;;   # GUI sign-out ends the session here too
        boot-*|lc-*) [ "${GUI_SCREEN:-}" = cloner ] || return 1; lc_gui_dispatch; return $? ;;   # #1322, lcgui.sh
        "drawer-cancel|"*) [ "${GUI_SCREEN:-}" = cloner ] || return 1; cancel_request "${token#*|}" "ממסך מחשב השיכפול"; [ $? -ne 1 ]; return $? ;;   # #1386: no sign-in, like SMART; 2 (asked) is not a handoff
        touch)
            # #434: a key press in the GUI -- the idle clock's activity, and
            # what cancels the power-off countdown. Before gui_role: it needs
            # no session (the login screen is exactly where a person waits).
            touch "$RUN_DIR/idle.touch" || return 1
            return 0 ;;
        smart-*)
            # ה-GUI שולח `smart-<action>|<nonce>` -- ה-nonce של הבקשה
            # ש**צוירה**. הגשר מקבל רק אם ה-nonce הזה תואם ל-nonce של
            # הבקשה הפעילה. ‏nonce ייחודי לכל בקשה (מונוטוני), ולכן גם
            # דיסק שנתבקש שוב (אותו port, nonce חדש) דוחה קליק מושהה עם
            # ה-nonce הישן -- מה שבדיקת port בלבד לא תפסה. קליק בלי בקשה
            # פעילה או על מסך אחר נדחה גם הוא.
            [ "${GUI_SCREEN:-}" = cloner ] || return 1
            case "$token" in *'|'*) ;; *) return 1 ;; esac    # ה-nonce חובה
            _sd_action=${token%%|*}
            case "$_sd_action" in smart-replace|smart-rescue|smart-skip) ;; *) return 1 ;; esac
            _sd_click_nonce=${token#*|}
            case "$_sd_click_nonce" in ''|*[!0-9-]*) return 1 ;; esac
            [ -s "$GUI_DIR/smart-request" ] || return 1
            IFS='|' read -r _sd_nonce _ < "$GUI_DIR/smart-request" || return 1
            [ -n "$_sd_nonce" ] || return 1
            [ "$_sd_click_nonce" = "$_sd_nonce" ] || return 1
            printf '%s|%s\n' "$_sd_nonce" "${_sd_action#smart-}" \
                > "$GUI_DIR/smart-decision.next" &&
                mv "$GUI_DIR/smart-decision.next" "$GUI_DIR/smart-decision"
            return $?
            ;;
    esac
    gui_role || { gui_error 'לא ניתן לאמת את ההתחברות — התחברו מחדש' 'Session/role could not be verified; restart and sign in'; return 1; }
    case "$token" in
        capture|room|classes|restore|direct) printf '%s\n' "$token" > "$GUI_DIR/mode" ;;
        tool-list) tools_gui_list ;;                                    # #649: writes $GUI_DIR/tools
        "tool-run|"*) tools_gui_run "$token" ;;                          # #649: guarded in tools.sh; result in $GUI_DIR/tool-result
        back|again) printf 'menu\n' > "$GUI_DIR/mode" ;;
        capture-start) gui_capture ;;   theme-light|theme-dark) gui_theme_save "${token#theme-}" ;;   # #1499: buildmenu.sh
        "syspart|"*) gui_syspart "$token" ;;   # #1896: C: of an old image, with this session (syspartgui.sh)
        room-open)
            case "$image" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
            case "$target" in ''|*[!0-9]*) return 1 ;; esac
            # room_open owns validation/body/POST.
            # #1275 (owner 11/10): the ticked drawers always go as target_slots,
            # none ticked = refused; #1269: with drivers=, their models too.
            case "$slots" in '') return 1 ;; esac
            _gx=$(room_open_extra "$slots" "$drivers") || return 1
            ROOM_OPEN_EXTRA=$_gx room_open "$image" "$target"
            ;;
        direct-open)
            # #715: the source is this machine's ONE internal disk (#1832),
            # from the server's fresh inventory (as gui_capture checks it);
            # the targets are the ticked drawers, "<mac>@<port>,<port>/<mac>@
            # <port>" (a MAC has colons of its own). The same POST the console sends;
            # the server validates every slot again (#695/#701). #1269: drivers=.
            _gslots=$(room_slots_json "$slots") || return 1
            _gdm=''; [ -z "$drivers" ] || _gdm=",\"driver_models\":$(room_drivers_json "$drivers")" || return 1
            http_get "$SERVER/api/v1/agent/state?mac=$MAC" > "$RUN_DIR/gui-inventory.json" || return 1
            _gd=$(jq -er '.disks | map(select(.removable == false)) | if length == 1 then .[0].dev else empty end' \
                "$RUN_DIR/gui-inventory.json") || { gui_error 'אין דיסק פנימי אחד בדיוק לשליחה' 'Not exactly one internal disk to send from'; return 1; }   # #1832: never .[0] of several
            printf '{"source":{"kind":"build_disk","mac":"%s","disk":"%s"},"target_slots":%s%s}' \
                "$MAC" "$_gd" "$_gslots" "$_gdm" > "$RUN_DIR/room_open.json" || return 1
            _gc=$(console_post room "$RUN_DIR/room_open.json" "$RUN_DIR/room_open_resp.json") || return 1
            [ "$_gc" = 200 ] || { gui_error "סבב ההפצה הישירה לא נפתח (http $_gc)" "The direct round was not opened (http $_gc)"; return 1; }
            log "direct round opened from the GUI: /dev/$_gd -> $slots"
            ;;
        room-wake|room-start) room_action "${token#room-}" "$cloner" ;;   # 17/10: wake = the picked cloners
        room-drawer-cancel) room_drawer_cancel "$cloner" "$dev" ;;   # #1386: roomflow.sh
        room-poweroff) room_poweroff "$cloner" "$confirm" "$local_copy" ;;   # #65/#1367
        room-poweroff-all) room_poweroff_all "$confirm" "$local_copy" ;;      # #1367
        room-close|class-start|class-close)
            if [ "$token" = room-close ]; then
                _gp=room/close
            else
                # Act on the published view, never fetch a replacement round
                # here and accidentally start/close a different classroom.
                _gs=$(jq -er '.session | select(.group_role == "classroom") | .id' "$GUI_DIR/session-shown.json") || return 1
                case "$_gs" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
                _gp="sessions/$_gs/${token#class-}"
            fi
            jq -n --arg c "$confirm" '{confirm_name:$c}' > "$RUN_DIR/gui-action.json" || return 1
            _gc=$(console_post "$_gp" "$RUN_DIR/gui-action.json" "$RUN_DIR/gui-result.json") || return 1
            [ "$_gc" = 200 ] && jq -e '.ok == true' "$RUN_DIR/gui-result.json" >/dev/null || return 1
            [ "$token" != room-close ] || rm -f "$RUN_DIR/room.watch"   # #1560: closed here -- nothing to ask
            ;;
        class-pick)
            # The class round after the pick was a text flow (sign-in again,
            # machines, image) and went with the text screens (owner, 11/10:
            # GUI only). Classrooms are v1.2 and hidden in v1 (#1081); until a
            # GUI round screen exists the pick is refused, visibly.
            gui_error 'סבבי כיתה אינם זמינים במסך הזה' 'Class rounds are not available on this screen' ;;
        restore-start)
            # #706: erase+write one image onto THIS machine. The GUI already
            # confirmed; re-check it here, and validate the id syntax. The
            # image itself is re-validated against fresh allowed_images in
            # guiparent.sh before a byte is written (principle 5/7).
            # #1073: the confirmation is the machine's registered name, read
            # fresh from the server -- not the one the screen showed 2 s ago,
            # and never an empty name matched by an empty field.
            case "$image" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
            http_get "$SERVER/api/v1/agent/state?mac=$MAC" > "$RUN_DIR/gui-restore-name.json" || return 1
            _gn=$(jq -er '.name | select(type == "string" and length > 0)' \
                "$RUN_DIR/gui-restore-name.json") || return 1
            [ "$confirm" = "$_gn" ] || return 1
            # The session goes along; guiparent.sh re-checks it before erasing.
            [ -s "$CONSOLE_JAR" ] && cp "$CONSOLE_JAR" "$GUI_DIR/handoff.jar" || return 1
            printf 'restore-start\n%s\n' "$image" > "$GUI_DIR/handoff.next" &&
                mv "$GUI_DIR/handoff.next" "$GUI_DIR/handoff" || return 1
            # #1561: the display is released next (gui_cleanup). While the GUI still holds it,
            # the VT's backlog is reset, so no English flashes before the restore's GUI.
            printf '\033c\033[?25l' > "${IMAGESRV_TTY:-/dev/tty0}" 2>/dev/null || :
            return 2
            ;;
        *) gui_error "פעולה לא מוכרת: $token" "Unknown action: $token" ;;
    esac
}

gui_records() {
    while IFS= read -r token; do
        dev='' name='' desc='' folder='' folder_new='' image='' target='' confirm='' group='' slots='' drivers='' cloner='' local_copy=''
        _complete=0; _bad=0; _seen='|'
        while IFS= read -r line; do
            [ -n "$line" ] || { _complete=1; break; }
            _key=${line%%=*}; _value=${line#*=}
            case "$_seen" in *"|$_key|"*) _bad=1 ;; esac
            _seen="$_seen$_key|"
            case "$line" in
                dev=*) dev=$_value ;; name=*) name=$_value ;; desc=*) desc=$_value ;;
                folder=*) folder=$_value ;; folder_new=*) folder_new=$_value ;;
                image=*) image=$_value ;; target=*) target=$_value ;;
                confirm=*) confirm=$_value ;; group=*) group=$_value ;;
                slots=*) slots=$_value ;; drivers=*) drivers=$_value ;; cloner=*) cloner=$_value ;; local_copy=*) local_copy=$_value ;;   # #1269; #65/#1367
                *) _bad=1 ;;
            esac
        done
        [ "$_complete" = 1 ] && [ "$_bad" = 0 ] || { gui_error 'בקשת הפעולה מהמסך פגומה או חסרה' 'Malformed/incomplete action record'; return 1; }
        rm -f "$GUI_DIR/error" || return 1
        gui_dispatch
        _gr=$?
        [ "$_gr" = 2 ] && return 0
        [ "$_gr" = 0 ] || gui_error "הפעולה נכשלה ולא אושרה הצלחה ($token)" "Action $token failed; no success confirmed (see agent log)"
    done
}

gui_cleanup() {
    for _pid in "${GUI_PID:-}" "${GUI_STATE_PID:-}"; do
        [ -n "$_pid" ] || continue
        if kill -0 "$_pid" 2>/dev/null; then
            kill "$_pid" || printf 'native-gui: cannot stop %s\n' "$_pid" >&2
        fi
        # Ceiling, never a bare wait: a GUI child that ignores SIGTERM must
        # not hang cleanup for ever -- wait_pid escalates to SIGKILL (waits.sh).
        wait_pid "$_pid" "$WAIT_HELPER_S" "native GUI process $_pid" || true
    done
    # A stale click must never authorize the next disk (principle 5).
    rm -f "$CONSOLE_JAR" "$RUN_DIR/console_login.json" "$GUI_DIR/actions" \
        "$GUI_DIR/smart-request" "$GUI_DIR/smart-decision" \
        "$GUI_DIR/shrink-request" "$GUI_DIR/shrink-decision" "$GUI_DIR/ask-request" "$GUI_DIR/ask-decision"
}

# The framebuffer device; monitor.sh tests the same one (#835).
FB_DEV="${FB_DEV:-/dev/fb0}"

gui_backend_args() {
    # ‏fbdev כברירת מחדל: על i915 אמיתי מסלול ה-DRM/KMS עושה modeset אבל
    # מצייר פריים שחור; ‏/dev/fb0 של fbcon (שבו GRUB ולוג האתחול משתמשים)
    # הוא הפלט המוכח על החומרה הזו (#641). לעקוף עם GUI_BACKEND=drm|auto|mem.
    # ‏#835: בלי /dev/fb0 בכלל (אין מסך מחובר -- מחשב שיכפול 2) הגואי מצייר
    # ל-$RUN_DIR/fb.mem, והמוניטור (monitor.sh) מגיש את הקובץ הזה ב-RFB.
    _gba=${GUI_BACKEND:-}
    if [ -z "$_gba" ]; then
        if [ -c "$FB_DEV" ]; then _gba=fbdev; else _gba=mem; fi
    fi
    printf '%s %s' --backend "$_gba"
    [ "$_gba" = mem ] && printf ' %s %s' --fb-file "$RUN_DIR/fb.mem"
    [ "$_gba" != mem ] && [ "${IMAGESRV_MONITOR:-0}" = 1 ] && printf ' %s %s' --fb-mirror "$RUN_DIR/fb.mem"  # #1475
    printf '\n'
}

gui_libs() {
    # The kiosk's own load chain: imagesrv-kiosk is a separate process, so
    # nothing imagesrv-agent sourced is here unless listed. A lib missing
    # from this list is "<fn>: not found" on the metal, once a second (#834).
    . "$LIB_DIR/common.sh"; . "$LIB_DIR/jsonq.sh"; . "$LIB_DIR/waits.sh"
    . "$LIB_DIR/roundbeep.sh"   # #2067: the kiosk owns the stream-age samples
    . "$LIB_DIR/buildmenuitems.sh"; . "$LIB_DIR/buildmenu.sh"   # #1073: items + gates, then the menu
    . "$LIB_DIR/buildcapture.sh"
    . "$LIB_DIR/roomflow.sh"   # #418: the room round (open/wake/start)
    . "$LIB_DIR/guistate.sh"
    . "$LIB_DIR/shrinkgui.sh"; . "$LIB_DIR/askgui.sh"; . "$LIB_DIR/syspartgui.sh"   # 11/10: the question dialog; #1896: C: of an old image
    . "$LIB_DIR/sysinfo.sh"; . "$LIB_DIR/disklink.sh"   # disk_port + disk_link (#432), for the cloner state producer
    . "$LIB_DIR/progress.sh"; . "$LIB_DIR/diskfit.sh"; . "$LIB_DIR/expand.sh"; . "$LIB_DIR/restore.sh"  # target_bytes, the per-drawer local truth; #1484: lc_verdict_for's min-free rule; restore.sh: manifest_plan, which expand.sh's _expand_candidate calls (min_free_check)
    . "$LIB_DIR/smart.sh"; . "$LIB_DIR/smartjudge.sh"; . "$LIB_DIR/hwissues.sh"; . "$LIB_DIR/manifest.sh"     # #1921: the tables judge the disk, the hw note per drawer; smart_hello_field: the cached verdict per cdisk (#834); manifest.sh: _partition_role, lc_role's fallback (#1591)
    . "$LIB_DIR/failmark.sh"; . "$LIB_DIR/slotnames.sh"; . "$LIB_DIR/writemark.sh"; . "$LIB_DIR/diskused.sh"   # #2030: cused= (size, used) per disk; the server's memory per cdisk (#874); slot names (#704); #1798 the mark per cdisk/lc_disk
    . "$LIB_DIR/lcinv.sh"; . "$LIB_DIR/lcraw.sh"; . "$LIB_DIR/lcgui.sh"; . "$LIB_DIR/clonergui.sh"; . "$LIB_DIR/drvgui.sh"; . "$LIB_DIR/drawers.sh"; . "$LIB_DIR/cancel.sh"; . "$LIB_DIR/lcpull.sh"; . "$LIB_DIR/usbclone.sh"; . "$LIB_DIR/usbset.sh"; . "$LIB_DIR/usbmap.sh"; . "$LIB_DIR/usbmapwin.sh"; . "$LIB_DIR/tools.sh"   # #649: the toolbox (tool-list / tool-run); tools_load pulls toolbins.sh + tools_*.sh itself (#1050)
}

gui_main() {
    gui_libs
    GUI_DIR=${GUI_DIR:-$RUN_DIR/gui}
    CONSOLE_JAR="$GUI_DIR/console.jar"
    export SERVER MAC IP RUN_DIR GUI_DIR CONSOLE_JAR HTTP_TIMEOUT HTTP_RETRIES
    [ "${1:-}" = --auth ] && { gui_auth; return $?; }
    umask 077
    mkdir -p "$GUI_DIR" || return 1
    rm -f "$GUI_DIR/handoff" "$GUI_DIR/handoff.jar" "$GUI_DIR/error" "$CONSOLE_JAR" \
        "$RUN_DIR/console_login.json" "$GUI_DIR/actions" || return 1
    if [ "${GUI_SCREEN:-}" = class ]; then _initial=classes; else _initial=menu; fi
    printf '%s\n' "$_initial" > "$GUI_DIR/mode" || return 1
    printf 'message=מתחבר לשרת|ממתין למצב תחנה מאומת\n' > "$GUI_DIR/state" || return 1
    mkfifo "$GUI_DIR/actions" || return 1
    trap gui_cleanup EXIT
    trap 'exit 1' HUP INT TERM
    export FONTCONFIG_FILE=/etc/imagesrv/fonts.conf
    if [ "${GUI_SCREEN:-}" = cloner ]; then
        cloner_gui_state_loop & GUI_STATE_PID=$!
    else
        gui_state_loop & GUI_STATE_PID=$!
    fi
    set --
    [ "${GUI_SCREEN:-}" = class ] && set -- --screen class
    [ "${GUI_SCREEN:-}" = cloner ] && set -- --screen cloner
    # shellcheck disable=SC2046 # --backend/--fb-file are words. nice: data path wins the CPU (#1493)
    /usr/bin/nice -n 10 /usr/bin/imagesrv-station-gui "$@" $(gui_backend_args) --mac "$MAC" --ip "$IP" \
        --state "$GUI_DIR/state" --auth-cmd '/usr/bin/imagesrv-kiosk --auth' > "$GUI_DIR/actions" &
    GUI_PID=$!; echo "$GUI_PID $GUI_STATE_PID" > "$GUI_DIR/station.pid"   # #1726: for gui_keep (guiparent.sh)
    gui_records < "$GUI_DIR/actions"
}
