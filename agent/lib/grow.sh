# grow.sh -- הצד השני של ההרחבה: אחרי שהזרם נגמר, מערכת הקבצים נמתחת
# לגודל המחיצה שכבר הורחבה לפניו.
# POSIX sh (busybox ash).
#
# פוצל מ-expand.sh ב-#478, כשזה הגיע ל-305 שורות. הכותרת של expand.sh
# טענה ששני שלבי ההרחבה "חייבים להישאר צמודים" — הם אינם קוראים זה
# לזה. הצימוד הוא דרך הסימון `$RUN_DIR/targets/<disk>/expanded`:
# ‏expand_last כותב אותו לפני הזרם, grow_expanded קורא אותו אחריו.
# הקובץ הזה נטען מיד אחרי expand.sh ב-imagectl-agent, ותלוי ב-
# ‏partition_node (restore.sh) ו-target_set (progress.sh) — שניהם
# נטענים לפניו.

grow_expanded() {
    # $1 = disk name. The second half of the expansion: the partition was
    # widened before the stream, and now that there is a filesystem inside it
    # it is told to follow. Nothing was widened -- nothing to do.
    _mark="$RUN_DIR/targets/$1/expanded"
    [ -f "$_mark" ] || return 0
    _idx=$(cut -d'|' -f1 "$_mark")
    _fs=$(cut -d'|' -f2 "$_mark")
    grow_filesystem "$_fs" "$(partition_node "$1" "$_idx")" || return 1
    # ‏#1897: המחיצות המכווצות שקיבלו את חלקן במקום (expandshare.sh) — גם הן גדלות.
    [ -f "$_mark.more" ] || return 0
    while IFS='|' read -r _gi _gf; do
        [ -n "$_gi" ] || continue
        grow_filesystem "$_gf" "$(partition_node "$1" "$_gi")" \
            || { GROW_ERROR="מחיצה $_gi ($_gf): ${GROW_ERROR:-הרחבת מערכת הקבצים נכשלה}"; return 1; }
    done < "$_mark.more"
}

grow_filesystem() {
    # $1 = fs, $2 = partition node. The partition has already been enlarged;
    # this makes the filesystem inside it follow. Each family has its own
    # tool (spec section 14) -- btrfs and xfs grow only while mounted.
    # Return the tool result; finish_grow converts failure into a warning.
    # #2011: GROW_GREW=1 -- it failed *after* the filesystem grew (dirty, still mounted).
    GROW_ERROR=""; GROW_GREW=""
    case "$1" in
        ntfs) ntfs_grow_target "$2" ;;   # #2196: the whole NTFS sequence, hibernated included -- ntfsunhib.sh
        ext4|ext3|ext2)
            e2fsck -f -y "$2" >> "$LOG_FILE" 2>&1
            _ck=$?
            # 0=נקי, 1=תוקן. 4 ומעלה = נשארו שגיאות. בלי הבדיקה הזו
            # קוד היציאה נזרק ורק resize2fs נספר — משפחת #346 על מסלול
            # הרסני (#444). חסר אינו 0.
            case "$_ck" in
                ''|*[!0-9]*) log "$2: e2fsck לא החזיר קוד יציאה"; return 1 ;;
            esac
            if [ "$_ck" -ge 4 ]; then
                log "$2: e2fsck נכשל (rc=$_ck)"
                return 1
            fi
            resize2fs "$2" >> "$LOG_FILE" 2>&1
            _rc=$?
            [ "$_rc" = "0" ] && return 0
            log "$2: resize2fs נכשל (rc=${_rc:-לא נרשם})"
            return 1 ;;
        btrfs)
            _m="$RUN_DIR/grow"
            mkdir -p "$_m"
            if ! mount -t btrfs "$2" "$_m" >> "$LOG_FILE" 2>&1; then
                GROW_ERROR="לא הצלחנו לעגן את מערכת הקבצים btrfs"
                return 1
            fi
            btrfs filesystem resize max "$_m" >> "$LOG_FILE" 2>&1
            _rc=$?
            umount "$_m" >> "$LOG_FILE" 2>&1
            _umrc=$?
            if [ "$_rc" -ne 0 ]; then GROW_ERROR="שינוי הגודל של btrfs נכשל (rc=$_rc)"; return 1; fi
            if [ "$_umrc" -ne 0 ]; then GROW_ERROR="btrfs הורחב, אך נשאר מעוגן (umount rc=$_umrc)"; GROW_GREW=1; return 1; fi
            return 0 ;;
        xfs)
            # XFS גדל רק mounted: xfs_growfs על נקודת העגינה, לא על
            # ההתקן (בניגוד ל-ext). RHEL/Fedora/Rocky/Alma (#667).
            _m="$RUN_DIR/grow"
            mkdir -p "$_m"
            if ! mount -t xfs "$2" "$_m" >> "$LOG_FILE" 2>&1; then
                GROW_ERROR="לא הצלחנו לעגן את מערכת הקבצים xfs"
                return 1
            fi
            xfs_growfs "$_m" >> "$LOG_FILE" 2>&1
            _rc=$?
            umount "$_m" >> "$LOG_FILE" 2>&1
            _umrc=$?
            if [ "$_rc" -ne 0 ]; then
                GROW_ERROR="xfs_growfs נכשל (rc=$_rc)"
                log "$2: xfs_growfs נכשל (rc=${_rc:-לא נרשם})"
                return 1
            fi
            if [ "$_umrc" -ne 0 ]; then GROW_ERROR="xfs הורחב, אך נשאר מעוגן (umount rc=$_umrc)"; GROW_GREW=1; return 1; fi
            return 0 ;;
        *)
            # המועמד כבר גדל ב-GPT. "אין כלי" אינו הצלחה (#444).
            log "אין כלי הרחבה ל-$1 -- המחיצה גדלה, מערכת הקבצים נשארה בגודל המקורי"
            return 1 ;;
    esac
}

finish_grow() {
    # $1 = disk, אחרי שחזור מלא; $2 = שמות המחיצות של המקור (שיכפול מקומי,
    # ‏#1798; בלי — ריקים, כמו במניפסט); $3 = המניפסט (#1979). ההרחבה היא best-effort (שיטת קרונזילה):
    # דיסק שקיבל תמונה מלאה הוא done — משכפלים בכל מקרה. כישלון הרחבה
    # אינו מפיל את המגירה; הוא נרשם כ-warning בשדה האבחון, וה-clone
    # נשאר done (#648). ‏grow_filesystem/grow_expanded שומרים על קודי
    # האבחון שלהם.
    # #2015: a moved NTFS/FAT partition says where it starts -- after the
    # read-back, before the grow (ntfsboot.sh). Not best-effort: BIOS/MBR boot.
    if [ -n "${3:-}" ] && ! boot_hidden_disk "$1" "$3"; then
        fail_written_target "$1" "${BOOT_HIDDEN_ERROR:-מיקום ההתחלה בסקטורי האתחול לא נבדק (ntfsboot.sh לא נטען)}"; return 1
    fi
    _grow_warning=""; rm -f "$RUN_DIR/targets/$1/grow_deferred"
    if ! grow_expanded "$1"; then
        _mark="$RUN_DIR/targets/$1/expanded"
        if [ -f "$_mark" ]; then
            _idx=$(cut -d'|' -f1 "$_mark"); _fs=$(cut -d'|' -f2 "$_mark")
        else
            _idx="?"; _fs="?"
        fi
        # #2011: grown and only the clean-up after it failed -- "הורחב", never "נדחתה" (still a warning)
        if [ "${GROW_GREW:-}" = 1 ]; then _grow_warning="הושלם, עם אזהרה: מחיצה ${_idx:-?} (${_fs:-?}); $GROW_ERROR"
        else _grow_warning="הושלם, אך ההרחבה נדחתה: מחיצה ${_idx:-?} (${_fs:-?}); ${GROW_ERROR:-הרחבת מערכת הקבצים נכשלה}"
            : > "$RUN_DIR/targets/$1/grow_deferred"; fi   # #2196: the table grew, the filesystem did not (lc_tgt's 13th field)
        log "WARNING: $1: $_grow_warning"
    fi
    # #1979: a Linux clone gets its own machine-id and SSH host keys (bottom)
    # -- after the grow, the last write but the mark's.
    # $3 = the manifest; every caller in the agent passes it (tested), and a
    # failure is said on the target like a deferred grow.
    # A subshell: _mount_linux's globals (_d, _n, _mnt...) stay out of the caller's loop.
    if [ -n "${3:-}" ] && ! _lid_why=$(linux_identity_reset "$1" "$3" || { echo "$LINUX_ID_ERROR"; exit 1; }); then
        _grow_warning="${_grow_warning:+$_grow_warning | }זהות הלינוקס לא אופסה: $_lid_why"
        log "WARNING: $1: $_grow_warning"
    fi
    # ‏#1798: the last write is behind us (the grow writes too) -- only now the
    # partitions get their names back (writemark.sh; $2 = the local clone's
    # source names), read back. A mark that stays is a target that is not done:
    # failed, in the open (principle 4). ‏#1877: when the name and the drivers
    # are still to be written (WRITEMARK_HOLD=1), the caller takes it off after
    # them (writemark_release_done) -- a cut there must leave the mark too.
    if [ "${WRITEMARK_HOLD:-0}" = 1 ]; then
        log "$1: the incomplete-write mark stays on until the post-deploy writes (#1877)"
    else
        writemark_release "$1" "${2:-}" || return 1
    fi
    target_set "$1" "done" "$_grow_warning"
    return 0
}

# --- #1979: the clone's own identity ------------------------------------------
#
# An image captured without cleaning carries its /etc/machine-id: every clone
# has the same DHCP DUID (systemd-networkd, NetworkManager) and gets the same
# lease -- and the same SSH host keys. The machine-id is emptied (not
# removed: an empty file is a new id on the next boot, not a "first boot"),
# and the host keys are deleted. Debian and Ubuntu do not make missing host
# keys on their own (sshd refuses to start), so a one-shot unit that runs
# `ssh-keygen -A` before sshd is installed with them. Without systemd there is
# no such unit, the keys stay, and that is said (principle 5). Every drawer,
# round and local clone passes here (finish_grow); a Windows disk returns at
# once, before anything is mounted. "Reset" is what reads back from the disk.
# The mount is hostname_linux.sh's (_mount_linux, btrfs roots included) and
# the release hostname.sh's (_umount_checked) -- both loaded before this runs.

LINUX_ID_ERROR=""
LINUX_ID_UNIT="imagesrv-ssh-hostkeys.service"

_linux_id_files() {
    # $1 = the restored /etc. Empties machine-id, deletes the host keys and
    # installs the unit that remakes them. Returns 1 with the reason set.
    if [ -f "$1/machine-id" ]; then
        : > "$1/machine-id" || { LINUX_ID_ERROR="לא הצלחנו לרוקן את /etc/machine-id"; return 1; }
    fi
    # dbus keeps a copy when it is not a link to /etc/machine-id.
    _dbus="${1%/etc}/var/lib/dbus/machine-id"
    [ -L "$_dbus" ] || rm -f "$_dbus" || { LINUX_ID_ERROR="לא הצלחנו למחוק את machine-id של dbus"; return 1; }
    ls "$1"/ssh/ssh_host_* > /dev/null 2>&1 || return 0
    [ -d "$1/systemd/system" ] || { LINUX_ID_ERROR="מפתחות ה-SSH לא הוחלפו: אין systemd שייצור חדשים"; return 1; }
    mkdir -p "$1/systemd/system/multi-user.target.wants" \
        && printf '[Unit]\nDescription=ImageSrv: new SSH host keys for this clone (#1979)\nBefore=ssh.service sshd.service\nConditionPathExists=/usr/bin/ssh-keygen\n\n[Service]\nType=oneshot\nExecStart=/usr/bin/ssh-keygen -A\n\n[Install]\nWantedBy=multi-user.target\n' \
            > "$1/systemd/system/$LINUX_ID_UNIT" \
        && ln -sf "../$LINUX_ID_UNIT" "$1/systemd/system/multi-user.target.wants/$LINUX_ID_UNIT" \
        && rm -f "$1"/ssh/ssh_host_* \
        || { LINUX_ID_ERROR="לא הצלחנו להחליף את מפתחות ה-SSH"; return 1; }
}

linux_identity_reset() {
    # $1 = disk, $2 = manifest. 0 = no Linux on the disk, or the identity was
    # reset and read back; 1 = the reason is in $LINUX_ID_ERROR.
    LINUX_ID_ERROR=""
    _lid_plan="$RUN_DIR/identity.plan"
    manifest_plan "$2" > "$_lid_plan" || { LINUX_ID_ERROR="לא הצלחנו לקרוא את תוכנית המחיצות"; return 1; }
    grep -qE '^[^|]*\|[^|]*\|linux\|' "$_lid_plan"
    case $? in
        0) ;;
        1) return 0 ;;
        *) LINUX_ID_ERROR="לא הצלחנו לבדוק את תוכנית המחיצות"; return 1 ;;
    esac
    # Every Linux partition, not the first: /boot or /home carry the same type
    # GUID as the root, and may come first. Mounted with no /etc = not a root.
    _lid_n=0
    for _lid_i in $(awk -F'|' '$3 == "linux" { print $1 }' "$_lid_plan"); do
        awk -F'|' -v i="$_lid_i" '$1 == i' "$_lid_plan" > "$_lid_plan.one"
        _lid_root=$(_mount_linux "$1" "$2" "$_lid_plan.one")
        case $? in
            0) ;;
            2|3) continue ;;
            *) LINUX_ID_ERROR="לא הצלחנו לעגן את מחיצת הלינוקס $_lid_i"; return 1 ;;
        esac
        _linux_id_files "$_lid_root/etc"; _lid_rc=$?
        if [ "$_lid_rc" -eq 0 ] && { [ -s "$_lid_root/etc/machine-id" ] \
                || ls "$_lid_root"/etc/ssh/ssh_host_* > /dev/null 2>&1; }; then
            LINUX_ID_ERROR="machine-id או מפתחות ה-SSH עדיין על הדיסק אחרי האיפוס"; _lid_rc=1
        fi
        _umount_checked "$RUN_DIR/linux" || { LINUX_ID_ERROR="${LINUX_ID_ERROR:-לא הצלחנו לנתק את מחיצת הלינוקס}"; return 1; }
        [ "$_lid_rc" -eq 0 ] || return 1
        log "$1: partition $_lid_i: machine-id emptied, SSH host keys replaced on first boot" >&2
        _lid_n=$((_lid_n + 1))
    done
    [ "$_lid_n" -gt 0 ] || log "$1: no Linux partition holds /etc -- no identity to reset" >&2
    return 0
}
