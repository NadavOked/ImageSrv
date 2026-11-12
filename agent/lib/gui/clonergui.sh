# clonergui.sh -- cloner GUI selection and locally authoritative state.
# POSIX sh (busybox ash).
#
# The cloning machine now has a screen (owner decision): it shows per-drawer
# progress and a drawer's questions (askgui.sh). The SMART choice that was
# drawn here is gone (#2044): red is never written (#1732), orange is written
# without a question (#2042). One rule never bends:
#   * A display that will not draw NEVER stops the write. gui_cloner_parent
#     always returns 0 -- do_restore_drawers must run headless all the same.

# Both processes agree on one directory: the agent and the kiosk
# (cloner_gui_state) exchange the state and the questions here, and it is the
# same $RUN_DIR/gui that guiparent.sh and gui_main use.
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

# #2046 (Nadav 24/10): the read-back's bar. fanout --readback -p keeps "<done> <total>" in
# $RUN_DIR/targets/<dev>/rb_prog (rbprog.sh). Display only -- the verdict is readback.sh's.
gui_rb_fields() {   # $1 = dev. Sets _cg_rbd/_cg_rbt; 1 = no bar (no file, or a line that is not two numbers)
    _cg_rbd=; _cg_rbt=
    [ -r "$RUN_DIR/targets/$1/rb_prog" ] || return 1
    read -r _cg_rbd _cg_rbt < "$RUN_DIR/targets/$1/rb_prog" 2>/dev/null
    case "$_cg_rbd:$_cg_rbt" in *[!0-9:]*|:*|*:) _cg_rbd=; _cg_rbt=; return 1 ;; esac
    [ "$_cg_rbt" -gt 0 ] && [ "$_cg_rbd" -le "$_cg_rbt" ] || { _cg_rbd=; _cg_rbt=; return 1; }
}

gui_rb_state() {
    # $1 = dev, $2 = its state. Sets _cg_rbp ("done|total", or empty), _cg_rbv (the verdict readback.sh wrote:
    # verified|sampled|unverified, or empty) and _cg_rbr (bytes/s of the check, -1 = not measured). A disk being
    # read back also gets that rate and its time left in _cg_rate/_cg_eta -- the write's counters are full by now.
    # 0 = there is a record to print.
    _cg_rbp=; _cg_rbv=; _cg_rbr=-1
    if gui_rb_fields "$1"; then
        _cg_rbp="$_cg_rbd|$_cg_rbt"
        if [ "$2" = verifying ]; then
            cloner_gui_pace "rb.$1" writing "$_cg_rbd" "$_cg_rbt"
            [ "$_cg_rate" != 0 ] || { _cg_rate=-1; _cg_eta=-1; }   # a bar that stands is "not measured", never "waiting for the stream"
            _cg_rbr=$_cg_rate
        fi
    fi
    [ "$2" = verifying ] || rm -f "$GUI_DIR/pace.rb.$1" "$GUI_DIR/moved.rb.$1"
    _cg_rbv=$(cat "$RUN_DIR/targets/$1/readback" 2>/dev/null)
    case "$_cg_rbv" in verified|sampled|unverified) ;; *) _cg_rbv= ;; esac
    [ -n "$_cg_rbp$_cg_rbv" ]
}

gui_rb_mode() {   # rb_mode=full|sample: the title says "אומתו" only for a check that read every chunk
    case "$(json_get "$RUN_DIR/response.json" ".readback_mode" 2>/dev/null)" in full) echo 'rb_mode=full' ;; *) echo 'rb_mode=sample' ;; esac
}

gui_rb_print() {   # $1 = the record's key, $2 = dev: <key>=dev|done|total|verdict|rate (done/total empty = no bar)
    printf '%s=%s|%s|%s|%s\n' "$1" "$2" "${_cg_rbp:-|}" "$_cg_rbv" "$_cg_rbr"
}

# #1843 (mockup 07/11): the server's expected end of this wave, from the hello the write started on
# (session.forecast) -- forecast=<clock 0|1>|<end epoch>|<no_history>, e.g. "1|1762512720|" or "1||no_history".
# clock 1 = the server proved its clock synced (#2144): only then does the screen draw an HH:MM at all.
# Nothing printed = nothing said: an older server, a wave that is not a room wave, a record that is not an object.
gui_forecast_record() {
    _cg_fc=$(json_get "$RUN_DIR/response.json" '.session.forecast | if type != "object" then empty else
        [(if .clock == true then 1 else 0 end), (if (.ends_at | type) == "number" then (.ends_at | floor) else "" end),
         (if .why == "no_history" then "no_history" else "" end)] | map(tostring) | join("|") end')
    case "$_cg_fc" in [01]\|*) printf 'forecast=%s\n' "$_cg_fc" ;; esac
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
    printf 'cloner=%s\n' "$(printf '%s' "$_cg_name" | tr '\r\n|' '   ')" >> "$_cg_next" || return 1   # #1635: one record, as guistate.sh
    [ -s "$RUN_DIR/server-restarted-wave" ] && printf 'server_restarted_wave=1\n' >> "$_cg_next"
    gui_header_state "$RUN_DIR/response.json" >> "$_cg_next" || return 1
    gui_rb_mode >> "$_cg_next" || return 1   # #2046
    gui_forecast_record >> "$_cg_next" || return 1   # #1843
    _cg_count=$(json_get "$RUN_DIR/response.json" ".drawer_count")
    case "$_cg_count" in [1-8]) printf 'drawer_count=%s\n' "$_cg_count" >> "$_cg_next" || return 1 ;; esac
    # #1304: the standby screen's connection line. A hello.state the reader
    # rejects writes nothing -- the screen then says "not known", never
    # "connected" -- and must not freeze the drawers' progress with it.
    gui_hello_state >> "$_cg_next" || :
    # #704: the operator's slot names (slotnames.sh); a failed read keeps the last file.
    slot_names_refresh || :; drawer_slots_refresh || :   # #1495: the drawer mapping, the same way
    if command -v disk_labels_refresh > /dev/null 2>&1; then disk_labels_refresh || :; fi   # #1857: the sets of the disks (disklabels.sh); a failed read keeps the last file
    { slot_names_records && drawer_slots_records; } >> "$_cg_next" || return 1
    fw_warn_record >> "$_cg_next" || :   # #2080: fw_warn=<n>|<text>, the moved-connection warning; advice, never freezes the drawers

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
        gui_rb_state "$_cg_dev" "$_cg_state" && _cg_rb=1 || _cg_rb=0   # #2046: after the write's pace -- a read-back's rate replaces it
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
        [ "$_cg_rb" = 0 ] || gui_rb_print drawer_rbp "$_cg_dev" >> "$_cg_next" || return 1   # #2046: the read-back's bar and verdict
    done

    stream_beep_tick   # #2067: one stream sample across all writing drawers, in the background
    stream_net_record >> "$_cg_next" || :   # #2067: net=lost|back, the screen's red and green bars (mockup 08); a bar that cannot be written never freezes the drawers
    # Idle -- no round targets yet: what is plugged in, not a bare "waiting".
    if [ "$_cg_any" = 0 ]; then
        cloner_cdisks "$(list_disks)" >> "$_cg_next" || return 1
        # #1859 (mockup 1859-smart-table): csmart= / csmartrow= -- every attribute, for the table behind a click. A table that cannot be built leaves the disk without its "all attributes" line
        if command -v smart_tables_state > /dev/null 2>&1; then smart_tables_state "$(list_disks)" >> "$_cg_next" || :; fi
    fi
    if command -v disk_used_records > /dev/null 2>&1; then disk_used_records "$(list_disks)" >> "$_cg_next" || return 1; fi   # #2030: cused= -- size and bytes in use per disk; none = "not measured" (guibridge.sh loads it, a test pins that)

    if command -v disk_label_records > /dev/null 2>&1; then disk_label_records "$(list_disks)" >> "$_cg_next" || :; fi   # #1857: cdisk_label=dev|text -- "קוריאים · xx-7" beside the drawer; no set = no record, the screen as it was (guibridge.sh loads the library, a test pins that)
    gui_ask_state >> "$_cg_next" || return 1   # #1808/#1898: a drawer's question (drawergate.sh, askgui.sh)
    # #410: when this snapshot was taken -- the screen shows its age.
    printf 'updated=%s\n' "$(date +%s)" >> "$_cg_next" || return 1
    mv "$_cg_next" "$GUI_DIR/state"
}

# cdisk=port|dev|model|size|smart|cause|link|link_max|crc|drawer|conn|mark|hw|f1date|f1src|f1smart (#432/#1366; #1798; #1921; #1858; not the build's disk=, #688) of
# the disks in $1: all on the idle screen, the drawers on the restore screen (lcpull.sh).
# #867: by disk number (sda can be disk 3), a disk with no slot last; the prefix after the sort.
cloner_cdisks() {
    _cg_idle="$GUI_DIR/cdisk.unsorted"
    : > "$_cg_idle" || return 1
    disk_failures_refresh || :   # #874: זיכרון השרת מה-hello האחרון; כשל = הקובץ הקודם
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
        _cg_link=$(disk_link "$_cg_d")   # #432: "<speed> <max>", null = not read -- fields 7/8
        # #1858: "נצפה F1" by serial -- the date and source (14/15), and the disk's own SMART beneath the red (16; empty = not known)
        _cg_f1=$(command -v disk_f1_observed >/dev/null 2>&1 && disk_f1_observed "$_cg_d") || _cg_f1="|"
        _cg_f1u=$(command -v disk_f1_under >/dev/null 2>&1 && [ "$_cg_f1" != "|" ] && disk_f1_under "$_cg_d")
        case "$_cg_f1u" in ok) _cg_f1u=passed ;; esac
        printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' "$_cg_last" "${_cg_dn%%|*}" "$_cg_dport" "$_cg_d" \
            "$_cg_dmodel" "$_cg_dsize" "$_cg_dsmart" "$_cg_dcause" "${_cg_link% *}" "${_cg_link#* }" "" "$_cg_dn" \
            "$(writemark_state "$_cg_d")" "$(command -v hw_note_he >/dev/null 2>&1 && hw_note_he "$_cg_d" "link_slow")" "$_cg_f1" "$_cg_f1u" \
            "$(command -v hw_words >/dev/null 2>&1 && hw_words "$_cg_d" | tr '\n|' '  ')" \
            >> "$_cg_idle" || return 1   # #1798: field 12 -- incomplete|clean|unchecked; #1921: 13 -- the drawer's hw note (the link has 7-8; 9, #1366's absolute CRC, is empty since #2846); #2848: 17 -- the hw words
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
