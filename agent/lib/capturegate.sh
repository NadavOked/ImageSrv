# capturegate.sh -- what a source disk must prove before a capture reads a
# byte of it. POSIX sh (busybox ash). capture.sh calls it; imagectl-agent loads it
# with capture.sh. The RAID check it asks (#1808) sits in diskfit.sh: a write asks it too.
#
# ‏#1901: הדיסק שהמפעיל בחר (serial), לא רק השם — סדר sda/sdb מתחלף.
# ‏#1808: דיסק שחבר במערך RAID (Intel RST / ‏DDF / ‏md) אינו דיסק רגיל.
# ‏#1900: מערכת קבצים שהשרת לא יקבל, או מקור 4Kn — נדחים לפני הבייט הראשון,
# לא אחרי שעה של קריאה והעלאה.

#: ‏#1900: התאום של `server/images.py:FILESYSTEMS` (בלי ""; ‏_fs_of לא מדפיס
#: ריק). ‏`tests/test_capture_gates.py` משווה את שתי הרשימות — מקור אחד, לא שניים.
CAPTURE_FILESYSTEMS="ntfs vfat ext4 btrfs swap unknown"

capture_identity_reason() {
    # $1 = disk, $2 = the serial the server recorded when the capture was
    # ordered ("" / "null" = none was known). Prints the refusal, or nothing.
    _ci_now=$(disk_serial "$1")
    case "$2" in
        ''|null)
            # בלי serial מההזמנה: מותר רק כשאין עם מה להתבלבל (#1901, "מקרה מותר").
            _ci_n=$(list_disks | wc -l); _ci_n=$((_ci_n + 0))
            [ "$_ci_n" -eq 1 ] && return 0
            echo "לא ניתן לוודא ש-/dev/$1 הוא הדיסק שנבחר: לא נרשם לו מספר סידורי בהזמנה, ובמחשב $_ci_n דיסקים — לא נקלט" ;;
        *)
            [ "$_ci_now" = "$2" ] && return 0
            echo "/dev/$1 אינו הדיסק שנבחר: בהזמנה מספר סידורי $2, עכשיו ${_ci_now:-לא ידוע} (סדר הדיסקים השתנה?) — לא נקלט" ;;
    esac
}

capture_source_reason() {
    # $1 = disk; $2 (a capture task, and a direct send since #1955) = the
    # serial it was ordered for. Prints the refusal, or nothing.
    if [ $# -ge 2 ]; then
        _cs=$(capture_identity_reason "$1" "$2"); [ -z "$_cs" ] || { echo "$_cs"; return; }
    fi
    _cs=$(raid_member_reason "$1") || { echo "בדיקת ה-RAID לא רצה (diskfit.sh לא נטען) — לא נקלט"; return; }
    [ -z "$_cs" ] || { echo "$_cs"; return; }
    # ‏#1900: השרת מקבל רק מניפסט של 512 (images.py). ריק = לא זמין — capture_disk מסרב לו בשמו.
    _cs=$(logical_block_size "$DEVROOT/$1")
    [ -z "$_cs" ] || [ "$_cs" = 512 ] \
        || echo "מקור 4Kn (סקטור לוגי של $_cs בייט) אינו נתמך — אימג' נשמר בסקטורים של 512 בלבד; לא נקלט"
}

capture_fs_reason() {
    # $1 = partition index, $2 = fs as _fs_of prints it. #1900: refused here,
    # before the space check and the first byte -- not by the server at the end.
    case " $CAPTURE_FILESYSTEMS " in *" $2 "*) return 0 ;; esac
    case "$2" in *[Bb]it[Ll]ocker*) _bitlocker_reason "$1" "$2" ""; return ;; esac
    echo "מחיצה $1: מערכת הקבצים $2 אינה נתמכת לקליטה (נתמכות: ntfs, vfat, ext4, btrfs) — לא נקלט"
}

#: ‏#1824: סוגי המחיצות של דיסק דינמי (LDM) — metadata ו-data (‏sgdisk 4201/4200).
CAPTURE_LDM_GUIDS="5808C8AA-7E8F-42E0-85D2-E1E90434CFB3 AF9B60A0-1431-4F62-BC68-3311714A69AD"

capture_layout_reason() {
    # $1 = disk, $2 = parts file (index|type_guid|...). After the per-partition
    # gates (hibernation, BitLocker), before the space check. Prints
    # "code|reason" -- or nothing; a check that could not run is a refusal too.
    # ‏#1824: מחיצת LDM אינה Basic Data — ‏_partition_role קורא לה data ו-partclone.dd
    # היה מעתיק חצי ממערך דינמי כאילו היה דיסק שלם.
    # ‏#1822: ‏Windows על GPT עולה רק מ-ESP. בלי ESP בדיסק הזה הוא נמצא על דיסק אחר,
    # והאימג' לא יעלה אחרי שחזור. ‏data ולינוקס (‏bios_grub) בלי ESP — נקלטים.
    _cl_esp=0
    while IFS='|' read -r _cl_i _cl_g _cl_r; do
        case " $CAPTURE_LDM_GUIDS " in *" $(printf '%s' "$_cl_g" | tr 'a-f' 'A-F') "*)
            echo "source_dynamic|זה דיסק דינמי של Windows (מחיצה $_cl_i) — אפשר לקלוט רק דיסק Basic; לא נקלט"; return 0 ;; esac
        [ "$(_partition_role "$_cl_g")" = esp ] && _cl_esp=1
    done < "$2"
    [ "$_cl_esp" = 1 ] && return 0
    while IFS='|' read -r _cl_i _cl_g _cl_r; do
        [ "$(_partition_role "$_cl_g")" = windows ] || continue
        _cl_n=$(partition_node "$1" "$_cl_i")
        _cl_s=$(_system_role "$_cl_i" "$_cl_n" "$(_fs_of "$_cl_n")") || { echo "source_disk|$_cl_s — לא נקלט"; return 0; }
        [ "$_cl_s" = windows ] && { echo "source_no_esp|על הדיסק הזה מותקן Windows (מחיצה $_cl_i) אבל אין בו מחיצת אתחול (EFI) — היא על דיסק אחר, ואימג' ממנו לא יעלה אחרי שחזור; לא נקלט"; return 0; }
    done < "$2"
    return 0
}
