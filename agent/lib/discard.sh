# discard.sh -- whole-device TRIM of a restore target before its table (#1969).
# POSIX sh (busybox ash).
#
# Called only after every gate: the SMART gate, the drawer mapping and the
# write check have already dropped their disks, and the callers skip a target
# that is failed or cancelled. It runs right before apply_gpt, the first write
# that destroys the old table anyway -- the TRIM takes nothing a restore would
# not take a second later. The write mark (#1798) is in that table, written
# after this. Never on a local clone: there a drawer is the source, and
# lccopy never calls this.
#
# Nadav 22/10: an SSD that says it takes discard is trimmed whole; a failed
# TRIM is logged and the restore goes on. A rotating disk is never trimmed,
# even one that advertises discard (DM-SMR). `blkdiscard` in the initramfs is
# busybox's applet (Debian 1:1.37.0-6+b9 has it, testrunner 22/10).
#
# Not measured: how long a whole-device TRIM of a 500GB 860 EVO takes. The log
# line carries the seconds, so the first metal round gives the number. Past
# DISCARD_MAX_S the TRIM is neither done nor failed -- discards may still be in
# flight behind a blkdiscard stuck in D state, and a table written now could
# land under them -- so that target fails by name (rc 2; the callers test for
# 2 itself, so a TRIM that only failed can never fail a disk).

DISCARD_MAX_S="${DISCARD_MAX_S:-180}"

discard_before_restore() {
    # $1 = disk name. 0 = trimmed, not trimmable, or the TRIM failed (logged);
    # 2 = the TRIM did not end within DISCARD_MAX_S -- the caller fails the disk.
    _db_q="$SYSROOT/sys/block/$1/queue"
    _db_rot=$(cat "$_db_q/rotational" 2>/dev/null)
    _db_max=$(cat "$_db_q/discard_max_bytes" 2>/dev/null)
    [ "$_db_rot" = 0 ] || { log "$1: not trimmed -- rotational=${_db_rot:-unreadable}"; return 0; }
    case "$_db_max" in ''|*[!0-9]*|0) log "$1: not trimmed -- discard_max_bytes=${_db_max:-unreadable}"; return 0 ;; esac
    log "$1: TRIM of the whole disk before the restore"
    _db_t0=$(date +%s)
    capped_run "$DISCARD_MAX_S" /dev/null blkdiscard "$DEVROOT/$1"; _db_rc=$?
    if [ "$CAPPED_TIMED_OUT" = 1 ]; then
        log "$1: TRIM did not end within ${DISCARD_MAX_S}s -- this disk fails"
        return 2
    fi
    if [ "$_db_rc" = 0 ]; then
        log "$1: TRIM done in $(($(date +%s) - _db_t0))s"
    else
        log "$1: TRIM failed (rc=$_db_rc) -- the restore goes on"
    fi
    return 0
}
