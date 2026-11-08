# failmark.sh -- מה נשאר מכשל כתיבה: הראיה, לא סימון (#874).
# POSIX sh (busybox ash).
#
# עד #874 הקובץ הזה סימן דיסק שנכשל **על הדיסק עצמו** (#845: שמות
# המחיצות, ה-ESP מאבדת את סוגה). הכרעת נדב 16/09: נפסל — הסוכן אינו משנה
# דיסק מחוץ לשיכפול, ואין "בדיקת כתיבה" של כמה MB. ולראיה: הדיסק שנכשל
# ב-07:03 נעלם מהקו לפני שנכתבה טבלה, ולא היה מה לסמן.
# ‏#1366 (נדב 13/10) הפך את חצי ה"בדיקת כתיבה" — **רק לדיסק שכבר נבחר לסבב**,
# לפני הזרם (writecheck.sh). סימון על הדיסק עדיין אין.
# ‏#1798 (נדב 19/10): "כתיבה לא הושלמה" כן נכתב על הדיסק -- כחלק מכתיבת הטבלה
# עצמה, לא בכשל (שמות המחיצות, writemark.sh): נפילת חשמל אינה מגיעה לכאן.
#
# במקום זה, שני דברים:
#
# 1. **בכשל** (‏fail_written_target) הסוכן אוסף את שורות ה-ATA של הקרנל על
#    הפורט של היעד — ‏`dmesg`, מסונן ל-`ata<N>` / `<סוג> error, dev <dev>`,
#    מאז ‏target_init, עד 40 שורות — ושולח אותן **כמות שהן** בדיווח
#    ההתקדמות (‏progress.sh → failure_json), עם הסידורי והחריץ. **הסוכן
#    אינו מסווג**: כבל או דיסק נקבע בשרת (‏server/ata_cause.py), במקום אחד.
#
# 2. **באתחול** hello עונה `disk_failures` — הרשומות הפתוחות בשרת שתואמות
#    לסידוריים של הדיסקים כאן או לחריץ במכונה הזאת (לפעמים הפורט אשם: שני
#    דיסקים שונים נכשלו באותה חתימה על פורט 1 של מחשב 2). ‏disk_failure_cause
#    קורא מהתשובה; המסך הממתין (‏clonergui.sh) והשער לפני כתיבה
#    (‏smartgate.sh) צובעים אדום. **שום דבר לא נכתב על הדיסק.**
#
# אותיות sd* מתחלפות בין אתחולים — כל זיהוי כאן הוא סידורי או ata<N>.

ata_port_of() {
    # $1 = disk. ה-N של `ata<N>` בנתיב ה-sysfs של ההתקן, או ריק (NVMe, VM).
    _ap=$(readlink -f "$SYSROOT/sys/block/$1/device" 2>/dev/null)
    case "$_ap" in
        */ata[0-9]*) _ap=${_ap##*/ata}; _ap=${_ap%%/*}
                     case "$_ap" in ''|*[!0-9]*) ;; *) printf '%s' "$_ap" ;; esac ;;
    esac
}

target_ident_keep() {
    # $1 = disk. ‏serial|port|ata_port כשהכונן עוד ב-sysfs (target_init, #1602).
    # מגירה שנשלפה יוצאת מ-sysfs, ואחריה disk_port ו-ata_port_of ריקים: על
    # ברזל (17/10) הגואי הראה `drawer=0|sdb`, והראיה הגיעה לשרת בלי זהות.
    printf '%s|%s|%s\n' "$(disk_serial "$1")" "$(disk_port "$1")" "$(ata_port_of "$1")" \
        > "$RUN_DIR/targets/$1/seen"
}

target_ident() {
    # $1 = disk. ‏serial|port|ata_port: מה שהכונן אומר עכשיו, ושדה שריק עכשיו
    # (הכונן כבר לא ב-sysfs) -- מה שנשמר ב-target_ident_keep (#1602).
    _ti_s=; _ti_p=; _ti_a=
    [ -f "$RUN_DIR/targets/$1/seen" ] && IFS='|' read -r _ti_s _ti_p _ti_a < "$RUN_DIR/targets/$1/seen"
    _ti_x=$(disk_serial "$1"); [ -z "$_ti_x" ] || _ti_s=$_ti_x
    _ti_x=$(disk_port "$1"); [ -z "$_ti_x" ] || _ti_p=$_ti_x
    _ti_x=$(ata_port_of "$1"); [ -z "$_ti_x" ] || _ti_a=$_ti_x
    printf '%s|%s|%s\n' "$_ti_s" "$_ti_p" "$_ti_a"
}

# --- #2434/#2429: הדיסק שנכתב הוא הדיסק שנבחר -----------------------------------
# שם התקן (sdb) אינו זהות: מגירה שנשלפה ודיסק שהוכנס, גם בחריץ שלא נבחר, מקבלים
# את אותו שם. לכן הזהות נקבעת ב-target_init -- רגע הבחירה -- כ-serial|סקטורים|חריץ
# (`bound`), ונבדקת מחדש לפני כל שלב הרסני (assert_target). אין `bound` = היעד לא
# נבחר במשימה הזו = לא נכתב (עיקרון 5). דיסק שאינו הנבחר מסומן `foreign`: הוא
# נכשל בשמו, ושום דבר אחריו -- לא done, לא ראיית ATA, לא מחיקת GPT -- אינו משנה
# אותו (target_set, progress.sh). serial ריק: המגירות (drawers_bound) והשיכפול
# המקומי (lc_run) דוחים אותו בבחירה; בתחנה של דיסק יחיד (בנייה/תלמיד) אין דיסק
# אחר להתבלבל איתו, והגודל והחריץ לבדם נבדקים -- זה לא נשבר בגלל VM בלי serial.

_target_key() { printf '%s|%s' "$(disk_serial "$1")" "$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null)"; }
_target_id() { printf '%s|%s' "$(_target_key "$1")" "$(disk_port "$1")"; }

target_bind() {
    # $1 = disk. מה שיושב בשם הזה עכשיו -- מ-target_init, ומשם בלבד. ותאום שיושב כאן עכשיו (#2448).
    mkdir -p "$RUN_DIR/targets/$1" && _target_id "$1" > "$RUN_DIR/targets/$1/bound" || return 1
    rm -f "$RUN_DIR/targets/$1/twin"; _tb_tw=$(target_twin "$1") && printf '%s\n' "$_tb_tw" > "$RUN_DIR/targets/$1/twin"
    return 0
}

# --- #2448: serial זהה מהמפעל -------------------------------------------------------
# שני כוננים מאותו דגם ובאותו גודל עם אותו serial (למשל 0000000000) הם אותה זהות בלי
# החריץ: מגירה 2 שעברה לחריץ 1 עונה בשם הזה ל-bound של מגירה 1. אי אפשר להבדיל
# ביניהם, ולכן כל אחד מהם שהוא יעד נכשל בשמו ולא נכתב -- כשהתאום כאן עכשיו, או היה
# כאן ב-target_init (`twin`: התאום שנשלף אחרי הבחירה עדיין אומר שהבחירה לא הייתה
# חד-משמעית). ‏serial ריק נספר כזהות: שני דיסקים בלי serial באותו גודל הם תאומים, ודיסק
# יחיד (בנייה/תלמיד) אין לו תאום -- הוא נכתב כמו קודם.

target_twin() {
    # $1 = disk. PRINTS the other disks here with its serial|sectors. 0 = there is one.
    case "$1" in loop*) return 1 ;; esac      # the VM bridge's file (vmdisk.py), not a drawer: the host's disks are not its twins
    _tt_k=$(_target_key "$1"); _tt=""
    case "$_tt_k" in *'|') return 1 ;; esac   # no size: it is not here -- target_same says so
    for _tt_d in $(list_disks); do
        [ "$_tt_d" = "$1" ] || [ "$(_target_key "$_tt_d")" != "$_tt_k" ] || _tt="$_tt $_tt_d"
    done
    [ -n "$_tt" ] || return 1
    printf '%s' "${_tt# }"
}

target_unique() {
    # $1 = disk. 0 = no twin, now or at target_init. 1 = it failed by name (foreign).
    _tu=$(target_twin "$1") || _tu=$(cat "$RUN_DIR/targets/$1/twin" 2>/dev/null) || return 0
    [ -n "$_tu" ] || return 0
    target_foreign "$1" "לדיסק $1 ולדיסק $_tu אותו מספר סידורי ואותו גודל — אי אפשר לדעת איזה מהם נבחר, לא נכתב על אף אחד מהם"
    return 1
}

target_bound_serial() {
    # $1 = disk. ה-serial שנקבע בבחירה (ריק = אין, או שלא נקבעה זהות כלל).
    _tb=$(cat "$RUN_DIR/targets/$1/bound" 2>/dev/null) || return 1
    printf '%s' "${_tb%|*|*}"   # serial יכול להכיל '|'; גודל וחריץ אינם
}

target_same() {
    # $1 = disk. 0 = הדיסק שבשם הזה עכשיו הוא הדיסק שנקבע ב-target_bind.
    [ -f "$RUN_DIR/targets/$1/bound" ] || return 1
    [ "$(_target_id "$1")" = "$(cat "$RUN_DIR/targets/$1/bound")" ]
}

target_foreign() {
    # $1 = disk, $2 = why. נכשל בשמו, בגלוי, וננעל (`foreign`) -- בלי לגעת בדיסק.
    target_set "$1" failed "$2"; stage_at "$1" end 2>/dev/null; : > "$RUN_DIR/targets/$1/foreign"
    log "$1: $2 (bound: $(cat "$RUN_DIR/targets/$1/bound" 2>/dev/null || echo none); now: $(_target_id "$1")) -- not written (#2434)" >&2   # >&2: callers run inside $( ) and > result files
}

assert_target() {
    # $1 = disk. לפני כל שלב הרסני. 0 = זה הדיסק שנבחר; 1 = היעד נכשל בשמו ולא נכתב.
    target_unique "$1" || return 1   # #2448: a twin here -- "the same" would not tell them apart
    target_same "$1" && return 0
    target_foreign "$1" "הדיסק שבשם $1 אינו הדיסק שנבחר לכתיבה (הוחלף או נשלף) — לא נכתב עליו"
    return 1
}

ata_log_json() {
    # $1 = disk. מערך JSON של שורות ata_log, מחרוזת לכל שורה.
    _aj="["; _aj_sep=""
    while IFS= read -r _aj_l; do
        _aj="$_aj$_aj_sep\"$(json_escape "$_aj_l")\""; _aj_sep=","
    done < "$RUN_DIR/targets/$1/ata_log"
    printf '%s]' "$_aj"
}

ata_capture() {
    # $1 = disk. שורות dmesg של הפורט של היעד מאז target_init (הקובץ
    # `since` = uptime באתחול היעד; בלעדיו — הכול) → ata_log, ata_log.json
    # (נבנה פעם אחת, לא בכל דיווח), ו-ident = serial|port|ata_port.
    # הראיה יושבת **בראש** הרצף (#890, נמדד על ברזל): exception → SError →
    # failed commands → איפוס → רעש. לכן 40 השורות **הראשונות** מה-exception
    # הראשון (או `I/O error, dev`), בלי הרעש שאחרי כל איפוס (ACPI filtered
    # out / SATA link up / configured for UDMA / EH complete). `tail` היה
    # שומר את הרעש ומוחק את ה-SError — והשרת סיווג כבל כ"דיסק".
    _ac_t="$RUN_DIR/targets/$1"
    _ac_id=$(target_ident "$1"); _ac_ata=${_ac_id##*|}   # #1602: גם אחרי שהכונן יצא מ-sysfs
    _ac_since=$(cat "$_ac_t/since" 2>/dev/null)
    printf '%s\n' "$_ac_id" > "$_ac_t/ident"
    dmesg 2>/dev/null | awk -v dev="$1" -v ata="$_ac_ata" -v since="${_ac_since:-0}" '
        {
            ts = -1
            if (match($0, /^\[ *[0-9]+\.[0-9]+\]/)) ts = substr($0, RSTART + 1, RLENGTH - 2) + 0
            if (ts >= 0 && ts < since + 0) next
            if ($0 ~ /ACPI cmd .* filtered out|configured for UDMA|SATA link up|EH complete/) next
            io = index($0, " error, dev " dev ",") > 0   # I/O, device offline, critical medium (#1602)
            if (!io && (ata == "" || $0 !~ ("ata" ata "(\\.[0-9]+)?:"))) next
            kept[++n] = $0
            if (!head && (io || $0 ~ /exception Emask/)) head = n
        }
        END {
            if (!head) head = 1
            for (i = head; i <= n && i < head + 40; i++) print kept[i]
        }' > "$_ac_t/ata_log"
    ata_log_json "$1" > "$_ac_t/ata_log.json"
    log "$1: $(wc -l < "$_ac_t/ata_log" | tr -d ' ') kernel lines kept for the report (ata${_ac_ata:-?})"
}

failure_json() {
    # $1 = disk. מה ש-build_progress מצרף ליעד failed (ממשק 4): הזהות
    # והראיה, כשדות JSON עם פסיק מוביל. ריק כשלא נאספה ראיה (כשל לפני הכתיבה).
    _fj_t="$RUN_DIR/targets/$1"
    [ -f "$_fj_t/ata_log.json" ] || return 0
    IFS='|' read -r _fj_s _fj_p _fj_a < "$_fj_t/ident" || return 0
    _fj_sj=null; [ -n "$_fj_s" ] && _fj_sj="\"$(json_escape "$_fj_s")\""
    case "$_fj_p" in ''|*[!0-9]*) _fj_p=null ;; esac
    case "$_fj_a" in ''|*[!0-9]*) _fj_a=null ;; esac
    printf ',"serial":%s,"port":%s,"ata_port":%s,"ata_log":%s' \
        "$_fj_sj" "$_fj_p" "$_fj_a" "$(cat "$_fj_t/ata_log.json")"
}

restore_blame() {
    # $1 = the station's target dir, $2 = the partition's sha256 (#1601). Sets
    # RESTORE_BLAME to `stream` unless there is positive evidence against the
    # disk -- the station's twin of drawers.sh's verdict (#1598): fanout lost its
    # reader under a flowing stream (partclone died: `write error`; it hung:
    # `stalled`), or the whole stream arrived (sha matches) and the write still
    # failed. A stream that timed out, ended early or never began is not the
    # disk's, and an ata_log on it is a disk_failure on a healthy disk.
    RESTORE_BLAME=""
    _rb=$(awk '{print $1}' "$1/sha.out" 2>/dev/null)
    is_sha256 "$_rb" && [ "$_rb" = "$2" ] && return 0
    # grep: 0 = found, 1 = not found (an answer), 2 = the check broke.
    grep -qE ' failed (write error|stalled)' "$1/fanout.out" 2>/dev/null
    case $? in
        0) ;;
        1) RESTORE_BLAME=stream ;;
        *) log "WARNING: fanout's report is unreadable -- the disk keeps its evidence" ;;
    esac
}

fail_written_target() {
    # $1 = disk, $2 = reason, $3 = `stream` כשהזרם אשם ולא הכונן (#1598).
    # ‏target_set failed, והראיה נאספת **פעם אחת** —
    # ליעד שעובר עכשיו ל-failed. יעד שכבר failed: הסיבה נדרסת (בכוונה, #73),
    # הראיה שכבר נאספה נשארת. הקוראים: restore.sh, drawers.sh, verdict.sh —
    # כל כשל בנתיב הכתיבה, כולל טבלה שלא נכתבה (הדיסק נעלם מהקו לפניה).
    # ‏capture.sh נשאר על target_set: דיסק המקור של קליטה אינו יעד.
    _was=$(cat "$RUN_DIR/targets/$1/state" 2>/dev/null)
    target_set "$1" "failed" "$2"; [ "$_was" = failed ] || stage_at "$1" end 2>/dev/null   # #1883: its timeline ends here
    # ‏#1429: בלי write_started לא נוסתה כתיבה — כשל תוכנה (תוכנית שנדחתה),
    # לא של הכונן. בלי ראיה השרת אינו רושם disk_failure נגד כונן תקין.
    # ‏#1598: ואחרי כשל של הזרם (פג, נקטע, sha) -- בלי ata_log גם השרת אינו רושם.
    [ "${3:-}" = stream ] || [ "$_was" = "failed" ] || [ ! -f "$RUN_DIR/targets/$1/write_started" ] || ata_capture "$1"
    # #1388: once the tmpfs failure record and ATA evidence exist, make a
    # partly restored disk visibly non-bootable too.  The device name is the
    # failed target passed by its own restore path; never enumerate drawers.
    if [ "$_was" != "failed" ] && [ ! -f "$RUN_DIR/targets/$1/evidence_only" ]; then
        wipe_written_gpt "$1" "restore failure" || :
    fi
    return 0
}

wipe_written_gpt() {
    # $1 = disk, $2 = why (the log line). #1388, and #1386's cancel: the primary
    # and backup GPT of the disk THIS run wrote -- write_started holds its serial;
    # another disk, or one with no serial (#1427), is not touched. 0 = wiped.
    # #2434: and only the disk chosen -- write_started alone recorded whoever was there.
    _written_serial=$(cat "$RUN_DIR/targets/$1/write_started" 2>/dev/null)
    [ -n "$_written_serial" ] && [ "$(disk_serial "$1")" = "$_written_serial" ] && target_same "$1" || return 1
    if sgdisk --zap-all "$DEVROOT/$1" >> "$LOG_FILE" 2>&1; then
        blockdev --rereadpt "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || :
        log "$1: wiped primary and backup GPT after $2"
        return 0
    fi
    log "WARNING: $1: failed to wipe GPT after $2"
    return 1
}

disk_failures_refresh() {
    # תשובת ה-hello האחרונה → $RUN_DIR/disk_failures, שורה לרשומה:
    # serial|port|cause. השורה הראשונה "ok" היא הראיה שה-JSON נקרא בשלמותו:
    # קובץ תשובה באמצע כתיבה (‏send_hello) או jq שנפל אינם "אין כשלים" —
    # הקובץ הקודם נשאר (עיקרון 5). 0 = רוענן.
    command -v disk_f1_refresh >/dev/null 2>&1 && { disk_f1_refresh || :; }   # #1858: "נצפה F1" לפי serial, מאותה תשובה
    _dr_n="$RUN_DIR/disk_failures.next"
    json_get_join "$RUN_DIR/response.json" \
        '["ok"] + ((.disk_failures // []) | map("\(.serial // "")|\(.port // "")|\(.cause // "")"))' \
        > "$_dr_n" 2>/dev/null
    [ "$(head -n 1 "$_dr_n" 2>/dev/null)" = ok ] || { rm -f "$_dr_n"; return 1; }
    mv "$_dr_n" "$RUN_DIR/disk_failures"
}

disk_failure_cause() {
    # $1 = disk. מדפיס את הסיבה (cable/disk/unclassified) של הרשומה הפתוחה
    # שתואמת לדיסק — לפי סידורי, או לפי החריץ במכונה הזאת — ומחזיר 0. אין
    # רשומה (או שהתשובה עוד לא נקראה) → 1 ובלי פלט.
    [ -f "$RUN_DIR/disk_failures" ] || return 1
    _dc_s=$(disk_serial "$1"); _dc_p=$(disk_port "$1")
    awk -F'|' -v s="$_dc_s" -v p="$_dc_p" \
        'NF >= 3 && ((s != "" && $1 == s) || (p != "" && $2 == p)) { print $3; f = 1; exit }
         END { exit !f }' "$RUN_DIR/disk_failures" 2>/dev/null
}

disk_failure_readonly() {
    # $1 = disk. ‏#1742: אמת כשיש רשומה פתוחה עם cause=readonly **על הדיסק הזה** --
    # לפי סידורי בלבד: קריאה-בלבד היא של הדיסק, לא של החריץ, ודיסק חדש באותו
    # חריץ אינו הוא. בלי סידורי אין ראיה -- שקר (‏smart.sh קורא גם את ‏/sys).
    [ -f "$RUN_DIR/disk_failures" ] || return 1
    _dro_s=$(disk_serial "$1"); [ -n "$_dro_s" ] || return 1
    awk -F'|' -v s="$_dro_s" 'NF >= 3 && $1 == s && $3 == "readonly" { f = 1; exit }
         END { exit !f }' "$RUN_DIR/disk_failures" 2>/dev/null
}
