# smartgate.sh -- the SMART gate before writing (#652): the verdict per disk,
# disk_event, and the orange note. No question (#1732 red, #2042 orange; the
# question itself was removed in #2044). POSIX sh (busybox ash).
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
    disk_failures_refresh || :   # #874: זיכרון השרת מה-hello האחרון; כשל = הקובץ הקודם
    # #1859/#2711: what rose since this serial's previous round -- compared with what is read NOW. A cloner
    # woken into a round sent its one hello before any probe (smart_attrs null: the cache is per boot), and the
    # server compares nothing it was not sent. Probe first, then one NON-joining hello (attended_hello: the beat
    # a running task sends -- a joining one mid-round marks the member lost, #1728 -- whose answer never touches
    # response.json), and read smart_learned from that answer. Not delivered = said (principle 5).
    for _pf_d in "$@"; do smart_probe "$_pf_d" > /dev/null; done
    if command -v smart_learned_refresh > /dev/null 2>&1; then
        if command -v attended_hello > /dev/null 2>&1 && attended_hello "" >&2 &&
                smart_learned_refresh "$RUN_DIR/attended.resp"; then :
        else
            log "SMART: not compared with the previous round after the probe -- the hello did not go through; the boot answer stands" >&2
            smart_learned_refresh || :
        fi
    fi
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
                # #2168: what the server learned is orange on an unchecked disk too (ro unread, a
                # timeout...). The event stays `unchecked <reason>`, not warn: its counters were not
                # all read, and warn is a CRC baseline on the server (room_precheck.crc_baseline).
                if [ "$_pf_v" = unchecked ] && command -v smart_learned_reason >/dev/null 2>&1 &&
                        _pf_l=$(smart_learned_reason "$_pf_d"); then
                    smart_note_he warn "$_pf_l" > "$RUN_DIR/targets/$_pf_d/smart_note"
                    log "$(smart_disk_label "$_pf_d"): orange (unchecked $_pf_r, learned $_pf_l) -- written without asking (#2168)" >&2
                fi
                _pf_write="$_pf_write $_pf_d"
                ;;
            fail)
                target_set "$_pf_d" "skipped" "$(smart_red_he "$_pf_d")$([ "$_pf_r" = readonly ] || [ "$_pf_r" = f1_observed ] || printf ' (%s)' "$(smart_reason_he "$_pf_r")")"   # #1742; #1861: no raw word
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

smart_gate() {
    # $1 = session, $2.. = disks. השער השלם, כדי שמסלולי השחזור יישארו
    # קצרים: מריץ את ה-preflight, מדפיס את רשימת הדיסקים לכתיבה, ומחזיר
    #   0 = כתוב את הרשימה שהודפסה
    #   1 = לא נשאר מה לכתוב (הכול דולג)
    # ‏#2044: אין עוד "החלף" (rc 2) -- אין שאלה, ולכן אין מי שבוחר בו.
    _sg_ses="$1"; shift
    _sg_list=$(smart_preflight "$_sg_ses" "$@")
    [ -n "$_sg_list" ] || return 1
    echo "$_sg_list"
}
