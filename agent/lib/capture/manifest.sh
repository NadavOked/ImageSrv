# manifest.sh -- how a source disk is described in the manifest it produces.
# POSIX sh (busybox ash).
#
# ‏capture.sh מזרים את הבייטים; כאן יושבים השדות שהמניפסט מצהיר עליהם
# ונגזרים מהדיסק עצמו — ‏role, ‏fs, ‏uuid, ‏used_bytes, ‏os ו-expandable.
# הפרדה ולא שינוי: אותן פונקציות, אותם שמות, אותה סמנטיקה, ושני הקבצים
# נטענים יחד (‏imagesrv-agent טוען את manifest.sh לפני capture.sh).

_partition_role() {
    # $1 = type guid. The roles named in section 1 of the interfaces.
    case "$(printf '%s' "$1" | tr 'a-z' 'A-Z')" in
        C12A7328-F81F-11D2-BA4B-00A0C93EC93B) echo "esp" ;;
        E3C9E316-0B5C-4DB8-817D-F92DF00215AE) echo "msr" ;;
        DE94BBA4-06D1-4D40-A16A-BFD50179D6AC) echo "recovery" ;;
        0FC63DAF-8483-4772-8E79-3D69D8477DE4) echo "linux" ;;
        0657FD6D-A4AB-43C4-84E5-0933C84B4F4F) echo "swap" ;;
        EBD0A0A2-B9E5-4433-87C0-68B6B72699C7) echo "windows" ;;
        *) echo "data" ;;
    esac
}

_fs_of() {
    # $1 = partition node. The filesystem, or one of two words that are not one
    # (#1905, principle 5): `unknown` = blkid answered "no TYPE" (rc 2) AND the
    # head of the partition reads back -- an MSR, a raw partition; `unreadable` =
    # blkid itself failed (4 usage, 8 ambivalent/I-O, 127 missing), or rc 2 on a
    # node whose first 64KiB (every superblock blkid looks for) did not read.
    _fo=$(blkid -o value -s TYPE "$1" 2>/dev/null); _fo_rc=$?
    case "$_fo_rc" in
        0) echo "${_fo:-unknown}" ;;
        2) if dd if="$1" bs=65536 count=1 of=/dev/null 2>/dev/null; then echo unknown; else echo unreadable; fi ;;
        *) echo unreadable ;;
    esac
}

_uuid_of() {
    # ה-UUID של מערכת הקבצים עצמה — לא ה-GUID של רשומת ה-GPT. ל-swap זה מה
    # ש-mkswap חותם בכותרת ומה ש-/etc/fstab של מתקין דביאן מחפש (‏UUID=); בלי
    # לתעדו כל שחזור מקבל UUID חדש וה-swap לא עולה (#48). אין UUID — ריק.
    blkid -o value -s UUID "$1" 2>/dev/null || echo ""
}

_probe_mount() {
    # $1 = node, $2 = fs, $3 = options (always ro), $4 = mount point.
    # ‏NTFS דרך ntfs-3g (‏FUSE, ‏built-in בקרנל) ולא `mount -t ntfs`: בקרנל
    # דביאן 13 אין סוג `ntfs` (‏CONFIG_NTFS_FS כבוי, ‏ntfs3 לא נארז), ו-mount
    # של busybox אינו מפעיל mount.ntfs. נמדד 13/10 בתוך ה-initramfs עצמו:
    # ‏"No such device", ‏used_bytes=null בכל מחיצת NTFS (#1368). ‏ntfs-3g הוא
    # אותה ספרייה שכבר כותבת את שם המחשב (hostname.sh) ובודקת שינה.
    case "$2" in
        ntfs|ntfs-3g) ntfs-3g -o "$3" "$1" "$4" 2>>"$LOG_FILE" ;;
        *) mount -t "$2" -o "$3" "$1" "$4" 2>>"$LOG_FILE" \
               || mount -o "$3" "$1" "$4" 2>>"$LOG_FILE" ;;
    esac
}

_system_role() {
    # $1 = index, $2 = node, $3 = fs; called for a partition whose GUID says
    # windows. stdout: windows | data. rc 1: not checked, stdout = the reason.
    # ‏#1369: ה-GUID של Basic Data משותף למחיצת המערכת ולכל מחיצת נתונים/OEM,
    # ושתי מחיצות "windows" שלחו את כתיבת השם ואת #1354 אל מחיצת 3GB ריקה.
    # ‏windows רק על ראיה — ‏Windows\System32 במחיצה, בעיגון לקריאה בלבד.
    # ‏Windows עולה רק מ-NTFS, ולכן מערכת קבצים אחרת היא data בלי עיגון.
    rm -f "$RUN_DIR/system.$1"   # #1875
    case "$3" in
        ntfs|ntfs-3g) ;;
        *) log "partition $1: $3, not NTFS -- data, not a system partition" >&2
           echo data; return 0 ;;
    esac
    _sm="$RUN_DIR/sysprobe"
    mkdir -p "$_sm"
    _probe_mount "$2" ntfs ro "$_sm" || {
        echo "מחיצה $1: לא הצלחנו לעגן לקריאה ולבדוק אם היא מחיצת המערכת (Windows\\System32)"
        return 1; }
    _sr=data
    [ -d "$_sm/Windows/System32" ] && _sr=windows
    # ‏#1875: באותו עיגון — כוורת SYSTEM היא הראיה שזו מחיצת המערכת, והיא
    # נרשמת במניפסט ("system": true, ‏_system_json). הסימון לכל אינדקס נמחק
    # בתחילת הפונקציה, כדי שקליטה קודמת באותו אתחול לא תדבר בשם זו.
    _hv=""
    [ "$_sr" = windows ] && [ -f "$_sm/Windows/System32/config/SYSTEM" ] \
        && : > "$RUN_DIR/system.$1" && _hv=" + SYSTEM hive"
    # עיגון שנשאר אינו "נבדק" — הקריאה של partclone הייתה רצה מול כרך חי.
    umount "$_sm" 2>>"$LOG_FILE" || {
        echo "מחיצה $1: הניתוק אחרי בדיקת מחיצת המערכת נכשל"; return 1; }
    log "partition $1: $_sr (Windows\\System32$_hv checked read-only)" >&2
    echo "$_sr"
}

_linux_system() {
    # $1 = index, $2 = node, $3 = fs; a partition whose GUID says linux. #1899:
    # the root is marked "system": true on evidence -- etc/os-release or
    # etc/fstab on it, or one level down on btrfs (Fedora "root", Ubuntu "@").
    # A separate /boot holds neither. Only the filesystems we capture are
    # mounted (ext4, btrfs); rc 1 = not checked, stdout = the reason.
    rm -f "$RUN_DIR/system.$1"
    case "$3" in ext4|btrfs) ;; *) return 0 ;; esac
    _lm="$RUN_DIR/sysprobe"; mkdir -p "$_lm"
    _probe_mount "$2" "$3" ro "$_lm" || {
        echo "מחיצה $1: לא הצלחנו לעגן לקריאה ולבדוק אם היא שורש הלינוקס (/etc)"; return 1; }
    for _le in "$_lm/etc" "$_lm"/*/etc; do
        if [ -f "$_le/os-release" ] || [ -f "$_le/fstab" ]; then : > "$RUN_DIR/system.$1"; break; fi
    done
    umount "$_lm" 2>>"$LOG_FILE" || { echo "מחיצה $1: הניתוק אחרי בדיקת שורש הלינוקס נכשל"; return 1; }
    [ -f "$RUN_DIR/system.$1" ] && log "partition $1: the linux root (/etc found read-only)" >&2
    return 0
}

_system_json() {
    # $1 = role, $2 = index. ‏,"system":true on the partition _system_role found
    # the SYSTEM hive on (#1875), or _linux_system found /etc on (#1899), in
    # THIS capture; nothing otherwise.
    case "$1" in windows|linux) [ -f "$RUN_DIR/system.$2" ] && printf ',"system":true' ;; esac
    return 0
}

_used_bytes() {
    # $1 = node, $2 = fs. Mounted read-only just to measure; a partition that
    # will not mount is reported as null (not measured) rather than guessed --
    # null and a measured 0 are two states, and the manifest keeps them apart.
    _m="$RUN_DIR/probe"
    mkdir -p "$_m"
    # A read-only mount can still replay a journal. Capacity preflight must
    # not change the source before the operator has approved shrinking it.
    _probe_opts=ro
    case "$2" in ext2|ext3|ext4) _probe_opts=ro,noload ;; btrfs) _probe_opts=ro,nologreplay ;; esac
    # ‏-t מפורש, ולא זיהוי אוטומטי: ‏mount בלי סוג בוחר רק מבין מערכות הקבצים
    # שכבר טעונות (/proc/filesystems), ואילו `-t ext4` מבקש מהקרנל לטעון את
    # המודול. כל עוד עץ kernel/fs לא נארז בכלל שני המסלולים נכשלו, וכל
    # המחיצות דיווחו used_bytes=0 (#84). ‏stderr ליומן ולא ל-/dev/null:
    # "לא הצלחנו לעגן" ו"עגנו וזה ריק" הם שני מצבים שונים, והסיבה מבדילה.
    if _probe_mount "$1" "$2" "$_probe_opts" "$_m"; then
        # ‏-kP ולא -B1: ה-df של busybox לא מכיר ‎-B, וכל המחיצות דווחו
        # בשקט used_bytes=0 (נתפס באימג' הראשון של המעבדה, #12).
        _used=""
        if _usage=$(df -kP "$_m" 2>>"$LOG_FILE"); then
            _used=$(printf '%s\n' "$_usage" | awk 'NR==2 && $3 ~ /^[0-9]+$/ {printf "%.0f\n", $3 * 1024}')
        fi
        if umount "$_m" 2>>"$LOG_FILE" && [ -n "$_used" ]; then
            echo "$_used"; return 0
        fi
    fi
    # An image captured from a hibernated Windows mounts dirty. Section 9 of the
    # spec tells the operator to run powercfg /h off first; saying so here is the
    # difference between a puzzling number and a known cause. ‏>&2: הפונקציה
    # נקראת בתוך $( ) ו-log מדבר גם ל-stdout — אחרת האזהרה שוברת מניפסט (#12).
    log "WARNING: $1 would not mount -- used_bytes unknown (hibernation? run powercfg /h off)" >&2
    # ‏null ולא 0: "לא הצלחנו למדוד" אינו "מדדנו, ריק" (עיקרון 5, #298). זהו
    # ערך JSON חוקי בשדה קיים — schema נשאר 1, וקורא ישן שמצפה למספר רואה null.
    echo null
}

_image_os() {
    # The image's operating system (spec section 14), from the partition
    # roles alone: a windows partition makes it windows, else linux.
    case "$1" in
        *'"role":"windows"'*) echo "windows" ;;
        *'"role":"linux"'*)   echo "linux" ;;
        *)                    echo "unknown" ;;
    esac
}

_mark_expandable() {
    # Marks the windows/linux partition that sits last *on the disk* -- by
    # start_sector, not by position in the list. The two are not the same thing:
    # this list is built in index order (that is what `sgdisk -p` prints), and on
    # the Debian cloud image the root is partition 1, first in the list and last
    # on the platter. Reading the list in order picked partition 16 (`data`),
    # marked nothing, and every 256->500 restore of that image quietly ended with
    # 244GB unallocated (#58).
    #
    # Whatever follows the candidate no longer disqualifies it. Restore rewrites
    # the whole table from the manifest anyway, so it moves that tail to the end
    # of the disk before a byte arrives and stretches the candidate up to it:
    # swap is recreated there (spec section 14, #46), anything else -- the
    # recovery partition that Windows 11 always puts last -- is written there
    # from its own stream file, by index (#58). The server enforces the same line
    # at intake. A manifest without start_sector (there is no such thing in
    # practice) falls back to the last windows/linux in the list, as before.
    printf '%s' "$1" | awk '{
        n = split($0, parts, "},");
        best = 0; pick = 0;
        for (i = 1; i <= n; i++) {
            if (parts[i] !~ /"role":"(windows|linux)"/) continue;
            s = -1;
            if (match(parts[i], /"start_sector":[0-9]+/))
                s = substr(parts[i], RSTART + 15, RLENGTH - 15) + 0;
            if (pick == 0 || s >= best) { best = s; pick = i; }
        }
        if (pick > 0)
            sub(/"expandable":false/, "\"expandable\":true", parts[pick]);
        out = "";
        for (i = 1; i < n; i++) out = out parts[i] "},";
        printf "%s%s", out, parts[n];
    }'
}

# --- ה-GUID הייחודי של המחיצה: נקרא בקליטה ונקרא בחזרה בשחזור (#1212) --------
# ה-BCD של Windows מאתר את מחיצת המערכת לפי זוג GUID — של הדיסק ושל
# המחיצה (#26). עד #1212 הקריאה בקליטה נבלעה ב-`2>/dev/null` והפכה ל-"",
# ‏apply_gpt דילג על `-u` כשהערך ריק, sgdisk המציא GUID חדש, ואיש לא קרא
# אותו בחזרה: ‏winload.efi 0xc000000e על כל מחשב משוחזר, אחרי done. קורא
# אחד לשני הצדדים (capture.sh, ‏verify_table ב-expand.sh), ופרסור אחד.

valid_guid() {
    # $1 = value. 8-4-4-4-12 hex digits and nothing else.
    [ "${#1}" -eq 36 ] && printf '%s\n' "$1" \
        | LC_ALL=C grep -Eqx '[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}'
}

read_unique_guid() {
    # $1 = disk node, $2 = partition index. rc 0: stdout is the GUID.
    # Otherwise stdout is the reason, and the rc says which of three states:
    # 1 = sgdisk failed (nothing about the partition was read, its type
    # included), 2 = sgdisk printed no unique GUID, 3 = what it printed is
    # not a GUID. ‏stderr ליומן ולא ל-/dev/null: הסיבה של הכלי היא הראיה.
    _rg_out=$(sgdisk -i "$2" "$1" 2>> "$LOG_FILE")
    _rg_rc=$?
    [ "$_rg_rc" -eq 0 ] || { echo "sgdisk -i $2 נכשל (rc=$_rg_rc)"; return 1; }
    _rg=$(printf '%s\n' "$_rg_out" \
        | awk -F': ' '/Partition unique GUID/ { print $2; exit }' | awk '{print $1}')
    [ -n "$_rg" ] || { echo "sgdisk -i $2 לא הדפיס GUID ייחודי"; return 2; }
    valid_guid "$_rg" || { echo "sgdisk -i $2 הדפיס '$_rg', שאינו GUID"; return 3; }
    echo "$_rg"
}

unique_guid_gate() {
    # $1 = parts file (index|type_guid|unique_guid|...), $2 = the reads that
    # failed (index|rc|reason). stdout: the refusal, or nothing.
    # ‏rc 1 ו-3 נדחים בכל אימג': sgdisk שנכשל לא קרא גם את סוג המחיצה, ולכן
    # אין ממה לגזור שהאימג' *אינו* Windows; וערך שאינו GUID היה נופל על
    # ‏`sgdisk -u` בכל שחזור. ‏rc 2 (אין ערך) נדחה רק באימג' Windows — אותו
    # כלל כמו `_image_os`, מתפקידי המחיצות. לינוקס פטור כמו disk_guid בשרת
    # (‏server/capture.py: ‏GRUB מאתר לפי UUID של מערכת הקבצים), ונרשם ריק
    # כמו עד כאן — אבל ביומן, בשמו, ולא בשקט.
    [ -s "$2" ] || return 0
    _gw=$(awk -F'|' '$2 != 2 { r = $0; sub(/^[^|]*\|[^|]*\|/, "", r)
        print "מחיצה " $1 ": לא ניתן לקרוא את ה-GUID הייחודי — " r; exit }' "$2")
    [ -n "$_gw" ] && { echo "$_gw"; return 0; }
    while IFS='|' read -r _gi _gt _grest; do
        [ "$(_partition_role "$_gt")" = windows ] || continue
        awk -F'|' '{ r = $0; sub(/^[^|]*\|[^|]*\|/, "", r)
            print "מחיצה " $1 ": אימג Windows בלי GUID ייחודי (" r ") — ה-BCD נקשר אליו (#26)"; exit }' "$2"
        return 0
    done < "$1"
    while IFS='|' read -r _gi _grc _gr; do
        log "partition $_gi: $_gr -- recorded empty; not a windows image, exempt like disk_guid" >&2
    done < "$2"
}

verify_unique_guids() {
    # $1 = disk name, $2 = plan file (manifest_plan). Every partition whose
    # manifest carries a unique GUID is read back from the disk and compared.
    # מחיצה בלי GUID במניפסט (אימג' ישן, או לינוקס שנפטר בקליטה) אינה
    # נכשלת, אבל נרשמת "לא נבדק" — "לא נבדק" ו"נבדק, תואם" הם שני מצבים.
    # ‏PLAN_ERROR נושא את הסיבה אל היעד, כמו בשער של apply_gpt (עיקרון 4).
    _vn=0
    while IFS='|' read -r _vi _r _r _r _r _r _r _r _r _vu _r; do
        if [ -z "$_vu" ]; then
            log "$1: partition $_vi has no unique GUID in the manifest -- not checked"
            continue
        fi
        if ! _vg=$(read_unique_guid "$DEVROOT/$1" "$_vi"); then
            PLAN_ERROR="מחיצה $_vi: לא הצלחנו לקרוא בחזרה מהדיסק את ה-GUID הייחודי ($_vg)"
        elif [ "$(printf '%s' "$_vg" | tr 'a-f' 'A-F')" != "$(printf '%s' "$_vu" | tr 'a-f' 'A-F')" ]; then
            log "$1: partition $_vi unique GUID on the disk $_vg, in the manifest $_vu"   # #1924: values in the log only
            PLAN_ERROR="מחיצה $_vi: ה-GUID הייחודי בדיסק אינו של האימג' (#26)"
        else
            _vn=$((_vn + 1))
            continue
        fi
        log "$1: $PLAN_ERROR"
        return 1
    done < "$2"
    log "$1: $_vn partition unique GUIDs came back from the disk"
}
