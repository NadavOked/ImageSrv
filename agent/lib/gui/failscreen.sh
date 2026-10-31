# failscreen.sh -- the one failure screen (#1569, mockup PR #1550). POSIX sh.
#
# Owner, 17/10: "no screen that is not graphic in our design", and no English
# on it. Until now a failure was `ui_error_hold`: English on the bare tty,
# hidden behind a live GUI or shown as squares (ui.sh). Every failing path --
# the hold (ui.sh) and the fatal reboot (common.sh die_local) -- now calls
# fail_screen, which turns the English line it already has into the records
# of one Hebrew screen: title, what happened (the target's own reason, in
# Hebrew -- fail_reason_he), what to do and the buttons that do it (#2068,
# hold.sh hold_action). The raw lines (fail_code) are not drawn: the log is in
# the machine's drawer on the console (Nadav 24/10).
#
# How it reaches a display -- in this order, never two GUIs on one framebuffer:
#   1. a live kiosk (guiparent.sh _gui_pid): its state writer copies
#      $RUN_DIR/fail.state into the state file (guistate.sh gui_hello_state);
#   2. the display-only GUI #1548 brought up (single_restore.sh _sf_pid): its
#      state file is rewritten with these records;
#   3. none: a display-only GUI is started on $RUN_DIR/gui/failed.state, and
#      a loop rewrites that file every 2 s (hello_age= moves: the status bar
#      says whether the server still hears the hold, principle 5).
# No GUI binary, or none that starts: the tty line stays, as before.
# "מכונה לא מוכרת" is not a failure (#1802): ui_unknown (ui.sh) writes case 8's
# records itself -- tone inf, no hold, fail_at -- and shows them via fail_show.

FAIL_GUI_BIN="${FAIL_GUI_BIN:-/usr/bin/imagesrv-station-gui}"
FAIL_SHOW_S="${FAIL_SHOW_S:-10}"   # die_local: how long the screen stays before the reboot

fail_clean() { printf '%s' "$1" | tr '\r\n|' '   '; }
fail_has_hebrew() { printf '%s' "$1" | LC_ALL=C grep -q "$(printf '\327')"; }   # 0xD7 leads every Hebrew letter

fail_reason() {
    # The first error line of the targets (progress.sh target_set), raw; "" = none.
    for _fr in "$RUN_DIR"/targets/*/error; do
        [ -s "$_fr" ] || continue
        _fr_l=$(head -n 1 "$_fr" | tr -d '\r|')
        [ -n "$_fr_l" ] && { printf '%s' "$_fr_l"; return 0; }
    done
}

fail_reason_he() {
    # $1 = a raw reason -> one Hebrew sentence for "מה קרה". A reason the agent
    # already wrote in Hebrew (writecheck.sh, smartgate.sh, directsend.sh) is
    # one; an English line is mapped, and one nobody mapped names the stage.
    [ -n "$1" ] && fail_has_hebrew "$1" && { printf '%s' "$1"; return 0; }
    case "$1" in
        *"manifest upload"*|*"upload rc="*|*"HTTP 5"*|*"HTTP 4"*) printf 'ההעלאה לשרת לא הסתיימה' ;;
        *"short write"*|*"lost bytes"*)      printf 'הדיסק איבד בייטים בזמן הכתיבה' ;;
        *"I/O error"*|*"Input/output"*)      printf 'שגיאת קריאה/כתיבה בדיסק' ;;
        *"not GPT"*)                         printf 'הדיסק אינו בפורמט GPT' ;;
        *"partition table"*|*sgdisk*|*GPT*)  printf 'טבלת המחיצות לא נכתבה' ;;
        *"receive-timeout"*|*"no data"*)     printf 'השידור נקטע לפני שהסתיים' ;;
        *SATA*|*"link reset"*|*"disconnect"*) printf 'הדיסק התנתק באמצע הכתיבה' ;;
        *"No space"*|*"too small"*)          printf 'אין מספיק מקום בדיסק' ;;
        *)                                   printf 'השלב "%s" נכשל' "$(fail_stage_he)" ;;
    esac
}

fail_stage() {
    # The agent's stage: $RUN_DIR/state, else the first target's; "" = none.
    _fg=$(cat "$RUN_DIR/state" 2>/dev/null)
    case "$_fg" in ''|failed|done) for _fg_t in "$RUN_DIR"/targets/*/state; do [ -f "$_fg_t" ] && { _fg=$(cat "$_fg_t"); break; }; done ;; esac
    printf '%s' "$_fg"
}

fail_stage_he() {
    case "$(fail_stage)" in
        capturing) printf 'קריאת הדיסק' ;;  uploading) printf 'העלאה לשרת' ;;  verifying) printf 'בדיקת האימג׳' ;;
        writing|sending) printf 'כתיבה' ;;  naming|staging) printf 'סיום: שם ודרייברים' ;;
        skipped) printf 'בדיקת SMART לפני כתיבה' ;;  waiting) printf 'לפני הכתיבה' ;;  *) printf 'הפעולה' ;;
    esac
}

fail_state_he() {   # $1 = a target's state word (progress.sh target_set) -> its pill, never the word (#1861)
    case "$1" in waiting|pending) printf 'ממתין' ;; writing) printf 'כותב' ;; verifying) printf 'מאמת' ;; replacing) printf 'ממתין להחלפה' ;;
        cancelled) printf 'בוטל' ;; skipped) printf 'דולג' ;; failed) printf 'נכשל' ;; done) printf 'נכתב' ;; *) printf 'לא ידוע' ;; esac
}

fail_disk_he() {
    # $1 = dev -> "פנימי · 500GB" from sysfs; 1 when unread. "פנימי" = disk_internal, the hello's one answer (#1909).
    _fd_s=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null)
    case "$_fd_s" in ''|*[!0-9]*) return 1 ;; esac
    printf '%s · %sGB' "$(disk_internal "$1" && echo פנימי || echo חיצוני)" "$((_fd_s * 512 / 1000000000))"
}

fail_case() {
    # $1 = the English hold line -> the screen's case. The suffix "and the
    # server was not told" (hold_unheard) is the unheard variant of its case.
    _fc_unheard=0; case "$1" in *"server was not told"*|*"server never confirmed"*) _fc_unheard=1 ;; esac
    case "$1" in
        "all drawers skipped"*)          _fc=skipped ;;
        "the disk was skipped"*)         _fc=skipped1 ;;
        "no drawer completed"*)          _fc=drawers ;;
        "restore did not complete"*|"restore cancelled"*) _fc=restore ;;   # #2068: a cancel whose wipe failed is a partial disk too
        "capture did not complete"*)     _fc=capture ;;
        "direct deployment did not"*)    _fc=direct ;;
        "the work finished but"*)        _fc=finished ;;
        *)                               _fc=other ;;
    esac
}

fail_texts() {
    # $1 = case, $2 = Hebrew reason, $3 = image name, $4 = optional capture recovery code.
    # Sets _ft_title _ft_sub _ft_why _ft_safe _ft_disk (the context cell's label for the
    # disk / drawers), _ft_do (what happened and what the buttons do; _ft_do2 a second,
    # muted line) and _ft_btn ("action|label;..." -- hold.sh hold_action does them).
    # #2068/#2084 (Nadav 24/10): the screen says what to do with a button on this
    # screen -- never "פנו ל-IT", never another screen, never a device name.
    _ft_safe=""; _ft_disk="דיסק"; _ft_img=${3:+ · $3}; _ft_do2=""
    _ft_do="המשימה נעצרה. המחשב נשאר דלוק ומדווח לשרת."; _ft_btn="retry|נסה שוב;off|כבה את המחשב"
    case "$1" in
        skipped)  _ft_title="כל המגירות דולגו — לא נכתב דבר"; _ft_sub="סבב$_ft_img · מחשב שיכפול"; _ft_disk="מגירות"
                  _ft_why="בבדיקת הדיסקים לפני הכתיבה כל המגירות דולגו, ולכן לא נכתב אף בייט — הדיסקים נשארו כפי שהיו."
                  _ft_do="כל הדיסקים דולגו בבדיקה לפני הכתיבה, ולא נכתב דבר. אחרי החלפת הדיסקים שסומנו, המחשב יצטרף לסבב הבא."
                  _ft_btn="off|כבה את המחשב;standby|בדוק את הדיסקים שוב" ;;
        skipped1) _ft_title="הדיסק דולג — לא נכתב דבר"; _ft_sub="שחזור לתחנה$_ft_img"
                  _ft_why="בדיקת הדיסק לפני השחזור סימנה אותו כבעייתי ($2), והשחזור לא התחיל. הדיסק נשאר כפי שהיה."
                  _ft_safe="המערכת הקיימת בדיסק לא נגעה — אפשר עדיין לאתחל ממנו, אבל הוא עלול להיכשל בקרוב."
                  _ft_do="הדיסק צריך החלפה. המחשב נשאר דלוק ומדווח לשרת." ;;
        drawers)  _ft_title="אף מגירה לא הסתיימה בהצלחה"; _ft_sub="סבב$_ft_img · מחשב שיכפול"; _ft_disk="מגירות"
                  _ft_why="בכל המגירות הכתיבה נכשלה ($2). הדיסקים נמצאים במצב חלקי — לא ניתן להשתמש בהם כמו שהם."
                  _ft_safe="אל תכניסו דיסקים אלה לתחנות לפני שיכפול מחדש."
                  _ft_do="הסיבה של כל מגירה כתובה ברשימה. בדקו שהדיסקים יושבים חזק ושהכבלים מחוברים."
                  _ft_do2="המחשב נשאר דלוק ומדווח לשרת. \"חזרה להמתנה\" מצרף אותו לסבב הבא בלי לאתחל."
                  _ft_btn="standby|חזרה להמתנה לסבב;off|כבה את המחשב" ;;
        restore)  _ft_title="השחזור נכשל"; _ft_sub="שחזור אימג׳ למחשב הזה$_ft_img"; _ft_disk="דיסק יעד"
                  _ft_why="הכתיבה לדיסק נעצרה לפני שהסתיימה: $2."
                  _ft_safe="הדיסק במצב לא תקין — אל תאתחלו ממנו עד שמחזירים אימג׳ תקין."
                  _ft_do="הכתיבה נעצרה לפני הסוף, והדיסק לא שמיש עד ששחזור יסתיים." ;;
        capture)  _ft_title="הקליטה נכשלה"; _ft_sub="קליטת אימג׳ חדש$_ft_img"; _ft_disk="דיסק מקור"
                  _ft_why="הדיסק נקרא, אבל ${2%.}, ולכן האימג׳ לא נכנס לספרייה."
                  _ft_safe="הדיסק של המחשב הזה לא שונה — קליטה רק קוראת ממנו. אפשר להתחיל מחדש."
                  _ft_btn="windows|הפעל את Windows;retry|נסה שוב"
                  case "${4:-unknown}" in
                      network_server) _ft_do="ההעלאה לשרת נעצרה. הדיסק של המחשב הזה לא שונה."; _ft_btn="retry|נסה שוב;off|כבה את המחשב" ;;
                      source_ntfs) _ft_do="‏NTFS במקור מסומן כלא תקין. ב-Windows: ⁦chkdsk C: /f⁩, הפעלה מחדש, ואז קליטה שוב." ;;
                      source_bitlocker) _ft_do="‏BitLocker פעיל במקור. ב-Windows: ⁦manage-bde -off C:⁩, והמתינו לסוף הפענוח." ;;
                      source_dynamic) _ft_do="המירו את הדיסק ל-Basic ב-Windows (בניהול הדיסקים ההמרה מוחקת את הכרכים — גבו קודם), ואז קלטו שוב." ;;   # #1824
                      source_no_esp) _ft_do="התקינו את Windows מחדש כשרק הדיסק הזה מחובר, או צרו בו מחיצת EFI (bcdboot) — ואז קלטו שוב." ;;   # #1822
                      source_disk) _ft_do="דיסק המקור לא נקרא עד הסוף. \"בדוק את הדיסק\" מציג כאן את מצב ה-SMART שלו."; _ft_btn="smart|בדוק את הדיסק;retry|נסה שוב" ;;
                      *) _ft_btn="retry|נסה שוב;off|כבה את המחשב" ;;
                  esac ;;
        direct)   _ft_title="השליחה הישירה נכשלה"; _ft_sub="ממחשב הבנייה אל מחשבי היעד · שליחה חד-פעמית"; _ft_disk="דיסק מקור"
                  _ft_why="השליחה אל מחשבי היעד נעצרה לפני שהסתיימה: $2. הדיסקים ביעדים אינם שלמים ולא יעלו."
                  _ft_safe="הדיסק של מחשב הבנייה לא שונה — הוא נקרא בלבד."
                  _ft_do="ודאו שהכבל בין המחשבים מחובר ושהיעדים עדיין דלוקים." ;;
        finished) _ft_title="המשימה הסתיימה — והשרת לא אישר"; _ft_sub="המשימה$_ft_img · הדיווח האחרון לא אושר"
                  _ft_why="העבודה הסתיימה, אבל השרת לא אישר שקיבל את הדיווח. בקונסולה המשימה עדיין נראית \"רצה\" — עד שהקשר יחזור."
                  _ft_safe="המחשב ממשיך לנסות לדווח. הדיסק לא שונה."
                  _ft_do="העבודה הסתיימה, והשרת לא אישר שקיבל את הדיווח. המחשב ממשיך לנסות לבד."; _ft_btn="beat|נסה לדווח עכשיו" ;;
        fatal)    _ft_title="המחשב נעצר — מאתחל"; _ft_sub="השרת מחליט מאיפה האתחול הבא"
                  _ft_why="$2"
                  _ft_safe="לא נכתב דבר לדיסק."
                  _ft_do="המחשב יאתחל בעוד ${FAIL_SHOW_S} שניות, והשרת יחליט מאיפה יעלה."; _ft_btn="reboot|אתחל עכשיו" ;;
        *)        _ft_title="המשימה נכשלה"; _ft_sub="המשימה$_ft_img נעצרה"
                  _ft_why="המשימה נעצרה לפני שהסתיימה: $2." ;;
    esac
    if [ "${_fc_unheard:-0}" = 1 ] && [ "$1" != finished ]; then
        _ft_title="$_ft_title — והשרת לא יודע"
        _ft_why="$_ft_why גם הדיווח על כך לא הגיע לשרת: בקונסולה המשימה עדיין נראית \"רצה\" — עד שהקשר יחזור."
        _ft_do2="כשהקשר יחזור, המחשב ידווח לבד."
    fi
}

fail_fatal_he() {
    # die_local's English, in Hebrew where the line is a known one.
    case "$1" in
        *"not authorized"*)        printf 'השחזור לא אושר מול השרת — כניסת הקונסולה של המסך הזה כבר אינה בתוקף. לא נכתב דבר.' ;;
        *"no longer allowed"*)     printf 'האימג׳ שנבחר כבר אינו מותר למחשב הזה. לא נכתב דבר.' ;;
        *"malformed image id"*)    printf 'מזהה האימג׳ שהגיע מהמסך פגום. לא נכתב דבר.' ;;
        *"manifest fetch failed"*) printf 'לא ניתן היה לקרוא מהשרת את פרטי האימג׳.' ;;
        *"manifest is not of the session"*) printf 'פרטי האימג׳ שהגיעו מהשרת שייכים לאימג׳ אחר מזה של הסבב (#1826). לא נכתב דבר.' ;;
        *"session portbase"*)     printf 'הפורט שהשרת נתן לסבב אינו תקין, ולכן אין דרך לדעת איזה שידור שייך לסבב (#1826). לא נכתב דבר.' ;;
        *"no internal disk"*)      printf 'לא נמצא דיסק פנימי במחשב הזה.' ;;
        *"more than one internal disk"*|*"no single internal disk"*) printf 'במחשב אין דיסק פנימי אחד בדיוק (אף אחד, או יותר מאחד) — לא משחזרים בניחוש. לא נכתב דבר.' ;;   # #1807
        *"split of the room was not approved"*) printf 'חלוקת המקום בין המחיצות לא אושרה. לא נכתב דבר.' ;;   *"split of the room did not compute"*) printf 'לא הצלחנו לחשב את חלוקת המקום בין המחיצות. לא נכתב דבר.' ;;   # #1898
        *"server unreachable"*)    printf 'השרת לא ענה במשך יותר מדקה.' ;;
        *"no work for this"*)      printf 'אין למחשב הזה משימה — הוא עולה מהדיסק המקומי.' ;;
        *"MAC not registered"*)    printf 'המחשב אינו רשום במערכת — הוא עולה מהדיסק המקומי.' ;;   *"another vlan"*) printf 'הסבב פתוח ברשת אחרת — המחשב אינו ברשת ההפצה.' ;;
        *"no image id"*|*"without image or id"*) printf 'לא נבחר אימג׳ למשימה. לא נכתב דבר.' ;;
        *"failed SMART"*)          printf 'הדיסק נכשל בבדיקת SMART ואינו יעד לכתיבה. לא נכתב דבר.' ;;   *"is read-only"*) printf 'הדיסק במצב קריאה בלבד ואינו יעד לכתיבה. לא נכתב דבר.' ;;
        *"target selection"*|*"selected drawer"*|*"selected no writable"*) printf 'בחירת המגירות לסבב חסרה או אינה תואמת לדיסקים במחשב. לא נכתב דבר.' ;;
        *"imagesrv.server"*|*"network configuration"*) printf 'הגדרות הרשת או כתובת השרת חסרות באתחול.' ;;   *"GUI unavailable"*) printf 'המסך הגרפי לא עלה.' ;;
        *)                         printf 'המחשב נעצר בשלב שאין לו הסבר כאן. הלוג המלא במגירת המכונה בקונסולה.' ;;   # #1861: never the English itself; #2068: no details box
    esac
}

fail_image_name() {
    # The manifest's name (a restore / drawers), else the task's (a capture / a send).
    for _fi in "$RUN_DIR/manifest.json" "$RUN_DIR/sr-manifest.json" "${RESP:-/nonexistent}"; do
        [ -s "$_fi" ] || continue
        case "$_fi" in *manifest*) _fi_n=$(json_get "$_fi" ".name" 2>/dev/null) ;; *) _fi_n=$(json_get "$_fi" ".task.name" 2>/dev/null) ;; esac
        case "$_fi_n" in null|'') ;; *) fail_clean "$_fi_n"; return 0 ;; esac
    done
}

fail_records() {
    # $1 = case, $2 = the English line, $3 = tone (bad|warn), $4 = hold (0|1),
    # $5 = the Hebrew reason. Prints the fail* records (docs/interfaces.md).
    _fx_img=$(fail_image_name); _fx_reason=""
    [ "$1" != capture ] || _fx_reason=$(cat "$RUN_DIR/capture.reason" 2>/dev/null)
    fail_texts "$1" "$5" "$_fx_img" "$_fx_reason"
    _fx_sub=$(fail_clean "$2")
    printf 'fail=%s|%s|%s|%s|%s\n' "$3" "$4" "$(fail_clean "$_ft_title")" "$(fail_clean "$_ft_sub")" "${D_ROLE:-}"   # field 5: the role, for the header
    printf 'fail_why=%s\n' "$(fail_clean "$_ft_why")"
    [ -n "$_ft_safe" ] && printf 'fail_safe=%s\n' "$(fail_clean "$_ft_safe")"
    for _fx_s in "$_ft_do" "$_ft_do2"; do [ -n "$_fx_s" ] && printf 'fail_step=%s\n' "$(fail_clean "$_fx_s")"; done
    printf '%s\n' "$_ft_btn" | tr ';' '\n' | sed 's/^/fail_btn=/'   # #2068: the buttons (hold.sh hold_action); the first is Enter
    # the context strip, the mockup's four cells: machine · disk/drawers · image · stage
    _fx_name=$(json_get "$RUN_DIR/response.json" ".name" 2>/dev/null); case "$_fx_name" in null|'') _fx_name=${MAC:-} ;; esac
    printf 'fail_ctx=מכונה|%s\n' "$(fail_clean "$_fx_name")"
    _fx_n=0; _fx_bad=0; _fx_rows=""; _fx_codes=""; _fx_first=""
    for _fx_t in "$RUN_DIR"/targets/*/; do
        [ -d "$_fx_t" ] || continue
        _fx_d=${_fx_t%/}; _fx_d=${_fx_d##*/}; _fx_st=$(cat "$_fx_t/state" 2>/dev/null); [ -n "$_fx_first" ] || _fx_first=$_fx_d
        _fx_n=$((_fx_n + 1)); case "$_fx_st" in failed|skipped) _fx_bad=$((_fx_bad + 1)) ;; esac
        _fx_e=$(head -n 1 "$_fx_t/error" 2>/dev/null | tr -d '\r|')
        case "$_fx_st" in skipped) _fx_k="wn|דולג" ;; failed) _fx_k="bad|נכשל" ;; done) _fx_k="ok|נכתב" ;; *) _fx_k="off|$(fail_state_he "$_fx_st")" ;; esac
        _fx_p=$(disk_port "$_fx_d" 2>/dev/null); _fx_dr=$(disk_drawer "$_fx_d" "${_fx_p:-0}" 2>/dev/null); _fx_dr=${_fx_dr%%|*}
        case "$_fx_dr" in ''|0|*[!0-9]*) _fx_lbl="דיסק לא ממופה" ;; *) _fx_lbl="מגירה $_fx_dr · דיסק $_fx_dr" ;; esac
        _fx_rows="${_fx_rows}fail_row=$_fx_lbl|$([ -n "$_fx_e" ] && fail_reason_he "$_fx_e")|$_fx_k
"
        _fx_codes="${_fx_codes}fail_code=targets/$_fx_d/state $(fail_clean "${_fx_st:-?}")
${_fx_e:+fail_code=targets/$_fx_d/error $_fx_e
}"
    done
    if [ "$_fx_n" -gt 1 ] || [ "$_ft_disk" = מגירות ]; then printf 'fail_ctx=מגירות|%s מתוך %s נכשלו או דולגו\n' "$_fx_bad" "$_fx_n"
    elif [ -n "$_fx_first" ]; then printf 'fail_ctx=%s|%s\n' "$_ft_disk" "$(fail_disk_he "$_fx_first" || printf 'לא נקרא')"; fi
    [ -z "$_fx_img" ] || printf 'fail_ctx=אימג׳|%s\n' "$_fx_img"
    _fx_pct=""   # a percentage only once bytes moved: not before the write, not on a skipped disk
    if [ -n "$_fx_first" ] && [ "$(fail_stage)" != skipped ] && [ "$(fail_stage)" != waiting ]; then
        _fx_b=$(target_bytes "$_fx_first" 2>/dev/null); _fx_z=$(cat "$RUN_DIR/targets/$_fx_first/total" 2>/dev/null)
        case "$_fx_b:$_fx_z" in *[!0-9:]*|:*|*:|*:0) ;; *) _fx_pct=" · $((_fx_b * 100 / _fx_z))%" ;; esac; fi
    printf 'fail_ctx=שלב|%s%s\n' "$(fail_stage_he)" "$_fx_pct"
    [ "$_fx_n" -gt 1 ] || [ "$_ft_disk" = מגירות ] || _fx_rows=""   # the mockup: rows for drawers, none for one disk
    printf '%s' "$_fx_rows$_fx_codes"
    printf 'fail_code=[agent] %s: %s\n' "${_fx_src:-ui_error_hold}" "$_fx_sub"
    _fx_tid=$(json_get "${RESP:-/nonexistent}" ".task.id" 2>/dev/null)
    case "$_fx_tid" in null|'') [ -n "${D_SESSION_ID:-}" ] && [ "$D_SESSION_ID" != null ] && printf 'fail_code=session %s\n' "$(fail_clean "$D_SESSION_ID")" ;;
        *) printf 'fail_code=task %s\n' "$(fail_clean "$_fx_tid")" ;; esac
    printf 'fail_code=mac %s · agent %s\n' "${MAC:-?}" "${AGENT_VERSION:-unknown}"
}

fail_gui_live() {
    # 0 when a GUI of ours is on the display and reads a state file we feed.
    [ -n "${_gui_pid:-}" ] && kill -0 "$_gui_pid" 2>/dev/null && return 0          # the kiosk
    { read -r _fl_p < "$RUN_DIR/gui.pid" && kill -0 "$_fl_p"; } 2>/dev/null && return 0   # #1590's record of it
    command -v sr_gui_alive >/dev/null 2>&1 && sr_gui_alive 2>/dev/null && return 0      # #1561's progress GUI
    return 1
}

fail_state_file() {
    # The display-only GUI's whole state: the records, the header, the hello.
    # gui_hello_state (guistate.sh) ends with the fail.state records itself; when
    # it is absent or refuses its hello.state, the records are still written.
    { gui_header_state "$RUN_DIR/response.json" || :; gui_hello_state || cat "$RUN_DIR/fail.state"; } 2>/dev/null > "$1.next" &&
        mv "$1.next" "$1"
}

fail_screen() {
    # $1 = case (hold|fatal), $2 = the English line. Writes the records and
    # gets them to a display. 0 = some GUI shows (or will show) them.
    _fs_hold=1; _fx_src=ui_error_hold; _fs_tone=bad
    if [ "$1" = fatal ]; then _fs_hold=0; _fx_src=die_local; _fc=fatal; _fc_unheard=0; _fs_raw=""; _fs_r=$(fail_fatal_he "$2")
    else fail_case "$2"; _fs_raw=$(fail_reason); _fs_r=$(fail_reason_he "$_fs_raw"); fi
    [ "${_fc_unheard:-0}" = 1 ] && _fs_tone=warn
    mkdir -p "$RUN_DIR/gui" || return 1
    { fail_records "$_fc" "$2" "$_fs_tone" "$_fs_hold" "$_fs_r"
      [ "$1" != fatal ] || printf 'fail_at=%s\n' "$(( $(date +%s) + FAIL_SHOW_S ))"; } > "$RUN_DIR/fail.state.next" || return 1
    mv "$RUN_DIR/fail.state.next" "$RUN_DIR/fail.state" || return 1
    log "failure screen: $_fc -- $_fs_r${_fs_raw:+ ($_fs_raw)}"
    fail_show
}

fail_show() {
    # $RUN_DIR/fail.state to a display, in the order at the top of this file.
    fail_gui_live && return 0
    _fs_f="$RUN_DIR/gui/failed.state"
    if [ -n "${_sf_pid:-}" ] && kill -0 "$_sf_pid" 2>/dev/null; then _fs_pid=$_sf_pid   # #1548's GUI, same file
    elif [ -n "${_fs_pid:-}" ] && kill -0 "$_fs_pid" 2>/dev/null; then :
    else
        [ -x "$FAIL_GUI_BIN" ] || return 1
        fail_state_file "$_fs_f" || return 1
        _fs_scr=""; [ "${D_ROLE:-}" = cloner ] && _fs_scr="--screen cloner"   # the header's role pill
        # shellcheck disable=SC2046,SC2086 # --backend/--fb-file and --screen are words (guibridge.sh)
        FONTCONFIG_FILE=/etc/imagesrv/fonts.conf "$FAIL_GUI_BIN" $(gui_backend_args) --mac "${MAC:-}" --ip "${IP:-}" \
            --state "$_fs_f" $_fs_scr > /dev/null 2>> "$LOG_FILE" &
        _fs_pid=$!
    fi
    ( while kill -0 "$_fs_pid" 2>/dev/null; do fail_state_file "$_fs_f"; sleep 2; done ) &
}

fail_fatal() {
    # die_local's screen: drawn, then held for FAIL_SHOW_S so a person reads
    # it (the 3 s line was not readable). 1 = nothing drawn, the caller's wait.
    fail_screen fatal "$1" || return 1
    [ "${IMAGESRV_TEST:-0}" = 1 ] && return 0
    _ff_n=0   # #2068: "אתחל עכשיו" (fail.action, hold.sh) ends the wait; die_local reboots
    while [ "$_ff_n" -lt "$FAIL_SHOW_S" ] && ! mv "${GUI_DIR:-$RUN_DIR/gui}/fail.action" "$RUN_DIR/fail.action.taken" 2>/dev/null; do sleep 1; _ff_n=$((_ff_n + 1)); done
}
