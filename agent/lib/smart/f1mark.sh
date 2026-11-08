# f1mark.sh -- #1858 (נדב 07/11): "נצפה F1 בהדלקה" לפי מספר סידורי.
# POSIX sh (busybox ash).
#
# ה-BIOS עוצר על F1 לפני שהסוכן עולה, והסוכן לא רואה את זה (עיקרון 5א): את הראיה
# רושם אדם בקונסולה, או השרת (מכונה שהוצגה "לא הגיע · ייתכן F1" וחזרה). ה-hello
# עונה `f1_observed` -- [{serial, date, source}] לדיסקים שהמכונה שלחה -- וכאן זה
# נעשה אדום: אותו אדום של קריאה-בלבד (smart.sh, smart_ro_check), סיבה `f1_observed`,
# ולכן גם מדולג בשיכפול (#1732). **שום דבר לא נכתב על הדיסק**. הזהות היא ה-serial
# בלבד: דיסק חדש באותו חריץ נקי. נקרא רק אם הקובץ הזה נטען (`command -v`).

SMART_F1_HE="נצפה F1 בהדלקה — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת"

disk_f1_refresh() {
    # תשובת ה-hello האחרונה -> $RUN_DIR/disk_f1, שורה לדיסק: serial|date|source. השורה
    # הראשונה "ok" היא הראיה שהשדה נקרא; שדה חסר (שרת ישן / קריאה שנכשלה / קובץ באמצע
    # כתיבה) אינו "אין סימונים" -- הקובץ הקודם נשאר (עיקרון 5). 0 = רוענן.
    _f1_n="$RUN_DIR/disk_f1.next"
    json_get_join "$RUN_DIR/response.json" \
        'if has("f1_observed") then ["ok"] + (.f1_observed | map("\(.serial // "")|\(.date // "")|\(.source // "")")) else [] end' \
        > "$_f1_n" 2>/dev/null
    [ "$(head -n 1 "$_f1_n" 2>/dev/null)" = ok ] || { rm -f "$_f1_n"; return 1; }
    mv "$_f1_n" "$RUN_DIR/disk_f1"
}

disk_f1_observed() {
    # $1 = disk. מדפיס date|source של הסימון **לפי ה-serial** ומחזיר 0; אין סימון,
    # אין serial, מקור שאינו manual/auto (שורה שאינה סימון), או שהקובץ עוד לא נקרא -> 1 ובלי פלט.
    [ -f "$RUN_DIR/disk_f1" ] || return 1
    _fo_s=$(disk_serial "$1"); [ -n "$_fo_s" ] || return 1
    awk -F'|' -v s="$_fo_s" 'NF >= 3 && $1 == s && ($3 == "manual" || $3 == "auto") { print $2 "|" $3; f = 1; exit } END { exit !f }' \
        "$RUN_DIR/disk_f1" 2>/dev/null
}

disk_f1_red() {
    # $1 = disk. מ-smart_ro_check. 0 = הדיסק מסומן: הרשומה היא `fail f1_observed`, וה-SMART האמיתי שלו
    # נשמר מתחתיה (`.under`, עם ה-serial) -- "נצפה F1" אינו "SMART תקול", והמסך אומר איזה מהשניים.
    # דיסק שה-SMART שלו אדום ממילא נשאר באדום שלו. 1 = לא מסומן.
    disk_f1_observed "$1" >/dev/null || return 1
    [ "$(smart_field "$1" 2)" = f1_observed ] && return 0
    SMART_F1_OFF=1; smart_probe "$1" >/dev/null; unset SMART_F1_OFF   # ה-probe האמיתי, פעם אחת (מטמון לפי serial)
    _fr_v=$(smart_field "$1" 1)
    printf '%s|%s\n' "$(disk_serial "$1")" "${_fr_v:-unchecked}" > "$RUN_DIR/smart/$1.under"
    [ "$_fr_v" = fail ] || _smart_store "$1" fail f1_observed "$(smart_field "$1" 3)" "$(smart_field "$1" 4)" \
        "$(smart_field "$1" 5)" "$(smart_field "$1" 6)"
    return 0
}

disk_f1_under() {
    # $1 = disk. ה-SMART האמיתי שמתחת לאדום של F1 (ok|warn|fail|unchecked); ריק = לא ידוע.
    _fu=$(cat "$RUN_DIR/smart/$1.under" 2>/dev/null) || return 1
    [ "${_fu%|*}" = "$(disk_serial "$1")" ] && printf '%s' "${_fu##*|}"
}
