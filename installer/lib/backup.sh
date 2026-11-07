# backup.sh -- the database backup chosen on the "גיבוי" step (#1956). POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module shares state with the entry point
#
# backup_time= (HH:MM) and backup_reserve_gb= (whole GB, at least 1, at most 20%
# of the chosen disk) in the answers file: the console's own rules
# (server/system/backup_job.py, parse_time and parse_reserve; a test runs both
# on the same values). Empty or absent = the server's defaults, 02:00 and 5 GB;
# firstboot hands a value to the server's settings (setup-boot-server
# --backup-time, --backup-reserve-gb).

BACKUP_CAP_PERCENT=20

valid_backup_time() {
    case "$1" in [01][0-9]:[0-5][0-9]|2[0-3]:[0-5][0-9]) return 0 ;; esac
    return 1
}

valid_backup_reserve() {
    case "$1" in ''|*[!0-9]*|0*) return 1 ;; esac
    [ "${#1}" -le 6 ]
}

# The cap of a disk, in whole GB (10^9, as the screen says). The size is a
# sysfs read: one that fails is a named failure, not "no cap" (principle 5).
backup_cap_gb() {
    IFS= read -r _bc_sectors < "$SYSROOT/sys/block/$1/size" || fail backup-disk-size "cannot read the size of $1"
    case "$_bc_sectors" in ''|*[!0-9]*) fail backup-disk-size "invalid size for $1" ;; esac
    printf '%s' "$((_bc_sectors * 512 * BACKUP_CAP_PERCENT / 100 / 1000000000))"
}

validate_backup() {
    ANSWER_BACKUP_TIME=${ANSWER_BACKUP_TIME:-}; ANSWER_BACKUP_RESERVE_GB=${ANSWER_BACKUP_RESERVE_GB:-}
    if [ -n "$ANSWER_BACKUP_TIME" ]; then
        valid_backup_time "$ANSWER_BACKUP_TIME" || fail invalid-backup-time "backup_time must be HH:MM"
    fi
    if [ -n "$ANSWER_BACKUP_RESERVE_GB" ]; then
        valid_backup_reserve "$ANSWER_BACKUP_RESERVE_GB" || fail invalid-backup-reserve "backup_reserve_gb must be a whole number of GB, at least 1"
        _vb_cap=$(backup_cap_gb "$DISK_NAME") || exit 1
        [ "$ANSWER_BACKUP_RESERVE_GB" -le "$_vb_cap" ] \
            || fail invalid-backup-reserve "backup_reserve_gb is above $BACKUP_CAP_PERCENT% of the disk ($_vb_cap GB)"
    fi
    export ANSWER_BACKUP_TIME ANSWER_BACKUP_RESERVE_GB
}
