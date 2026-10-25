# ntfsboot.sh -- #1487: the NTFS backup boot sector, after partclone -r;
# #2015 (bottom): 'hidden sectors' of a partition the restore moved.
# POSIX sh (busybox ash).
#
# partclone.ntfs -r writes the volume's clusters. The backup boot sector
# lives in the sector right after the volume -- sector number_of_sectors
# (boot sector bytes 0x28..0x2F, little-endian), which is the partition's
# last sector when the volume fills it -- outside the cluster area, so it
# was never written. Measured on cloner 2, 16/10: every NTFS target's last
# sector differed from its sector 0, except the partition ntfsresize grew
# (ntfsresize rewrites the backup), and `ntfsfix -n` said "Failed to fix the
# alternate boot sector". The table and xxh64 checks all passed: they compare
# the stream, and this sector is not in it.
#
# The fix copies sector 0 to sector number_of_sectors, as ntfsclone and
# ntfsresize do, then drops the cache and reads it back (principle 5): a
# write that "succeeded" but did not land is a failed target, by name.
# Never past the partition's end. Called once per NTFS partition, right
# after its own write was judged, so before finish_grow's ntfsresize.

_nb_bytes() {
    # $1 = file, $2 = offset, $3 = count. The bytes as unsigned decimals.
    od -An -tu1 -j "$2" -N "$3" "$1" 2>> "$LOG_FILE"
}

ntfs_backup_boot() {
    # $1 = partition node. 0 = sector 0 is at sector number_of_sectors and was
    # read back equal. 1 = NTFS_BOOT_ERROR says why.
    NTFS_BOOT_ERROR=""
    _nb_n="$1"; _nb_t="$RUN_DIR/ntfsboot.${1##*/}"
    _nb_oem=$(od -An -tx1 -j 3 -N 8 "$_nb_n" 2>> "$LOG_FILE" | tr -d ' \n')
    if [ "$_nb_oem" != 4e54465320202020 ]; then
        NTFS_BOOT_ERROR="סקטור 0 אינו סקטור אתחול של NTFS (${_nb_oem:-לא נקרא})"; return 1
    fi
    # shellcheck disable=SC2046 # the bytes are a word list on purpose
    set -- $(_nb_bytes "$_nb_n" 11 2)
    [ "$#" -eq 2 ] || { NTFS_BOOT_ERROR="גודל הסקטור לא נקרא מסקטור האתחול"; return 1; }
    _nb_bps=$(( $1 + ($2 << 8) ))
    case "$_nb_bps" in
        512|1024|2048|4096) ;;
        *) NTFS_BOOT_ERROR="גודל סקטור $_nb_bps בסקטור האתחול אינו תקין"; return 1 ;;
    esac
    # shellcheck disable=SC2046
    set -- $(_nb_bytes "$_nb_n" 40 8)
    [ "$#" -eq 8 ] || { NTFS_BOOT_ERROR="מספר הסקטורים לא נקרא מסקטור האתחול"; return 1; }
    _nb_tot=$(( $1 + ($2 << 8) + ($3 << 16) + ($4 << 24) + ($5 << 32) + ($6 << 40) + ($7 << 48) + ($8 << 56) ))
    _nb_size=$(blockdev --getsize64 "$_nb_n" 2>> "$LOG_FILE")
    case "$_nb_size" in
        ''|*[!0-9]*) NTFS_BOOT_ERROR="גודל המחיצה לא נקרא (blockdev) — לא נכתב דבר"; return 1 ;;
    esac
    # (tot + 1) * bps <= size, written without the multiplication (no overflow).
    if [ "$_nb_tot" -lt 1 ] || [ "$_nb_tot" -gt $(( _nb_size / _nb_bps - 1 )) ]; then
        NTFS_BOOT_ERROR="הווליום מצהיר על $_nb_tot סקטורים של $_nb_bps, והמחיצה $_nb_size בתים — לא נכתב דבר"; return 1
    fi
    rm -f "$_nb_t.0" "$_nb_t.back"
    if ! dd if="$_nb_n" of="$_nb_t.0" bs="$_nb_bps" count=1 2>> "$LOG_FILE" \
            || [ "$(( $(wc -c < "$_nb_t.0") ))" -ne "$_nb_bps" ]; then
        NTFS_BOOT_ERROR="סקטור 0 לא נקרא"; rm -f "$_nb_t.0"; return 1
    fi
    dd if="$_nb_t.0" of="$_nb_n" bs="$_nb_bps" seek="$_nb_tot" count=1 conv=notrunc,fsync 2>> "$LOG_FILE"
    _nb_rc=$?
    if [ "$_nb_rc" -ne 0 ]; then
        NTFS_BOOT_ERROR="הכתיבה לסקטור $_nb_tot נכשלה (dd rc=$_nb_rc)"; rm -f "$_nb_t.0"; return 1
    fi
    # The read-back must come from the disk, not from the cache dd just filled.
    if ! blockdev --flushbufs "$_nb_n" >> "$LOG_FILE" 2>&1; then
        NTFS_BOOT_ERROR="המטמון לא רוקן (blockdev --flushbufs) — אין קריאה חוזרת מהדיסק"; rm -f "$_nb_t.0"; return 1
    fi
    dd if="$_nb_n" of="$_nb_t.back" bs="$_nb_bps" skip="$_nb_tot" count=1 2>> "$LOG_FILE"
    _nb_rc=$?
    # cmp: 0 = equal, 1 = differ (an answer), 2 = the check itself broke.
    cmp -s "$_nb_t.0" "$_nb_t.back"; _nb_c=$?
    rm -f "$_nb_t.0" "$_nb_t.back"
    if [ "$_nb_rc" -ne 0 ]; then NTFS_BOOT_ERROR="הקריאה החוזרת של סקטור $_nb_tot נכשלה (dd rc=$_nb_rc)"; return 1; fi
    case "$_nb_c" in
        0) return 0 ;;
        1) NTFS_BOOT_ERROR="סקטור $_nb_tot שנקרא בחזרה שונה מסקטור 0 — הכתיבה לא נקלטה" ;;
        *) NTFS_BOOT_ERROR="ההשוואה לסקטור 0 לא בוצעה (cmp rc=$_nb_c)" ;;
    esac
    return 1
}

ntfs_boot_target() {
    # $1 = disk, $2 = partition index, $3 = fs. 0 = not NTFS, or the backup was
    # written and read back (the index is recorded in targets/<disk>/ntfs_boot
    # for the end screen). 1 = NTFS_BOOT_ERROR names the partition and why;
    # the caller fails the target with it.
    [ "$3" = ntfs ] || return 0
    if ntfs_backup_boot "$(partition_node "$1" "$2")"; then
        echo "$2" >> "$RUN_DIR/targets/$1/ntfs_boot"
        log "$1: partition $2: NTFS backup boot sector written and read back"
        return 0
    fi
    NTFS_BOOT_ERROR="מחיצה $2: סקטור אתחול הגיבוי של NTFS — $NTFS_BOOT_ERROR"
    log "$1: $NTFS_BOOT_ERROR"
    return 1
}

# --- #2015: 'hidden sectors' -- where the boot sector says it starts ---------
#
# Boot sector bytes 0x1C..0x1F (NTFS, and the FAT BPB at the same offset) are
# the partition's start, in the volume's sectors. partclone writes the source's
# boot sector as it was, and a restore puts partitions somewhere else: capture
# compacts the layout after a shrunk partition (shrink.sh, source_start_sector),
# expand_last moves the tail to the disk's end and share_head moves a head
# forward (expandshare.sh). Measured 22/10 (lab, read-only): a Windows partition
# at 5919226 said 10185210, a recovery at 975110144 said 467197952. UEFI does
# not read the field; NTFS's boot code under BIOS/MBR does -- Windows (and WinRE)
# does not start.
#
# finish_grow (grow.sh) calls boot_hidden_disk once per target: AFTER the
# read-back (readback.sh compares the written blocks with the source's, and this
# sector is one of them) and BEFORE the grow (ntfsresize copies sector 0 to the
# backup). The start is the kernel's (sysfs: the table it read back). A field
# that already says it is left alone; one that does not is written, the cache
# dropped and read back -- then NTFS's backup boot sector (#1487, above) and
# FAT32's (BPB 0x32) follow it. Anything else fails the target, by name.

BOOT_HIDDEN_ERROR=""

_bh_u16() {   # $1 = file, $2 = offset. The little-endian word, in decimal.
    # shellcheck disable=SC2046
    set -- $(_nb_bytes "$1" "$2" 2)
    [ "$#" -eq 2 ] || return 1
    echo $(( $1 + ($2 << 8) ))
}

_bh_u32() {   # $1 = file, $2 = offset. The little-endian dword, in decimal.
    # shellcheck disable=SC2046
    set -- $(_nb_bytes "$1" "$2" 4)
    [ "$#" -eq 4 ] || return 1
    echo $(( $1 + ($2 << 8) + ($3 << 16) + ($4 << 24) ))
}

_bh_write() {
    # $1 = node, $2 = sector, $3 = bytes per sector, $4 = the value. That sector
    # with bytes 0x1C..0x1F = $4, written, the cache dropped, the dword read
    # back from the disk. 0 = it says $4; 1 = BOOT_HIDDEN_ERROR.
    _bw_t="$RUN_DIR/hidden.sector"; rm -f "$_bw_t" "$_bw_t.new"
    if ! dd if="$1" of="$_bw_t" bs="$3" skip="$2" count=1 2>> "$LOG_FILE" \
            || [ "$(( $(wc -c < "$_bw_t") ))" -ne "$3" ]; then
        BOOT_HIDDEN_ERROR="סקטור $2 לא נקרא — לא נכתב דבר"; rm -f "$_bw_t"; return 1
    fi
    _bw_le=""; _bw_v=$4   # the 4 bytes, little-endian, as printf's octal escapes
    for _bw_k in 1 2 3 4; do _bw_le="$_bw_le\\$(printf %03o $((_bw_v & 255)))"; _bw_v=$((_bw_v >> 8)); done
    # shellcheck disable=SC2059 # the format IS the bytes
    { head -c 28 "$_bw_t"; printf "$_bw_le"; tail -c +33 "$_bw_t"; } > "$_bw_t.new"
    if [ "$(( $(wc -c < "$_bw_t.new") ))" -ne "$3" ]; then
        BOOT_HIDDEN_ERROR="הסקטור החדש לא נבנה — לא נכתב דבר"; rm -f "$_bw_t" "$_bw_t.new"; return 1
    fi
    dd if="$_bw_t.new" of="$1" bs="$3" seek="$2" count=1 conv=notrunc,fsync 2>> "$LOG_FILE"; _bw_rc=$?
    rm -f "$_bw_t" "$_bw_t.new"
    [ "$_bw_rc" -eq 0 ] || { BOOT_HIDDEN_ERROR="הכתיבה לסקטור $2 נכשלה (dd rc=$_bw_rc)"; return 1; }
    blockdev --flushbufs "$1" >> "$LOG_FILE" 2>&1 \
        || { BOOT_HIDDEN_ERROR="המטמון לא רוקן (blockdev --flushbufs) — אין קריאה חוזרת מהדיסק"; return 1; }
    _bw_got=$(_bh_u32 "$1" $(($2 * $3 + 28))) || { BOOT_HIDDEN_ERROR="הקריאה החוזרת של סקטור $2 נכשלה"; return 1; }
    [ "$_bw_got" = "$4" ] && return 0
    BOOT_HIDDEN_ERROR="סקטור $2 נקרא בחזרה עם $_bw_got ולא $4 — הכתיבה לא נקלטה"; return 1
}

_boot_hidden() {
    # $1 = disk, $2 = node, $3 = fs (ntfs|vfat). See boot_hidden_target.
    _bh_start=$(cat "$SYSROOT/sys/block/$1/${2##*/}/start" 2>> "$LOG_FILE")
    case "$_bh_start" in ''|*[!0-9]*)
        BOOT_HIDDEN_ERROR="מיקום המחיצה לא נקרא מהקרנל (sysfs) — לא נכתב דבר"; return 1 ;;
    esac
    if [ "$3" = ntfs ]; then
        _bh_s=$(od -An -tx1 -j 3 -N 8 "$2" 2>> "$LOG_FILE" | tr -d ' \n')
        [ "$_bh_s" = 4e54465320202020 ] || { BOOT_HIDDEN_ERROR="סקטור 0 אינו סקטור אתחול של NTFS (${_bh_s:-לא נקרא})"; return 1; }
    else
        _bh_s=$(od -An -tx1 -j 510 -N 2 "$2" 2>> "$LOG_FILE" | tr -d ' \n')
        [ "$_bh_s" = 55aa ] || { BOOT_HIDDEN_ERROR="סקטור 0 אינו סקטור אתחול של FAT (חתימה ${_bh_s:-לא נקראה})"; return 1; }
    fi
    _bh_bps=$(_bh_u16 "$2" 11) || { BOOT_HIDDEN_ERROR="גודל הסקטור לא נקרא מסקטור האתחול"; return 1; }
    case "$_bh_bps" in
        512|1024|2048|4096) ;;
        *) BOOT_HIDDEN_ERROR="גודל סקטור $_bh_bps בסקטור האתחול אינו תקין"; return 1 ;;
    esac
    # sysfs counts 512-byte sectors; the field counts the volume's.
    [ $((_bh_start * 512 % _bh_bps)) -eq 0 ] \
        || { BOOT_HIDDEN_ERROR="המחיצה מתחילה ב-$_bh_start, לא בגבול של סקטור $_bh_bps — לא נכתב דבר"; return 1; }
    _bh_want=$((_bh_start * 512 / _bh_bps))
    if [ "$_bh_want" -gt 4294967295 ]; then   # 4 bytes; MBR's boot code cannot reach it either
        log "$1: ${2##*/} starts at $_bh_want -- past what 'hidden sectors' holds; left as it is (#2015)"; return 0
    fi
    _bh_cur=$(_bh_u32 "$2" 28) || { BOOT_HIDDEN_ERROR="השדה לא נקרא מסקטור האתחול"; return 1; }
    [ "$_bh_cur" != "$_bh_want" ] || return 0
    _bh_write "$2" 0 "$_bh_bps" "$_bh_want" || return 1
    if [ "$3" = ntfs ]; then
        ntfs_backup_boot "$2" \
            || { BOOT_HIDDEN_ERROR="סקטור 0 תוקן, אבל סקטור הגיבוי לא — $NTFS_BOOT_ERROR"; return 1; }
    else   # FAT32 (no 16-bit FAT size) has a backup boot sector; FAT12/16 do not
        _bh_f16=$(_bh_u16 "$2" 22) && _bh_bk=$(_bh_u16 "$2" 50) && _bh_res=$(_bh_u16 "$2" 14) \
            || { BOOT_HIDDEN_ERROR="סוג ה-FAT ומיקום סקטור הגיבוי לא נקראו"; return 1; }
        if [ "$_bh_f16" -eq 0 ] && [ "$_bh_bk" -gt 0 ] && [ "$_bh_bk" -lt "$_bh_res" ]; then
            _bh_write "$2" "$_bh_bk" "$_bh_bps" "$_bh_want" \
                || { BOOT_HIDDEN_ERROR="סקטור 0 תוקן, אבל סקטור הגיבוי $_bh_bk לא — $BOOT_HIDDEN_ERROR"; return 1; }
        fi
    fi
    log "$1: ${2##*/}: hidden sectors $_bh_cur -> $_bh_want (the partition's start), read back (#2015)"
}

boot_hidden_target() {
    # $1 = disk, $2 = partition index, $3 = fs. 0 = not NTFS/FAT, or bytes
    # 0x1C..0x1F say where the partition starts (already, or written and read
    # back). 1 = BOOT_HIDDEN_ERROR names the partition and why.
    BOOT_HIDDEN_ERROR=""
    case "$3" in ntfs|vfat) ;; *) return 0 ;; esac
    _boot_hidden "$1" "$(partition_node "$1" "$2")" "$3" && return 0
    BOOT_HIDDEN_ERROR="מחיצה $2: מיקום ההתחלה בסקטור האתחול (hidden sectors) — $BOOT_HIDDEN_ERROR"
    log "$1: $BOOT_HIDDEN_ERROR"; return 1
}

boot_hidden_disk() {
    # $1 = disk, $2 = manifest. Every partition of the plan, in its order.
    # 0 = each NTFS/FAT one says where it starts; 1 = BOOT_HIDDEN_ERROR.
    BOOT_HIDDEN_ERROR=""
    manifest_plan "$2" > "$RUN_DIR/hidden.plan" \
        || { BOOT_HIDDEN_ERROR="לא הצלחנו לקרוא את תוכנית המחיצות — מיקום ההתחלה בסקטורי האתחול לא נבדק"; return 1; }
    while IFS='|' read -r _bd_i _bd_g _bd_r _bd_fs _bd_rest <&3; do
        [ -n "$_bd_i" ] || continue
        boot_hidden_target "$1" "$_bd_i" "$_bd_fs" || return 1
    done 3< "$RUN_DIR/hidden.plan"
}
