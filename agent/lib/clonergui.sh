# clonergui.sh -- cloner GUI selection and locally authoritative state.
# POSIX sh (busybox ash).
#
# The cloning machine now has a screen (owner decision): it shows per-drawer
# progress and the graphical SMART choice -- the only one since the text menu
# of #666 was removed (owner, 11/10: GUI only). Two rules never bend:
#   * A display that will not draw NEVER stops the write. gui_cloner_parent
#     always returns 0 -- do_restore_drawers must run headless all the same.
#   * A display failure is NEVER an approval of the operator's (principle 5).
#     If the GUI vanishes while a SMART choice is pending, gui_smart_choice
#     fails and smart_ask takes the no-answer default of the colour (16/09):
#     orange writes, red skips.

# Both processes agree on one directory: the agent (gui_smart_choice) and the
# kiosk (cloner_gui_state) exchange smart-request/smart-decision here, and it
# is the same $RUN_DIR/gui that guiparent.sh and gui_main use.
GUI_DIR="${GUI_DIR:-$RUN_DIR/gui}"

gui_cloner_parent() {
    [ "$D_ROLE" = cloner ] || return 0
    gui_parent || {
        [ "${_cloner_gui_failed_logged:-0}" = 1 ] ||
            log 'cloner: native GUI unavailable -- the write runs headless'
        _cloner_gui_failed_logged=1
        return 0
    }
}

gui_restore_parent() {
    case "$D_ROLE" in
        classroom)
            gui_parent || log 'restore: native GUI unavailable -- the write runs headless'
            ;;
        cloner)
            gui_cloner_parent
            ;;
    esac
}

# #410: rate and ETA of one drawer, from the local counter (the same truth
# the record is built from). Sets _cg_rate (bytes/s) and _cg_eta (seconds);
# -1 = not measured -- 0 only when measured still (#2055 below; rule 5). The
# rate is the average since the first sample this kiosk saw with bytes on
# the drawer ($GUI_DIR/pace.<dev> = "epoch bytes"); a drawer that is not
# writing drops its anchor so the next wave starts a new average.
cloner_gui_pace() {
    # $1 = dev, $2 = state, $3 = bytes, $4 = total
    _cg_rate=-1; _cg_eta=-1; _cg_moved=
    if [ "$2" != writing ] || [ "$3" -le 0 ]; then
        rm -f "$GUI_DIR/pace.$1" "$GUI_DIR/moved.$1"; return 0
    fi
    _cg_now=$(date +%s); _cg_mb=   # #2055: the bytes last moved at _cg_moved (moved.<dev> = "epoch bytes"); still
    # for CLONER_STALL_S = rate 0, measured: "waiting for the stream". The write goes on (display only).
    [ -r "$GUI_DIR/moved.$1" ] && read -r _cg_moved _cg_mb < "$GUI_DIR/moved.$1"
    case "$_cg_moved" in ''|*[!0-9]*) _cg_mb= ;; esac
    [ "$_cg_mb" = "$3" ] || { _cg_moved=$_cg_now; printf '%s %s\n' "$_cg_now" "$3" > "$GUI_DIR/moved.$1"; }
    [ $((_cg_now - _cg_moved)) -lt "${CLONER_STALL_S:-10}" ] || { _cg_rate=0; return 0; }
    _cg_t0=; _cg_b0=
    [ -r "$GUI_DIR/pace.$1" ] && read -r _cg_t0 _cg_b0 < "$GUI_DIR/pace.$1"
    case "$_cg_t0:$_cg_b0" in
        *[!0-9:]*|:*|*:) printf '%s %s\n' "$_cg_now" "$3" > "$GUI_DIR/pace.$1"; return 0 ;;
    esac
    _cg_dt=$((_cg_now - _cg_t0)); _cg_db=$(($3 - _cg_b0))
    [ "$_cg_dt" -gt 0 ] && [ "$_cg_db" -gt 0 ] || return 0
    _cg_rate=$((_cg_db / _cg_dt))
    [ "$_cg_rate" -gt 0 ] || { _cg_rate=-1; return 0; }
    [ "$4" -gt "$3" ] && _cg_eta=$((($4 - $3) / _cg_rate))
    return 0
}

cloner_gui_state() {
    # Written by the kiosk process. The per-drawer files are the local source
    # of truth (progress.sh), so the screen keeps updating even when a server
    # report fails. Sorted/labelled by port happens in the native screen.
    _cg_next="$GUI_DIR/state.next"
    : > "$_cg_next" || return 1

    _cg_name=
    [ -f "$RUN_DIR/manifest.json" ] &&
        _cg_name=$(json_get "$RUN_DIR/manifest.json" ".name")
    case "$_cg_name" in null) _cg_name= ;; esac
    printf 'cloner=%s\n' "$_cg_name" >> "$_cg_next" || return 1
    [ -s "$RUN_DIR/server-restarted-wave" ] && printf 'server_restarted_wave=1\n' >> "$_cg_next"
    gui_header_state "$RUN_DIR/response.json" >> "$_cg_next" || return 1
    _cg_count=$(json_get "$RUN_DIR/response.json" ".drawer_count")
    case "$_cg_count" in [1-8]) printf 'drawer_count=%s\n' "$_cg_count" >> "$_cg_next" || return 1 ;; esac
    # #1304: the standby screen's connection line. A hello.state the reader
    # rejects writes nothing -- the screen then says "not known", never
    # "connected" -- and must not freeze the drawers' progress with it.
    gui_hello_state >> "$_cg_next" || :
    # #704: the operator's slot names (slotnames.sh); a failed read keeps the last file.
    slot_names_refresh || :; drawer_slots_refresh || :   # #1495: the drawer mapping, the same way
    { slot_names_records && drawer_slots_records; } >> "$_cg_next" || return 1

    _cg_any=0
    for _cg_dir in "$RUN_DIR"/targets/*/; do
        [ -d "$_cg_dir" ] || continue
        _cg_any=1
        _cg_dev=$(basename "$_cg_dir")
        _cg_port=$(disk_port "$_cg_dev"); [ -n "$_cg_port" ] || _cg_port=$(cut -d'|' -f2 "$_cg_dir/seen" 2>/dev/null)   # #1602: a pulled drawer keeps its slot
        _cg_state=$(cat "$_cg_dir/state" 2>/dev/null)
        _cg_total=$(cat "$_cg_dir/total" 2>/dev/null)
        _cg_bytes=$(target_bytes "$_cg_dev")
        _cg_error=
        [ -f "$_cg_dir/error" ] && _cg_error=$(cat "$_cg_dir/error")

        case "$_cg_port" in ''|*[!0-9]*) _cg_port=0 ;; esac
        case "$_cg_bytes" in ''|*[!0-9]*) _cg_bytes=0 ;; esac
        case "$_cg_total" in ''|*[!0-9]*) _cg_total=0 ;; esac

        # A field separator or newline inside the error would corrupt the
        # record; fold them to spaces before the value reaches the parser.
        _cg_error=$(printf '%s' "$_cg_error" | tr '\r\n|' '   ')
        # #410: fields 7-8 are rate (bytes/s) and ETA (s); -1 = not measured. #1386: 11-12 = who
        # asked to cancel ("" = nobody) and when it was done (epoch; "" = not yet).
        cloner_gui_pace "$_cg_dev" "$_cg_state" "$_cg_bytes" "$_cg_total"
        _cg_drv=''; [ ! -e "$_cg_dir/drivers.json" ] || _cg_drv=$(drivers_gui_fields "$_cg_dir/drivers.json") || return 1   # #1331: fields 13-17 (+18-20 for Linux)
        printf 'drawer=%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s%s\n' \
            "$_cg_port" "$_cg_dev" "$_cg_state" \
            "$_cg_bytes" "$_cg_total" "$_cg_error" \
            "$_cg_rate" "$_cg_eta" "$(disk_drawer "$_cg_dev" "$_cg_port")" \
            "$(cat "$_cg_dir/cancel" 2>/dev/null | tr -d '\r\n|')" "$(cat "$_cg_dir/cancelled_at" 2>/dev/null)" "$_cg_drv" >> "$_cg_next" ||
            return 1
        [ -z "$_cg_moved" ] || printf 'drawer_moved=%s|%s\n' "$_cg_dev" "$_cg_moved" >> "$_cg_next" || return 1   # #2055: the screen's age
        # #1907: done, not verified -- the server's check list did not come down (readback.sh): orange on the row
        [ "$(cat "$_cg_dir/readback_why" 2>/dev/null)" != list_not_fetched ] || printf 'drawer_rb=%s|list_not_fetched\n' "$_cg_dev" >> "$_cg_next" || return 1
    done

    stream_beep_tick   # #2067: one stream sample across all writing drawers, in the background
    # Idle -- no round targets yet: what is plugged in, not a bare "waiting".
    if [ "$_cg_any" = 0 ]; then
        cloner_cdisks "$(list_disks)" >> "$_cg_next" || return 1
    fi
    if command -v disk_used_records > /dev/null 2>&1; then disk_used_records "$(list_disks)" >> "$_cg_next" || return 1; fi   # #2030: cused= -- size and bytes in use per disk; none = "not measured" (guibridge.sh loads it, a test pins that)

    if [ -f "$GUI_DIR/smart-request" ]; then
        # ‏smart-request הוא nonce|port|verdict|reason|model|serial (16/10: _cg_reason נושא את שלושתם). ה-nonce **זורם
        # ל-GUI** ומצויר עם ה-prompt, כדי שהקליק יחזיר אותו: הוא הזהות
        # הבלתי-משתנה של הבקשה שצוירה. בלי זה קליק מושהה על אותו port
        # (דיסק שנתבקש שוב, nonce חדש) היה מאושר לבקשה הלא-נכונה.
        IFS='|' read -r _cg_nonce _cg_sport _cg_verdict _cg_reason \
            < "$GUI_DIR/smart-request" || return 1
        printf 'smart_prompt=%s|%s|%s|%s\n' \
            "$_cg_nonce" "$_cg_sport" "$_cg_verdict" "$_cg_reason" >> "$_cg_next" ||
            return 1
    fi

    gui_ask_state >> "$_cg_next" || return 1   # #1808/#1898: a drawer's question (drawergate.sh, askgui.sh)
    # #410: when this snapshot was taken -- the screen shows its age.
    printf 'updated=%s\n' "$(date +%s)" >> "$_cg_next" || return 1
    mv "$_cg_next" "$GUI_DIR/state"
}

# cdisk=port|dev|model|size|smart|cause|link|link_max|crc|drawer|conn|mark|hw (#432/#1366; #1798; #1921; not the build's disk=, #688) of
# the disks in $1: all on the idle screen, the drawers on the restore screen (lcpull.sh).
# #867: by disk number (sda can be disk 3), a disk with no slot last; the prefix after the sort.
cloner_cdisks() {
    _cg_idle="$GUI_DIR/cdisk.unsorted"
    : > "$_cg_idle" || return 1
    disk_failures_refresh || :   # #874: זיכרון השרת מה-hello האחרון; כשל = הקובץ הקודם
    # ‏#1366: סף ה-CRC המוחלט מה-hello (הגדרה בקונסולה); ריק = שרת ישן — לא צובעים.
    _cg_crcmax=$(json_get "$RUN_DIR/response.json" ".crc_warn_threshold" 2>/dev/null)
    for _cg_d in $1; do
        _cg_dport=$(disk_port "$_cg_d")
        case "$_cg_dport" in ''|*[!0-9]*) _cg_dport=0 ;; esac
        # #1495: sorted by the drawer of the mapping, "not mapped" (0) last; fields 10/11
        _cg_dn=$(disk_drawer "$_cg_d" "$_cg_dport"); _cg_last=0; [ "${_cg_dn%%|*}" = 0 ] && _cg_last=1
        _cg_sect=$(cat "$SYSROOT/sys/block/$_cg_d/size" 2>/dev/null)
        case "$_cg_sect" in ''|*[!0-9]*) _cg_sect=0 ;; esac
        _cg_dsize=$((_cg_sect * 512))
        _cg_dmodel=$(trim "$(cat "$SYSROOT/sys/block/$_cg_d/device/model" 2>/dev/null)")
        _cg_dmodel=$(printf '%s' "$_cg_dmodel" | tr '\r\n|' '   ')
        # #709: cached SMART verdict (never probes), ok->passed for the GUI.
        _cg_dsmart=$(smart_hello_field "$_cg_d")
        case "$_cg_dsmart" in ok) _cg_dsmart=passed ;; warn|fail|passed) ;; *) _cg_dsmart=unchecked ;; esac
        # ‏#867/#874: זיכרון השרת גובר על SMART -- דיסק שכשל בכתיבה (לפי סידורי
        # או חריץ) עובר SMART. השדה השישי הוא הסיבה שהשרת סיווג (cable/disk),
        # ריק כשירוק. ‏disk_failure_cause יושב ב-failmark.sh: גם השער קורא אותו.
        [ "$_cg_dsmart" != fail ] && _cg_dcause=$(disk_failure_cause "$_cg_d") && _cg_dsmart=failed_last   # #1580: red SMART wins
        case "$_cg_dsmart" in fail|warn) _cg_dcause=$(smart_field "$_cg_d" 2) ;; esac   # #1366: why F1; #1859: why orange -- field 6
        # #1366: 199 only when smartctl read it (disk_link_json's rule), over the hello's threshold
        _cg_crc=$(disk_link_json "$_cg_d" | sed -n 's/.*"crc":\([0-9][0-9]*\).*/\1/p')
        case "$_cg_crcmax" in ''|0|*[!0-9]*) _cg_crc= ;; *) [ -n "$_cg_crc" ] && [ "$_cg_crc" -gt "$_cg_crcmax" ] || _cg_crc= ;; esac
        _cg_link=$(disk_link "$_cg_d")   # #432: "<speed> <max>", null = not read -- fields 7/8
        printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' "$_cg_last" "${_cg_dn%%|*}" "$_cg_dport" "$_cg_d" \
            "$_cg_dmodel" "$_cg_dsize" "$_cg_dsmart" "$_cg_dcause" "${_cg_link% *}" "${_cg_link#* }" "$_cg_crc" "$_cg_dn" \
            "$(writemark_state "$_cg_d")" "$(command -v hw_note_he >/dev/null 2>&1 && hw_note_he "$_cg_d" "crc link_slow")" \
            >> "$_cg_idle" || return 1   # #1798: field 12 -- incomplete|clean|unchecked; #1921: 13 -- the drawer's hw note (crc/link have 7-9)
    done
    sort -t'|' -k1,1n -k2,2n -k3,3n "$_cg_idle" > "$_cg_idle.sorted" || return 1
    sed 's/^[01]|[0-9]*|/cdisk=/' "$_cg_idle.sorted"
}

cloner_gui_state_loop() {
    while :; do
        rm -f "$GUI_DIR/disk-event"   # #1971: consumed kernel inventory notification
        if lc_gui_active; then   # #1322: the boot choice / local clone own the screen
            lc_gui_state || printf 'native-gui: local clone state could not be verified\n' >&2
        elif ! cloner_gui_state; then
            printf 'native-gui: cloner state could not be verified\n' >&2
        fi
        _cg_wait=0
        while [ "$_cg_wait" -lt 10 ] && [ ! -e "$GUI_DIR/disk-event" ]; do sleep 0.1; _cg_wait=$((_cg_wait + 1)); done
    done
}

gui_smart_choice() {
    # $1=disk, $2=verdict, $3=reason. stdout is the decision (replace/rescue/
    # skip). Returns non-zero when the GUI cannot answer -- the caller then
    # takes the no-answer default (smart_ask). A display failure is never an
    # approval.
    #
    # ‏**כל בקשה נושאת nonce ייחודי**, וההחלטה מתקבלת רק אם היא כבולה
    # לאותו nonce. בלי זה קליק מושהה/כפול על דיסק אחד היה מאושר לדיסק
    # הבא -- ובמקרה הגרוע `rescue` ישן היה מאשר כתיבה לדיסק החשוד הלא-
    # נכון, בדיוק מה ש-SMART נועד למנוע (עיקרון 5/7). ה-nonce מגובה-קובץ
    # מונוטוני, כי הפונקציה רצה בתת-מעטפת של `$( )` ומשתנה מקומי לא היה
    # שורד בין דיסקים.
    [ "$D_ROLE" = cloner ] || return 1
    [ -n "${_gui_pid:-}" ] && kill -0 "$_gui_pid" 2>/dev/null || return 1

    _gsc_port=$(disk_port "$1")
    case "$_gsc_port" in ''|*[!0-9]*) return 1 ;; esac

    _gsc_seq=$(cat "$GUI_DIR/smart-seq" 2>/dev/null)
    case "$_gsc_seq" in ''|*[!0-9]*) _gsc_seq=0 ;; esac
    _gsc_seq=$((_gsc_seq + 1))
    echo "$_gsc_seq" > "$GUI_DIR/smart-seq" || return 1
    _gsc_nonce="$_gsc_seq-$_gsc_port"; _gsc_t=0

    rm -f "$GUI_DIR/smart-decision"
    printf '%s|%s|%s|%s|%s|%s\n' "$_gsc_nonce" "$_gsc_port" "$2" "$3" "$(trim "$(cat "$SYSROOT/sys/block/$1/device/model" 2>/dev/null)" | tr '|\n' '  ')" \
        "$(disk_serial "$1" | tr '|\n' '  ')" > "$GUI_DIR/smart-request.next" || return 1   # 16/10: model, serial -- the SMART window's details
    mv "$GUI_DIR/smart-request.next" "$GUI_DIR/smart-request" || return 1

    while kill -0 "$_gui_pid" 2>/dev/null && [ -e "$GUI_DIR/smart-request" ] && [ "$_gsc_t" -lt "${WAIT_SMART_GUI_S:-1800}" ] && [ ! -e "$RUN_DIR/session-closed" ]; do   # rc2 17/10: a closed round ends the question
        attended_power_check
        if [ -s "$GUI_DIR/smart-decision" ]; then
            IFS='|' read -r _gsc_dnonce _gsc_dec < "$GUI_DIR/smart-decision" || {
                rm -f "$GUI_DIR/smart-request" "$GUI_DIR/smart-decision"
                return 1
            }
            # רק החלטה שכבולה ל-nonce של הבקשה הזאת, ורק ערך חוקי. החלטה
            # ישנה/לא-תואמת/פגומה נמחקת וההמתנה נמשכת -- לא משוחררת.
            if [ "$_gsc_dnonce" = "$_gsc_nonce" ]; then
                case "$_gsc_dec" in
                    replace|rescue|skip)
                        # #989 item 2: the server settles who answered first
                        # (console or this screen) and returns the winner.
                        # Before the request is cleared: the beat still asks.
                        if command -v attended_claim >/dev/null 2>&1; then
                            _gsc_dec=$(attended_claim "$_gsc_nonce" "$_gsc_dec")
                        fi
                        rm -f "$GUI_DIR/smart-request" "$GUI_DIR/smart-decision"
                        printf '%s\n' "$_gsc_dec"
                        return 0
                        ;;
                esac
            fi
            rm -f "$GUI_DIR/smart-decision"
        fi
        sleep 1; _gsc_t=$((_gsc_t + 1))
    done

    # The GUI died (its bridge took the request, #1726), or no answer in WAIT_SMART_GUI_S (17/10): clear the handshake; smart_ask takes the no-answer default
    [ "$_gsc_t" -lt "${WAIT_SMART_GUI_S:-1800}" ] || printf 'native GUI: no SMART answer in %ss\n' "$_gsc_t" >&2
    rm -f "$GUI_DIR/smart-request" "$GUI_DIR/smart-decision"
    return 1
}

# ‏#695: קובע את $_disks לרשימת המגירות שנבחרו לסבב. סבב עם בחירה
# (‏session.target_ports הוא מערך) כותב **רק** לפורטים האלה, וכל פורט
# חייב למפות לדיוק דיסק אחד — אחרת die_local, בלי נפילה ל"כל הדיסקים"
# (עיקרון 4/5/7). null אינו אישור לכתוב לכל המגירות (#1410).
# מריץ בשל המעטפת של הסוכן (לא בתת-מעטפת), כדי ש-die_local יעצור באמת.
resolve_target_drawers() {
    _rt_ports=$(json_get "$RESP" ".session.target_ports")
    if [ "$_rt_ports" = "null" ]; then
        die_local "room target selection is missing"
    fi
    _disks=""
    _rt_list=$(jq -er '.session.target_ports
        | select(type == "array" and length > 0)
        | .[] | select(type == "number" and floor == .)' "$RESP") \
        || die_local "room target selection is missing or malformed"
    for _rt_port in $_rt_list; do
        _rt_match=""
        for _rt_dev in $(list_drawers); do
            [ "$(disk_port "$_rt_dev")" = "$_rt_port" ] && _rt_match="$_rt_match $_rt_dev"
        done
        # shellcheck disable=SC2086 # word-splitting the match list is intended
        set -- $_rt_match
        [ "$#" -eq 1 ] \
            || die_local "selected drawer $_rt_port does not map to exactly one disk"
        _disks="$_disks $1"
    done
    [ -n "$_disks" ] || die_local "the room selected no writable drawers"
}
