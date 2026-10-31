# shrink.sh -- ‏#87: כיווץ מחיצות ה-NTFS של דיסק המקור לקליטה ("resizable
# image" של FOG). POSIX sh (busybox ash).
#
# דיסק בנייה של 512GB עם 100GB בשימוש מייצר אימג' של 512GB, ובדיקה 2.7
# (‏disk_fits) חוסמת אותו בצדק על כל כונן 256. הרצפה של מחיצה היא הגודל
# שבו נקלטה -- partclone מסרב לשחזר לתוך מחיצה קטנה ממנה -- ולכן מה
# שנקלט חייב להיות מערכת קבצים מכווצת.
#
# ‏#1466 (הכרעת נדב 16/10): **דיסק המקור אינו נכתב לעולם.** הכיווץ רץ על
# עותק copy-on-write בזיכרון (lcsnap.sh: ‏dm-snapshot מעל המחיצה, המאגר
# ב-brd), ו-partclone קורא את העותק: ‏ntfsresize --info (קריאה בלבד) →
# גודל מינימלי + מרווח → אישור של אדם ליד המכונה → עותק, ‏ntfsresize -n
# (התקציב) → ‏ntfsresize -s ו-ntfsfix -d **על העותק** → הזרם → שחרור העותק.
# אין sgdisk, אין rereadpt ואין רשומה בשרת (#926): אין מה להחזיר. אין
# עותק -- הקליטה נעצרת בשם ועם המספרים; "בלי כיווץ" בשאלה הוא האימג'
# בגודל מלא, והוא קריאה בלבד.
#
# ‏#929 (הכרעת נדב 17/09): **כל** מחיצות ה-NTFS-data מכווצות, כל אחת
# במקומה בפריסה, בסדר start_sector. המדידה, היעד והשאלה (אחת לכולן)
# יושבים ב-shrinkplan.sh.
#
# המניפסט מתאר פריסה **מצומצמת**: כל מחיצה שיושבת אחרי מחיצה מכווצת
# (‏D: אחרי C:, ‏recovery של Windows 11) נרשמת עם תחילה שזזה אחורה בסכום
# ההפרשים שלפניה, ו-`source_start_sector` שומר את מקומה האמיתי -- ו-apply_gpt
# בונה את הטבלה הזו על היעד ו-expand_last דוחף את הזנב לסופו. ההפרשים הם
# כפולות של MiB ולכן היישור (2048) נשמר.
#
# ‏**ההחזרה במקום** (‏_shrink_gpt, ‏_shrink_restore_one, ‏shrink_restore_source)
# נשארת לדיסקים שכווצו במקום **לפני** #1466 ועדיין רשומים בשרת: ההצעה
# באתחול (shrinkoffer.sh) מחזירה אותם לגודלם. היא לא מכווצת דבר.
# שם, ‏ntfsfix רק `-d`: ‏`-b` היה מוחק את רשימת הקלאסטרים הפגומים של דיסק
# הבנייה ($BadClus).
#
# #2213: dirty NTFS is refused; hibernation is dropped on the view before shrink.
# שידור ישיר (‏CAPTURE_SINK=discard, ‏#715) מכווץ רק את הזנב, על עותק **אחד**
# שנבנה פעם אחת ונקרא פעמיים (‏_shrink_direct, ‏#1171) -- הקריאה השנייה שם
# משווה sha256 לראשונה, ועותק שנבנה פעמיים אינו מובטח להיות אותם בייטים.
#
# כל צעד נבדק בקוד יציאה ובקריאה חוזרת (עיקרון 5): "לא הצלחנו לכווץ"
# הוא כשל גלוי של הקליטה, לא "כווצנו". ‏SHRINK_ERROR נושא את הסיבה אל
# ‏_capture_failed, כמו PLAN_ERROR ב-expand.sh.

#: אחרי הקליטה המקור חוזר לגודלו (1) -- מחשב הבנייה נשאר כפי שהיה.
#: ‏0 = משאירים אותו מכווץ. כשל בהחזרה הוא אזהרה, לא כשל קליטה: ווינדוס
#: עולה גם ממחיצה קטנה מהדיסק, והאימג' כבר בספרייה.
CAPTURE_SHRINK_RESTORE="${CAPTURE_SHRINK_RESTORE:-1}"
SHRINK_ERROR=""

_shrink_gpt() {
    # $1 disk $2 idx $3 start $4 size_sectors $5 type $6 uguid $7 name $8 attrs.
    # הכניסה נכתבת מחדש (מחיקה+יצירה באותו אינדקס) עם ה-GUID הייחודי, השם
    # והדגלים -- ה-BCD מאתר את מחיצת המערכת לפי זוג ה-GUIDים (#26). ‏`-a 1`
    # לפני `-n`: ‏CreatePartition של sgdisk מיישר תחילה שאינה כפולה של 2048
    # בלי לצעוק (Align), ו-NTFS שאינו בתחילת המחיצה שלו הוא ווינדוס שלא
    # עולה -- כניסה שכבר הייתה כאן נבנית **בדיוק** במקומה. ואז הקרנל מתבקש
    # לקרוא את הטבלה, והתחילה והגודל נקראים בחזרה **מהדיסק** (#51): ‏sgdisk
    # שלא צעק אינו טבלה שהשתנתה. 1/2/3 = איזה צעד נכשל.
    _sg_u=""; [ -n "$6" ] && _sg_u="-u $2:$6"
    _sg_a=""; [ -n "$8" ] && _sg_a="-A $2:=:0x$8"
    # shellcheck disable=SC2086 # הפיצול של $_sg_u/$_sg_a מכוון
    sgdisk -a 1 -d "$2" -n "$2:$3:$(($3 + $4 - 1))" -t "$2:$5" $_sg_u -c "$2:$7" $_sg_a \
        "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || return 1
    blockdev --rereadpt "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || return 2
    _sg_got=$(sgdisk -i "$2" "$DEVROOT/$1" 2>/dev/null \
              | awk -F': ' '/^First sector/ { split($2, f, " ") } /^Partition size/ { split($2, z, " ") }
                            END { print f[1] "|" z[1] }')
    [ "$_sg_got" = "$3|$4" ] || return 3
}

_shrink_entry() {
    # $1 disk $2 idx. מדפיס "name|attrs" מ-sgdisk -i. הדגלים נכתבים מחדש עם
    # הכניסה; קריאה שלא הניבה 16 ספרות הקס אינה "אין דגלים" -- היא סירוב
    # (1, SHRINK_ERROR), לפני שנכתב בייט (עיקרון 5). נקרא עם `> קובץ` ולא
    # ב-`$( )`, כדי ש-SHRINK_ERROR ייכתב במעטפת הזו.
    _se_info=$(sgdisk -i "$2" "$DEVROOT/$1" 2>> "$LOG_FILE")
    _se_name=$(printf '%s\n' "$_se_info" | sed -n "s/^Partition name: '\(.*\)'.*/\1/p")
    _se_attr=$(printf '%s\n' "$_se_info" | awk -F': ' '/^Attribute flags/ { print $2 }' | awk '{print $1}')
    case "$_se_attr" in ????????????????) printf '%s|%s\n' "$_se_name" "$_se_attr" ;; *)
        SHRINK_ERROR="לא הצלחנו לקרוא את כניסת מחיצה $2 בטבלה (sgdisk -i: דגלים '$_se_attr') — הדיסק לא שונה"
        return 1 ;;
    esac
}

shrink_before_capture() {
    # $1 disk $2 parts file $3 sector size. ‏0 = אין מה לכווץ, או כווץ
    # ו-$2 כבר מתאר את הפריסה המצומצמת; ‏1 = הקליטה נעצרת, SHRINK_ERROR אומר למה.
    SHRINK_ERROR=""
    rm -f "$RUN_DIR/targets/$1/shrunk" "$RUN_DIR/parts.orig.txt"
    [ "${CAPTURE_SINK:-upload}" = discard ] && { _shrink_direct "$1" "$2" "$3"; return; }
    shrink_plan "$1" "$2" "$3" || return 1
    _sb_plan="$RUN_DIR/shrink.plan"; [ -s "$_sb_plan" ] || return 0
    # ‏#926: לפי זיכרון השרת (ה-hello שהביא את המשימה) הדיסק כבר מכווץ מקליטה
    # קודמת שלא הוחזרה -- לא מכווצים פעמיים, ולא שואלים סתם.
    shrink_records_refresh || :
    if _sb_pend=$(shrink_pending_for "$1"); then
        SHRINK_ERROR="לפי זיכרון השרת מחיצה $(printf '%s\n' "$_sb_pend" | awk -F'|' '{ s = s (s ? ", " : "") $3 } END { print s }') בדיסק הזה כבר כווצה לקליטה קודמת (רשומה #${_sb_pend%%|*}) ולא הוחזרה לגודלה — אתחלו את מחשב הבנייה ובחרו החזרה, או נקו את הרשומה בקונסולה; הקליטה בוטלה"
        return 1
    fi
    _sb_q=$(shrink_question "$_sb_plan")
    # ‏#906: hello ממשיך בזמן ההמתנה לאדם, והשאלה מגיעה לקונסולה. שאלה אחת לכולן.
    _sb_dec=$(attended "$_sb_q" shrink_ask "$_sb_plan")
    case "$_sb_dec" in
        continue) ;;
        plain) log "partitions captured at their full size by the operator's choice"; return 0 ;;
        *) SHRINK_ERROR="הכיווץ לא אושר ליד המכונה ($_sb_q) — הקליטה בוטלה"; return 1 ;;
    esac
    # ‏#1466 (הכרעת נדב 16/10): **המקור אינו נכתב לעולם**. הכיווץ רץ על עותק
    # copy-on-write בזיכרון (lcsnap.sh: ‏dm-snapshot מעל המחיצה, המאגר ב-brd),
    # ‏partclone קורא ממנו (lc_src_node ב-capture.sh), ואין ntfsresize/ntfsfix/
    # ‏sgdisk על המקור ואין רשומה בשרת (#926) -- אין מה להחזיר. אין עותק (אין
    # dm, אין מקום, טבלה שלא נקראה בחזרה): הקליטה **נעצרת** בשם ועם המספרים;
    # ‏"בלי כיווץ" בשאלה הוא הדרך לאימג' בגודל מלא, והוא לא כותב דבר.
    [ "$3" = 512 ] || { SHRINK_ERROR="סקטור של $3 בייט: העותק בזיכרון נבנה על סקטורים של 512 בלבד — המקור לא נכתב והקליטה נעצרה; קלטו בלי כיווץ"; return 1; }
    _shrink_view "$1" "$2"; _sb_rc=$?
    [ "$_sb_rc" != 3 ] || SHRINK_ERROR="אין עותק לקריאה בלבד לכיווץ: $LC_SNAP_WHY — המקור לא נכתב והקליטה נעצרה; לאימג' בגודל מלא קלטו שוב ובחרו בלי כיווץ"
    [ "$_sb_rc" = 0 ]
}

_shrink_direct() {
    # $1 disk $2 parts file $3 sector size. ‏#1171: שידור ישיר (CAPTURE_SINK=discard)
    # מכווץ **רק את הזנב** של #1202 (‏shrink_plan בלי המרווח הרגיל), אחרי אישור
    # בדיאלוג של הקליטה (נדב 17/10: לא בשקט). "כווץ ושלח" = כיווץ; "שלח בלי
    # כיווץ", ביטול או **בלי תשובה** (אין גואי, פקיעה) = הפריסה בגודלה המלא --
    # כמו בקליטה ובשיכפול המקומי, שום דבר לא מכווץ בלי אדם שאמר כן, אבל
    # השידור עצמו אינו נעצר: יעד קטן נפסל בשמו ובכמה (disk_fits). עותק **אחד**,
    # ושתי הקריאות קוראות אותו -- אותם בייטים, ולכן אותו sha256; הוא משוחרר
    # ב-direct_send_task אחרי השנייה. אין זנב או אין עותק: גם כן גודל מלא, וביומן למה.
    # ‏0 = יוצאים לדרך (‏$2 מצומצם או כפי שהיה); ‏1 = SHRINK_ERROR.
    if ! shrink_plan "$1" "$2" "$3"; then
        [ ! -s "$RUN_DIR/capture.unhib" ] || return 1   # #2213: a required view cannot bypass a failed measure
        log "direct send: no tail -- $SHRINK_ERROR; the layout is sent at its full size"; SHRINK_ERROR=""; return 0
    fi
    [ -s "$RUN_DIR/shrink.plan" ] || return 0
    [ "$3" = 512 ] || { log "direct send: no tail -- $3-byte sectors (the RAM view is 512 only); the layout is sent at its full size"; return 0; }
    _sd_q=$(shrink_question "$RUN_DIR/shrink.plan")
    _sd_dec=$(attended "$_sd_q" shrink_ask "$RUN_DIR/shrink.plan")
    [ "$_sd_dec" = continue ]         || { log "direct send: no tail -- the shrink was not confirmed ($_sd_dec; no answer is cancel); the layout is sent at its full size"; return 0; }
    _shrink_view "$1" "$2"; _sd_rc=$?
    [ "$_sd_rc" = 3 ] || return "$_sd_rc"
    log "direct send: no tail -- no RAM view ($LC_SNAP_WHY); the layout is sent at its full size"
}

_shrink_view() {
    # $1 disk $2 parts file; התוכנית ב-$RUN_DIR/shrink.plan. ‏0 = כווץ על העותק,
    # ‏$2 מתאר את הפריסה המצומצמת ו-partclone קורא את העותק (lc_src_node);
    # ‏3 = אין עותק (‏LC_SNAP_WHY), ולא נשאר ממנו דבר; ‏1 = SHRINK_ERROR.
    [ ! -s "$RUN_DIR/capture.unhib" ] || { capture_snap "$1" "$2"; return; }   # #2213: one plan, one set of views
    _sb_plan="$RUN_DIR/shrink.plan"
    LC_SNAP_RUN="$RUN_DIR/capsnap"; rm -rf "$LC_SNAP_RUN"
    { mkdir -p "$LC_SNAP_RUN" && cp "$_sb_plan" "$LC_SNAP_RUN/shrink.plan" && cp "$2" "$LC_SNAP_RUN/parts.txt"; } \
        || { SHRINK_ERROR="לא הצלחנו להכין את העותק בזיכרון — המקור לא נכתב"; return 1; }
    # #1505: the live table -- every planned partition is shrinking while its view is
    while IFS='|' read -r _sb_idx _sb_rest; do cd_state "$_sb_idx" shrinking 2>/dev/null || :; done < "$_sb_plan"
    lc_snap_shrink "$1"; _sb_rc=$?
    case "$_sb_rc" in
        0) cp "$LC_SNAP_RUN/parts.txt" "$2" || { SHRINK_ERROR="לא הצלחנו לכתוב את הפריסה המצומצמת — המקור לא נכתב"; return 1; }
           log "partitions shrunk on a RAM view of $1 -- the source disk is not written"
           while IFS='|' read -r _sb_idx _sb_rest; do cd_state "$_sb_idx" waiting 2>/dev/null || :; done < "$_sb_plan" ;;
        3) return 3 ;;
        *) SHRINK_ERROR="${LC_ERROR:-הכיווץ של העותק נכשל — המקור לא נכתב}"; return 1 ;;
    esac
}

_shrink_compact() {
    # $1 = mark file (idx|start|size|newsec|...), $2 = original parts file.
    # הפריסה המצומצמת: כל מחיצה מכווצת בגודלה החדש, וכל מחיצה זזה אחורה
    # בסכום ההפרשים של המכווצות שיושבות **לפניה** -- כפולות של MiB, ולכן
    # היישור נשמר. ‏FILENAME ולא FNR==NR: קובץ סימון ריק היה מבלבל את השניים.
    awk -F'|' -v OFS='|' -v mark="$1" '
        FILENAME == mark { n[$1] = $4; st[$1] = $2; d[$1] = $3 - $4; next }
        { s = 0; for (i in st) if (st[i] + 0 < $4 + 0) s += d[i]
          if ($1 in n) $5 = n[$1]
          $4 = sprintf("%.0f", $4 - s); print }' "$1" "$2"
}

shrink_json_extra() {
    # $1 idx $2 start $3 size_sectors $4 sector size. השדות שמבדילים פריסה
    # מכווצת מזו שעל דיסק המקור -- או כלום: שדה חסר = כמו על המקור.
    [ -f "$RUN_DIR/parts.orig.txt" ] || return 0
    awk -F'|' -v i="$1" -v s="$2" -v z="$3" -v ss="$4" '$1 == i {
        if ($5 + 0 != z + 0) printf ",\"shrunk_from_bytes\":%.0f", $5 * ss
        if ($4 + 0 != s + 0) printf ",\"source_start_sector\":%s", $4 }' "$RUN_DIR/parts.orig.txt"
}

_shrink_restore_one() {
    # $1 disk, $2..$9 = שורת סימון (idx start old new type uguid name attrs).
    # הטבלה, ואז מערכת הקבצים. ‏-f -f: מותח גם ווליום שנשאר מלוכלך (ntfsfix -d
    # אחרי הכיווץ נכשל), בלי שאלה. אין ntfsfix לפני -- המקור אינו יעד שיכפול.
    # ‏0 = חזרה; ‏1 ו-_sr1_why אומר איזו מחיצה ומה נפל.
    _sr1_node=$(partition_node "$1" "$2"); _sr1_why=""
    log "restoring partition $2 on $1 to $4 sectors"
    if _shrink_gpt "$1" "$2" "$3" "$4" "$6" "$7" "$8" "$9"; then
        if ntfsresize -f -f --no-progress-bar "$_sr1_node" >> "$LOG_FILE" 2>&1; then
            ntfsfix -d "$_sr1_node" >> "$LOG_FILE" 2>&1 || _sr1_why="דגל ה-dirty לא נוקה אחרי המתיחה (ntfsfix -d)"
        else _sr1_why="ntfsresize לא הצליח למתוח את מערכת הקבצים בחזרה"; fi
    else _sr1_why="טבלת המחיצות לא שוחזרה (שלב $?)"; fi
    [ -n "$_sr1_why" ] || { log "partition $2 restored to $4 sectors"; return 0; }
    _sr1_why="מחיצה $2 נשארה $5 סקטורים במקום $4: $_sr1_why"
    return 1
}

shrink_restore_source() {
    # $1 = disk. הצד השני: כל המחיצות חוזרות לגודלן, בסדר הפוך לכיווץ --
    # ‏D: ואז C:. נקרא פעם אחת -- מסיום הקליטה או מ-_capture_failed -- והסימון
    # נמחק. ‏0 גם כשאין מה להחזיר. כשל = אזהרה בשדה error, לא כשל קליטה;
    # והרשומה בשרת (#926) נסגרת רק כש**כולן** חזרו -- מחיצה אחת שנשארה
    # מכווצת היא דיסק מכווץ.
    # ‏#1466: העותק בזיכרון של הקליטה הזו משוחרר ונקרא בחזרה; כשל = אזהרה.
    if [ -n "${LC_SNAP_RUN:-}" ] && ! lc_snap_teardown; then
        _sr_msg="המקור לא נכתב, אבל העותק בזיכרון לא שוחרר ($LC_SNAP_TD) — אתחלו את מחשב הבנייה לפני קליטה נוספת"
        log "WARNING: $1: $_sr_msg"; echo "$_sr_msg" > "$RUN_DIR/targets/$1/error"; unset LC_SNAP_RUN; return 1
    fi
    unset LC_SNAP_RUN
    _sr_mark="$RUN_DIR/targets/$1/shrunk"
    [ -f "$_sr_mark" ] || return 0
    awk '{ a[NR] = $0 } END { for (i = NR; i >= 1; i--) print a[i] }' "$_sr_mark" > "$RUN_DIR/shrink.undo"
    rm -f "$_sr_mark"
    [ "$CAPTURE_SHRINK_RESTORE" = 1 ] || { log "$1 left shrunk (CAPTURE_SHRINK_RESTORE=$CAPTURE_SHRINK_RESTORE)"; return 0; }
    _sr_bad=""
    while IFS='|' read -r _sr_i _sr_s _sr_o _sr_n _sr_t _sr_u _sr_nm _sr_a <&3; do
        [ -n "$_sr_i" ] || continue
        _shrink_restore_one "$1" "$_sr_i" "$_sr_s" "$_sr_o" "$_sr_n" "$_sr_t" "$_sr_u" "$_sr_nm" "$_sr_a" \
            || _sr_bad="$_sr_bad${_sr_bad:+; }$_sr1_why"
    done 3< "$RUN_DIR/shrink.undo"
    [ -n "$_sr_bad" ] || { shrink_close_record "$1" || :; return 0; }   # #926
    _sr_msg="המקור לא הוחזר לגודלו אחרי הקליטה: $_sr_bad — ווינדוס יעלה"
    log "WARNING: $1: $_sr_msg"
    echo "$_sr_msg" > "$RUN_DIR/targets/$1/error"
    shrink_note_record "$1" "$_sr_msg"   # #926: הרשומה נשארת פתוחה, והקונסולה רואה למה
    return 1
}
