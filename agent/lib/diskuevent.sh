# diskuevent.sh -- kernel-driven drawer inventory refresh (#1971).
# POSIX sh (busybox ash).

disk_uevent_start() {
    [ "$D_ROLE" = cloner ] || return 0
    [ -n "${_disk_uevent_pid:-}" ] && kill -0 "$_disk_uevent_pid" 2>/dev/null && return 0
    imagectl-uevent "$GUI_DIR/disk-event" >> "$LOG_FILE" 2>&1 &
    _disk_uevent_pid=$!
}
