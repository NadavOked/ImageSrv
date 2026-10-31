# shrinkmem.sh -- ‏#926: זיכרון הכיווץ בשרת. POSIX sh (busybox ash).
#
# ‏#87 מכווץ את מחיצות ה-NTFS של דיסק הבנייה לפני הקליטה ומחזיר אותן
# אחריה, והפריסה המקורית ישבה ב-`targets/<disk>/shrunk` — ‏tmpfs. אובדן
# חשמל או אתחול בין `ntfsresize -s` לבין ההחזרה השאיר את הדיסק מכווץ
# בלי שאיש יודע: ווינדוס עולה, ‏C: קטן, והסוכן כבר לא זוכר מה היה. אותה
# משפחה כמו #856 ו-#874, ואותו פתרון — **השרת זוכר**, לפי סידורי:
#
# 1. **‏#1466 (16/10): הסוכן כבר אינו פותח רשומות.** הכיווץ רץ על עותק
#    בזיכרון והמקור אינו נכתב, ולכן אין מה לזכור. `shrink_open_record`
#    הוסר; מה שנשאר כאן הוא לדיסקים שכווצו במקום **לפני** #1466 ועדיין
#    רשומים בשרת: הסגירה, ההערה וההצעה באתחול.
# 2. אחרי החזרה מוצלחת של **כולן** — `shrink-close`. סגירה שנכשלה היא
#    אזהרה: הדיסק כבר בגודלו, הרשומה נשארת כתומה בקונסולה, והאתחול הבא
#    סוגר אותה בעצמו (‏shrink_offer_pending רואה שהכול כבר בגודלו).
# 3. **באתחול** hello עונה `shrink_open` (ממשק 3) לדיסק עם רשומה פתוחה,
#    ו-shrinkoffer.sh מציע להחזיר — שורה **למחיצה** ב-`$RUN_DIR/shrink_open`.
#
# ‏HTTP כאן בלי `-f`: הקוד הוא הפסק (‏buildmenu.sh:console_signin) — 409
# "כבר פתוח" ו-000 "לא נשאל" הן שתי תשובות שונות, ו-`-f` היה מקפל את
# שתיהן ל-22.

SHRINKMEM_ERROR=""

_sm_json_str() { if [ -n "$1" ] && [ "$1" != null ]; then printf '"%s"' "$(json_escape "$1")"; else printf null; fi; }

_sm_post() {
    # $1 = path under /api/v1/agent, $2 = body file, $3 = response file.
    # Prints the HTTP code; 000 when the server was never reached.
    _mp=$(curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" -o "$3" -w '%{http_code}' \
        -H "Content-Type: application/json" --data-binary "@$2" "$SERVER/api/v1/agent/$1") || _mp=000
    printf '%s' "$_mp"
}

shrink_close_record() {
    # $1 = disk. ‏0 = השרת סגר את הרשומה. כשל נרשם ביומן ואינו מפיל דבר:
    # הדיסק כבר בגודלו, והרשומה נשארת כתומה בקונסולה עד "נקה" או עד
    # האתחול הבא (ראו למעלה, 2).
    _sc_id=$(cat "$RUN_DIR/targets/$1/shrink_id" 2>/dev/null); case "$_sc_id" in ''|*[!0-9]*) _sc_id=null ;; esac
    rm -f "$RUN_DIR/targets/$1/shrink_id"
    printf '{"mac":"%s","serial":%s,"id":%s}' "${MAC:-}" "$(_sm_json_str "$(disk_serial "$1")")" "$_sc_id" > "$RUN_DIR/shrink_close.json"
    _sc_code=$(_sm_post shrink-close "$RUN_DIR/shrink_close.json" "$RUN_DIR/shrink_close.resp")
    if [ "$_sc_code" = 200 ] && [ "$(json_get "$RUN_DIR/shrink_close.resp" ".ok")" = true ]; then
        log "shrink record $_sc_id closed on the server for $1"; return 0
    fi
    log "WARNING: $1: the server did not close shrink record $_sc_id (http $_sc_code) -- it stays in the console until cleared"
    return 1
}

shrink_records_refresh() {
    # תשובת ה-hello האחרונה → $RUN_DIR/shrink_open, שורה **למחיצה** (‏#929):
    # id|serial|idx|start|size|type|uguid|name|attrs|ntfs|opened_at. רשומה של
    # שרת שקדם ל-#929 (בלי `partitions`) נקראת מהשדות העליונים — שורה אחת.
    # כמו disk_failures_refresh: "ok" ראשונה היא הראיה שה-JSON נקרא בשלמותו;
    # תשובה שלא נקראה משאירה את הקובץ הקודם (עיקרון 5). 0 = רוענן.
    _sr_n="$RUN_DIR/shrink_open.next"
    # shellcheck disable=SC2016 # ‏$r הוא משתנה של jq, לא של המעטפת
    json_get_join "${RESP:-$RUN_DIR/response.json}" \
        '["ok"] + [(.shrink_open // [])[] as $r | ($r.partitions // [$r])[] | "\($r.id)|\($r.serial // "")|\(.idx)|\(.start_sector)|\(.size_sectors)|\(.type_guid)|\(.unique_guid // "")|\(.name // "")|\(.attrs // "")|\(.ntfs_bytes // "")|\($r.opened_at // "")"]' \
        > "$_sr_n" 2>/dev/null
    [ "$(head -n 1 "$_sr_n" 2>/dev/null)" = ok ] || { rm -f "$_sr_n"; return 1; }
    mv "$_sr_n" "$RUN_DIR/shrink_open"
}

shrink_pending_for() {
    # $1 = disk. מדפיס את **כל** שורות הרשומה הפתוחה של הסידורי של הדיסק
    # הזה (שורה למחיצה, מ-shrink_records_refresh) ומחזיר 0; אין רשומה, או
    # אין סידורי — 1.
    [ -f "$RUN_DIR/shrink_open" ] || return 1
    _pf_s=$(disk_serial "$1"); [ -n "$_pf_s" ] || return 1
    awk -F'|' -v s="$_pf_s" 'NF >= 6 && $2 == s { print; f = 1 } END { exit !f }' "$RUN_DIR/shrink_open" 2>/dev/null
}

shrink_note_record() {
    # $1 = disk, $2 = למה הרשומה עדיין פתוחה. best-effort: הקונסולה מציגה את
    # הסיבה ליד הרשומה; כשל בשליחה נרשם ביומן ואינו משנה דבר.
    _sn_id=$(cat "$RUN_DIR/targets/$1/shrink_id" 2>/dev/null); case "$_sn_id" in ''|*[!0-9]*) _sn_id=null ;; esac
    printf '{"mac":"%s","serial":%s,"id":%s,"note":"%s"}' "${MAC:-}" "$(_sm_json_str "$(disk_serial "$1")")" "$_sn_id" "$(json_escape "$2")" > "$RUN_DIR/shrink_note.json"
    _sn_code=$(_sm_post shrink-note "$RUN_DIR/shrink_note.json" "$RUN_DIR/shrink_note.resp")
    [ "$_sn_code" = 200 ] || log "WARNING: $1: the reason was not delivered to the server (shrink-note http $_sn_code)"
}
