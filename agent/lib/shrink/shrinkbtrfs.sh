# Only a single-device filesystem can be shortened through one partition view.
# Read the specified device offline, including its device ID (not always 1).
btrfs_geometry() {
    _bt_super=$(LC_ALL=C btrfs inspect-internal dump-super "$1" 2>> "$LOG_FILE") || return 1
    printf '%s\n' "$_bt_super" | awk -v field="$2" '
        $1 == "num_devices" { devices=$2; n++ }
        $1 == "log_root" { journal=$2; j++ }
        $1 == field { value=$2; v++ }
        /SEEDING/ { seed=1 }
        END {
            if (seed || n != 1 || devices != "1" || j != 1 || journal != "0" ||
                v != 1 || value !~ /^[1-9][0-9]*$/) exit 1
            print value
        }'
}

shrink_btrfs() {
    # Start read-only without replay. Verify the mounted device, even in the
    # presence of another filesystem with the same UUID, before enabling writes.
    _bt_mount="$RUN_DIR/shrink.btrfs"
    _bt_id=$(btrfs_geometry "$1" dev_item.devid) && _bt_sector=$(btrfs_geometry "$1" sectorsize) || {
        LC_ERROR="$1: btrfs dump-super: הגאומטריה חסרה, יש יומן שלא הוחל, או שמערכת הקבצים על יותר מהתקן אחד"; return 1; }
    _bt_bytes=$(($2 / _bt_sector * _bt_sector))
    _bt_view=$(stat -L -c '%t:%T' "$1") && [ -n "$_bt_view" ] && [ "$_bt_view" != 0:0 ] && mkdir -p "$_bt_mount" || { LC_ERROR="$1: הכנת העגינה של btrfs נכשלה"; return 1; }
    # dump-super above requires log_root == 0. nologreplay is a rescue
    # option that the kernel cannot retain when remounting read-write.
    mount -t btrfs -o ro,subvolid=5 "$1" "$_bt_mount" >> "$LOG_FILE" 2>&1 || {
        LC_ERROR="$1: btrfs read-only mount rc=$?"; return 1; }
    LC_ERROR=""
    if _bt_show=$(LC_ALL=C btrfs filesystem show --raw "$_bt_mount" 2>> "$LOG_FILE") &&
        _bt_path=$(printf '%s\n' "$_bt_show" | awk -v id="$_bt_id" '
            $1 == "devid" { n++; if ($2 == id && $7 == "path") dev=$8 }
            END { if (n != 1 || !dev) exit 1; print dev }') &&
        _bt_actual=$(stat -L -c '%t:%T' "$_bt_path") && [ "$_bt_actual" = "$_bt_view" ]; then
        if mount -o remount,rw "$1" "$_bt_mount" >> "$LOG_FILE" 2>&1; then
            btrfs filesystem resize "$_bt_id:$_bt_bytes" "$_bt_mount" >> "$LOG_FILE" 2>&1 ||
                LC_ERROR="$1: btrfs filesystem resize rc=$?"
        else LC_ERROR="$1: btrfs remount rw rc=$?"; fi
    else LC_ERROR="$1: ההתקן שעוגן אינו העותק (btrfs)"; fi
    umount "$_bt_mount" >> "$LOG_FILE" 2>&1 || { LC_ERROR="${LC_ERROR:+$LC_ERROR; }$1: btrfs umount rc=$?"; return 1; }
    [ -z "$LC_ERROR" ] || return 1
    _bt_actual=$(btrfs_geometry "$1" dev_item.total_bytes) && [ "$_bt_actual" = "$_bt_bytes" ] || {
        LC_ERROR="$1: הגאומטריה של btrfs נקראה בחזרה $_bt_actual, ולא $_bt_bytes"; return 1; }
}
