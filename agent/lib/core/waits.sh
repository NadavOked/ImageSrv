# waits.sh -- לכל המתנה בסוכן יש תקרה, וכשהיא נחצית היא מדווחת.
# POSIX sh (busybox ash).
#
# קפיאה אילמת היא הפרה של עיקרון 4 בדיוק כמו זריקת בלוק בשקט. במעבדה
# היא נראית גרוע יותר: מכונה שנתקעה בלי סיבה, בלי שורה אחת בלוג שאומרת
# על מה חיכינו. ‏#49 היה בדיוק זה — מגירה שפתחה fifo שאיש לא יכתוב
# אליו, ו-`zstd -dc < feed` שנחסם ב-open() לנצח.
#
# **המדד הוא חוסר התקדמות, לא משך כולל.** שחזור מחיצה של 100GB על כונן
# איטי-אך-יציב לוקח דקות ארוכות וזה תקין לחלוטין; מה שאינו תקין הוא מונה
# שהפסיק לזוז. אותו עיקרון בדיוק שמנחה את ה-back-pressure ב-fanout.c (#660).
#
# ‏timeout(1) לא בשימוש כאן בכוונה: הוא אינו ברשימת BINARIES של הבנאי
# (tools/build_initramfs.sh), ולהישען על אפלט של busybox שאולי הודר החוצה
# מהבנייה זו בדיוק ההנחה השקטה שנגמרת בקפיאה. ‏kill -0 ולולאת sleep
# עובדים בכל sh, בלי לארוז דבר.

# --- התקרות, במקום אחד -------------------------------------------------------

# כמה זמן בין דגימות (מונה, יומן, פעימה). שנייה: לא מבזבז CPU לאורך שחזור. סוף
# התהליך נתפס תוך 0.1ש' (_nap, #1705: ‏3ש' בכל מעבר מחיצה, ברזל 17/10).
WAIT_POLL_S="${WAIT_POLL_S:-1}"

# עד הבייט הראשון. השרת מפעיל udp-sender רק אחרי ש-min-receivers הצטרפו
# או ש-max-wait פג, ותחנה שהצטרפה באמצע מחיצה ממתינה לשידור הבא —
# דקות זה סביר. עשר דקות בלי בייט אחד זה כבר סבב שמת בשרת.
WAIT_STREAM_START_S="${WAIT_STREAM_START_S:-600}"

# עד הבייט הראשון של **המחיצה השנייה והלאה** (#957, כמו FOG GH-536).
# שם ההמתנה אינה למפעיל אלא לחדר: השולח ממתין עד `max_wait_later`
# (‏600ש', ‏server/sender.py) למגירה האיטית שעוד כותבת את המחיצה
# הקודמת — ואם המכונה הזו היא שהצטרפה ראשונה, היא ממתינה את כל
# ה-600 בעצמה. ‏udp-receiver סופר את ההמתנה הזאת ב-`--start-timeout`
# שלו (‏receivedata.c: הבייט הראשון אחרי החיבור נמדד ב-startTimeout, לא
# ב-receiveTimeout), ולכן התקרה כאן חייבת להיות **גדולה** מ-600 —
# אחרת המקבל שהגיע ראשון פוקע בדיוק כשהשולח מתחיל. ‏300 מעל: אותו
# מרווח כמו תקרת השקט. גם מחשב הבנייה בהפצה ישירה ממתין כך למקבלים
# שלו (‏`--start-timeout` של השולח שם הוא 780 = 600 + 180).
WAIT_STREAM_START_LATER_S="${WAIT_STREAM_START_LATER_S:-900}"

# אחרי שהזרם התחיל: כמה שקט מותר באמצעו. חמש דקות (11/09/2026, הכרעת
# נדב): back-pressure בזנב של אימג' גדול (67GB) מאט את הזרם למקבל האיטי
# במקום להפילו — גישת FOG/Clonezilla — ותקרת 120ש' חתכה אותו דווקא אז.
# עדיין הרבה מתחת ל"הטכנאי הלך הביתה", ומתחת ל-600ש' של ה-watchdog
# (watchdog.sh), שמוזן בכל דגימה כאן — ולכן תקרת השקט הזו נורית לפניו.
WAIT_STREAM_STALL_S="${WAIT_STREAM_STALL_S:-300}"

# מגירה שסיימה לקבל בייטים ועוד מרוקנת את החוצץ שלה (עד 256MB) אל
# הכונן. חמש דקות (11/09/2026): מאז #660 ‏fanout **אינו** מוותר על מגירה
# איטית — היא מאטה את כל הזרם במקום להיפסל (FOG/Clonezilla), ולכן מגירה
# איטית-אך-חיה עשויה להתנקז אחרי ה-EOF יותר מ-120ש', ותקרת 120 חתכה
# אותה דווקא אז. הגג תואם ל-WAIT_STREAM_STALL_S. התקרה כאן היא רשת
# הביטחון של הצד השני של ה-fifo — הצינור שנתקע ב-open() כי אף אחד לא כתב
# אליו מעולם; מגירה שבורה (לא איטית) נתפסת בקוד היציאה של partclone.
# המגירות נבדקות בזו אחר זו, ולכן זה גם הגג לכל המכונה כפול מספר המגירות.
WAIT_DRAWER_S="${WAIT_DRAWER_S:-300}"

# תהליכי עזר שכבר קיבלו EOF ורק צריכים לסיים: sha256sum על הזנב,
# ה-curl שמעלה מחיצה שנקראה עד סופה. שנייה הייתה מספיקה; חצי דקה כי
# כונן עמוס יכול לעכב גם אותם.
WAIT_HELPER_S="${WAIT_HELPER_S:-30}"

# שאלת הכיווץ בדיאלוג של המסך הגרפי (‏#928, ‏shrinkgui.sh). המפעיל לחץ
# "התחל קליטה" רגע לפני ועומד ליד המכונה; חצי שעה בלי תשובה היא מסך
# שלא מציג את השאלה (מצב שלא נקרא, רשומה שנדחתה) או חדר ריק. פקיעה =
# הקליטה **מבוטלת** בגלוי, ושום דבר לא כווץ -- לעולם לא "כווץ" בשקט.
WAIT_SHRINK_GUI_S="${WAIT_SHRINK_GUI_S:-1800}"

# שאלת ה-SMART במסך מחשב השיכפול (‏clonergui.sh). אותה חצי שעה: אחריה --
# "בלי תשובה" של הצבע (נדב 16/09): כתום כותב, אדום מדלג. עד 17/10 לא הייתה
# תקרה, והשולח חיכה לשאלה לנצח. ‏server/smart_ask.py ‏ASK_HOLD_MAX_SECONDS
# הוא התקרה הזו + 300: הסוכן מוותר ראשון, והשרת הוא רשת הביטחון.
WAIT_SMART_GUI_S="${WAIT_SMART_GUI_S:-1800}"

# בדיקת הכתיבה לפני הגל (#1366, writecheck.sh), לכל דיסק: 3×4MiB כתיבה ישירה
# עם fsync וקריאה חוזרת — שנייה על דיסק תקין, גם על 15 MB/s פחות מ-2ש'. חצי
# דקה בלי סיום = דיסק שלא מקבל כתיבה בפועל, והוא נכשל בשמו. המגירות נבדקות
# בזו אחר זו, ולכן זה גם הגג לכל המכונה כפול מספר המגירות.
WAIT_WRITECHECK_S="${WAIT_WRITECHECK_S:-30}"
WAIT_READBACK_S="${WAIT_READBACK_S:-7200}"   # #1805 readback.sh, one disk after its write: 500GB used at 70MB/s
# קוד היציאה שמסמן "פג הזמן" בהמשך הזרימה. אותו 124 של timeout(1) —
# ‏rc שאינו 0 ואינו קוד אמיתי של אף כלי בצינור.
WAIT_TIMED_OUT=124

# ‏smartctl אחד (‏#1367). דיסק בריא עונה בפחות משנייה; דיסק שה-ioctl שלו
# נתקע בקרנל לא עונה לעולם, ובלי תקרה הוא משתיק את ה-hello של מחשב שיכפול
# שלם — ה-probe רץ **בתוך** build_hello. מעבר לתקרה: "לא נבדק", בשמו.
SMARTCTL_MAX_S="${SMARTCTL_MAX_S:-30}"
NVME_MAX_S="${NVME_MAX_S:-30}"

# --- איזו תקרה למחיצה הבאה -------------------------------------------------

# כמה מחיצות כבר הוזרמו בשחזור (או בשידור הישיר) הנוכחי. הלולאה
# שמתחילה שחזור מאפסת; next_stream_ceiling סופר. ‏swap אינה נספרת —
# היא נבראת, לא מוזרמת — ולכן הספירה היא של זרמים, לא של שורות בתוכנית.
STREAMED_PARTITIONS=0

next_stream_ceiling() {
    # קובע את STREAM_START_CEILING — התקרה עד הבייט הראשון של המחיצה
    # שעומדת להיות מוזרמת — וסופר אותה. הראשונה: WAIT_STREAM_START_S
    # ("המפעיל לא התחיל"); מהשנייה: WAIT_STREAM_START_LATER_S ("השולח
    # ממתין למגירה איטית"). פונקציה ולא ביטוי אצל הקוראים, כי שני
    # מסלולי השחזור (restore.sh, drawers.sh) והשידור הישיר חייבים
    # לענות על השאלה הזאת באותה צורה, ולתת אותו מספר ל-udp-receiver
    # ול-wait_progress שמסתכל עליו.
    if [ "${STREAMED_PARTITIONS:-0}" -gt 0 ]; then
        STREAM_START_CEILING="$WAIT_STREAM_START_LATER_S"
    else
        STREAM_START_CEILING="$WAIT_STREAM_START_S"
    fi
    STREAMED_PARTITIONS=$((${STREAMED_PARTITIONS:-0} + 1))
}

# --- ההמתנות עצמן ------------------------------------------------------------

# על ההריגה: ‏kill על תת-מעטפת שמריצה צינור הורג את המנהלת ולא את חבריה,
# והם נשארים יתומים. ב-POSIX sh אין דרך נקייה להרוג קבוצת תהליכים בלי
# job control, ולכן זה מכוון: המטרה כאן היא שהסוכן ימשיך וידווח, לא
# שהמכונה תהיה נקייה. בפועל היתומים נגמרים מעצמם ברגע שהמקור מת — ולכן
# ‏udp-receiver ו-curl קיבלו תקרות משלהם, ולכן unblock_fifo קיים: הם
# מטפלים בשורש, וההריגה כאן היא רשת הביטחון האחרונה.

# One poll tick (#1450). Sets _tk to the tenths actually slept. Debian's
# busybox is built with FLOAT_DURATION, but one built without it refuses
# `sleep 0.1` at once -- and a refused tick must cost a real second, never
# zero: a hot loop would call every smartctl "timed out" within milliseconds.
poll_tick() { if sleep 0.1 2>/dev/null; then _tk=1; else sleep 1; _tk=10; fi; }
_nap() { _nap_t=0; while [ "$_nap_t" -lt $((WAIT_POLL_S * 10)) ] && kill -0 "$1" 2>/dev/null; do poll_tick; _nap_t=$((_nap_t + _tk)); done; }   # $1 ends = back now

capped_run() {
    # $1 = תקרה בשניות, $2 = קובץ ל-stdout של הפקודה, $3.. = הפקודה (‏stderr
    # ללוג). מחזיר את קוד היציאה של הפקודה עצמה; כשלא הסתיימה בזמן --
    # ‏CAPPED_TIMED_OUT=1, שורת לוג ו-$WAIT_TIMED_OUT. הקורא בודק את
    # ‏CAPPED_TIMED_OUT ולא את הקוד: אצל smartctl כל קוד 0-255 הוא תשובה.
    # ‏**בלי wait אחרי ההריגה**: תהליך שתקוע ב-ioctl (מצב D) אינו מת מ-kill -9,
    # ו-wait עליו הוא בדיוק הקפיאה שהפונקציה הזו קיימת כדי לסיים (#1367).
    _cr_max="$1"; _cr_limit=$((_cr_max * 10)); _cr_out="$2"; shift 2; CAPPED_TIMED_OUT=0; _cr_t=0
    # a log that cannot be opened must not stop the command itself (a redirect
    # that fails runs nothing): no log directory (host, tests) = no stderr
    _cr_err="${LOG_FILE:-/dev/null}"; [ -d "${_cr_err%/*}" ] || _cr_err=/dev/null
    "$@" > "$_cr_out" 2>> "$_cr_err" < /dev/null &
    _cr_pid=$!
    while kill -0 "$_cr_pid" 2>/dev/null; do
        if [ "$_cr_t" -ge "$_cr_limit" ]; then
            log "פג הזמן: $1 לא ענה תוך $_cr_max שניות -- נעזב, לא נבדק" >&2
            kill -9 "$_cr_pid" 2>/dev/null; CAPPED_TIMED_OUT=1
            return "$WAIT_TIMED_OUT"
        fi
        poll_tick; _cr_t=$((_cr_t + _tk))
    done
    wait "$_cr_pid"
}

wait_pid() {
    # $1 = pid, $2 = תקרה בשניות, $3 = על מה מחכים (לשורת הלוג).
    # 0 = הסתיים בזמן. 1 = פג הזמן: התהליך נהרג, נכתבה שורת לוג שאומרת
    # על מה ולכמה, והקורא ממשיך לסמן failed ולעבור הלאה.
    _w_pid="$1"; _w_max="$2"; _w_what="$3"; _w_spent=0   # tenths (#1705)
    while kill -0 "$_w_pid" 2>/dev/null; do
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        if [ "$_w_spent" -ge $((_w_max * 10)) ]; then
            log "פג הזמן: $_w_what לא הסתיים תוך $_w_max שניות -- נכשל"
            kill -9 "$_w_pid" 2>/dev/null
            wait "$_w_pid" 2>/dev/null
            return 1
        fi
        _nap "$_w_pid"; _w_spent=$((_w_spent + _nap_t))
    done
    wait "$_w_pid" 2>/dev/null
    return 0
}

wait_progress() {
    # $1 = pid, $2 = קובץ מונה שגדל כל עוד יש התקדמות (הפלט של pv -n -b),
    # $3 = תקרה עד הבייט הראשון, $4 = תקרת שקט אחריו, $5 = על מה מחכים.
    # $6.. = fifos whose blocked readers must be released after a timeout.
    # WAIT_STARTED_LOG + WAIT_STARTED_TEXT optionally delay the quiet clock
    # until explicit start evidence appears (#1497); they are one-shot.
    # WAIT_ABORT_FILE (one-shot, metal 17/10): when that file appears the
    # stream is stopped at once and rc=1 -- the server closed the session.
    #
    # כל עוד המונה זז ממתינים בלי שום הגבלה — זו כל הנקודה: כונן איטי
    # מעכב, הוא לא נתקע. השעון מתחיל לרוץ רק כשהמונה עומד, והתקרה
    # מתחלפת ברגע שהבייט הראשון הגיע: "השידור עוד לא התחיל" ו"השידור
    # נפסק באמצע" הם שני מצבים שונים עם סבלנות שונה.
    _p_pid="$1"; _p_file="$2"; _p_max="$3"; _p_stall="$4"; _p_what="$5"
    _p_glog="${WAIT_STARTED_LOG:-}"; _p_gtxt="${WAIT_STARTED_TEXT:-}"
    _p_abort="${WAIT_ABORT_FILE:-}"
    WAIT_STARTED_LOG=""; WAIT_STARTED_TEXT=""; WAIT_ABORT_FILE=""
    _p_gopen=1
    [ -n "$_p_glog" ] && [ -n "$_p_gtxt" ] && _p_gopen=0
    shift 5
    _p_seen=""; _p_still=0
    while kill -0 "$_p_pid" 2>/dev/null; do
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        _nap "$_p_pid"
        if [ -n "$_p_abort" ] && [ -e "$_p_abort" ]; then
            log "$_p_what: the server closed the session -- stopped"
            _p_kill "$@"; return 1
        fi
        # Missing text (grep 1) and a log not written yet or unreadable (grep 2,
        # its message dropped: it is this branch, not a lost one) both keep the
        # bounded START ceiling -- never success: the caller judges that.
        if [ "$_p_gopen" -eq 0 ] && grep -qF -- "$_p_gtxt" "$_p_glog" 2>/dev/null; then
            _p_gopen=1
            _p_still=0
            _p_max="$_p_stall"
            continue
        fi
        # רק שורות מספריות: שורת שגיאה של `pv` היא שינוי בקובץ, והיא
        # נראתה כאן כמו "התקדמות" — כלומר איפסה את שעון התקיעה בדיוק
        # ברגע שבו הזרם נשבר (#100). ההשוואה מחרוזתית ולכן לא התפוצצה,
        # והשקר היה שקט.
        _p_now=$(tr -d '\r' < "$_p_file" 2>/dev/null \
            | awk '/^[0-9]+$/ { n = $0 } END { if (n != "") print n }')
        # ‏"0" אינו בייט ראשון (#1430): ‏pv -n מדפיס אותו בכל פעימה לפני
        # שהגיע בייט, ושינוי ""→"0" החליף את תקרת ההתחלה בתקרת השקט.
        if [ "$_p_now" != "$_p_seen" ] && [ "$_p_now" != 0 ]; then
            _p_seen="$_p_now"
            _p_still=0
            [ "$_p_gopen" -eq 0 ] || _p_max="$_p_stall"
            continue
        fi
        _p_still=$((_p_still + WAIT_POLL_S))
        [ "$_p_still" -lt "$_p_max" ] && continue
        log "פג הזמן: $_p_what -- $_p_max שניות בלי התקדמות (עצר על ${_p_seen:-0} בייטים) -- נכשל"
        _p_kill "$@"; return 1
    done
    wait "$_p_pid" 2>/dev/null
    return 0
}

_p_kill() {   # wait_progress's way out: $@ = the fifos to release
    kill -9 "$_p_pid" 2>/dev/null
    wait "$_p_pid" 2>/dev/null
    for _p_fifo in "$@"; do unblock_fifo "$_p_fifo"; done
}

unblock_fifo() {
    # $1 = נתיב ל-fifo. פותחים אותו לקריאה ולכתיבה וסוגרים מיד: גם קורא
    # וגם כותב שתקועים ב-open() משתחררים, ולכן הצינור נגמר במקום להיתלות.
    # ‏kill על תת-המעטפת לבדו לא מספיק — הוא משאיר את zstd יתום וחסום.
    #
    # ברקע בכוונה: אם הקורא כבר איננו, ה-open הזה הוא שייחסם, ואסור לו
    # לגרור איתו את מי שקרא לו. תהליך תלוי אחד ב-tmpfs שממילא מאותחל.
    ( exec 3<> "$1"; : ) 2>/dev/null &
}

# #1571: this machine's own UDP drops around one multicast stream. RcvbufErrors =
# the kernel dropped datagrams because udp-receiver's socket buffer was full (it
# did not get the CPU in time) -- a re-xmit the network did not cause. Counters
# are machine-wide; on a cloner the stream is the only UDP of any volume.
udp_drops() {   # "<RcvbufErrors> <InErrors>"; rc 1 = not read -- never "0 0"
    awk '$1 == "Udp:" { if (!h) { for (i = 2; i <= NF; i++) c[$i] = i; h = 1; next }
        if (c["RcvbufErrors"] && c["InErrors"] && $c["RcvbufErrors"] ~ /^[0-9]+$/ &&
            $c["InErrors"] ~ /^[0-9]+$/) { print $c["RcvbufErrors"], $c["InErrors"]; ok = 1 }
        exit } END { exit !ok }' "${SYSROOT:-}/proc/net/snmp" 2>/dev/null || return 1
}
udp_drops_mark() { UDP_DROPS0=""; [ "$1" != multicast ] || UDP_DROPS0=$(udp_drops) || :; }
udp_drops_log() {   # $1 = mode, $2 = what was streamed
    [ "$1" = multicast ] || return 0
    _ud1=$(udp_drops) || _ud1=""
    if [ -n "${UDP_DROPS0:-}" ] && [ -n "$_ud1" ]; then
        set -- "$2" $UDP_DROPS0 $_ud1
        log "$1: udp drops on this machine rcvbuf=$(($4 - $2)) inerrors=$(($5 - $3))"
        udp_drops_add $(($4 - $2)) $(($5 - $3))
    else
        log "$2: udp drops not read (/proc/net/snmp)"
        udp_drops_add null null
    fi
}
# Rate policy (Nadav 17/10): the server raises the next wave only when every
# receiver reported rcvbuf=0. The sum over this restore's multicast partitions
# rides in the progress report (udp_drops); one unread partition makes the whole
# sum unread -- a partial sum would read as "no drops" (principle 5).
udp_drops_reset() { rm -f "$RUN_DIR/udp_drops.sum" "$RUN_DIR/udp_drops.json"; }
udp_drops_add() {   # $1 = rcvbuf, $2 = inerrors of one partition ("null" = not read)
    _ua=$(cat "$RUN_DIR/udp_drops.sum" 2>/dev/null) || _ua="0 0"
    case "$1 $_ua" in
        *null*) set -- null null ;;
        *) set -- $(($1 + ${_ua% *})) $(($2 + ${_ua#* })) ;;
    esac
    echo "$1 $2" > "$RUN_DIR/udp_drops.sum"
    printf '{"rcvbuf":%s,"inerrors":%s}\n' "$1" "$2" > "$RUN_DIR/udp_drops.json"
}

reread_table() {
    # $1 = the disk node. blockdev --rereadpt is refused (EBUSY) while anything holds a
    # partition open -- udev probing the nodes the previous reread or sgdisk just made.
    # Under load that met the next table write (#1594): a target failed "the kernel
    # refused" and its twin did not. Tried every second, at most REREAD_TRIES times; a
    # refusal that outlasts them is the kernel's answer (1, last message in the log).
    _rr_n=0
    while :; do
        blockdev --rereadpt "$1" >> "$LOG_FILE" 2>&1 && return 0
        _rr_n=$((_rr_n + 1))
        [ "$_rr_n" -lt "${REREAD_TRIES:-10}" ] || return 1
        sleep 1
    done
}
