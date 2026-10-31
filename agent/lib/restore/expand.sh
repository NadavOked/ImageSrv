# expand.sh -- טבלת המחיצות: כתיבתה מהמניפסט, מתיחת המחיצה האחרונה
# לגודל הכונן, והראיה שהכול אכן הגיע לדיסק.
# POSIX sh (busybox ash).
#
# פוצל מ-restore.sh כשזה הגיע לגבול 300 השורות. החלוקה אינה שרירותית:
# כאן יושב כל מה שנוגע ב*טבלת המחיצות* לפני שהגיע בייט אחד, ושם נשאר
# מה שצורך את הזרם עצמו. כאן יושב השלב הראשון של ההרחבה (‏expand_last,
# לפני הזרם) ואיתו apply_gpt, שכותב את אותה טבלה בדיוק ורץ שורה לפניו.
# השלב השני (‏grow_expanded, אחרי הזרם) עבר ל-grow.sh ב-#478: הם אינם
# קוראים זה לזה, אלא מצטמדים דרך הסימון `targets/<disk>/expanded`.
#
# ומכאן גם הראיה: ‏verify_table הוא הצד השני של אותה מטבע — מי שכותב
# את הטבלה קורא אותה בחזרה מהדיסק לפני שמותר לזרם להתחיל. ‏#51 היה
# בדיוק החוסר הזה, ולכן הוא סוגר את הקובץ הזה משני קצותיו.

# כמה שניות מותר לקרנל ול-devtmpfs לבנות את /dev/sdaN אחרי כתיבת
# הטבלה. זו אינה "המתנה שאולי תעזור": הבדיקה נקראת שוב ושוב, מפסיקה
# ברגע שכל הצמתים קיימים, ובסוף התקרה נכשלת בגלוי.
TABLE_SETTLE_S="${TABLE_SETTLE_S:-10}"

# --- הראיה שהטבלה באמת על הדיסק ----------------------------------------------

verify_table() {
    # $1 = disk name, $2 = manifest file. הראיה החיובית שהטבלה הגיעה
    # לדיסק: כל אינדקס שבתוכנית נקרא בחזרה כצומת מחיצה, והמספר חייב
    # להשתוות. ‏sgdisk שיצא 0 בזמן שהקרנל לא בנה את /dev/sdaN הוא בדיוק
    # המצב שבו partclone פותח **קובץ רגיל** ב-devtmpfs, כותב את המחיצה
    # ל-RAM ויוצא 0 — וה-sha256 עובר, כי הוא נלקח על הבייטים שהתקבלו
    # ולא על מה שיושב על הדיסק (#51). "לא צעק" אינו "נכתב".
    _plan="$RUN_DIR/table${PREP_TAG:-}.plan"   # PREP_TAG: one file per drawer when they run at once (#1704)
    manifest_plan "$2" > "$_plan" \
        || { log "$1: no partition plan to check the table against"; return 1; }
    _want=$(awk 'END { print NR }' "$_plan")
    [ "$_want" -gt 0 ] || { log "$1: the partition plan is empty"; return 1; }
    _spent=0
    while :; do
        _live=0
        _missing=""
        for _i in $(cut -d'|' -f1 "$_plan"); do
            if node_is_block "$(partition_node "$1" "$_i")"; then
                _live=$((_live + 1))
            else
                _missing="$_missing $_i"
            fi
        done
        [ "$_live" -eq "$_want" ] && break
        [ "$_spent" -ge "$TABLE_SETTLE_S" ] && break
        sleep 1
        _spent=$((_spent + 1))
    done
    if [ "$_live" -ne "$_want" ]; then
        log "$1: $_live of $_want partitions came back from the disk (missing:$_missing)"
        return 1
    fi
    log "$1: all $_want partitions are live block devices"
    # GUID הדיסק נקרא בחזרה מהדיסק, לא מקוד היציאה של sgdisk -U:
    # ethtool -s יוצא 0 גם כשהדגל לא נתפס (R17), ו-sgdisk באותה משפחה.
    # Windows קושר את ה-BCD ל-GUID הזה — ערך שלא נכתב הוא #26 על כל כיתה.
    # מניפסט בלי GUID (null/ריק) מדלג, כמו apply_gpt — זה #571, לא כאן.
    _want_guid=$(json_get "$2" ".disk_guid")
    if [ -n "$_want_guid" ] && [ "$_want_guid" != "null" ]; then
        _got_guid=$(sgdisk -p "$DEVROOT/$1" 2>/dev/null \
            | awk -F': ' '/Disk identifier/ { print $2 }' | awk '{print $1}')
        if [ "$_got_guid" != "$_want_guid" ]; then
            log "$1: disk GUID on the disk is ${_got_guid:-empty}, not $_want_guid"
            return 1
        fi
        log "$1: disk GUID $_got_guid came back from the disk"
    fi
    # ‏#1212: וה-GUID הייחודי של כל מחיצה — אותו זוג, אותה ראיה (manifest.sh).
    verify_unique_guids "$1" "$_plan"
}

apply_gpt() {
    # $1 = disk name, $2 = manifest file.
    # The partition table never travels in the stream -- it is derived
    # from the manifest alone, then the GPT backup is pushed to the real
    # end of the disk (matters when restoring onto a bigger drive).
    PLAN_ERROR=""   # מאופס לפני השער הראשון: מגירה קודמת לא מדברת בשם הנוכחית
    # כל מספר בטבלה הוא סקטורים: אימג' 512n על יעד 4Kn = הכול פי 8 שגוי (#958).
    # לכן היעד נמדד (`blockdev --getss`) ומושווה לסקטור שהאימג' נקלט ממנו,
    # לפני ה---zap-all. מניפסט בלי השדה = 512: כל קליטה לפני #670 כתבה 512 קשיח.
    _ss=$(json_get "$2" ".sector_size")
    case "$_ss" in ''|null) _ss=512 ;; esac
    _ts=$(blockdev --getss "$DEVROOT/$1" 2>> "$LOG_FILE")
    case "$_ts" in ''|*[!0-9]*)   # "לא הצלחנו לבדוק" אינו "תואם" (עיקרון 5)
        PLAN_ERROR="לא ניתן לקרוא את גודל הסקטור של הדיסק — לא נכתב בייט"
        log "$1: $PLAN_ERROR"; return 1 ;;
    esac
    if [ "$_ts" != "$_ss" ]; then
        PLAN_ERROR="האימג' נקלט מדיסק $_ss בייט/סקטור; הדיסק הזה $_ts — לא ניתן לשחזר בלי המרה"
        log "$1: $PLAN_ERROR"; return 1
    fi
    [ "$_ss" = "512" ] || { log "unsupported sector size: $_ss"; return 1; }
    _scheme=$(json_get "$2" ".scheme")
    [ "$_scheme" = "gpt" ] || { log "unsupported scheme: $_scheme"; return 1; }

    # השער האחרון לפני מחיקת הטבלה בשורה שמתחת, ומכאן והלאה אין חזרה.
    # מחיצה שאינה swap ובלי קובץ היא **מניפסט פגום**, ולא מחיצת swap:
    # ההסקה ההפוכה שלחה `mkswap` על מחיצת NTFS ומחקה אותה (#424). ‏jq -r
    # מרנדר JSON null כמחרוזת `null`, ולכן שתי הצורות נבדקות. ‏PLAN_ERROR
    # נושא את הסיבה אל `targets/<dev>/error`, כי "לא הצלחנו לכתוב טבלה"
    # אינו אומר לטכנאי איזו מחיצה ולמה (עיקרון 4).
    manifest_plan "$2" > "$RUN_DIR/gate${PREP_TAG:-}.plan" || return 1
    validate_restore_plan "$1" "$2" || return 1
    _bad=$(awk -F'|' '$4 != "swap" && ($7 == "" || $7 == "null") {
        printf "%s%s (%s)", (n++ ? ", " : ""), $1, $4 }' "$RUN_DIR/gate${PREP_TAG:-}.plan")
    if [ -n "$_bad" ]; then
        PLAN_ERROR="למחיצה $_bad אין קובץ והיא אינה swap — המניפסט פגום, הדיסק לא שונה"
        log "$1: $PLAN_ERROR"
        return 1
    fi

    target_write_started "$1" || { PLAN_ERROR="לא הצלחנו לתעד את זהות היעד לפני הכתיבה"; return 1; }
    # ‏#1545: על טבלה ישנה פגומה --zap-all מוחק ויוצא 2 (gptcl.cc); השני רץ על דיסק נקי וחייב 0
    sgdisk --zap-all "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || { log "$1: zap-all exited non-zero (damaged old table?) -- again on the wiped disk"; sgdisk --zap-all "$DEVROOT/$1" >> "$LOG_FILE" 2>&1; } || return 1
    # #1714: the whole table in ONE sgdisk call -- every writing sgdisk sleeps 1s
    # (DiskSync, diskio-unix.cc). Same operations, same order (gptcl.cc runs them
    # in command-line order), `-a 1` before every `-n` (#1448). The lines are the
    # plan checked above: every field passed validate_restore_plan (digits, GUID,
    # hex), so splitting $_gpt is safe. The unique GUID when there is one (BCD
    # pairs disk+partition GUIDs, #26 #1212), the attributes (#1130), -U, then -e
    # (backup to the real end, check 2.5). Its exit code is the one write: != 0 = no table.
    _gpt="-a 1"
    while IFS='|' read -r _idx _guid _role _fs _start _size _f _sha _exp _uguid _uuid _attrs; do
        _gpt="$_gpt -n $_idx:$_start:$((_start + _size / 512 - 1)) -t $_idx:$_guid"
        [ -z "$_uguid" ] || _gpt="$_gpt -u $_idx:$_uguid"
        [ -z "$_attrs" ] || _gpt="$_gpt -A $_idx:=:0x$_attrs"
    done < "$RUN_DIR/gate${PREP_TAG:-}.plan" || return 1
    _gpt="$_gpt$(writemark_args "$RUN_DIR/gate${PREP_TAG:-}.plan")"   # #1798: "not finished", in the same write
    _dguid=$(json_get "$2" ".disk_guid")
    if [ -n "$_dguid" ] && [ "$_dguid" != "null" ]; then _gpt="$_gpt -U $_dguid"; fi
    # מכאן ומטה כל שורה היא ראיה, ואף אחת מהן אינה מסתיימת ב-`|| true`:
    # ‏rereadpt הוא הסימן היחיד שהקרנל קיבל את הטבלה ובנה את /dev/sdaN,
    # ו-verify_table קורא בחזרה מהדיסק. עד כאן הפונקציה נגמרה ב-`sleep 1`
    # ולכן החזירה תמיד 0 — טבלה שנכשלה נראתה כמו טבלה שנכתבה (#51).
    # shellcheck disable=SC2086 # הפיצול של $_gpt מכוון
    sgdisk $_gpt -e "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 \
        || { log "$1: sgdisk refused the table (one call: partitions, disk GUID, backup to the end): $_gpt -e"; return 1; }
    reread_table "$DEVROOT/$1" \
        || { log "$1: the kernel refused to re-read the partition table"; return 1; }
    verify_table "$1" "$2"
}

expand_last() {
    # $1 = disk name, $2 = manifest file. Rewrites the table so the candidate
    # partition fills the disk. Runs whenever the manifest has a candidate and
    # the disk really is 1GiB larger -- and *before* the data arrives, so that
    # the mkswap of the restore loop lands on the swap's final place, once.
    # הסימון הוא לכל כונן בנפרד — בחדר השיכפולים מגירה שנכשלה לא גוררת
    # את השאר, ולכן גם השלב השני של ההרחבה נשאל לכל אחת לחוד.
    _mark="$RUN_DIR/targets/$1/expanded"
    mkdir -p "$RUN_DIR/targets/$1"
    rm -f "$_mark" "$_mark.more"   # #1897: .more = the shrunk partitions that share the room
    # תוכנית חלקית כאן אינה "אין מועמד" אלא מועמד **שגוי**, ואיתו הזזה של
    # מחיצות שאיש לא ספר. כל הקוראים למטה מריצים את manifest_plan בתוך
    # החלפת פקודה או צינור, ושם קוד היציאה שלו אינו נראה — ולכן הוא נבדק
    # פעם אחת כאן, לפני שנגזרת ממנו החלטה.
    manifest_plan "$2" > /dev/null || return 1
    _line=$(_expand_candidate "$2")
    [ -n "$_line" ] || return 0
    _idx=$(echo "$_line" | cut -d'|' -f1)
    _guid=$(echo "$_line" | cut -d'|' -f2)
    _fs=$(echo "$_line" | cut -d'|' -f4)
    _start=$(echo "$_line" | cut -d'|' -f5)
    _size=$(echo "$_line" | cut -d'|' -f6)
    _uguid=$(echo "$_line" | cut -d'|' -f10)
    _attrs=$(echo "$_line" | cut -d'|' -f12)

    # כל מה שיושב אחרי המועמד חוסם את מתיחתו — ה-swap של מתקין דביאן
    # (‏#46) ומחיצת ה-recovery של Windows 11, שהיא הפריסה השכיחה ולא
    # מקרה קצה (#58). בשחזור הטבלה נכתבת מחדש מהמניפסט ממילא, ולכן הזנב
    # נפרס אחרת: המחיצות שאחרי המועמד עוברות לסוף הכונן **בסדר הפיזי
    # שלהן**, והמועמד נמתח עד תחילתן. ‏swap נבראת שם מחדש ב-mkswap (אפיון
    # סעיף 14), וכל השאר נכתבת שם מקובץ הזרם שלה — הזרם כותב לפי אינדקס,
    # והמחיצה כבר נבראה מחדש באותו אינדקס במקום החדש.
    # מי "אחרי" נקבע לפי start_sector ולא לפי סדר הרשימה: הרשימה בסדר
    # אינדקסים, ובאימג' ענן זה אינו הסדר על הדיסק.
    _tail=$(manifest_plan "$2" | awk -F'|' -v s="$_start" '$5 + 0 > s + 0')
    _tailsec=$(printf '%s\n' "$_tail" | awk -F'|' '{ n += int($6 / 512) } END { print n + 0 }')

    _disk_sectors=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null || echo 0)
    _table_end=$((_start + _size / 512))
    # הסף נמדד על מה שבאמת יתווסף למחיצה: הזנב, פחות **כל** מה שיחזור אליו.
    [ $((_disk_sectors - _table_end - _tailsec)) -gt 2097152 ] || return 0
    _add=$((_disk_sectors - 34 - _table_end - _tailsec)); _sh=""; [ ! -e "$RUN_DIR/targets/$1/share.ok" ] || _sh=1
    _tail=$(SHARE_HEAD=$_sh share_tail "$1" "$2" "$_line" "$_tail" "$_add") || return 1   # #1897: the room, by the source ratio (expandshare.sh)
    _head=$_start; [ -z "$_sh" ] || _head=$(share_head "$1" "$2" "$_line" "$_add") || return 1   # #1898: approved (drawergate.sh) -- a shrunk one before it too
    _start=$(printf '%s\n' "$_head" | sed -n 1p); _head=$(printf '%s\n' "$_head" | sed 1d)
    # #2016: rebuilt here anyway -- an odd source start (58653315, field 22/10) is aligned UP to 1MiB.
    _start=$(( (_start + 2047) / 2048 * 2048 ))

    log "expanding partition $_idx to fill the disk"
    # #1714: the tail's move and the candidate's rebuild are ONE sgdisk call, in
    # the order the separate calls ran. The unique GUID survives the rebuild,
    # or the BCD loses the system partition just restored (#26).
    _gpt="-a 1$(_move_to_tail "$_tail" "$_disk_sectors") -d $_idx$(_head_args "$_head") -n $_idx:$_start:0 -t $_idx:$_guid"
    [ -z "$_uguid" ] || _gpt="$_gpt -u $_idx:$_uguid"
    [ -z "$_attrs" ] || _gpt="$_gpt -A $_idx:=:0x$_attrs"
    _gpt="$_gpt$(printf '%s\n%s\n%s\n' "$_line" "$_tail" "$_head" | writemark_args -)"   # #1798: -d/-n rebuilt them unnamed
    # ‏0 בסוף = סוף השטח הפנוי הגדול ביותר, כלומר עד תחילת ה-swap החדשה.
    # shellcheck disable=SC2086 # הפיצול של $_gpt מכוון
    sgdisk $_gpt "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 \
        || { log "$1: sgdisk refused the expanded table (one call): $_gpt"; return 1; }
    # ההרחבה בנתה טבלה **שונה** מזו של apply_gpt, ולכן היא נקראת בחזרה
    # שוב: מי שכתב לא מעיד על עצמו. ‏rereadpt שנכשל היה `|| true` עד כאן,
    # והוא הסימן היחיד שהקרנל בכלל קיבל את הטבלה החדשה (#51).
    reread_table "$DEVROOT/$1" \
        || { log "$1: the kernel refused to re-read the expanded table"; return 1; }
    verify_table "$1" "$2" || return 1
    # מערכת הקבצים גדלה רק כשיהיה בה מה להגדיל — grow_expanded, אחרי הזרם.
    echo "$_idx|$_fs" > "$_mark"
}

_expand_candidate() {
    # $1 = manifest file. The one plan line to stretch, or nothing at all.
    #
    # #59: the console's choice at round-open time, via $EXPAND_OVERRIDE
    # ("auto" / "none" / a partition index) -- set from
    # .session.expand_partition before the restore starts (imagesrv-agent).
    # Unset means exactly "auto": an agent that predates #59, or a caller
    # that never set it, sees the same behaviour as before this file
    # existed at all (principle 1). "none" disables expansion outright --
    # no candidate, no table change. An index that jq can't find in *this*
    # manifest (mismatched image) is not trusted blindly; it falls through
    # to the automatic pick below instead of expanding nothing at all.
    case "${EXPAND_OVERRIDE:-auto}" in
        none) return 0 ;;
        auto) ;;
        *[!0-9]*) ;;
        *)
            _chosen=$(manifest_plan "$1" \
                | awk -F'|' -v i="$EXPAND_OVERRIDE" '$1 == i')
            if [ -n "$_chosen" ]; then
                printf '%s\n' "$_chosen"
                return 0
            fi
            # ‏>&2 חובה: הפונקציה הזו נקראת בתוך `_line=$(_expand_candidate ...)`
            # ב-expand_last, ו-log כותבת גם ל-stdout האמיתי (לא רק ל-LOG_FILE) —
            # בלי ההפניה כאן, הודעת האזהרה הייתה נבלעת לתוך $_line ומשחיתה
            # את כל הפענוח שאחריה (index/size/start וכו', כולם מ-$_line אחד).
            log "expand override $EXPAND_OVERRIDE: no such partition in this manifest -- falling back to the automatic choice" >&2
            ;;
    esac
    #
    # ההחלטה נגזרת מהמניפסט **בכל שחזור**, ולא נקראת מהסימון שבו: הסימון
    # נעשה בקליטה בלבד, ולכן בכל אימג' שנקלט לפני #58 כל המחיצות הן
    # ‏`expandable: false` — ו"אף אחד לא סימן" נראה בדיוק כמו "אל תרחיב".
    # שני מצבים שונים שנראים אותו דבר הם עיקרון 5, וזה מה שהיה משאיר
    # 244GB לא מוקצים בלי שורה אחת שמסבירה למה.
    #
    # מניפסט שמסמן **בדיוק** מחיצה אחת עדיין גובר — זו העקיפה הידנית,
    # וזו גם התאימות לאחור. יותר מאחת (מניפסט שאיש לא אימת) חוזר לבחירה
    # האוטומטית במקום להכריע בין השתיים.
    _marked=$(manifest_plan "$1" | awk -F'|' '$9 == "true"')
    if [ "$(printf '%s\n' "$_marked" | awk 'NF { n++ } END { print n + 0 }')" -eq 1 ]
    then
        printf '%s\n' "$_marked"
        return 0
    fi
    # אחרת: המחיצה האחרונה **פיזית** שתפקידה windows או linux. הסדר הוא
    # של start_sector ולא של הרשימה, שהיא בסדר אינדקסים (באימג' ענן
    # השורש הוא מחיצה 1 — ראשון ברשימה, אחרון על הפלטה). ‏`recovery`
    # לעולם אינה מועמדת: היא אינה windows/linux, ולנפח אותה היה משאיר
    # מחיצת שחזור ענקית ליד מערכת שלא גדלה.
    manifest_plan "$1" | awk -F'|' '
        $3 == "windows" || $3 == "linux" {
            if (best == "" || $5 + 0 > k) { k = $5 + 0; best = $0 }
        }
        END { if (best != "") print best }'
}
