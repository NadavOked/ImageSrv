# smart.sh -- SMART health gate on the restore side (#652), like Clonezilla.
# POSIX sh (busybox ash). Checked before writing to a target, and (16/10) on the
# build machine's capture/restore screens (guistate.sh) -- never on a machine that is only booting local.
#
# עיקרון 5, בצורתו החזקה ביותר: **SMART הוא שער החלטה, לא שער חסימה.**
# רק ממצא בריאות שלילי עוצר ומבקש בחירה מהמפעיל. אין SMART, כונן USB,
# בקר RAID, או `smartctl` שנכשל לפתוח את ההתקן -> `unchecked`, וכונן
# ‏`unchecked` נכתב **בלי שאלה**. "לא הצלחנו לבדוק" איננו "בדקנו ונכשל".
#
# חוט ההוכחה: הקוד היוצא של `smartctl` הוא מפת-סיביות שבה "הדיסק נכשל"
# הוא סיבית לגיטימית (bit 3 = exit 8), בדיוק כמו `grep`/`git check-ignore`
# שבהם `1` הוא תשובה ולא כשל (CLAUDE.md). לכן לעולם לא מסיקים "נכשל
# לפתוח" מ-`rc != 0` — רק מ-bit 1 (`rc & 2`). המסקנה מגיעה מהפלט שנקרא,
# לא מהיעדר-כשל.
#
# הקובץ הזה: קריאת smartctl, הסיווג והמטמון לכל דיסק (מה שה-hello וה-kiosk
# צריכים). השער מול המפעיל -- תפריט, disk_event ומסך ההחלפה -- ב-smartgate.sh
# (#1211: פוצל לפני קיר 300 השורות).

SMARTCTL="${SMARTCTL:-smartctl}"
#: ‏#1859 (נדב 20/10): מילות הכתום, החמורה ראשונה -- הראשונה שמתקיימת היא שנאמרת. ‏#1921: גם מילות
#: הכתום של טבלת הידע (smartjudge.sh, ‏data/smart-knowledge.tsv); 197/198 כתומים, לא אדומים (נדב 20/10).
SMART_WARN_REASONS="critical_rose downshift_rose timeout_rose hot pending_sectors uncorrectable reported_uncorrectable unc_logged media_errors reallocated bad_blocks realloc_event program_fail data_path selftest_failed spin_retry nvme_reliability ssd_worn spare_exhausted hot_now"

smart_was_read() {
    # $1 = disk. 0 when the cached record is smartctl's reading of the disk -- its counters are
    # numbers it printed, not the 0 stored for a disk it never read (crcdelta.sh, disklink.sh, smartattrs.sh).
    _swr=$(smart_field "$1" 2)
    case " ok health_failed media_readonly no_table no_health $SMART_WARN_REASONS " in *" $_swr "*) [ -n "$_swr" ] ;; *) return 1 ;; esac
}

smart_classify() {
    # $1=health(PASSED|FAILED|UNREAD|unknown) $2=realloc $3=pending
    # $4=uncorrectable $5=crc. מדפיס "<verdict> <reason>". פונקציה טהורה,
    # נבדקת שורה-שורה בלי smartctl.
    #
    # ‏#872 (נדב 16/09): **שורת הבריאות לבדה מכריעה** — מה שה-BIOS מציג.
    # המונים 5/197/198/199 נקראים ונשמרים (מידע) ואינם צובעים (‏#1859: 5 ושאר הכתום -- smart_warn_static, אחרי הסיווג הזה): על 6 דיסקי
    # הכיתה CRC הוא 137–55,242 והם משכפלים מושלם — מונה מצטבר מספר על
    # ההיסטוריה של הכבלים, ושאלה שנשאלת על 6/6 דיסקים אינה אומרת כלום.
    #
    # ‏#1921 (נדב 20/10, סופי): **אדום = הדיסק עצמו מדווח FAILED** (‏SMART RETURN STATUS -- מה
    # שעוצר BIOS של HP על F1). ‏197/198 מעל 0 בדיסק ש-PASSED הם כתום, מטבלת הידע
    # (smartjudge.sh), כמו שאר הכתום -- לא כאן. המונים נשמרים כמידע.
    if [ "$1" = "FAILED" ]; then echo "fail health_failed"; return; fi
    if [ "$1" = "PASSED" ]; then echo "ok ok"; return; fi
    # ‏#2078: smartctl's own UNKNOWN! -- the status *and* the table did not read (aborted). The BIOS stops on F1
    # there (870 EVO, 24/10): red, never written -- "we could not read" is not orange (principle 5).
    if [ "$1" = "UNREAD" ]; then echo "fail health_unread"; return; fi
    # ‏-H לא PASSED ולא FAILED = לא קראנו בריאות; **אינו** `ok` (עיקרון 5).
    echo "unchecked no_health"
}

_smart_int() {
    # מדפיס את ערך ה-RAW של מאפיין SMART לפי מזהה (199) או שם, כמספר שלם או 0.
    # ‏$1 = פלט `smartctl -A`, $2.. = מזהים/שמות; המזהה יציב בין יצרנים, השם
    # לא — סמסונג מדפיסה `CRC_Error_Count` ולא `UDMA_CRC_Error_Count` (#866).
    # ה-RAW הוא העמודה האחרונה; ממנו רק הספרות המובילות ("35 (Min/Max ..)").
    # ‏#858: NVMe אינו טבלה אלא `Media and Data Integrity Errors:    1,234` —
    # השם עד הנקודתיים (רווחים -> קו תחתון) מושווה גם הוא, ופסיקי-אלפים מוסרים.
    _si_f="$1"; shift
    for _si_name in "$@"; do
        _si_v=$(awk -v n="$_si_name" '{ k = $0; sub(/:.*/, "", k); gsub(/ /, "_", k) }
            $1 == n || $2 == n || k == n { v = $NF; gsub(/,/, "", v)
                sub(/[^0-9].*/, "", v); if (v != "") print v; exit }' \
            "$_si_f" 2>/dev/null)
        [ -n "$_si_v" ] && { printf '%s' "$_si_v"; return; }
    done
    printf '0'
}

_smart_store() {
    # $1=disk $2=verdict $3=reason $4=realloc $5=pending $6=uncorr $7=crc.
    # רשומה אחת בשורה, שממנה גם ה-hello (שדה `smart`) וגם ה-disk_event
    # קוראים בלי לחזור על ה-probe.
    _ss_d="$RUN_DIR/smart"; mkdir -p "$_ss_d"
    printf '%s %s %s %s %s %s\n' \
        "$2" "$3" "${4:-0}" "${5:-0}" "${6:-0}" "${7:-0}" > "$_ss_d/$1.v"
    _ss_s=$(disk_serial "$1")
    if [ -n "$_ss_s" ]; then printf '%s\n' "$_ss_s" > "$_ss_d/$1.serial"
    else rm -f "$_ss_d/$1.serial"; fi
}

smart_field() {
    # $1=disk, $2=field index (1=verdict 2=reason 3=realloc 4=pending
    # 5=uncorr 6=crc). ריק אם אין רשומה.
    awk -v i="$2" '{print $i}' "$RUN_DIR/smart/$1.v" 2>/dev/null
}

smart_ro_check() {
    # $1 = disk. ‏#1742 (נדב 18/10): דיסק לקריאה בלבד הוא **אדום** -- `fail`
    # עם הסיבה `readonly`, אותו אדום של SMART שנכשל (#1732: לעולם לא יעד, מותר
    # כמקור). שתי ראיות: ‏`/sys/block/<d>/ro` = 1, או זיכרון השרת שהכתיבה
    # הקודמת על הדיסק הזה נדחתה כ-EPERM/EROFS (‏cause=readonly, ‏ata_cause.py;
    # ‏disk_failure_readonly ב-failmark.sh). נקרא **בכל קריאה**, מחוץ למטמון:
    # ‏`blockdev --setro` יכול לבוא אחרי ה-probe של ההמתנה.
    # מחזיר 0 = אדום (הרשומה נכתבה), 1 = ‏ro=0, 2 = לא נקרא -- "לא נבדק", לעולם
    # לא "תקין" (עיקרון 5): רשומת `ok` הופכת ל-`unchecked ro_unread`.
    _sr_v=$(cat "$SYSROOT/sys/block/$1/ro" 2>/dev/null)
    if [ "$_sr_v" = 1 ] || { command -v disk_failure_readonly >/dev/null 2>&1 &&
            disk_failure_readonly "$1"; }; then
        _smart_store "$1" fail readonly; return 0
    fi
    case "$(smart_field "$1" 2)" in   # כבר לא לקריאה בלבד / ‏ro נקרא שוב: probe מחדש
        readonly) rm -f "$RUN_DIR/smart/$1.v" ;;
        ro_unread) [ "$_sr_v" = 0 ] && rm -f "$RUN_DIR/smart/$1.v" ;;
    esac
    [ "$_sr_v" = 0 ] && return 1
    [ "$(smart_field "$1" 1)" = ok ] && _smart_store "$1" unchecked ro_unread \
        "$(smart_field "$1" 3)" "$(smart_field "$1" 4)" "$(smart_field "$1" 5)" "$(smart_field "$1" 6)"
    return 2
}

smart_ro_stale() {
    # $1 = disk, $2 = שורה שמורה שנושאת `<verdict>|<reason>|` (‏guistate.sh: dsmart).
    # אמת כשמצב הקריאה-בלבד השתנה מאז שנכתבה (#1742) -- קריאת sysfs אחת, בלי smartctl.
    smart_ro_check "$1"; _rs_rc=$?
    case "$(cat "$2" 2>/dev/null)" in
        *"|fail|readonly|"*) [ "$_rs_rc" != 0 ] ;;
        *"|ok|"*) [ "$_rs_rc" != 1 ] ;;   # ‏ro שלא נקרא אינו "תקין" (עיקרון 5)
        *) [ "$_rs_rc" = 0 ] ;;
    esac
}

smart_probe() {
    # $1 = disk name. מריץ smartctl פעם אחת, מסווג, ושומר רשומה. מדפיס
    # את ה-verdict. דיסק שלא נפתח / בלי SMART הוא `unchecked`, שנכתב; ‏UNKNOWN! של
    # smartctl (הדיסק דחה את הקריאה) אדום (#2078). תוצאה נשמרת במטמון — קריאה שנייה על אותו דיסק לא מריצה שוב.
    # ‏#1742: לפני הכול smart_ro_check -- דיסק לקריאה בלבד אדום בלי smartctl.
    _sp_d="$1"; _sp_dev="$DEVROOT/$1"
    mkdir -p "$RUN_DIR/smart"
    smart_ro_check "$1"; _sp_ro=$?
    [ "$_sp_ro" = 0 ] && { echo fail; return; }
    _sp_serial=$(disk_serial "$1")
    if [ -f "$RUN_DIR/smart/$1.v" ] && [ -n "$_sp_serial" ] &&
            [ "$(cat "$RUN_DIR/smart/$1.serial" 2>/dev/null)" = "$_sp_serial" ]; then
        smart_field "$1" 1; return
    fi
    rm -f "$RUN_DIR/smart/$1.v" "$RUN_DIR/smart/$1.serial"
    if ! command -v "$SMARTCTL" >/dev/null 2>&1; then
        _smart_store "$1" unchecked no_smartctl; echo unchecked; return
    fi
    _sp_out="$RUN_DIR/smart/$1.out"
    # ‏#1367: בתקרה (waits.sh). דיסק שלא ענה הוא `timeout` -- "לא נבדק", נשמר
    # במטמון כמו כל תשובה: עוד 30 שניות על אותו דיסק תקוע לא יביאו ראיה.
    # ‏#1921 (נדב 20/10): `-x` -- היומנים, devstat וה-WCTEMP; ‏`-f old` שומר את טבלת המאפיינים
    # בצורה שכל קוראיה מכירים (‏-x לבדו מדפיס brief; ‏smartjudge.sh קורא את שתיהן).
    capped_run "$SMARTCTL_MAX_S" "$_sp_out" "$SMARTCTL" -x -f old -n never "$_sp_dev"; _sp_rc=$?
    [ "$CAPPED_TIMED_OUT" = 1 ] && { _smart_store "$1" unchecked timeout; echo unchecked; return; }
    # bit 1 (rc & 2) = פתיחת ההתקן נכשלה. רק אז מנסים דרך שכבת ה-SAT
    # (גשר USB-SATA); שאר הסיביות (בעיקר bit 3 = הדיסק נכשל) הן תשובה.
    if [ "$((_sp_rc / 2 % 2))" -eq 1 ]; then
        capped_run "$SMARTCTL_MAX_S" "$_sp_out" "$SMARTCTL" -d sat -x -f old -n never "$_sp_dev"
        _sp_rc=$?
        [ "$CAPPED_TIMED_OUT" = 1 ] && { _smart_store "$1" unchecked timeout; echo unchecked; return; }
        if [ "$((_sp_rc / 2 % 2))" -eq 1 ]; then
            _smart_store "$1" unchecked open_failed; echo unchecked; return
        fi
    fi
    # בקר RAID / התקן בלי SMART: smartctl פותח אבל אין הערכת בריאות.
    if grep -qiE 'SMART (support is: )?Unavailable|does not support SMART' \
            "$_sp_out" 2>/dev/null; then
        _smart_store "$1" unchecked no_smart; echo unchecked; return
    fi
    # ‏#858: SCSI/SAS מדפיס `SMART Health Status: FAILURE` (לא FAILED), NVMe `FAILED!`;
    # ו-bit 3 של קוד היציאה ("הדיסק נכשל") הוא ראיה חיובית גם בניסוח זר (עיקרון 5).
    case "$(grep -iE 'overall-health|SMART Health Status' "$_sp_out" 2>/dev/null)" in
        *FAIL*|*BAD*) _sp_h=FAILED ;;
        *PASS*|*OK*)  _sp_h=PASSED ;;
        *UNKNOWN!*)   _sp_h=UNREAD ;;   # #2078
        *)            _sp_h=unknown ;;
    esac
    [ "$((_sp_rc / 8 % 2))" -eq 1 ] && _sp_h=FAILED
    # ‏#1903: PASSED לבדו אינו קריאה -- bit 2 (rc & 4) = פקודת SMART נכשלה, ובלי טבלה (שורות ATA,
    # ‏NVMe Health, רשימת פגמים של SCSI) המונים הם 0 של _smart_int ולא מספר שהודפס. unchecked, לא ok.
    # שורת ATA: FLAG של -f old (0x0033) או FLAGS של brief (PO--CK) -- ‏-x של #1921 מדפיס את שתיהן.
    _sp_u=; if [ "$_sp_h" = PASSED ]; then
        [ "$((_sp_rc / 4 % 2))" -eq 1 ] && _sp_u=cmd_failed
        [ -n "$_sp_u" ] || grep -qE '^ *[0-9]+ +[A-Za-z_]+ +(0x[0-9a-fA-F]+|[-POSRCK]{6}) |^SMART/Health Information|grown defect list' "$_sp_out" 2>/dev/null || _sp_u=no_attrs
    fi
    # ‏#1921 ס2: FAILED ש-smartctl חישב מהמאפיינים (הדיסק אינו תומך ב-RETURN STATUS) אינו ה-F1 --
    # מידע, "לא נבדק ע״י הדיסק". ‏NVMe: ‏FAILED! הוא כל ביט ב-Critical Warning, והביטים מכריעים (ס1).
    grep -q 'This result is based on an Attribute check' "$_sp_out" 2>/dev/null && _sp_h=unknown
    # #1922: by (id, name) -- 198/199 of an OCZ Intrepid are the bytes it read and wrote, not a counter of faults
    _sp_re=$(_smart_int "$_sp_out" Reallocated_Sector_Ct Reallocated_Event_Count)
    _sp_pe=$(_smart_int "$_sp_out" Current_Pending_Sector)
    _sp_un=$(_smart_int "$_sp_out" Offline_Uncorrectable Media_and_Data_Integrity_Errors)
    _sp_cr=$(_smart_int "$_sp_out" UDMA_CRC_Error_Count CRC_Error_Count SATA_CRC_Error_Count)
    _sp_j=no_table; command -v smart_judge_disk >/dev/null 2>&1 && { _sp_j=$(smart_judge_disk "$_sp_d") || _sp_j=no_table; }
    _sp_g=2; [ "$_sp_h" = FAILED ] && grep -q '^Critical Warning:' "$_sp_out" 2>/dev/null &&
        grep -Eq '^(orange|red)[|][a-z_]+[|]nvmebit[|]' "$RUN_DIR/smart/$_sp_d.find" 2>/dev/null &&
        { _sp_g=0; grep -Eq '^unknown[|]-[|](nvmebit|nvme[|]critical_warning)[|]' "$RUN_DIR/smart/$_sp_d.find" 2>/dev/null || _sp_g=$?; }
    [ "$_sp_g" = 1 ] && _sp_h=PASSED   # every bit set is known and judged (grep 1, not 2 #231); one bit not known keeps FAILED -- red (review, 20/10)
    # shellcheck disable=SC2046 # פיצול המילים מכוון — ל-$1/$2
    set -- $(smart_classify "$_sp_h" "$_sp_re" "$_sp_pe" "$_sp_un" "$_sp_cr")
    case "$_sp_j" in
        "red "*) [ "$1" = fail ] || set -- fail "${_sp_j#red }" ;;   # NVMe read-only (ס1): never a target
        "orange "*) [ "$1" = fail ] || set -- warn "${_sp_j#orange }" ;;   # an early sign; the disk works
        no_table) [ "$1" = ok ] && set -- unchecked no_table ;;   # no table = nothing judged, never "fine"
    esac
    [ -n "$_sp_u" ] && [ "$1" != fail ] && set -- unchecked "$_sp_u"   # #1903 after the judge: never ok/warn; red stays red
    [ "$_sp_ro" = 2 ] && [ "$1" = ok ] && set -- unchecked ro_unread   # #1742: ro לא נקרא
    [ "$1" = fail ] && [ "$2" = media_readonly ] && set -- fail media_readonly   # the word, for the scans (#1861)
    command -v hw_refresh >/dev/null 2>&1 && hw_refresh "$_sp_d"   # #1921: the drawer's note, beside the colour
    _smart_store "$_sp_d" "$1" "$2" "$_sp_re" "$_sp_pe" "$_sp_un" "$_sp_cr"
    echo "$1"
}

#: ‏#1732: מה שהמפעיל קורא על דיסק אדום, בכל מסלול שמסרב לכתוב עליו. כאן ולא
#: ב-smartgate.sh: כל מי שטוען את smart_probe מקבל גם את הסירוב.
SMART_RED_HE="SMART נכשל — לא כותבים על הדיסק הזה; אפשר להעתיק ממנו או לחלץ לשרת"
#: ‏#1742: אותו אדום, אבל הסיבה אחרת -- דיסק לקריאה בלבד אינו "SMART נכשל".
SMART_RO_HE="הדיסק במצב קריאה בלבד"

smart_red_he() {
    # $1 = disk. המשפט של הסירוב על דיסק אדום, לפי הסיבה שבמטמון (smart_probe).
    if [ "$(smart_field "$1" 2)" = readonly ]; then printf '%s' "$SMART_RO_HE"; else printf '%s' "$SMART_RED_HE"; fi
}

smart_reason_he() {
    # $1 = a reason word -- smart_classify/_smart_store's, or the server's cause of a
    # failed clone (ata_cause.py CAUSES) -> the operator's words. The same words as
    # console.js SMART_REASON_HE/FAILURE_CAUSE_HE and native-gui smart_reason_text;
    # tests/test_reasons_he_1861.py keeps the three in step with what smart.sh writes.
    case "$1" in
        ok|'') ;;
        health_failed) printf 'בדיקת הבריאות נכשלה' ;;   pending_sectors) printf 'סקטורים ממתינים (197)' ;;
        uncorrectable) printf 'סקטורים שלא ניתנים לתיקון (198)' ;;   readonly) printf '%s' "$SMART_RO_HE" ;;
        no_health) printf 'הדיסק אינו מדווח על בריאות' ;;   no_smartctl) printf 'smartctl חסר ב-initramfs' ;;
        timeout) printf 'smartctl לא ענה בזמן' ;;   open_failed) printf 'smartctl לא הצליח לפתוח את הדיסק' ;;
        no_smart) printf 'הדיסק אינו תומך ב-SMART' ;;   ro_unread) printf 'לא נקרא אם הדיסק במצב קריאה בלבד' ;;
        cmd_failed) printf 'פקודת SMART לדיסק נכשלה' ;;   no_attrs) printf 'הדיסק לא החזיר טבלת מאפייני SMART' ;;   # #1903
        health_unread) printf 'ה-SMART לא נקרא — ה-BIOS עלול לעצור ב-F1' ;;   # #2078
        critical_rose) printf 'מאפיין קריטי עלה מאז הסבב הקודם — להחליף את הדיסק' ;;   # #1859: the server learned it
        downshift_rose) printf 'ירידות מהירות SATA (183) עלו מאז הסבב הקודם' ;;   timeout_rose) printf 'פקודות שנתקעו (188) עלו מאז הסבב הקודם' ;;
        hot) printf 'הדיסק התחמם בכתיבה בסבב הקודם' ;;   reported_uncorrectable) printf 'שגיאות שלא תוקנו (187)' ;;
        reallocated) printf 'סקטורים שהועברו (5)' ;;   realloc_event) printf 'אירועי העברה (196)' ;;   spin_retry) printf 'ניסיונות סיבוב חוזרים (10)' ;;
        ssd_worn) printf 'הדיסק שחוק — 90%% או יותר' ;;   spare_exhausted) printf 'הרזרבה של הדיסק נגמרה' ;;
        # #1921 (נדב 20/10): כתום -- סימן מוקדם, הדיסק עובד; משפט שאומר למה. אדום: NVMe לקריאה בלבד.
        hot_now) printf 'הדיסק חם מעבר לסף שלו עכשיו — לבדוק אוורור' ;;   bad_blocks) printf 'הדיסק מצא בלוקים פגומים בזמן עבודה (183)' ;;
        unc_logged) printf 'ביומן הדיסק יש קריאה שלא תוקנה (UNC)' ;;   media_errors) printf 'הדיסק דיווח על שגיאות נתונים שלא תוקנו (NVMe)' ;;
        program_fail) printf 'הזיכרון של ה-SSD נכשל בכתיבה או במחיקה' ;;   data_path) printf 'שגיאות בנתיב הנתונים בתוך הדיסק' ;;
        selftest_failed) printf 'הבדיקה העצמית האחרונה של הדיסק נכשלה' ;;   nvme_reliability) printf 'הבקר מדווח שהאמינות של הדיסק נפגעה (NVMe)' ;;
        media_readonly) printf 'הבקר נעל את הדיסק לקריאה בלבד (NVMe)' ;;   no_table) printf 'טבלת הידע של SMART חסרה — לא נבדק' ;;
        cable) printf 'כבל/חריץ' ;;   disk) printf 'הדיסק' ;;   removed) printf 'המגירה נשלפה' ;;   write_error) printf 'שגיאת כתיבה לדיסק' ;;   unclassified) printf 'לא סווג' ;;
        *) printf 'סיבה לא מוכרת' ;;   # the word itself stays in the log and the disk_event
    esac
}

smart_why_he() {
    # $1 = verdict (warn|failed_last|fail|...), $2 = reason -> one Hebrew phrase for the
    # question, the skip and the replace lines (smartgate.sh) -- never the raw words.
    _sw_r=$(smart_reason_he "$2")
    case "$1" in
        failed_last) printf 'נכשל בשיכפול הקודם%s' "${_sw_r:+ — סיבה: $_sw_r}" ;;
        warn) printf 'SMART אזהרה%s' "${_sw_r:+ ($_sw_r)}" ;;
        fail) printf 'SMART תקול%s' "${_sw_r:+ ($_sw_r)}" ;;
        *) printf 'SMART לא נבדק' ;;
    esac
}

smart_red() {
    # $1 = disk. אמת כשהדיסק אדום (SMART fail, smart_probe) -- לעולם לא יעד
    # (#1732). ‏unchecked אינו אדום: "לא נבדק" אינו "תקול" (עיקרון 5).
    [ "$(smart_probe "$1")" = fail ]
}

smart_hello_field() {
    # $1 = disk. **מטמון בלבד — לעולם אינו מריץ probe** (בלי זה כל אתחול
    # של תחנת כיתה שעולה לדיסק מקומי היה משלם שנייה על smartctl מיותר).
    # מלא ע"י smart_preflight בצד השחזור וע"י smart_probe_idle במחשב
    # השיכפול הממתין — ומשם הקונסולה מציגה בריאות פר-דיסק *לפני* start.
    # ‏#1742: קריאה בלבד נבדקת גם כאן (קובץ sysfs, לא smartctl) -- ה-probe של
    # ההמתנה רץ פעם אחת, ו-`blockdev --setro` אחריו חייב להגיע למסך לפני המשיכה.
    smart_ro_check "$1"
    case "$(smart_field "$1" 1)" in
        ok|warn|fail) smart_field "$1" 1 ;;
        *)            printf 'unchecked' ;;
    esac
}

smart_probe_idle() {
    # $@ = disks. במחשב השיכפול הממתין: probe חד-פעמי לכל מגירה כדי
    # שה-hello יישא בריאות והקונסולה תראה אותה לפני שהמפעיל משגר סבב
    # (דרישת נדב #652). ממוסמס ע"י המטמון של smart_probe.
    for _pi_d in "$@"; do smart_probe "$_pi_d" >/dev/null; done
}
