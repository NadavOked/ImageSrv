# capture.sh -- reads a source disk into a new image (flow 13.1). POSIX sh (busybox ash).
#
# The inverse of restore: partclone reads only the used blocks, zstd
# compresses, and each partition is uploaded as it is produced. Nothing is
# staged here -- a build machine has no room for a second copy of its own disk.
#
# The manifest is written last and uploaded last. That ordering is the server's
# signal that the capture is complete: it validates every sha256 against the
# manifest before the image enters the library.
#
# תיאור הדיסק במניפסט — ‏role, ‏fs, ‏uuid, ‏used_bytes, ‏os ו-expandable — יושב
# ב-manifest.sh; שני הקבצים נטענים יחד. כאן נשאר מה שמזרים את הבייטים עצמם.
# שערי הקריאה-בלבד (מהובר #651, BitLocker #671) יושבים ב-hibernation.sh;
# כיווץ מחיצת המקור לפני הזרם (#87), על עותק בזיכרון בלבד (#1466) — ב-shrink.sh.
# לאן התוצאה הולכת — ההעלאה, המניפסט וסיבת הכשל — ב-capturesink.sh (#1217).

# ‏#72, נמדד ולא נאמד (‏4GiB מראש `p3` של tiny11, שרת המעבדה): רמה 9 עולה
# פי 3.1 בזמן ומחזירה 1.1% — ‏~80MB על 7.3GB, ‏4 שניות בשידור שאורך 5:54.
CAPTURE_LEVEL="${CAPTURE_LEVEL:-3}"
# ‏-T0 = כל הליבות (הכרעת נדב 16/10). ‏"-T2 מהיר כמו -T0" נמדד על שרת המעבדה (2 ליבות).
# ‏HP-BUILD (‏i5-8400, ‏6 ליבות, קליטה של ~20GB, top כל 15ש', 16/10): ‏zstd ‏15-32% מהמכונה
# (= תקרת -T2) בזמן ש-33-56% idle; העלאה ~70MB/s, שיא 117. ‏OOM של #21 היה במכונת 512MB.
CAPTURE_THREADS="${CAPTURE_THREADS:-0}"
# ‏#87: הכונן ה**קטן ביותר** שהאימג' הזה חייב להיכנס אליו, בבייטים.
# ריק = לא נבדק — וזה נרשם במניפסט כ-`null` ולא כמספר, כי "לא בדקנו"
# ו"בדקנו, נכנס" הם שני מצבים שונים (עיקרון 5). הערך אינו נגזר מהמשפחה:
# ‏`family` היא תווית בת שתי מחלקות ולא מידה, ושני כוננים באותה משפחה
# נבדלים בעשרות ג'יגה. מי שיודע את המספר הוא מי שמדד כונן יעד אמיתי.
CAPTURE_TARGET_BYTES="${CAPTURE_TARGET_BYTES:-}"

capture_pcl_flags() {
    # $1 = partclone tool, $2 = fs. ONE place for both reads of a direct send
    # (directsend.sh): its receivers hash pass 2 against pass 1, and #1472 gave
    # pass 1 alone -a0 -z -- every direct send failed on partition 1 (#1574).
    # -I: the preflight already refused dirty/hibernated NTFS; still read-only.
    # ‏#1472: ‏-a0 -z 524288 כמו lccopy.sh (נמדד שם); ‏fanout -S מגבב את כל הזרם.
    _cf=""; [ "$2" = ntfs ] && _cf="-I"
    [ "$1" = partclone.dd ] || _cf="$_cf -a0 -z 524288"
    echo "$_cf"
}

capture_disk() {
    # $1 = task id, $2 = disk name, $3 = the serial it was ordered for (#1901; a direct send too, #1955). Emits the manifest path on success.
    _task="$1"; _disk="$2"
    rm -f "$RUN_DIR/capture.reason"
    _node_base="$DEVROOT/$_disk"
    capture_stage ntfs_check   # #980 §4: the source checks (GPT, partitions, NTFS hibernation)

    # ‏target_init לפני הבדיקה הראשונה: על יעד שלא נרשם אין לאן לכתוב סיבה,
    # וארבעת הסירובים שלמטה קורים לפני שבייט אחד זז — עד #106 הם הגיעו
    # לקונסולה כ-failed ריק. ‏GPT בלבד נשארת החלטת אפיון; מה שהשתנה הוא
    # שהסירוב אומר *מה כן נמצא*, כי "mbr" הוא אבחנה ו-"failed" אינו.
    # (‏node_is_block ולא `[ -b ]` ישיר, מאותה סיבה שב-restore.sh.)
    target_init "$_disk" 0
    node_is_block "$_node_base" || { _capture_failed "$_disk" "אין דיסק כזה: /dev/$_disk"; return 1; }
    _why=$(capture_source_reason "$_disk" ${3+"$3"}) || _why="שערי המקור לא רצו (capturegate.sh) — לא נקלט"; [ -z "$_why" ] || { _capture_failed "$_disk" "$_why"; return 1; }   # #1901 serial, #1808 RAID, #1900 4Kn (capturegate.sh)
    _sector_size=$(logical_block_size "$_node_base")
    [ -n "$_sector_size" ] \
        || { _capture_failed "$_disk" "גודל הסקטור הלוגי של /dev/$_disk לא זמין"; return 1; }
    _scheme=$(disk_scheme "$_node_base")
    [ "$_scheme" = "gpt" ] || { _capture_failed "$_disk" "הכונן אינו GPT (נמצא: $_scheme)"; return 1; }
    case "$(writemark_state "$_disk")" in   # #1798: a write that was cut is never a source (direct send too)
        clean) ;; incomplete) _capture_failed "$_disk" "$WRITEMARK_HE: הכתיבה האחרונה לדיסק נקטעה — לא נקלט"; return 1 ;;
        *) _capture_failed "$_disk" "לא הצלחנו לבדוק אם הכתיבה האחרונה לדיסק הושלמה — לא נקלט"; return 1 ;;
    esac

    _sectors=$(cat "$SYSROOT/sys/block/$_disk/size" 2>/dev/null || echo 0)
    _disk_bytes=$((_sectors * 512))
    # שתי משפחות בלבד (סעיף 9): מתחת ל-300GB = 256, אחרת 500.
    if [ "$_disk_bytes" -lt 322122547200 ]; then _family=256; else _family=500; fi

    echo "capturing" > "$RUN_DIR/state"
    # מה שבאמת ירוץ, ליד ה-stderr של zstd ביומן: ‏zstd שהודר בלי ריבוי
    # תהליכונים מזהיר ומתעלם מ-`-T`, וזה ההפרש בין "ביקשנו" ל"רצנו".
    log "compressing with zstd -$CAPTURE_LEVEL -T$CAPTURE_THREADS"

    _parts="$RUN_DIR/parts.txt"
    # ה-GUID הייחודי של הדיסק ושל כל מחיצה חייבים לשרוד את השחזור:
    # ה-BCD של Windows מאתר את מחיצת המערכת לפי הזוג הזה, וטבלה עם
    # GUIDים חדשים = ‏winload.efi 0xc000000e על כל מחשב משוחזר (#26). GUID מחיצה שלא נקרא — unique_guid_gate (#1212).
    _disk_guid=$(sgdisk -p "$_node_base" 2>/dev/null \
                 | awk -F': ' '/Disk identifier/ { print $2 }' | awk '{print $1}')
    # index:start:size:type-guid, from the partition table itself.
    sgdisk -p "$_node_base" 2>/dev/null | awk '/^ *[0-9]+ / { print $1 }' > "$RUN_DIR/idx.txt"
    : > "$_parts"; : > "$RUN_DIR/partition.attrs"; : > "$RUN_DIR/uguid.fail"
    while read -r _idx; do
        [ -n "$_idx" ] || continue
        _guid=$(sgdisk -i "$_idx" "$_node_base" 2>/dev/null \
                | awk -F': ' '/Partition GUID code/ { print $2 }' | awk '{print $1}')
        _uguid=$(read_unique_guid "$_node_base" "$_idx") || { echo "$_idx|$?|$_uguid" >> "$RUN_DIR/uguid.fail"; _uguid=""; }
        _first=$(sgdisk -i "$_idx" "$_node_base" 2>/dev/null \
                 | awk -F': ' '/First sector/ { print $2 }' | awk '{print $1}')
        _size=$(sgdisk -i "$_idx" "$_node_base" 2>/dev/null \
                 | awk -F': ' '/Partition size/ { print $2 }' | awk '{print $1}')
        _shrink_entry "$_disk" "$_idx" > "$RUN_DIR/partition.entry" \
            || { _capture_failed "$_disk" "$SHRINK_ERROR"; return 1; }
        printf '%s|%s\n' "$_idx" "$(awk -F'|' '{print $NF}' "$RUN_DIR/partition.entry")" >> "$RUN_DIR/partition.attrs"
        echo "$_idx|$_guid|$_uguid|$_first|$_size" >> "$_parts"
    done < "$RUN_DIR/idx.txt"
    # כותרת GPT תקינה וטבלה ריקה — מצב אחר לגמרי מ"אינו GPT" (עיקרון 5).
    [ -s "$_parts" ] || { _capture_failed "$_disk" "לא נמצאו מחיצות על /dev/$_disk"; return 1; }
    _why=$(unique_guid_gate "$_parts" "$RUN_DIR/uguid.fail"); [ -z "$_why" ] || { _capture_failed "$_disk" "$_why"; return 1; }

    while IFS='|' read -r _idx _guid _uguid _first _sizesec; do
        _node=$(partition_node "$_disk" "$_idx"); _fs=$(_fs_of "$_node")
        _why=$(_bitlocker_reason "$_idx" "$_fs" "$_node"); [ -z "$_why" ] || { _capture_failed "$_disk" "$_why" source_bitlocker; return 1; }   # #1806: every partition, before a byte
        case "$_fs" in ntfs|ntfs-3g)
            _why=$(capture_ntfs_hibernation_reason "$_node" "$RUN_DIR/ntfs-check.$_idx"); _hn_status=$?
            [ -z "$_why" ] || { _hn_code=source_disk; [ "$_hn_status" != 1 ] || _hn_code=source_ntfs; _capture_failed "$_disk" "$_why" "$_hn_code"; return 1; } ;;
            unreadable) _capture_failed "$_disk" "מחיצה $_idx: לא הצלחנו לקרוא את מערכת הקבצים שלה (blkid) — לא נקלט"; return 1 ;;   # #1905: never raw, never past the hibernation gate
            *) _why=$(capture_fs_reason "$_idx" "$_fs") || _why="בדיקת מערכת הקבצים לא רצה (capturegate.sh) — לא נקלט"; [ -z "$_why" ] || { _capture_failed "$_disk" "$_why"; return 1; } ;; esac   # #1900: before the space check, not at the end
    done < "$_parts"; _why=$(capture_layout_reason "$_disk" "$_parts") || _why="source_disk|בדיקת הדיסק הדינמי ומחיצת האתחול לא רצה (capturegate.sh) — לא נקלט"; [ -z "$_why" ] || { _capture_failed "$_disk" "${_why#*|}" "${_why%%|*}"; return 1; }   # #1824 LDM, #1822 no ESP
    # ‏#87: אחרי שער המהובר ולפני חישוב הפריסה — הכיווץ משכתב את $_parts,
    # וכל מה שלמטה (הרצפה, השער החמישי, המניפסט) רואה את הפריסה המצומצמת.
    capture_stage shrink; cd_init "$_disk" "$_parts" "$_sector_size" 2>/dev/null || :   # #1505: the console's partition table
    capture_space_preflight "$_task" "$_disk" "$_parts" "$_sector_size" \
        || { _capture_failed "$_disk" "$CAPTURE_SPACE_ERROR" "${CAPTURE_SPACE_CODE:-network_server}"; return 1; }
    shrink_before_capture "$_disk" "$_parts" "$_sector_size" \
        || { _capture_failed "$_disk" "${SHRINK_ERROR:-הכיווץ נכשל בלי סיבה (shrink.sh לא נטען?)}"; return 1; }

    # ‏min_target_bytes הוא מה שהאימג' באמת צריך — סוף המחיצה האחרונה שבטבלה
    # ועוד 256KiB לגיבוי ה-GPT (#1171) — ולא גודל דיסק המקור (#82): דיסק VM
    # ‏"256GB" הוא 256 GiB, שבעה אחוזים יותר מכל כונן פיזי מאותה מחלקה, וכל עוד
    # הדרישה הייתה גודל המקור אף אימג' זהב שנבנה ב-VM לא נכנס לשום ברזל. הרצפה
    # היא הגודל שבו נקלטה כל מחיצה ולא כמה תפוס בה — partclone מסרב לשחזר לתוך
    # מחיצה קטנה ממנה — ולכן אין כאן used_bytes; אותו כלל בשרת
    # (server/images.py). השדות: $4 סקטור התחלה, $5 גודל בסקטורים.
    _min_target=$(awk -F'|' -v disk="$_disk_bytes" -v sector="$_sector_size" '
        { e = ($4 + $5) * sector; if (e > end) end = e }
        END { need = end + 262144            # ‏#1171: עותק הגיבוי של ה-GPT, בלי עיגול
              if (disk >= end && disk < need) need = disk
              printf "%.0f\n", need }' "$_parts")
    # "לא הצלחנו לחשב" אינו "אין מחיצות" ואינו כשל מחיצה (עיקרון 5).
    [ -n "$_min_target" ] || { _capture_failed "$_disk" "לא הצלחנו לחשב את פריסת המחיצות"; return 1; }

    # ‏#87, השער החמישי: פריסה שאינה נכנסת לכונן היעד נתפסת **כאן**, לפני
    # שבייט אחד זז, ולא מול כיתה. דיסק בנייה גדול מכונן הכיתה — או דיסק
    # ‏VM ‏"256GB" שהוא 256 GiB — מייצר אימג' תקין לגמרי שאין לאן לכתוב
    # אותו, ובדיקה 2.7 בשחזור תחסום אותו בצדק על כל תחנה בנפרד. הסירוב
    # נושא את שלושת המספרים שהופכים אותו לפעולה: מה צריך, מה יש, וכמה
    # לכווץ. הכיווץ עצמו כבר רץ למעלה (shrink.sh) — מה שנשאר גדול מדי, נשאר.
    _floor_json="null"
    if [ -n "$CAPTURE_TARGET_BYTES" ]; then
        # רצפה שאינה מספר אינה "אין רצפה": ערך שגוי שמדלג על הבדיקה הוא
        # בדיוק "לא הצלחנו לבדוק" שנראה כמו "בדקנו, הכל תקין".
        #
        # ‏`0?*` פוסל אפסים מובילים, וזה **לא קפדנות**: הערך נכתב למניפסט
        # כמספר JSON, ו-`010000000000` אינו JSON תקין. ההשוואה המספרית
        # למטה דווקא מצליחה, ולכן הקליטה הייתה רצה עד סופה — המניפסט
        # נכתב אחרון — ורק אז השרת היה דוחה את האימג'. שעה של קריאת
        # דיסק, ואז כישלון שאין לו שום קשר נראה לערך שהוקלד.
        case "$CAPTURE_TARGET_BYTES" in
            *[!0-9]* | 0?*)
                _capture_failed "$_disk" \
                    "רצפת כונן היעד אינה מספר בייטים תקין (ספרות בלבד, בלי אפסים מובילים): $CAPTURE_TARGET_BYTES"
                return 1 ;;
        esac
        if [ "$_min_target" -gt "$CAPTURE_TARGET_BYTES" ]; then
            _capture_failed "$_disk" \
                "הפריסה גדולה מכונן היעד: האימג' צריך $_min_target בייט, הקטן שביעדים מחזיק $CAPTURE_TARGET_BYTES — יש לכווץ $((_min_target - CAPTURE_TARGET_BYTES)) בייט לפני הקליטה$(shrink_unshrinkable_hint "$_disk" "$_parts" "$_sector_size")"
            return 1
        fi
        _floor_json="$CAPTURE_TARGET_BYTES"
    fi

    capture_stage stream; cd_init "$_disk" "$_parts" "$_sector_size" 2>/dev/null || :   # sizes after the shrink
    _json_parts=""
    _total=0
    # ‏#85: ה-ESP נקרא שוב אחרי הלולאה, לגזירת החותם של מטעני האתחול.
    _esp_node=""
    while IFS='|' read -r _idx _guid _uguid _first _sizesec; do
        _node=$(lc_src_node "$_disk" "$_idx")   # #1466: a shrunk partition is read from its RAM view
        _attrs=$(awk -F'|' -v i="$_idx" '$1 == i { print $2 }' "$RUN_DIR/partition.attrs")
        _fs=$(_fs_of "$_node")
        _role=$(_partition_role "$_guid")
        [ "$_role" = windows ] && { _role=$(_system_role "$_idx" "$_node" "$_fs") || { _capture_failed "$_disk" "$_role"; return 1; }; }   # #1369
        [ "$_role" = linux ] && { _why=$(_linux_system "$_idx" "$_node" "$_fs") || { _capture_failed "$_disk" "$_why"; return 1; }; }   # #1899: the root, not /boot
        # A generic linux-data GUID holding swap is still swap; a swap GUID
        # is swap regardless of what blkid says.
        [ "$_fs" = "swap" ] && _role="swap"
        [ "$_role" = "esp" ] && _esp_node="$_node"
        if [ "$_role" = "swap" ]; then
            # Nothing in swap is worth keeping (spec section 14): it is described
            # in the manifest and recreated on restore, never read. ה-UUID כן
            # נשמר — בלעדיו שורת ה-swap ב-fstab לא תמצא את החתימה (#48).
            _uuid=$(_uuid_of "$_node")
            if [ -n "$_uuid" ]; then _ujson="\"$_uuid\""; else _ujson="null"; fi
            log "partition $_idx (swap): recorded, not read"; cd_part "$_idx" swap swap "done" 2>/dev/null || :
            _json_parts="$_json_parts{\"index\":$_idx,\"type_guid\":\"$_guid\",\"unique_guid\":\"$_uguid\",\"attrs\":\"$_attrs\",\"uuid\":$_ujson,\"role\":\"swap\",\"fs\":\"swap\",\"start_sector\":$_first,\"size_bytes\":$((_sizesec * _sector_size)),\"used_bytes\":0,\"file\":null,\"sha256\":null,\"expandable\":false$(shrink_json_extra "$_idx" "$_first" "$_sizesec" "$_sector_size")},"
            continue
        fi
        _file="p$_idx.$_role.pcl.zst"
        _used=$(_used_bytes "$_node" "$_fs"); cd_used "$_idx" "$_used" 2>/dev/null || :
        log "partition $_idx ($_role, $_fs): reading"; cd_part "$_idx" "$_role" "$_fs" reading 2>/dev/null || :

        _out="$RUN_DIR/upload.$_idx"
        rm -f "$_out"
        mkfifo "$_out"
        # The upload runs while partclone is still reading: nothing is
        # staged on this machine. (capture_sink: the consumer of the fifo.)
        ( capture_sink "$_out" "$_task" "$_file" > "$RUN_DIR/up.$_idx.out" 2>> "$LOG_FILE"
          echo "$?" > "$RUN_DIR/up.$_idx.rc" ) &
        _uppid=$!

        rm -f "$RUN_DIR/sha.$_idx" "$RUN_DIR/pcl.$_idx.rc" \
            "$RUN_DIR/zstd.$_idx.rc" "$RUN_DIR/fanout.$_idx.rc" "$_out.bytes" \
            "$RUN_DIR/targets/$_disk/partclone.err"   # ‏#1254: האחוז של המחיצה הזו בלבד (progress.sh)
        _pcl=$(partclone_for_fs "$_fs")
        _ignore=$(capture_pcl_flags "$_pcl" "$_fs")
        # ה-rc של כל שלב נלכד במפורש: ב-busybox ash אין pipefail, ו-$? של
        # הצינור הוא של השלב האחרון בלבד — ‏sha256sum שמצליח גם על קלט ריק
        # הפך כשל אמצע לקובץ "מוצלח" (מעבדה #12, קליטה קטועה #776).
        # ‏#1438: מונה, גיבוב והעתקה ל-fifo — בתוך fanout ולא pv|tee|sha256sum
        # (‏tee של busybox מעתיק 1KiB לקריאה). ‏-S נכתב רק בסוף זרם שנקרא
        # כולו; ‏-P בפורמט pv -n. ‏open() של ה-fifo תחום ב-fanout (5ש'), ו-curl
        # שנפל אחריו הוא EPIPE = ‏rc 1. ברקע, עם עין על המונה: כונן איטי
        # מתקדם לאט — ולכן המדד הוא חוסר התקדמות, לא משך.
        (
            # shellcheck disable=SC2046 # דגלי partclone_mode הם רשימת מילים
            { "$_pcl" $(partclone_mode "$_pcl" -c) $_ignore -s "$_node" \
                  -L "$RUN_DIR/targets/$_disk/partclone.log" 2>> "$RUN_DIR/targets/$_disk/partclone.err"
              echo "$?" > "$RUN_DIR/pcl.$_idx.rc"; } \
                | { zstd -"$CAPTURE_LEVEL" -T"$CAPTURE_THREADS" -c 2>> "$LOG_FILE"
                    echo "$?" > "$RUN_DIR/zstd.$_idx.rc"; } \
                | { fanout -S "$RUN_DIR/sha.$_idx" -P "$RUN_DIR/targets/$_disk/bytes.raw" \
                        16777216 "$_out" > "$RUN_DIR/fanout.$_idx.out" 2>> "$LOG_FILE"
                    echo "$?" > "$RUN_DIR/fanout.$_idx.rc"; }
        ) &
        _readpid=$!
        _stage=pipeline
        if wait_progress "$_readpid" "$RUN_DIR/targets/$_disk/bytes.raw" \
                "$WAIT_STREAM_START_S" "$WAIT_STREAM_STALL_S" \
                "קריאת מחיצה $_idx מ-$_disk" "$_out"; then
            for _stage in fanout zstd pcl; do
                _rc=$(cat "$RUN_DIR/$_stage.$_idx.rc" 2>/dev/null || echo 1)
                [ "$_rc" -eq 0 ] || break
            done
            [ "$_rc" -eq 0 ] && _stage=none
        else
            _rc="$WAIT_TIMED_OUT"
        fi
        # ה-stderr של partclone ממשיך ליומן — אחרי המחיצה במקום תוך כדי.
        [ ! -f "$RUN_DIR/targets/$_disk/partclone.err" ] || cat "$RUN_DIR/targets/$_disk/partclone.err" >> "$LOG_FILE"
        # ממתינים להעלאה בלבד, בשם: wait ריק תופס גם את דמון ההתקדמות
        # שרץ ברקע — וכשהפונקציה רצה ב-shell הראשי זה נעצר לנצח (מעבדה #12).
        # ובתקרה: ‏curl שנתקע בפתיחת ה-fifo לא ייתלה כאן לנצח.
        wait_pid "$_uppid" "$WAIT_HELPER_S" "העלאת מחיצה $_idx" \
            || echo "$WAIT_TIMED_OUT" > "$RUN_DIR/up.$_idx.rc"
        rm -f "$_out" "$_out.bytes"

        _uprc=$(cat "$RUN_DIR/up.$_idx.rc" 2>/dev/null || echo 1)
        if [ "$_rc" -ne 0 ] || [ "$_uprc" -ne 0 ]; then
            # הסיבה מהכלי עצמו — זה מה שהקונסולה תציג ליד failed.
            _why=$(tail -n 1 "$RUN_DIR/targets/$_disk/partclone.log" 2>/dev/null | tr -d '\r')
            log "partition $_idx capture tool said: ${_why:-nothing}"   # the tool's own line: log, not the headline
            _cf_code=unknown; [ "$_uprc" -eq 0 ] || _cf_code=network_server; [ "$_stage" != pcl ] || _cf_code=source_disk
            _capture_failed "$_disk" \
                "מחיצה $_idx: הקליטה נכשלה (שלב=$_stage rc=$_rc העלאה=$_uprc)" "$_cf_code"
            return 1
        fi

        _sha=$(awk '{print $1}' "$RUN_DIR/sha.$_idx" 2>/dev/null)
        # ‏-S נכתב רק ב-EOF נקי (#1438): בלי ערך — אין ראיה, ואין "sha256":"" במניפסט.
        is_sha256 "$_sha" || { _capture_failed "$_disk" "partition $_idx: ה-sha256 לא חושב -- אין ראיה שהזרם הגיע שלם"; return 1; }
        # הבייטים של המחיצה הזו בלבד — מונה ה-pv שלה. target_bytes מחזיר
        # מצטבר-סבב, וחיבורו בכל סיבוב ניפח את total_compressed_bytes
        # פי כמה (האימג' הראשון במעבדה: 1.8GB במקום 490MB, ‏#12).
        _bytes=$(tr -d '\r' < "$RUN_DIR/targets/$_disk/bytes.raw" 2>/dev/null | tail -n 1)
        [ -n "$_bytes" ] || _bytes=0
        _total=$((_total + _bytes))
        target_partition_done "$_disk"; cd_part "$_idx" "$_role" "$_fs" "done" 2>/dev/null || :

        # Every partition is written not expandable; _mark_expandable picks
        # the one candidate once the whole list is known.
        _json_parts="$_json_parts{\"index\":$_idx,\"type_guid\":\"$_guid\",\"unique_guid\":\"$_uguid\",\"attrs\":\"$_attrs\",\"role\":\"$_role\",\"fs\":\"$_fs\",\"start_sector\":$_first,\"size_bytes\":$((_sizesec * _sector_size)),\"used_bytes\":$_used,\"file\":\"$_file\",\"sha256\":\"$_sha\",\"expandable\":false$(_system_json "$_role" "$_idx")$(shrink_json_extra "$_idx" "$_first" "$_sizesec" "$_sector_size")$(rb_capture_json "$_node" "$_fs" "$((_sizesec * _sector_size))")},"   # #1805: a direct send's digest
    done < "$_parts"
    # ‏#1466: הבייטים עברו; העותק בזיכרון משוחרר (המקור לא נכתב). כשל = אזהרה בלבד.
    capture_stage restore_size
    [ "${CAPTURE_SINK:-upload}" = discard ] || shrink_restore_source "$_disk"   # #1171: a direct send reads the view again; direct_send_task releases it

    _json_parts=${_json_parts%,}
    _json_parts=$(_mark_expandable "$_json_parts")
    _os=$(_image_os "$_json_parts")
    # ‏#85, אחרי שהבייטים כבר עברו: החותם הוא מידע על האימג', לא שער עליו.
    # ‏boot_ca_json לעולם אינו נכשל — הוא מחזיר `null` וסיבה.
    _bootca=$(boot_ca_json "$_esp_node")
    # תשובה ריקה היתה מייצרת `,,` — מניפסט שאינו JSON, אחרי שכל הבייטים
    # כבר עברו והשרת כבר קיבל אותם. "לא הצלחנו לגזור" הוא מצב מוכר ויש
    # לו ייצוג; מניפסט פגום אינו מצב, הוא תקלה שמתגלה בסוף שעה של קריאה.
    [ -n "$_bootca" ] \
        || _bootca='"boot_ca":null,"boot_ca_error":"החותם לא נגזר בקליטה הזו"'

    # הנתיב קבוע והקורא מכיר אותו. בלי echo של הנתיב ל-stdout: log()
    # מדבר גם הוא ל-stdout, ולכידת $(capture_disk) החזירה פעם בליל
    # שורות לוג במקום נתיב — וה-curl של המניפסט נכשל בשקט (מעבדה, #12).
    printf '{"schema":1,"family":%s,"os":"%s",%s,"source_disk_bytes":%s,"min_target_bytes":%s,"target_floor_bytes":%s,"scheme":"gpt","sector_size":%s,"disk_guid":"%s","partitions":[%s],"total_compressed_bytes":%s,"compression":"zstd-%s"}\n' \
        "$_family" "$_os" "$_bootca" "$_disk_bytes" "$_min_target" "$_floor_json" "$_sector_size" "$_disk_guid" "$_json_parts" "$_total" "$CAPTURE_LEVEL" \
        > "$RUN_DIR/new-manifest.json"
}
