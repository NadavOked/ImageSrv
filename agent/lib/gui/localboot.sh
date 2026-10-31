# localboot.sh -- when the GUI did not come up: point the next boot at the
# local disk, and tell the server (11/10). POSIX sh (busybox ash).
#
# die_local is a reboot, not a descent to the local disk (common.sh). A
# build or classroom machine with no task boots straight back into ImageSrv
# (#641), and the server's boot-loop guard counts only boots with a bounded
# context (bootguard.py) -- so a GUI that fails on every boot was a reboot
# loop until somebody came. Nadav, 11/10: before die_local, BootNext goes to
# a local-disk entry, read back like bootentry.sh does (a write is not
# evidence, principle 5), and a hello carries "the GUI did not come up" so
# the console shows it (interfaces.md, hello `gui_failed`).

boot_next_local() {
    # Prints one line, state:detail -- set:Boot#### / skipped:<why> /
    # failed:<why> -- and returns 0 only on "set". The entry is the first
    # Boot#### on a local disk (an HD(...) device path, not a network one):
    # BootOrder first, then any other entry. BootOrder is NOT touched.
    _bl_vars="${SYSROOT:-}/sys/firmware/efi/efivars"
    if [ ! -d "$_bl_vars" ] || [ -z "$(ls -A "$_bl_vars" 2>/dev/null)" ]; then
        echo "skipped:אין efivars (BIOS ישן, או ש-efivarfs לא מעוגן)"; return 1
    fi
    command -v efibootmgr >/dev/null 2>&1 || { echo "failed:efibootmgr אינו קיים ב-initramfs"; return 1; }
    _bl_table=$(efibootmgr -v 2>> "$LOG_FILE") || { echo "failed:efibootmgr -v לא קרא את הטבלה"; return 1; }
    _bl_order=$(printf '%s\n' "$_bl_table" | sed -n 's/^BootOrder: *//p' | tr ',' ' ')
    _bl_all=$(printf '%s\n' "$_bl_table" | sed -n 's/^Boot\([0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]\).*/\1/p')
    _bl_num=""
    for _bl_n in $_bl_order $_bl_all; do
        _bl_line=$(printf '%s\n' "$_bl_table" | awk -v n="Boot$_bl_n" 'index($0, n) == 1 { print; exit }')
        case "$_bl_line" in *HD\(*) ;; *) continue ;; esac
        case "$_bl_line" in *MAC\(*|*IPv4\(*|*IPv6\(*|*[Pp][Xx][Ee]*) continue ;; esac
        _bl_num=$(printf '%s' "$_bl_n" | tr 'a-f' 'A-F'); break
    done
    [ -n "$_bl_num" ] || { echo "failed:אין רשומת Boot#### של דיסק מקומי"; return 1; }
    efibootmgr -n "$_bl_num" >> "$LOG_FILE" 2>&1
    _bl_rc=$?
    _bl_next=$(efibootmgr 2>> "$LOG_FILE" | sed -n 's/^BootNext: *//p' | tr 'a-f' 'A-F' | tr -d ' \r')
    if [ "$_bl_next" != "$_bl_num" ]; then
        echo "failed:BootNext נקרא בחזרה '$_bl_next' ולא Boot$_bl_num (efibootmgr -n rc=$_bl_rc)"; return 1
    fi
    echo "set:Boot$_bl_num"
}

gui_failed_report() {
    # $1 = screen (build / recovery), $2 = boot_next state, $3 = detail.
    # One non-joining hello with the field. Not delivered = said in the log,
    # never assumed (principle 5); the reboot goes ahead either way.
    _gf_body=$(build_hello false) || { log "GUI failure not reported: hello could not be built"; return 1; }
    _gf_body="${_gf_body%\}},\"gui_failed\":{\"screen\":\"$1\",\"boot_next\":\"$2\",\"detail\":\"$(json_escape "$3")\"}}"
    printf '%s' "$_gf_body" > "$RUN_DIR/gui_failed.json" || return 1
    if http_post_json "$SERVER/api/v1/agent/hello" "$RUN_DIR/gui_failed.json" > /dev/null; then
        log "GUI failure reported to the server"
        return 0
    fi
    log "GUI failure NOT reported -- the server did not take the hello"
    return 1
}
