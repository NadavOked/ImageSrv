# usbinstall.sh -- #2062: one stick, clone and install. POSIX sh.
#
# Nadav 23/10 + 12/11: "התקן שרת ImageSrv על המחשב הזה" in the stick's settings, and
# the packages on a partition of the stick -- the install needs no internet.
#
# The stick built with `build_usb_clone.sh --server-iso` carries a third partition:
# the server's install ISO, byte for byte (label IMAGESRV_INSTALL, iso9660), and its
# one initramfs holds the live installer too (build_initramfs.sh --installer). The
# button does not reboot: the agent closes the clone screen, and execs what init
# execs on the ISO (installer/imagesrv-installer) with that partition on /cdrom.
#
# Four rules:
#   * The button erases nothing and chooses no disk. The disk is chosen on the
#     installer's own screen and erased only after its red "החל התקנה" (#2097).
#   * The medium is the stick this machine started from, never "a disk with the
#     label" (#2431: a label is not the stick). The engine refuses the disk that
#     holds /cdrom (installer/lib/disks.sh disk_is_iso) -- so a medium taken from a
#     drawer would protect the drawer and offer the stick as a target.
#   * Positive evidence, all of it, before the clone screen is left (principle 5):
#     exactly one such partition, iso9660, mounted read-only where /proc/mounts
#     says so, imagesrv-iso.json on it with a version, the installer in this image.
#     Anything else is said on the dialog and nothing is opened.
#   * Nothing on the stick is written: the partition is mounted read-only and the
#     data partition is unmounted before the installer starts.

USB_INSTALL_LABEL="${USB_INSTALL_LABEL:-IMAGESRV_INSTALL}"
USB_INSTALL_MNT="${USB_INSTALL_MNT:-/cdrom}"
USB_INSTALLER_BIN="${USB_INSTALLER_BIN:-/usr/lib/imagesrv/installer/imagesrv-installer}"

usb_install_find() {
    # Sets USB_INSTALL_DEV (sdb3): the install partition of the stick. 0 = exactly one,
    # iso9660; 1 = the stick has none; 2 = it cannot be told. USB_INSTALL_WHY says why.
    USB_INSTALL_DEV=""; USB_INSTALL_WHY=""; _uif_c=""
    _uif_m=$(cat "$RUN_DIR/usb-media.disks" 2>/dev/null)
    [ -n "$_uif_m" ] || { USB_INSTALL_WHY="הדיסק-און-קי שממנו עלה המחשב לא זוהה, ולכן אין מאיפה להתקין"; return 2; }
    for _uif_d in $_uif_m; do
        if disk_internal "$_uif_d"; then continue; fi   # #2431: an internal disk is never the stick
        for _uif_p in "$SYSROOT/sys/block/$_uif_d/$_uif_d"*; do
            [ -e "$_uif_p" ] && [ "$(usb_part_label "${_uif_p##*/}")" = "$USB_INSTALL_LABEL" ] || continue
            _uif_c="$_uif_c${_uif_c:+ }${_uif_p##*/}"
        done
    done
    case "$_uif_c" in
        '') USB_INSTALL_WHY="הדיסק-און-קי הזה נבנה בלי חבילות ההתקנה של השרת (אין עליו מחיצת $USB_INSTALL_LABEL)"; return 1 ;;
        *' '*) USB_INSTALL_WHY="על הדיסק-און-קי יש יותר ממחיצת התקנה אחת ($_uif_c), ולא ידוע איזו מהן נכונה"; return 2 ;;
    esac
    _uif_t=$(blkid -s TYPE -o value "$SYSROOT/dev/$_uif_c" 2>/dev/null)
    [ "$_uif_t" = iso9660 ] || { USB_INSTALL_WHY="מחיצת ההתקנה ($_uif_c) אינה iso9660 (${_uif_t:-סוג לא נקרא})"; return 2; }
    USB_INSTALL_DEV=$_uif_c
}

usb_install_mounted() {   # the device /proc/mounts names on USB_INSTALL_MNT (the last one), or nothing
    _uim_on=""
    while read -r _uim_dev _uim_mnt _uim_rest; do
        [ "$_uim_mnt" = "$USB_INSTALL_MNT" ] && _uim_on=$_uim_dev
    done 2>/dev/null < "$SYSROOT/proc/mounts"
    printf '%s\n' "$_uim_on"
}

usb_install_check() {
    # 0 = ready, with all the evidence; sets USB_INSTALL_DEV and USB_INSTALL_TAG (the
    # server version on the medium). Otherwise USB_INSTALL_WHY, and nothing is opened.
    USB_INSTALL_TAG=""
    usb_install_find || return 1
    [ -x "$USB_INSTALLER_BIN" ] || { USB_INSTALL_WHY="מתקין השרת אינו חלק מהדיסק-און-קי הזה"; return 1; }
    _uic_dev="$SYSROOT/dev/$USB_INSTALL_DEV"; _uic_on=$(usb_install_mounted)
    if [ -z "$_uic_on" ]; then
        modprobe isofs 2>/dev/null || log "usbclone: isofs did not load -- the install partition may not mount"
        mkdir -p "$USB_INSTALL_MNT" && mount -t iso9660 -o ro "$_uic_dev" "$USB_INSTALL_MNT" 2>> "$LOG_FILE" \
            || { USB_INSTALL_WHY="מחיצת ההתקנה ($USB_INSTALL_DEV) לא נקראה מהדיסק-און-קי"; return 1; }
        _uic_on=$(usb_install_mounted)   # read back: the kernel's word, not mount's exit code
    fi
    [ "$_uic_on" = "$_uic_dev" ] \
        || { USB_INSTALL_WHY="על $USB_INSTALL_MNT מעוגן ${_uic_on:-שום דבר} ולא מחיצת ההתקנה של הדיסק-און-קי ($USB_INSTALL_DEV)"; return 1; }
    [ -f "$USB_INSTALL_MNT/imagesrv-iso.json" ] \
        || { USB_INSTALL_WHY="במחיצת ההתקנה ($USB_INSTALL_DEV) אין imagesrv-iso.json — זו אינה מדיית התקנה של ImageSrv"; return 1; }
    USB_INSTALL_TAG=$(json_get "$USB_INSTALL_MNT/imagesrv-iso.json" .imagesrv_tag)
    case "$USB_INSTALL_TAG" in
        ''|null|*[!A-Za-z0-9._+-]*) USB_INSTALL_TAG=""; USB_INSTALL_WHY="גרסת השרת לא נקראה ממחיצת ההתקנה ($USB_INSTALL_DEV)"; return 1 ;;
    esac
}

usb_install_release() {   # the dialog closed without an install: our read-only mount comes off
    [ "$(usb_install_mounted)" = "$SYSROOT/dev/${USB_INSTALL_DEV:-none}" ] || return 0
    umount "$USB_INSTALL_MNT" 2>> "$LOG_FILE" || log "usbclone: $USB_INSTALL_MNT stays mounted read-only (umount failed)"
}

# --- the kiosk: the settings item and its dialog (usbset.sh, kind "install") --------------------

usbinstall_open() {   # Enter on the item: the check runs once, and its answer is the dialog
    if usb_install_check; then _us_put inst ready; else _us_put inst refused; fi
    _us_put insttag "$USB_INSTALL_TAG"; _us_put instwhy "$USB_INSTALL_WHY"; _us_put instdev "$USB_INSTALL_DEV"; _us_put instgo 0
}

usbinstall_key() {
    # $1 = a DCUI token, the dialog is open. The install is asked for only by Enter on the
    # red button, and the focus starts on "ביטול" (#1960's dialog; no typed word, #1270).
    [ "$(_us_get instgo)" != 1 ] || return 0   # asked: the agent is taking the screen
    USB_INSTALL_DEV=$(_us_get instdev)
    case "$1" in
        "KEY ESC") _us_put dlg 0; usb_install_release ;;
        "KEY TAB"|"KEY BACKTAB"|"KEY LEFT"|"KEY RIGHT")
            [ "$(_us_get inst)" = ready ] || return 0
            if [ "$(_us_get foc)" = go ]; then _us_put foc no; else _us_put foc go; fi ;;
        "KEY ENTER")
            if [ "$(_us_get inst)" != ready ] || [ "$(_us_get foc)" != go ]; then _us_put dlg 0; usb_install_release
            elif usb_install_check && _lc_req usbinstall.go "$USB_INSTALL_DEV" && _lc_req end.req back; then _us_put instgo 1
            else _us_put err "מתקין השרת לא נפתח: ${USB_INSTALL_WHY:-הבקשה לא נכתבה}"; fi ;;
    esac
}

usbinstall_frame() {
    # $1 = dialog open, $2 = the error. The pane says what the button does; the dialog shows
    # what was read from the stick (the version), or why nothing can be installed from it.
    _us_rec pane "התקן שרת ImageSrv על המחשב הזה" "פותח את מתקין השרת מהדיסק-און-קי הזה: בלי אינטרנט ובלי דיסק-און-קי נוסף. הדיסק שעליו יותקן השרת נבחר במתקין, ונמחק רק אחרי הכפתור האדום <b>החל התקנה</b> במסך הסיכום.&#10;<b>הדיסק-און-קי עצמו לא נמחק ולא מוצע כיעד</b>, וההגדרות והדוחות שעליו נשארים."
    if [ "$1" != 1 ]; then
        _ui_enter="התקנה"
        if usb_install_find; then _us_rec prow "חבילות ההתקנה" "" "על הדיסק-און-קי" "" "מחיצה $USB_INSTALL_LABEL" ok "בלי אינטרנט" ""
        else _us_rec prow "חבילות ההתקנה" "" "לא נמצאו" "" "" warn "אי אפשר להתקין" ""; _us_rec pnote "$USB_INSTALL_WHY."; _ui_enter="פרטים"; fi
        _us_rec pnote "השיכפול נסגר כשהמתקין נפתח. כדי לחזור לשיכפול מפעילים את המחשב מחדש מהדיסק-און-קי."
        _us_rec key "↑ ↓" "בחירה" 0
        _us_rec key Enter "$_ui_enter" 0
        _us_rec key Esc "חזרה לשיכפול" 0
        _us_rec sb ok "דיסק-און-קי" "· הגדרות"
    elif [ "$(_us_get instgo)" = 1 ]; then
        _us_rec dlg plain 640 "פותח את מתקין השרת…" "מסך השיכפול נסגר, והמתקין יופיע בעוד רגע. שום דיסק לא נמחק עד הכפתור האדום במתקין."
        _us_rec sb ok "דיסק-און-קי" "· התקנת שרת"
    elif [ "$(_us_get inst)" = ready ]; then
        _us_rec dlg danger 640 "להתקין שרת ImageSrv על המחשב הזה?" "מסך השיכפול ייסגר ומתקין השרת ייפתח. כדי לחזור לשיכפול אחר כך צריך להפעיל את המחשב מחדש."
        _us_rec d.kv "גרסת השרת" "$(_us_iso "$(_us_get insttag)")" 0
        _us_rec d.kv "מקור החבילות" "הדיסק-און-קי הזה — בלי אינטרנט" 0
        _us_rec d.kv "הדיסק שיימחק" "נבחר במתקין, ונמחק רק אחרי הכפתור האדום שם" 0
        _us_rec d.kv "הדיסק-און-קי" "לא נמחק ולא מוצע כיעד" 0
        [ -z "$2" ] || _us_rec d.error "$2"
        _ui_go=0; [ "$(_us_get foc)" = go ] && _ui_go=1
        _us_rec d.btn "ביטול" Esc plain $((1 - _ui_go))
        _us_rec d.btn "המשך להתקנת שרת" "" danger "$_ui_go"
        _us_rec key Tab "מעבר" 0
        _us_rec key Enter "ביצוע" 0
        _us_rec key Esc "ביטול" 0
        if [ -n "$2" ]; then _us_rec sb warn "לא נפתח" "· התקנת שרת"; else _us_rec sb ok "דיסק-און-קי" "· התקנת שרת"; fi
    else
        _us_rec dlg plain 640 "אי אפשר להתקין שרת מהדיסק-און-קי הזה" "$(_us_get instwhy)."
        _us_rec d.note "שום דבר לא נפתח ושום דיסק לא נמחק. להתקנת שרת: דיסק-און-קי שנבנה עם חבילות ההתקנה, או ה-ISO של השרת."
        _us_rec d.btn "סגירה" Esc plain 1
        _us_rec key Enter "סגירה" 0
        _us_rec key Esc "סגירה" 0
        _us_rec sb warn "לא נפתח" "· התקנת שרת"
    fi
}

# --- the agent: from the clone screen to the installer -----------------------------------------

usb_install_handover() {
    # usbclone_main, when the local screen ended with the kiosk's request ($LC/usbinstall.go:
    # the partition the dialog showed). Checked again here, by the process that will exec:
    # the kiosk's answer is a moment old. Returns (1) only when the installer was NOT
    # started -- the reason is on the clone screen (usb.notice), and nothing was closed.
    _uh_want=$(cat "$LC/usbinstall.go" 2>/dev/null); rm -f "$LC/usbinstall.go"
    if usb_install_check && [ "$USB_INSTALL_DEV" = "$_uh_want" ]; then :; else
        [ -n "$USB_INSTALL_WHY" ] || USB_INSTALL_WHY="מחיצת ההתקנה השתנתה מאז האישור (${_uh_want:-לא נרשמה} ← ${USB_INSTALL_DEV:-אין})"
        log "usbclone: server install NOT started -- $USB_INSTALL_WHY"
        printf '%s\n' "מתקין השרת לא נפתח: $USB_INSTALL_WHY" > "$RUN_DIR/usb.notice"
        usb_install_release; return 1
    fi
    log "usbclone: server install asked on the settings screen -- ImageSrv $USB_INSTALL_TAG from $USB_INSTALL_DEV (read-only); the stick is not written, and no disk is erased before the installer's own confirmation"
    # The network screen's "current address" (the install itself is offline). While the dialog
    # still says "opening": its lines go to the log, never onto the framebuffer under the GUI.
    installer_dhcp_all >> "$LOG_FILE" 2>&1 || log "usbclone: the temporary DHCP step failed to start"
    [ -z "${_pwr_pid:-}" ] || kill "$_pwr_pid" 2>/dev/null   # the power button was this screen's (powerbtn.sh)
    gui_early_stop "server install"                           # the keeper, the GUI, its bridge and state writer
    sync
    if umount "$USB_DATA_DIR" 2>> "$LOG_FILE"; then log "usbclone: data partition unmounted"
    elif [ "$(cat "$RUN_DIR/usb.data" 2>/dev/null)" = mounted ]; then
        mount -o remount,ro "$USB_DATA_DIR" 2>> "$LOG_FILE" || log "usbclone: the data partition is still mounted read-write (umount and remount,ro failed)"
    fi
    echo "/dev/$USB_INSTALL_DEV" > "$INSTALL_MEDIA_FILE" || log "usbclone: $INSTALL_MEDIA_FILE not written -- the installer cannot eject the medium"
    if [ "${IMAGESRV_TEST:-0}" = 1 ]; then echo "TEST-INSTALLER: $USB_INSTALL_DEV $USB_INSTALL_TAG"; exit 0; fi
    exec "$USB_INSTALLER_BIN"
}
