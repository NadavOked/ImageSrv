# usbclone.sh -- #1914: the clone stick. The machine booted from a USB stick
# (tools/build_usb_clone.sh) with imagectl.mode=usbclone on the kernel line --
# the only imagectl.* word there (principle 2). No server, no hello, no DHCP:
# init hands over before its network loop, and this file takes the agent
# straight to the local clone screen (localclone.sh, lc_*). POSIX sh.
#
# Three rules:
#   * The stick never shows as a source or a target. It is found by the
#     labels the build gives its partitions (USB_LABEL, USB_DATA_LABEL), never
#     by a name; every disk that carries one is left out of the local clone's
#     inventory (lcinv.sh lc_is_boot_media). Not found = nothing is offered (principle 5): a stick we
#     could not tell apart from the disks is a stick we could write over.
#   * Nothing goes to a server. The functions that would talk to one are
#     replaced, for this boot only, inside usbclone_main -- never at load,
#     where they would change the other modes.
#   * What the server would have sent is a fixed default here (Nadav 20/10),
#     overridden by settings.json on the stick's data partition when it is
#     valid. A value that is not valid is refused aloud, never used.

USB_LABEL="${USB_LABEL:-IMGSRV_USB}"
USB_DATA_LABEL="${USB_DATA_LABEL:-IMGSRV_DATA}"
USB_DATA_DIR="${USB_DATA_DIR:-$RUN_DIR/usbdata}"
#: the stick appears a few seconds after init (USB enumeration); the ceiling
USB_FIND_S="${USB_FIND_S:-20}"
# Nadav 20/10: sample read-back, 20GB free (#1354 twin), CRC 500 (#1366), 60C (#1859)
USB_DEF_MIN_FREE_GB=20
USB_DEF_READBACK=sample
USB_DEF_CRC_WARN=500
USB_DEF_TEMP_WARN_C=60

usb_part_label() {   # $1 = block device name (sdb1). Its filesystem label, or nothing.
    blkid -s LABEL -o value "$SYSROOT/dev/$1" 2>/dev/null
}

usb_media_disks() {
    # The disks that carry a partition labelled USB_LABEL or USB_DATA_LABEL,
    # one per line. Whole-disk labels count too (a stick written without a
    # partition table). Read from sysfs: a partition is a directory of its disk.
    for _um_d in $(list_disks); do
        for _um_p in "$SYSROOT/sys/block/$_um_d/$_um_d"* "$SYSROOT/sys/block/$_um_d"; do
            [ -e "$_um_p" ] || continue
            case "$(usb_part_label "${_um_p##*/}")" in
                "$USB_LABEL"|"$USB_DATA_LABEL") echo "$_um_d"; break ;;
            esac
        done
    done
}

usb_find_media() {
    # Writes $RUN_DIR/usb-media.disks. 0 = found; 1 = not within USB_FIND_S.
    _uf_n=0
    while :; do
        _uf_m=$(usb_media_disks)
        if [ -n "$_uf_m" ]; then
            printf '%s\n' "$_uf_m" > "$RUN_DIR/usb-media.disks"
            log "usbclone: boot media on $(echo "$_uf_m" | tr '\n' ' ')-- never a source or a target"
            return 0
        fi
        [ "$_uf_n" -lt "$USB_FIND_S" ] || return 1
        sleep 1; _uf_n=$((_uf_n + 1))
    done
}

usb_data_state_read() {   # the kiosk's view of usb_data_mount's answer (usbset.sh)
    USB_DATA_STATE=$(cat "$RUN_DIR/usb.data" 2>/dev/null); : "${USB_DATA_STATE:=none}"
}

usb_settings_path() {   # where the settings live: the stick, or this session only
    if [ "${USB_DATA_STATE:-none}" = mounted ]; then echo "$USB_DATA_DIR/settings.json"; else echo "$RUN_DIR/usb-settings.json"; fi
}

usb_data_mount() {
    # The data partition, read-write, for settings.json and the reports.
    # USB_DATA_STATE = mounted|none (no such partition)|failed -- also in $RUN_DIR/usb.data.
    USB_DATA_STATE=none; _usb_data_mount; _ud_rc=$?
    echo "$USB_DATA_STATE" > "$RUN_DIR/usb.data"; return "$_ud_rc"
}

_usb_data_mount() {
    for _ud_d in $(cat "$RUN_DIR/usb-media.disks" 2>/dev/null); do
        for _ud_p in "$SYSROOT/sys/block/$_ud_d/$_ud_d"*; do
            [ -e "$_ud_p" ] || continue
            [ "$(usb_part_label "${_ud_p##*/}")" = "$USB_DATA_LABEL" ] || continue
            mkdir -p "$USB_DATA_DIR"
            if mount -t vfat -o rw,noatime,flush "$SYSROOT/dev/${_ud_p##*/}" "$USB_DATA_DIR" 2>> "$LOG_FILE"; then
                USB_DATA_STATE=mounted; log "usbclone: data partition ${_ud_p##*/} mounted"; return 0
            fi
            USB_DATA_STATE=failed; log "usbclone: data partition ${_ud_p##*/} did not mount -- settings for this session only"
            return 1
        done
    done
    log "usbclone: no data partition ($USB_DATA_LABEL) -- settings for this session only"
    return 1
}

_usb_int() {   # $1 = value, $2 = min, $3 = max. 0 = a whole number in range, no leading zero.
    case "$1" in 0) [ "$2" -le 0 ]; return ;; ''|0*|*[!0-9]*|??????????*) return 1 ;; esac
    [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]
}

usb_settings_load() {
    # Sets USB_MIN_FREE_GB USB_READBACK USB_CRC_WARN USB_TEMP_WARN_C and
    # USB_NOTICE (what the operator must be told; "" = nothing). The defaults,
    # then settings.json field by field: a field that is there and not valid
    # is refused by name and the default stays.
    USB_MIN_FREE_GB=$USB_DEF_MIN_FREE_GB; USB_READBACK=$USB_DEF_READBACK
    USB_CRC_WARN=$USB_DEF_CRC_WARN; USB_TEMP_WARN_C=$USB_DEF_TEMP_WARN_C; USB_NOTICE=""
    _us_f=$(usb_settings_path)
    [ "${USB_DATA_STATE:-none}" = mounted ] \
        || USB_NOTICE="אין מחיצת נתונים על הדיסק-און-קי — ההגדרות לסשן הזה בלבד, ולא יישמרו"
    [ -f "$_us_f" ] || return 0
    jq -e 'type == "object"' "$_us_f" > /dev/null 2>&1 \
        || { USB_NOTICE="${USB_NOTICE:+$USB_NOTICE · }settings.json אינו JSON תקין — נדחה, ברירות המחדל בשימוש"; log "usbclone: $USB_NOTICE" >&2; return 0; }
    _us_bad=""
    _us_v=$(jq -r '.min_free_gb // empty' "$_us_f")
    if [ -n "$_us_v" ]; then _usb_int "$_us_v" 0 500 && USB_MIN_FREE_GB=$_us_v || _us_bad="$_us_bad min_free_gb=$_us_v"; fi
    _us_v=$(jq -r '.readback_mode // empty' "$_us_f")
    case "$_us_v" in '') ;; sample|full) USB_READBACK=$_us_v ;; *) _us_bad="$_us_bad readback_mode=$_us_v" ;; esac
    _us_v=$(jq -r '.crc_warn_threshold // empty' "$_us_f")
    if [ -n "$_us_v" ]; then _usb_int "$_us_v" 0 1000000 && USB_CRC_WARN=$_us_v || _us_bad="$_us_bad crc_warn_threshold=$_us_v"; fi
    _us_v=$(jq -r '.temp_warn_c // empty' "$_us_f")
    if [ -n "$_us_v" ]; then _usb_int "$_us_v" 30 100 && USB_TEMP_WARN_C=$_us_v || _us_bad="$_us_bad temp_warn_c=$_us_v"; fi
    _us_mc=0; usb_map_json > /dev/null || _us_mc=$?   # #1962 (usbmap.sh): 2 = this model's list is not valid
    [ "$_us_mc" != 2 ] || _us_bad="$_us_bad drawer_slots[$(usb_model)]"
    _us_mc=0; usb_names_json > /dev/null || _us_mc=$?   # #2001: this model's names, the same way
    [ "$_us_mc" != 2 ] || _us_bad="$_us_bad slot_names[$(usb_model)]"
    if [ -n "$_us_bad" ]; then
        USB_NOTICE="${USB_NOTICE:+$USB_NOTICE · }ערכים לא תקינים ב-settings.json נדחו (ברירת המחדל בשימוש):$_us_bad"; log "usbclone: $USB_NOTICE" >&2   # stderr: usbset_now reads this function's callers through $( )
    fi
}

usb_answer() {
    # The answer a server would have given, written where every reader of the
    # last hello looks ($RUN_DIR/response.json). schema 1, role cloner.
    # #1962: + the drawer_slots in force (usbmap.sh usb_map_live: this model's, else -- #2001 --
    # every connection with a disk, in order), as a server's hello; with none (no disk), the
    # last list in force goes (drawer_slots_refresh keeps a file otherwise).
    _ua_ds=$(usb_map_live) || _ua_ds=null
    jq -n --argjson ds "$_ua_ds" --arg v "${AGENT_VERSION:-unknown}" --argjson mf "$USB_MIN_FREE_GB" --arg rb "$USB_READBACK" \
        --argjson crc "$USB_CRC_WARN" --argjson temp "$USB_TEMP_WARN_C" --arg st "${USB_DATA_STATE:-none}" \
        '{schema: 1, known: true, role: "cloner", task: null, session: null, name: "USB",
          server_name: "דיסק-און-קי ImageSrv", server_version: null, tz: null,
          min_free_gb: $mf, readback_mode: $rb, crc_warn_threshold: $crc, smart_learned: [],
          usb: {version: $v, data: $st, temp_warn_c: $temp}} + (if $ds == null then {} else {drawer_slots: $ds} end)' \
        > "$RUN_DIR/response.json" || return 1
    if [ "$_ua_ds" = null ]; then rm -f "$RUN_DIR/drawer_slots"; else drawer_slots_refresh; fi
    usb_drawer_names   # #2001: the names of this model's connections, for every screen
}

lc_reboot() {
    # The USB stick's result screen: "הפעל מחדש" (Nadav 20/10). The person asked.
    _lc_req run/notice "מפעיל מחדש…"; log "local clone: reboot -- operator request"
    if [ "${IMAGECTL_TEST:-0}" = 1 ]; then echo TEST-REBOOT; return 0; fi
    umount "$USB_DATA_DIR" 2>/dev/null; sync; reboot -f
}

usb_offline() {
    # This boot only: everything that would reach a server says no, at once.
    send_hello() { return 1; }
    http_get() { log "usbclone: no server -- GET $1 not sent"; return 1; }
    http_post_json() { log "usbclone: no server -- POST $1 not sent"; return 1; }
    http_get_stream() { log "usbclone: no server -- GET $1 not sent"; return 1; }
    hello_post() { return 1; }
    trace_step() { return 0; }
    attended_start() { :; }
    attended_hello() { return 1; }
    lc_round_calls() { return 1; }
    lc_power_check() { :; }
    # no Wake-on-LAN to arm: nothing powers a stick machine on from afar
    safe_poweroff() {
        POWEROFF_WHY=""; rm -f "$RUN_DIR/power.fail"
        if [ -n "${2:-}" ]; then "$2" || return 1; fi
        log "${1:-power}: powering off (USB, no Wake-on-LAN)"
        if [ "${IMAGECTL_TEST:-0}" = 1 ]; then echo "TEST-POWEROFF"; return 0; fi
        umount "$USB_DATA_DIR" 2>/dev/null; sync; poweroff -f
        echo "המחשב לא כובה: פקודת הכיבוי חזרה. כבו אותו בכפתור ההפעלה." > "$RUN_DIR/power.fail"
        POWEROFF_WHY=poweroff; return 1
    }
}

usb_stop() {
    # $1 = why (Hebrew, the failure screen), $2 = the same in English for the
    # text console, $3 = nogui when the GUI itself is what failed. Nothing is
    # offered; the reason stays on the screen -- never a black one (Nadav 17/10).
    # The text console has no Hebrew font, so its words are English (VCLONE-2,
    # ESXi BIOS + VMware SVGA, 20/10: no framebuffer, the GUI never came up).
    log "usbclone: $1 -- no clone is offered"
    [ "${3:-}" = nogui ] || { command -v fail_fatal >/dev/null 2>&1 && fail_fatal "$1" || :; }
    _us_dg=$(cat "$RUN_DIR/usb.diag" 2>/dev/null)
    printf '\033[2J\033[H\n  ImageSrv USB %s -- local clone\n\n  %s\n\n  Nothing was written to any disk. Power the machine off with its button.\n%s' \
        "${AGENT_VERSION:-unknown}" "$2" "${_us_dg:+  A diagnostic report was saved on the USB stick: $_us_dg
}"
    [ "${IMAGECTL_TEST:-0}" = 1 ] && { echo "TEST-USBSTOP: $1"; exit 87; }
    while :; do sleep 3600; done
}

#: the words when the screen could not be drawn at all (no framebuffer, no GPU driver)
USB_NOGUI="The clone screen could not start: this machine gave no graphics display (no /dev/fb0) -- its display adapter got no working driver. On a VM, boot it in UEFI mode."

usb_fb_wait() {
    # #1937 (HP 6005, Radeon HD 4200, 21/10): no framebuffer, and the GUI came up anyway --
    # drawing into RAM (guibridge.sh: the headless cloner's mem backend, #835), behind the
    # boot log. On the stick a person is always at the screen: no fb0 = say so, in its
    # place. 0 = fb0 is there (waited up to USB_FB_WAIT_S for a late driver); 1 = it is not.
    _fw_i=0
    until [ -e "$SYSROOT/sys/class/graphics/fb0" ]; do
        [ "$_fw_i" -lt "${USB_FB_WAIT_S:-10}" ] || return 1
        sleep 1; _fw_i=$((_fw_i + 1))
    done
}

usbclone_main() {
    # Never returns.
    usb_offline
    SERVER=""; D_ROLE=cloner; D_SCHEMA=1; D_KNOWN=true; D_MODE=usbclone; D_SESSION_STATE=none
    for _uc_n in "$SYSROOT"/sys/class/net/*; do
        [ "${_uc_n##*/}" = lo ] || { MAC=$(cat "$_uc_n/address" 2>/dev/null); break; }
    done
    mkdir -p "$RUN_DIR/targets" "$LC"
    log "usbclone: ImageSrv USB ${AGENT_VERSION:-unknown} -- local clone only, no server"
    usb_find_media || usb_stop "לא זוהה הדיסק-און-קי שממנו עלה המחשב ($USB_LABEL). כדי שלא ייכתב עליו, השיכפול לא נפתח" \
        "The USB stick this machine started from was not found (label $USB_LABEL). So that nothing can write over it, no clone is offered."
    usb_data_mount || :
    usb_ctrl_mode || :   # the controller's mode: IDE is said on the screen (Nadav 20/10)
    usb_diag boot || :   # every boot: what the machine has, before any screen (college 20/10)
    USB_SURVEY_NOTICE=""; usb_survey_detect || :; usb_survey || :   # the survey stick only (usbsurvey.sh); never stops the clone
    usb_settings_load; usb_answer || usb_stop "לא הצלחנו לכתוב את הגדרות ברירת המחדל" "The default settings could not be written."
    printf '%s\n' "$USB_NOTICE${USB_NOTICE:+${USB_SURVEY_NOTICE:+ · }}$USB_SURVEY_NOTICE" > "$RUN_DIR/usb.notice"
    RESP="$RUN_DIR/response.json"; min_free_take "$RESP"
    usb_fb_wait || usb_stop "אין מסך גרפי (אין /dev/fb0) — מתאם התצוגה לא קיבל דרייבר" "$USB_NOGUI" nogui
    gui_cloner_parent
    lc_gui_alive || usb_stop "מסך השיכפול לא עלה" "$USB_NOGUI" nogui
    cut -d' ' -f1 "$SYSROOT/proc/uptime" > "$RUN_DIR/usb.gui_up" 2>/dev/null; usb_diag screen || :   # the survey's boot time
    power_watch_start   # #1918: the power button, with this boot's safe_poweroff (usb_offline)
    while :; do
        lc_session
        log "usbclone: the local screen ended (${LC_END:-}) -- back to it"
        lc_gui_alive || gui_parent || usb_stop "מסך השיכפול נסגר ולא עלה שוב" "$USB_NOGUI" nogui
        [ "${IMAGECTL_TEST:-0}" = 1 ] && [ -n "${IMAGECTL_TEST_ONCE:-}" ] && exit 0
        sleep 1
    done
}
