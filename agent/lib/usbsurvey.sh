# usbsurvey.sh -- #1914/#1921: the survey stick (Nadav 20/10, night). A stick
# built with `build_usb_clone.sh --survey` carries the marker /imagesrv/survey
# on its boot partition (IMGSRV_USB) -- not a kernel word: the kernel line
# stays imagectl.mode=usbclone alone (principle 2). On that stick only, every
# boot keeps the SMART of every disk that is not the stick on IMGSRV_DATA,
# survey/<stamp>-<drawer>-<hash>.txt, to build and test the SMART tables
# (#1921). The regular stick keeps nothing about disks (Nadav 20/10) and never
# reads further than the marker. POSIX sh. Sourced by the agent (load gate).
#
#   * The serial is never written: <hash> is the first 12 hex of the sha256 of
#     it -- the same disk is the same file name in two rounds. Every serial and
#     wwid the disk has (usbdiag.sh's candidates, and smartctl's own Serial
#     Number lines) is scrubbed, the identity lines (serial, WWN, EUI-64,
#     NGUID) go by structure, and then it is checked: one candidate still in
#     the text, or none known at all, and the raw output is not kept --
#     "removed, cannot verify" in its place (fail closed, as the diag).
#   * Nothing from what is ON the disk: smartctl and nvme smart-log read the
#     drive's firmware, never a partition (no label, GUID or file).
#   * A full data partition is said on the screen; it never stops the clone.

USB_SURVEY_MARK="imagesrv/survey"
#: the free space a file needs (smartctl -x is 20-60 KB, measured on HDD/SSD)
USB_SURVEY_MIN_KB="${USB_SURVEY_MIN_KB:-256}"
USB_SURVEY_RM="הוסר — לא ניתן לוודא שאין בו serial (removed: cannot verify it holds no serial)"
USB_SURVEY_FULL_HE="מחיצת הנתונים בדיסק-און-קי מלאה — נתוני ה-SMART של חלק מהדיסקים לא נשמרו. העתיקו את התיקייה survey למחשב ומחקו אותה מהדיסק-און-קי"

usb_survey_detect() {
    # $RUN_DIR/usb.survey = 1 when the boot partition carries the marker, else 0.
    # Read-only mount; one that fails is 0, said: the default keeps nothing.
    echo 0 > "$RUN_DIR/usb.survey" || return 1
    for _sd_d in $(cat "$RUN_DIR/usb-media.disks" 2>/dev/null); do
        for _sd_p in "$SYSROOT/sys/block/$_sd_d/$_sd_d"*; do
            [ -e "$_sd_p" ] || continue
            [ "$(usb_part_label "${_sd_p##*/}")" = "$USB_LABEL" ] || continue
            _sd_m="$RUN_DIR/usbboot"; mkdir -p "$_sd_m" || return 1
            mount -t vfat -o ro "$SYSROOT/dev/${_sd_p##*/}" "$_sd_m" 2>> "$LOG_FILE" \
                || { log "usbclone: the boot partition did not mount -- not read whether this is the survey stick; nothing about the disks is kept"; return 1; }
            _sd_on=0; [ -f "$_sd_m/$USB_SURVEY_MARK" ] && _sd_on=1
            umount "$_sd_m" 2>> "$LOG_FILE" || log "usbclone: the boot partition did not unmount (read-only)"
            echo "$_sd_on" > "$RUN_DIR/usb.survey"
            [ "$_sd_on" = 0 ] || log "usbclone: the survey stick -- every disk's SMART is kept on it, without its serial"
            return 0
        done
    done
    return 1
}

usb_survey_is() { [ "$(cat "$RUN_DIR/usb.survey" 2>/dev/null)" = 1 ]; }

usb_hash12() { printf '%s' "$1" | sha256sum | cut -c1-12; }   # $1 = serial

_sv_run() {   # $1 = out file, $2.. = the command. Its exit code, or "timeout".
    _sr_o="$1"; shift
    capped_run "${SMARTCTL_MAX_S:-30}" "$_sr_o" "$@"; _sr_rc=$?
    if [ "$CAPPED_TIMED_OUT" = 1 ]; then echo timeout; else echo "$_sr_rc"; fi
}

_sv_nodata() {   # $1 = rc, $2 = out. 0 = smartctl gave no SMART data (open failed, or no section)
    case "$1" in timeout) return 0 ;; ''|*[!0-9]*) return 0 ;; esac
    [ "$(($1 / 2 % 2))" -eq 1 ] && return 0
    grep -q '^=== START OF' "$2" 2>/dev/null && return 1
    return 0   # not found, or not read (#231): no data either way
}

_sv_ids() {
    # Structure first: every identity line of smartctl -x keeps its name, loses its value.
    sed -e 's/^\([[:space:]]*Serial [Nn]umber:\).*/\1 <serial>/' \
        -e 's/^\([[:space:]]*LU WWN Device Id:\).*/\1 <wwn>/' -e 's/^\([[:space:]]*Logical Unit id:\).*/\1 <wwn>/' \
        -e 's/^\(.*IEEE EUI-64:\).*/\1 <eui>/' -e 's/^\(.*NGUID:\).*/\1 <nguid>/'
}

usb_survey_assess() {
    # $1 = disk. The verdict the local clone screen shows (lcinv.sh's smart and
    # failed_last, screens_local.c smart_chip): color, chip, reason.
    _sa_v=$(smart_probe "$1"); _sa_r=$(smart_field "$1" 2)
    [ "$_sa_v" != fail ] && command -v disk_failure_cause > /dev/null 2>&1 && _sa_c=$(disk_failure_cause "$1" 2>/dev/null) \
        && { _sa_v=failed_last; _sa_r=$_sa_c; }
    case "$_sa_v:$_sa_r" in
        fail:readonly) printf 'אדום · קריאה בלבד' ;;   fail:*) printf 'אדום · SMART תקול' ;;
        warn:*) printf 'כתום · אזהרת SMART' ;;   failed_last:*) printf 'כתום · נכשל בשיכפול הקודם' ;;
        ok:*) printf 'ירוק · SMART תקין' ;;   *) printf 'אפור · SMART לא נבדק' ;;
    esac
    _sa_h=$(smart_reason_he "$_sa_r"); printf '%s (%s/%s)\n' "${_sa_h:+ · $_sa_h}" "$_sa_v" "${_sa_r:-}"
}

_sv_link() {   # $1 = disk. Its SATA link speed, from the ataN on its path.
    _sl_p=$(usb_disk_syspath "$1")
    case "$_sl_p" in */ata[0-9]*) _sl_n=${_sl_p##*/ata}; _sl_n=${_sl_n%%/*}; _dx "/sys/class/ata_link/link$_sl_n/sata_spd" ;; *) printf 'לא SATA' ;; esac
}

_sv_ctrl() {   # $1 = disk. driver, mode of the PCI controller it sits behind.
    _sc_b=$(usb_disk_syspath "$1" | tr '/' '\n' | grep -E '^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$' | tail -n 1)
    [ -n "$_sc_b" ] || { printf '%s' "$USB_NR"; return; }
    _sc_r=$(usb_pci_driver "$_sc_b"); _sc_c=$(cat "$SYSROOT/sys/bus/pci/devices/$_sc_b/class" 2>/dev/null)
    printf '%s driver=%s mode=%s' "$_sc_b" "${_sc_r:-$USB_NR}" "$(usb_ctrl_kind "$_sc_r" "$_sc_c")"
}

usb_survey_disk() {
    # $1 = disk, $2 = stamp. 0 = written; 2 = no room (said by the caller); 1 = not written.
    _sv_d=$1; _sv_w="$RUN_DIR/survey.w"; rm -rf "$_sv_w"; mkdir -p "$_sv_w" || return 1
    _sv_dev="$DEVROOT/$_sv_d"; _sv_sm="${SMARTCTL:-smartctl}"
    _sv_xrc=$(_sv_run "$_sv_w/x" "$_sv_sm" -x -n never "$_sv_dev"); _sv_src=""
    _sv_nodata "$_sv_xrc" "$_sv_w/x" && _sv_src=$(_sv_run "$_sv_w/xs" "$_sv_sm" -x -d sat -n never "$_sv_dev")
    _sv_hrc=$(_sv_run "$_sv_w/h" "$_sv_sm" -H -n never "$_sv_dev"); _sv_nrc=""
    case "$_sv_d" in nvme*) command -v nvme > /dev/null 2>&1 && _sv_nrc=$(_sv_run "$_sv_w/n" nvme smart-log "$_sv_dev") ;; esac
    # every string that names this disk: the stick's candidates, its wwid, smartctl's own serial lines
    { usb_serial_candidates "$_sv_d"; disk_serial "$_sv_d"; echo
      cat "$SYSROOT/sys/block/$_sv_d/device/wwid" 2>/dev/null
      cat "$_sv_w/x" "$_sv_w/xs" "$_sv_w/h" 2>/dev/null | sed -n 's/^[[:space:]]*Serial [Nn]umber:[[:space:]]*//p'
    } | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | awk 'length($0) >= 4 && !seen[$0]++' > "$_sv_w/ids"
    _sv_ser=$(cat "$_sv_w/x" "$_sv_w/xs" 2>/dev/null | sed -n 's/^[[:space:]]*Serial [Nn]umber:[[:space:]]*//p' | head -n 1)
    _sv_ser=$(trim "$_sv_ser"); [ -n "$_sv_ser" ] || _sv_ser=$(trim "$(cat "$SYSROOT/sys/block/$_sv_d/device/serial" 2>/dev/null)")
    if [ -n "$_sv_ser" ]; then _sv_hash=$(usb_hash12 "$_sv_ser"); else _sv_hash=noserial; fi
    case "$_sv_hash" in noserial|[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) return 1 ;; esac
    {   for _sv_s in x:"smartctl -x" xs:"smartctl -x -d sat" h:"smartctl -H" n:"nvme smart-log"; do
            [ -f "$_sv_w/${_sv_s%%:*}" ] || continue
            case "${_sv_s%%:*}" in x) _sv_rc=$_sv_xrc ;; xs) _sv_rc=$_sv_src ;; h) _sv_rc=$_sv_hrc ;; *) _sv_rc=$_sv_nrc ;; esac
            echo; echo "== ${_sv_s#*:} (exit $_sv_rc)"; cat "$_sv_w/${_sv_s%%:*}"
        done; } | _sv_ids | usb_scrub "$_sv_w/ids" > "$_sv_w/body" || return 1
    # the evidence that it is clean: something to look for, and none of it found
    _sv_ok=0; _sv_g=2; [ -s "$_sv_w/ids" ] && { _sv_g=0; grep -qF -f "$_sv_w/ids" "$_sv_w/body" || _sv_g=$?; }
    [ "$_sv_g" = 1 ] && _sv_ok=1   # 1 = none found; 2 = grep itself failed, not clean (#231)
    if [ "$_sv_ok" = 0 ]; then
        log "usbclone: survey: $_sv_d -- its serial could not be shown gone; the raw SMART is not kept" >&2
        { echo; echo "== smartctl -x (exit $_sv_xrc)${_sv_src:+, -d sat (exit $_sv_src)}, -H (exit $_sv_hrc)${_sv_nrc:+, nvme smart-log (exit $_sv_nrc)}"
          echo "$USB_SURVEY_RM"; } > "$_sv_w/body"
    fi
    _sv_port=$(disk_port "$_sv_d"); case "$_sv_port" in ''|*[!0-9]*) _sv_port=x ;; esac
    _sv_free=$(df -Pk "$USB_DATA_DIR" 2>/dev/null | awk 'NR == 2 { print $4 }')
    case "$_sv_free" in ''|*[!0-9]*) ;; *) [ "$_sv_free" -ge "$USB_SURVEY_MIN_KB" ] || return 2 ;; esac
    _sv_f="$USB_DATA_DIR/survey/$2-$_sv_port-$_sv_hash.txt"
    {   echo "ImageSrv USB ${AGENT_VERSION:-unknown} -- SMART survey (#1921; no serial: hash = sha256(serial)[:12])"
        echo "time: $(date '+%Y-%m-%d %H:%M:%S') (the machine's clock, not verified)"
        echo "disk: $_sv_d · drawer $_sv_port · hash $_sv_hash"
        echo "model: $(trim "$(cat "$SYSROOT/sys/block/$_sv_d/device/model" 2>/dev/null)" | grep . || echo "$USB_NR") · $(( $(cat "$SYSROOT/sys/block/$_sv_d/size" 2>/dev/null || echo 0) / 2097152 )) GB"
        echo "machine: $(_dx /sys/class/dmi/id/sys_vendor) $(_dx /sys/class/dmi/id/product_name) · BIOS $(_dx /sys/class/dmi/id/bios_version)"
        echo "controller: $(_sv_ctrl "$_sv_d") · machine mode $(cut -d'|' -f1 "$RUN_DIR/usb.ctrl" 2>/dev/null | grep . || echo "$USB_NR")"
        echo "SATA link: $(_sv_link "$_sv_d")"
        echo "assessment (the screen): $(usb_survey_assess "$_sv_d")"
        cat "$_sv_w/body"
    } | usb_scrub "$_sv_w/ids" > "$_sv_f.next" && { _sv_g=0; grep -qF -f "$_sv_w/ids" "$_sv_f.next" 2>/dev/null || _sv_g=$?; [ "$_sv_g" = 1 ]; } && [ -s "$_sv_f.next" ] \
        && mv "$_sv_f.next" "$_sv_f" && { rm -rf "$_sv_w"; return 0; }
    rm -f "$_sv_f.next" "$_sv_f"; rm -rf "$_sv_w"
    _sv_free=$(df -Pk "$USB_DATA_DIR" 2>/dev/null | awk 'NR == 2 { print $4 }')
    case "$_sv_free" in ''|*[!0-9]*) return 1 ;; esac
    [ "$_sv_free" -ge "$USB_SURVEY_MIN_KB" ] || return 2
    return 1
}

usb_survey() {
    # Every boot, the survey stick only. USB_SURVEY_NOTICE = what the screen must say ("" = nothing).
    USB_SURVEY_NOTICE=""; usb_survey_is || return 0
    if [ "${USB_DATA_STATE:-none}" != mounted ] || ! mkdir -p "$USB_DATA_DIR/survey"; then
        USB_SURVEY_NOTICE="אין מחיצת נתונים זמינה על הדיסק-און-קי — נתוני ה-SMART לא נשמרו"
        log "usbclone: survey: no data partition -- nothing kept"; return 1
    fi
    _su_t=$(date -u +%Y%m%d-%H%M%S); _su_n=0; _su_full=0; _su_bad=0
    for _su_d in $(list_disks); do
        grep -qx "$_su_d" "$RUN_DIR/usb-media.disks" 2>/dev/null && continue   # the stick: never surveyed
        if [ "$_su_full" = 1 ]; then _su_bad=$((_su_bad + 1)); continue; fi
        usb_survey_disk "$_su_d" "$_su_t"
        case $? in 0) _su_n=$((_su_n + 1)) ;; 2) _su_full=1; _su_bad=$((_su_bad + 1)) ;; *) _su_bad=$((_su_bad + 1)) ;; esac
    done
    sync
    log "usbclone: survey: $_su_n disk(s) kept on the stick, $_su_bad not"
    if [ "$_su_full" = 1 ]; then USB_SURVEY_NOTICE=$USB_SURVEY_FULL_HE; log "usbclone: survey: the data partition is full" >&2
    elif [ "$_su_bad" -gt 0 ]; then USB_SURVEY_NOTICE="נתוני ה-SMART של $_su_bad דיסקים לא נשמרו על הדיסק-און-קי"; fi
    [ "$_su_bad" = 0 ]
}
