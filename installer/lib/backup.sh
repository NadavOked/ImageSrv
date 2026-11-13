# backup.sh -- the database backup chosen on the "גיבוי" step (#1956). POSIX sh.
# shellcheck disable=SC2034,SC2154 # sourced module shares state with the entry point
#
# backup_time= (HH:MM) and backup_reserve_gb= (whole GB, at least 1, at most 20%
# of the chosen disk) in the answers file: the console's own rules
# (server/system/backup_job.py, parse_time and parse_reserve; a test runs both
# on the same values). Empty or absent = the server's defaults, 02:00 and 5 GB;
# firstboot hands a value to the server's settings (setup-boot-server
# --backup-time, --backup-reserve-gb).
#
# #2865 (Nadav 11/11): backup_enabled=false is "בלי גיבוי כרגע" -- no backup and
# no space kept, so a reserved size with it is refused by name; firstboot turns
# the console's setting off (setup-boot-server --backup-off). Empty or absent =
# on, the default. Any other value is refused: not a guess at "off".

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

# #2792: the cap of what the server is installed on -- the RAID array when the
# answers ask for one (validate_raid ran first: RAID_LEVEL, RAID_MEMBERS,
# RAID_COUNT, RAID_SPARES), else the one disk. The array's capacity is the
# smallest member times the data members: RAID1 = 1, RAID5 = N-1, RAID6 = N-2,
# RAID10 = N/2, with N the active members (a spare holds no data). The GUI's
# install_store_bytes (native-gui/src/install_raid.h) is the same rule.
backup_install_cap_gb() {
    [ "${RAID_LEVEL:-none}" != none ] || { backup_cap_gb "$DISK_NAME"; return; }
    _bi_min=
    for _bi_m in $RAID_MEMBERS; do
        IFS= read -r _bi_s < "$SYSROOT/sys/block/$_bi_m/size" || fail backup-disk-size "cannot read the size of $_bi_m"
        case "$_bi_s" in ''|*[!0-9]*) fail backup-disk-size "invalid size for $_bi_m" ;; esac
        if [ -z "$_bi_min" ] || [ "$_bi_s" -lt "$_bi_min" ]; then _bi_min=$_bi_s; fi
    done
    [ -n "$_bi_min" ] || fail backup-disk-size "no RAID member to measure"
    _bi_n=$((RAID_COUNT - ${RAID_SPARES:-0}))
    case "$RAID_LEVEL" in
        raid1) _bi_data=1 ;; raid5) _bi_data=$((_bi_n - 1)) ;; raid6) _bi_data=$((_bi_n - 2)) ;;
        raid10) _bi_data=$((_bi_n / 2)) ;; *) fail backup-disk-size "unknown RAID level: $RAID_LEVEL" ;;
    esac
    printf '%s' "$((_bi_min * 512 * _bi_data * BACKUP_CAP_PERCENT / 100 / 1000000000))"
}

validate_backup() {
    ANSWER_BACKUP_TIME=${ANSWER_BACKUP_TIME:-}; ANSWER_BACKUP_RESERVE_GB=${ANSWER_BACKUP_RESERVE_GB:-}
    ANSWER_BACKUP_ENABLED=${ANSWER_BACKUP_ENABLED:-}
    case "$ANSWER_BACKUP_ENABLED" in
        '') ;;
        false) [ -z "$ANSWER_BACKUP_RESERVE_GB" ]                    || fail invalid-backup-enabled "backup_enabled=false keeps no space: backup_reserve_gb must be empty" ;;
        *) fail invalid-backup-enabled "backup_enabled must be empty (on) or false" ;;
    esac
    if [ -n "$ANSWER_BACKUP_TIME" ]; then
        valid_backup_time "$ANSWER_BACKUP_TIME" || fail invalid-backup-time "backup_time must be HH:MM"
    fi
    if [ -n "$ANSWER_BACKUP_RESERVE_GB" ]; then
        valid_backup_reserve "$ANSWER_BACKUP_RESERVE_GB" || fail invalid-backup-reserve "backup_reserve_gb must be a whole number of GB, at least 1"
        _vb_cap=$(backup_install_cap_gb) || exit 1
        [ "$ANSWER_BACKUP_RESERVE_GB" -le "$_vb_cap" ] \
            || fail invalid-backup-reserve "backup_reserve_gb is above $BACKUP_CAP_PERCENT% of the disk ($_vb_cap GB)"
    fi
    export ANSWER_BACKUP_TIME ANSWER_BACKUP_RESERVE_GB ANSWER_BACKUP_ENABLED
}
