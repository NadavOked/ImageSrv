# lcscan.sh -- #2991: the local clone's drawer check (lc_inventory), split off
# lcinv.sh before the 300-line wall. Metal 12/11 (cloner 2): one bad drawer
# held "בודק את הדיסקים במחשב…" on an empty screen for some 3.5 minutes,
# because the loop read every drawer in turn with no ceiling, and the screen
# drew nothing until all of them were done.
#
# * each drawer has its own ceiling (LC_INV_MAX_S, capped_run -- waits.sh).
#   One that does not answer in it is a line that says so: no size, no
#   serial, not usable -- never a source, and never a target (lc_run compares
#   the serial; lc_verdict calls an unread size "unchecked");
# * a drawer is on the screen as soon as its own check is done: its .line is
#   renamed in at once, and inv/scan says which one is being read now and
#   since when (lcgui.sh's lc_scan);
# * the log says how long each drawer took -- which one held the others.
# The drawers are read one at a time, not side by side: lc_inv_disk mounts on
# the shared probe points ($RUN_DIR/probe, sysprobe, lc-probe) and reads the
# drawer evidence under $RUN_DIR. POSIX sh (busybox ash).

#: Not measured: a healthy drawer's check was never timed on metal. A failing
#: read costs the kernel some 30 s (the issue's guess, 12/11) -- 60 leaves one
#: such read and a margin, and is still a third of what held the screen.
LC_INV_MAX_S="${LC_INV_MAX_S:-60}"
LC_INV_LATE_HE="לא הגיבה בזמן — לא תיכתב"

lc_inventory() {
    # Every drawer, once, into $LC/inv/. inv.state says when it is complete:
    # a half-written inventory must never look like a machine with fewer disks.
    mkdir -p "$LC/inv" || return 1
    rm -f "$LC/inv/"* "$LC/measure/"* 2>/dev/null
    echo scanning > "$LC/inv.state"
    _lv_all=$(list_drawers); _lv_left=$(printf '%s\n' $_lv_all | grep -c .)
    for _lv_d in $_lv_all; do
        _lv_left=$((_lv_left - 1))
        lc_is_boot_media "$_lv_d" && continue   # #1914: the USB stick is never a source or a target
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        _lv_t=$(date +%s); echo "$_lv_d|$_lv_t|$_lv_left" > "$LC/inv/scan"
        capped_run "$LC_INV_MAX_S" "$LC/inv/$_lv_d.next" lc_inv_disk "$_lv_d"; _lv_rc=$?
        if [ "$CAPPED_TIMED_OUT" = 1 ]; then
            _lc_inv_late "$_lv_d" > "$LC/inv/$_lv_d.late" && mv "$LC/inv/$_lv_d.late" "$LC/inv/$_lv_d.line"
            log "local clone: $_lv_d did not answer its check in ${LC_INV_MAX_S}s -- not a source, not a target" >&2   # the log, not the console: what the caller prints is its own
        elif [ "$_lv_rc" = 0 ]; then
            mv "$LC/inv/$_lv_d.next" "$LC/inv/$_lv_d.line"
        fi
        log "local clone: the check of $_lv_d took $(($(date +%s) - _lv_t))s (rc $_lv_rc)" >&2
    done
    rm -f "$LC/inv/scan"; echo "done" > "$LC/inv.state"
}

_lc_inv_late() {
    # $1 = disk. lc_inv_disk's line for a drawer that did not answer: what
    # sysfs says without touching the disk (the port, the model), and nothing
    # that was not read -- size and serial empty, usable 0, the why.
    _ll_p=$(disk_port "$1"); case "$_ll_p" in ''|*[!0-9]*) _ll_p=0 ;; esac
    printf '%s|%s|%s||||||0|-1|-1|0|%s\n' "$_ll_p" "$1" \
        "$(_lc_clean "$(trim "$(cat "$SYSROOT/sys/block/$1/device/model" 2>/dev/null)")")" "$LC_INV_LATE_HE"
}

lc_is_boot_media() {
    # #1914: 0 = $1 is the stick this machine booted from (usbclone.sh found it by
    # label). In usbclone mode a list that was not written is every disk: fail closed.
    [ "${IMAGESRV_MODE:-}" = usbclone ] || return 1
    [ -s "$RUN_DIR/usb-media.disks" ] || return 0
    grep -qx "$1" "$RUN_DIR/usb-media.disks"
}
