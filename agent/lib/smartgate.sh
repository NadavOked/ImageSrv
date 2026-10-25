# smartgate.sh -- the SMART gate before writing (#652): the operator's choice,
# disk_event, and the power-off-and-swap screen. POSIX sh (busybox ash).
# נשען על smart.sh (smart_probe/smart_field -- הקריאה, הסיווג והמטמון), שנטען
# לפניו; פוצל ממנו ב-#1211 לפני קיר 300 השורות, בלי שינוי התנהגות.

smart_disk_label() {
    # $1 = disk name (sda). התווית שהמפעיל רואה על ה-TTY. שם ההתקן בקרנל
    # הוא סדר הגילוי ולא החריץ; ה-`port` הוא החריץ (#27), ולכן דיסק עם
    # port הוא "Disk N". בלי port (NVMe/USB/VM) — שם ההתקן, כי מספר שגוי
    # גרוע ממספר חסר (אותו כלל כמו `port` ב-hello). ה-TTY של הסוכן הוא
    # ASCII/אנגלית (הקונסולה הלינוקסית אינה מרנדרת RTL); הקונסולה
    # ה-web מציגה "דיסק N" מאותו port.
    _sl_p=$(disk_port "$1")
    case "$_sl_p" in
        ''|*[!0-9]*) printf '%s %s' "${2:-Disk}" "$1" ;;   # $2 = the word: 'דיסק' for the console prompt, default for the ASCII tty
        *)           printf '%s %s' "${2:-Disk}" "$_sl_p" ;;
    esac
}

smart_choice() { attended "$1: $2" smart_ask "$@"; }   # #906: hello keeps going while a person is asked (attended.sh)
smart_ask() {
    # $1 = label (Disk N), $2 = context line, $3 = disk, $4 = verdict,
    # $5 = reason, $6 = ברירת המחדל בלי תשובה. מחזיר את ההכרעה: replace /
    # rescue / skip.
    #
    # ההכרעה נלקחת **רק** מהמסך הגרפי של מחשב השיכפול (#666, gui_smart_choice).
    # תפריט הטקסט שהיה כאן הוסר (נדב 11/10: GUI בלבד, בלי מסך טקסט חלופי).
    # בלי מסך, או מסך שנעלם/לא ענה -- "המשך" של אותו צבע, בדיוק מה שתפריט
    # הטקסט עשה על EOF (נדב 16/09): כתום כותב, אדום מדלג; הברירה מגיעה מהקורא
    # (‏$6), וריק = skip.
    # תשובה מפורשת מבוצעת כמות שהיא (#1580): עד 17/10 ‏rescue על failed_last
    # הונמך כאן בשקט ל-skip. אדום (SMART תקול) אינו נשאל בכלל (#1732), ומאז
    # #2042 (נדב 23/10) גם כתום אינו נשאל -- smart_preflight אינו קורא לכאן עוד.
    if [ "$#" -ge 5 ] &&
            _sc_gui=$(gui_smart_choice "$3" "$4" "$5"); then
        printf '%s\n' "$_sc_gui"
        return 0
    fi
    log "$1: no answer from the GUI -- the default for ${4:-this verdict}: ${6:-skip}" >&2
    printf '%s\n' "${6:-skip}"
}

smart_event_json() {
    # חוזה disk_event (interfaces.md סעיף 12): הסוכן מכריע מקומית, השרת
    # שומר לריבוט-החלפה ולצפיית #380. $1=session $2=disk $3=verdict
    # $4=reason $5=write.state $6=decision(''|replace|rescue|skip).
    _ej_dec="null"; [ -n "$6" ] && _ej_dec="\"$6\""
    _ej_port=$(disk_port "$2"); [ -n "$_ej_port" ] || _ej_port=null
    _ej_ser=$(disk_serial "$2")
    _ej_ser_j="null"; [ -n "$_ej_ser" ] && _ej_ser_j="\"$(json_escape "$_ej_ser")\""
    printf '{"session_id":"%s","mac":"%s","disk":"%s","port":%s,"serial":%s,"smart":{"verdict":"%s","reason":"%s","realloc":%s,"pending":%s,"uncorrectable":%s,"crc":%s,"attrs":%s},"write_state":"%s","decision":%s}' \
        "$1" "${MAC:-}" "$2" "$_ej_port" "$_ej_ser_j" \
        "$3" "$4" "$(smart_field "$2" 3)" "$(smart_field "$2" 4)" \
        "$(smart_field "$2" 5)" "$(smart_field "$2" 6)" "$(command -v smart_attrs_json >/dev/null 2>&1 && smart_attrs_json "$2" 1 || printf null)" "$5" "$_ej_dec"   # #1859: attrs
}

smart_send_event() {
    # שולח disk_event לשרת. best-effort כמו trace_step: אבחון שמפיל
    # שחזור הוא נזק. לעולם מחזיר 0, ומדווח אם לא נמסר.
    smart_event_json "$@" > "$RUN_DIR/disk_event.json" 2>/dev/null
    if http_post_json "$SERVER/api/v1/agent/disk-event" \
            "$RUN_DIR/disk_event.json" >/dev/null 2>&1; then
        return 0
    fi
    # ‏#875: ל-stderr -- הפונקציה רצה בתוך smart_preflight, ש-smart_gate לוכד
    # ב-`$( )`; שורת יומן על stdout הייתה נכנסת לרשימת הדיסקים לכתיבה.
    log "disk_event for $2 was not delivered -- continuing" >&2
    return 0
}

smart_preflight() {
    # $1 = session id, $2.. = disks. השער לפני כתיבה: probe לכל דיסק,
    # disk_event לכל דיסק; fail (אדום) מדולג; warn/failed_last (כתום) נכתב
    # בלי שאלה עם הערה (#2042). מדפיס את רשימת הדיסקים שייכתבו בפועל.
    # ‏ok/unchecked לא נשאלים — unchecked נכתב, זה קריטי (עיקרון 5).
    _pf_ses="$1"; shift
    rm -f "$RUN_DIR/smart/awaiting_replace"
    disk_failures_refresh || :   # #874: זיכרון השרת מה-hello האחרון; כשל = הקובץ הקודם
    command -v smart_learned_refresh >/dev/null 2>&1 && { smart_learned_refresh || :; }   # #1859: what the server learned, the same way
    _pf_write=""
    for _pf_d in "$@"; do
        # תיקיית היעד קיימת כבר מ-target_init; ה-mkdir הופך את השער לעצמאי --
        # target_set חייב תיקייה, וקפיאה על היעדרה תהיה כשל שקט (עיקרון 5).
        mkdir -p "$RUN_DIR/targets/$_pf_d"; rm -f "$RUN_DIR/targets/$_pf_d/smart_note"   # #2042: this round's note only
        _pf_v=$(smart_probe "$_pf_d")
        _pf_r=$(smart_field "$_pf_d" 2)
        # SMART תקול (‏fail) = **אדום**. נכשל בשיכפול הקודם (זיכרון השרת, ‏#874)
        # כש-SMART אינו fail = **כתום**, "נסה שוב". עצירה באמצע (הזרם לא הגיע, הסבב נעצר)
        # אינה צובעת -- השרת אינו מחזיר אותה (‏disk_failures.py).
        # ‏#1732 (נדב 18/10, אחרי התייעצות עם איש מקצוע) מחליף את ה"כתוב בכל
        # זאת" של #1580: אדום **לעולם אינו יעד** -- לא נשאל, לא ממתינים לו,
        # מדולג בגלוי. מותר רק כמקור (העתקה ממנו, קליטה, חילוץ).
        if [ "$_pf_v" != fail ] && _pf_c=$(disk_failure_cause "$_pf_d"); then
            _pf_v=failed_last; _pf_r="$_pf_c"
        # #1859: what rose since this serial's previous round (the server compared) -- orange, asked
        elif { [ "$_pf_v" = ok ] || [ "$_pf_v" = warn ]; } && command -v smart_learned_reason >/dev/null 2>&1 &&
                _pf_l=$(smart_learned_reason "$_pf_d"); then
            _pf_v=warn; _pf_r="$_pf_l"
        fi
        case "$_pf_v" in
            ok|unchecked)
                smart_send_event "$_pf_ses" "$_pf_d" "$_pf_v" "$_pf_r" pending ""
                _pf_write="$_pf_write $_pf_d"
                ;;
            fail)
                target_set "$_pf_d" "skipped" "$(smart_red_he "$_pf_d")$([ "$_pf_r" = readonly ] || printf ' (%s)' "$(smart_reason_he "$_pf_r")")"   # #1742; #1861: no raw word
                smart_send_event "$_pf_ses" "$_pf_d" "$_pf_v" "$_pf_r" skipped skip
                log "$(smart_disk_label "$_pf_d"): SMART failed ($_pf_r) -- never written (#1732), skipped" >&2
                ;;
            warn|failed_last)
                # #2042 (נדב 23/10): כתום נכתב **בלי שאלה** -- סבב, שחזור, משיכה והפצה ישירה. שאלה
                # על WD אחד (187=1682) עצרה גל שלם 113 ש'. אין הכרעת אדם: decision null. ההערה
                # הכתומה נשמרת כאן ונצמדת ליעד שהסתיים done (smart_note_apply) -- "הושלם עם אזהרה".
                smart_send_event "$_pf_ses" "$_pf_d" "$_pf_v" "$_pf_r" pending ""
                smart_note_he "$_pf_v" "$_pf_r" > "$RUN_DIR/targets/$_pf_d/smart_note"
                log "$(smart_disk_label "$_pf_d"): orange ($_pf_v $_pf_r) -- written without asking (#2042)" >&2
                _pf_write="$_pf_write $_pf_d"
                ;;
        esac
    done
    # רווח מוביל מוסר; מילה אחת או ריק חוזרים כמות שהם.
    echo $_pf_write
}

smart_note_he() {
    # $1 = verdict (warn|failed_last), $2 = reason. #2042: the orange note of a disk written
    # without asking -- the reason in words (smart_reason_he: the knowledge table's), never the raw word.
    if [ "$1" = failed_last ]; then smart_why_he "$1" "$2"; return 0; fi
    _sn_r=$(smart_reason_he "$2"); printf 'SMART: %s' "${_sn_r:-אזהרה}"
}

smart_note_apply() {
    # $@ = disks. #2042: a target that ended done and was orange before the write carries
    # its note in `error` -- "done with a warning" (#856/#943), after an earlier warning.
    # Not done (failed, cancelled) = its own reason stands.
    for _na_d in "$@"; do
        _na_t="$RUN_DIR/targets/$_na_d"
        [ -s "$_na_t/smart_note" ] && [ "$(cat "$_na_t/state" 2>/dev/null)" = "done" ] || continue
        _na_w=$(cat "$_na_t/smart_note")
        [ -s "$_na_t/error" ] && _na_w="$(cat "$_na_t/error") | $_na_w"
        target_set "$_na_d" "done" "$_na_w"
    done
    return 0
}

smart_awaiting_replace() {
    # אמת אם המפעיל בחר "החלף" על דיסק כלשהו. אז המכונה מדפיסה את הוראת
    # ההחלפה ומתכבה (כיבוי הוא כל המכונה — אסור להתחיל כתיבה ואז לכבות).
    [ -s "$RUN_DIR/smart/awaiting_replace" ]
}

smart_gate() {
    # $1 = session, $2.. = disks. השער השלם, כדי שמסלולי השחזור יישארו
    # קצרים: מריץ את ה-preflight, מדפיס את רשימת הדיסקים לכתיבה, ומחזיר
    #   0 = כתוב את הרשימה שהודפסה
    #   2 = המפעיל בחר "החלף" -- המכונה מתכבה להחלפה, **לפני** כל כתיבה
    #   1 = לא נשאר מה לכתוב (הכול דולג)
    _sg_ses="$1"; shift
    _sg_list=$(smart_preflight "$_sg_ses" "$@")
    smart_awaiting_replace && { echo "$_sg_list"; return 2; }
    [ -n "$_sg_list" ] || return 1
    echo "$_sg_list"
}

smart_replace_screen() {
    # מסך "כבה -> החלף -> הדלק" לפני שהמכונה מתכבה. כיבוי הוא כל המכונה,
    # ולכן זה קורה לפני שנכתב בייט; ה-job כבר נשמר בשרת ב-disk_event
    # (write_state=replacing), ואחרי הריבוט הסוכן מפקד מחדש לפי port+serial.
    ui_clear; ui_header
    echo "  Replace the flagged disk, then power the machine back on."
    echo
    while read -r _rs_d; do
        echo "    $(smart_disk_label "$_rs_d") -- swap it out"
    done < "$RUN_DIR/smart/awaiting_replace"
    echo
    echo "  Powering off for the swap."
}

smart_replace_stop() {
    # smart_gate rc=2, on either restore path (drawers, #1222; station,
    # #1224): the screen, then the power-off -- before a byte is written.
    # Never returns.
    smart_replace_screen
    [ "${IMAGECTL_TEST:-0}" = "1" ] && exit 0
    finish_and_stop
    # finish_and_stop returns only when it stayed on: WoL (#587), or poweroff came back (#1483).
    # Nothing failed and the disk still has to come out, so this is not
    # "skipped" / "no drawer completed", and not back into the round either
    # (the next hello hands the same round back, with this progress_loop
    # still running): hold on this screen, heartbeat on (#64), for a
    # power-off by hand.
    # #1483: or the power-off itself came back -- said as what it was
    if [ "${POWEROFF_WHY:-}" = poweroff ]; then
        log "smart: replace chosen -- the power-off did not take, staying powered on"
        echo "  The power-off command returned: power off by hand for the swap."
    else
        log "smart: replace chosen -- wol not armed, staying powered on"
        echo "  Wake-on-LAN could not be armed: power off by hand for the swap."
    fi
    HOLD_PROMPT="Replace the flagged disk: power off by hand"
    hold_watch hold_beat
}
