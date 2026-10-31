# writecheck.sh -- #1366: does the disk take a write, asked before the stream.
# POSIX sh (busybox ash).
#
# ‏13/10, על ברזל: מגירה עם SMART תקין וקישור 6.0Gb/s נכשלה אחרי 0.86GB
# ב-`fsync errno=5`. ‏SMART לא ראה את זה, וזיכרון השרת (#874) אינו עוזר
# לדיסק חדש. הכרעת נדב (#1366): לכל דיסק **שכבר נבחר לסבב** — אחרי שער
# ה-SMART, אחרי disk_fits (ובתחנה אחרי שער ה-CA), ולפני apply_gpt —
# כותבים WRITECHECK_MIB בשלושה מקומות (התחלה/אמצע/סוף) ישירות לדיסק
# (‏oflag=direct + ‏conv=fsync), קוראים בחזרה (‏iflag=direct) ומשווים.
#
# זו אינה כתיבה "מחוץ לשיכפול" (ההכרעה של 16/09 ב-failmark.sh): הדיסק
# נבחר, ו-apply_gpt עומד למחוק את אותם מקומות שנייה אחרי. מגירה שלא נבחרה
# לא מגיעה לכאן — הקוראים מעבירים רק את רשימת הסבב (#1275).
#
# שלושה מצבים, כמו שער ה-CA (bootcadb.sh) ושער ה-SMART:
#   ok        — נכתב, נקרא בחזרה, זהה. ממשיכים.
#   failed    — כתיבה/קריאה נכשלה, נקרא משהו אחר, או שלא הסתיים בזמן:
#               היעד נכשל **בשמו, לפני הזרם** (fail_written_target — הראיה
#               של הקרנל נאספת), והשאר ממשיכים.
#   unchecked — הבדיקה עצמה לא יכלה לרוץ: "לא נבדק" בשם, מדווחים וממשיכים.
#               לעולם אינו מקופל ל-ok (עיקרון 5).
#   slow      — נכתב ונקרא נכון, אבל לאט (write_speed_verdict למטה): **אדום**.
# התוצאה נשמרת ליעד (write_check_state/reason/mbps) ויוצאת בדיווח כ-
# ‏`targets[].write_preflight` (interfaces.md סעיף 4).
#
# **המהירות** (נדב 13/10): MB/s של שלוש הכתיבות הישירות, כולל ה-fsync, לפי
# ‏/proc/uptime לפניהן ואחריהן. ⚠️ הרזולוציה 10ms: על 12MiB ב-50 MB/s (‏~250ms,
# הסף) זה ‏~4% — מספיק להכרעה; על דיסק מהיר המספר גס, וזה בסדר כי הוא רחוק
# מהסף. dd של busybox-static (Debian 13) **אינו** מדפיס שורת סיכום עם זמן
# (נמדד 13/10 על ה-Testrunner), ולכן לא ממנו. ‏MB = 10^6 בייט, כמו קצב הגל.
# זמן שלא נקרא או שלא עבר = **לא נמדד** (אין קובץ mbps, ‏`null` בדיווח) —
# לעולם לא 0, ושום תנאי אינו נבנה עליו (עיקרון 5ב).

WRITECHECK_MIB="${WRITECHECK_MIB:-4}"          # MiB in each of the three places
# WAIT_WRITECHECK_S, the ceiling of the whole check per disk, sits in waits.sh with the others.
# The floor: below it a disk holds the whole wave back. Source: Nadav 13/10
# (#1366), "under the wave's pace" -- the wave's ceiling is 900 Mbit/s =
# 112 MB/s (IMAGESRV_MAX_BITRATE, server/app.py), measured 106 MB/s on
# 17/09 (500->146GB round); 50 is under half of it.
WRITECHECK_MIN_MBPS="${WRITECHECK_MIN_MBPS:-50}"

_wc_place() { case "$1" in 1) printf 'התחלה' ;; 2) printf 'אמצע' ;; *) printf 'סוף' ;; esac; }

_wc_why() { # $1 = stderr file, $2 = fallback: dd's own line, not a guess
    _wy=$(grep -m 1 'dd:' "$1" 2>/dev/null | tr -d '\r')
    printf '%s' "${_wy:-$2}"
}

_wc_body() {
    # $1 = disk, $2 = node, $3 = work dir, remaining = offsets in MiB. Prints
    # one line, state|reason. Runs as a background job: write_check bounds it.
    _wb_d=$1; _wb_n=$2; _wb_w=$3; shift 3; _wb_b=$((WRITECHECK_MIB * 1048576))
    _wb_i=0
    for _wb_o in "$@"; do   # a different pattern per place: a disk that folds addresses is caught
        _wb_i=$((_wb_i + 1))
        head -c "$_wb_b" /dev/urandom > "$_wb_w/pat.$_wb_i" 2>/dev/null
        [ "$(wc -c < "$_wb_w/pat.$_wb_i" 2>/dev/null | tr -d ' ')" = "$_wb_b" ] \
            || { echo "unchecked|לא נוצר דפוס לבדיקה (urandom)"; return; }
    done
    target_write_started "$_wb_d" || { echo "unchecked|לא ניתן לשמור את זהות היעד לפני הכתיבה"; return; }
    _wb_i=0; _wb_t0=$(cut -d' ' -f1 "$SYSROOT/proc/uptime" 2>/dev/null)
    for _wb_o in "$@"; do   # all three written before any is read back
        _wb_i=$((_wb_i + 1))
        dd if="$_wb_w/pat.$_wb_i" of="$_wb_n" bs=1048576 seek="$_wb_o" count="$WRITECHECK_MIB" \
            oflag=direct conv=fsync 2> "$_wb_w/err" \
            || { echo "failed|$(_wc_place $_wb_i) ($_wb_o MiB): $(_wc_why "$_wb_w/err" 'הכתיבה נכשלה')"; return; }
    done
    # The clock after the third write: both read, and time passed, or no number.
    awk -v a="$_wb_t0" -v b="$(cut -d' ' -f1 "$SYSROOT/proc/uptime" 2>/dev/null)" -v n=$((_wb_b * $#)) \
        'BEGIN { if (a ~ /^[0-9]+\.[0-9]+$/ && b ~ /^[0-9]+\.[0-9]+$/ && b + 0 > a + 0) printf "%d\n", n / (b - a) / 1000000 }' \
        > "$_wb_w/mbps" 2>/dev/null
    _wb_i=0
    for _wb_o in "$@"; do
        _wb_i=$((_wb_i + 1)); _wb_p="$(_wc_place $_wb_i) ($_wb_o MiB)"
        dd if="$_wb_n" of="$_wb_w/back" bs=1048576 skip="$_wb_o" count="$WRITECHECK_MIB" \
            iflag=direct 2> "$_wb_w/err" \
            || { echo "failed|$_wb_p: הקריאה החוזרת נכשלה: $(_wc_why "$_wb_w/err" 'dd')"; return; }
        # cmp: 0 = same, 1 = different (an answer), anything else = the comparison broke.
        cmp -s "$_wb_w/pat.$_wb_i" "$_wb_w/back"; _wb_rc=$?
        case "$_wb_rc" in
            0) ;;
            1) echo "failed|$_wb_p: מה שנקרא בחזרה שונה ממה שנכתב"; return ;;
            *) echo "unchecked|$_wb_p: ההשוואה לא רצה (cmp rc=$_wb_rc)"; return ;;
        esac
    done
    _wc_alias "$_wb_n" "$_wb_w" "$_wc_mib" || return   # #1805, CLR-02
    echo "ok|נכתבו ונקראו בחזרה ${WRITECHECK_MIB}MiB בהתחלה, באמצע ובסוף, וסמנים בכל חזקה של 2 MiB"
}

_wc_alias() {
    # $1 = node, $2 = work dir, $3 = the disk in MiB. CLR-02 (#1805): a counterfeit
    # disk reports more than it holds and maps an address past its real end back
    # onto one it has -- most drop the high address bits, a power of two. The
    # three places above can all land in what it really has (3 x 32MiB of one
    # backing passes them). So a 4KiB random marker at 0, at every power-of-two
    # MiB below the end and in the last 4KiB: all written, then all read back. A
    # marker that a higher one overwrote is the wrap itself. Prints a line, 1 = a verdict.
    _wa_o=0; _wa_m=1
    while [ "$_wa_m" -lt "$3" ]; do _wa_o="$_wa_o $((_wa_m * 256))"; _wa_m=$((_wa_m * 2)); done
    _wa_o="$_wa_o $(($3 * 256 - 1))"; _wa_i=0   # 4KiB blocks
    for _wa_b in $_wa_o; do
        _wa_i=$((_wa_i + 1)); head -c 4096 /dev/urandom > "$2/mk.$_wa_i" 2>/dev/null
        [ "$(wc -c < "$2/mk.$_wa_i" 2>/dev/null | tr -d ' ')" = 4096 ] || { echo "unchecked|לא נוצר סמן לבדיקה (urandom)"; return 1; }
        dd if="$2/mk.$_wa_i" of="$1" bs=4096 seek="$_wa_b" count=1 oflag=direct conv=fsync 2> "$2/err" \
            || { echo "failed|סמן ב-$((_wa_b / 256)) MiB: $(_wc_why "$2/err" 'הכתיבה נכשלה')"; return 1; }
    done
    _wa_i=0
    for _wa_b in $_wa_o; do
        _wa_i=$((_wa_i + 1))
        dd if="$1" of="$2/back" bs=4096 skip="$_wa_b" count=1 iflag=direct 2> "$2/err" \
            || { echo "failed|סמן ב-$((_wa_b / 256)) MiB: הקריאה החוזרת נכשלה: $(_wc_why "$2/err" 'dd')"; return 1; }
        cmp -s "$2/mk.$_wa_i" "$2/back"; _wa_rc=$?
        case "$_wa_rc" in
            0) ;;
            1) echo "failed|סמן ב-$((_wa_b / 256)) MiB נקרא בחזרה שונה — הדיסק מחזיר תוכן של מקום אחר (קיבולת מזויפת?)"; return 1 ;;
            *) echo "unchecked|סמן ב-$((_wa_b / 256)) MiB: ההשוואה לא רצה (cmp rc=$_wa_rc)"; return 1 ;;
        esac
    done
}

_wc_finish() { # disk, state, reason
    _wf_r=$3; [ "$2" = unchecked ] && _wf_r="לא נבדק: $3"
    log "$1: write check: $2 -- $_wf_r"
    printf '%s' "$2" > "$RUN_DIR/targets/$1/write_check_state"
    printf '%s' "$_wf_r" > "$RUN_DIR/targets/$1/write_check_reason"
    [ "$2" = failed ] || return 0
    fail_written_target "$1" "הדיסק לא מקבל כתיבה — $3"
    return 1
}

write_check() {
    # $1 = disk name (sdb), **one of the round's disks**. 0 = write it
    # (ok/unchecked), 1 = failed: the target is failed by name, before the stream.
    _wc_n="$DEVROOT/$1"; mkdir -p "$RUN_DIR/targets/$1"
    [ -b "$_wc_n" ] || { _wc_finish "$1" unchecked "$_wc_n אינו התקן בלוק"; return; }
    # The flags are part of the check: a dd without them is "not checked", never "failed".
    dd --help 2>&1 | grep -q 'direct' \
        || { _wc_finish "$1" unchecked "ה-dd כאן אינו מכיר direct"; return; }
    _wc_mib=$(blockdev --getsize64 "$_wc_n" 2>> "$LOG_FILE")
    case "$_wc_mib" in ''|*[!0-9]*) _wc_finish "$1" unchecked "גודל הדיסק לא נקרא"; return ;; esac
    _wc_mib=$((_wc_mib / 1048576))
    [ "$_wc_mib" -ge $((WRITECHECK_MIB * 3)) ] \
        || { _wc_finish "$1" unchecked "הדיסק קטן מ-$((WRITECHECK_MIB * 3))MiB"; return; }
    _wc_w="$RUN_DIR/writecheck/$1"; rm -rf "$_wc_w"; mkdir -p "$_wc_w"
    ( _wc_body "$1" "$_wc_n" "$_wc_w" 0 $((_wc_mib / 2)) $((_wc_mib - WRITECHECK_MIB)) > "$_wc_w/result" ) &
    if ! wait_pid "$!" "$WAIT_WRITECHECK_S" "בדיקת הכתיבה של $1"; then
        rm -rf "$_wc_w"
        _wc_finish "$1" failed "לא סיים לכתוב ולקרוא בחזרה תוך $WAIT_WRITECHECK_S שניות"; return
    fi
    _wc_r=$(head -n 1 "$_wc_w/result" 2>/dev/null)
    _wc_s=$(cat "$_wc_w/mbps" 2>/dev/null); rm -rf "$_wc_w"
    rm -f "$RUN_DIR/targets/$1/write_check_mbps"
    case "$_wc_r" in ok\|*) case "$_wc_s" in ''|*[!0-9]*) ;; *)   # measured, or no file
        printf '%s' "$_wc_s" > "$RUN_DIR/targets/$1/write_check_mbps" ;; esac ;; esac
    case "$_wc_r" in
        ok\|*|failed\|*|unchecked\|*) _wc_finish "$1" "${_wc_r%%|*}" "${_wc_r#*|}" ;;
        *) _wc_finish "$1" unchecked "הבדיקה לא החזירה תוצאה" ;;
    esac
}

_wc_mbps() { # $1 = disk: the measured MB/s of a disk that passed, or nothing
    [ "$(cat "$RUN_DIR/targets/$1/state" 2>/dev/null)" = failed ] && return
    [ "$(cat "$RUN_DIR/targets/$1/write_check_state" 2>/dev/null)" = ok ] || return
    _wm_v=$(cat "$RUN_DIR/targets/$1/write_check_mbps" 2>/dev/null)
    case "$_wm_v" in ''|*[!0-9]*) ;; *) printf '%s' "$_wm_v" ;; esac   # a number, or nothing
}

write_speed_verdict() {
    # $@ = the round's disks on this machine. A disk that wrote below
    # WRITECHECK_MIN_MBPS, or below half the median of the **other** measured
    # disks here (two at least: a median of one is one disk's mood), is
    # **red** (Nadav 13/10): it holds the whole wave back, and red skips when
    # nobody answers -- failed by name, before its table. Not measured = no
    # verdict at all (5b). 1 = a disk was failed here.
    _sv_rc=0
    for _sv_d in "$@"; do
        _sv_m=$(_wc_mbps "$_sv_d"); [ -n "$_sv_m" ] || continue
        _sv_med=$(for _sv_o in "$@"; do [ "$_sv_o" = "$_sv_d" ] || _wc_mbps "$_sv_o"; echo; done \
            | sort -n | awk '/^[0-9]+$/ { v[++n] = $1 } END { if (n >= 2) print (n % 2) ? v[(n + 1) / 2] : int((v[n / 2] + v[n / 2 + 1]) / 2) }')
        if [ "$_sv_m" -lt "$WRITECHECK_MIN_MBPS" ]; then
            _sv_why="איטי — $_sv_m MB/s, מתחת לסף $WRITECHECK_MIN_MBPS MB/s"
        elif [ -n "$_sv_med" ] && [ $((_sv_m * 2)) -lt "$_sv_med" ]; then
            _sv_why="איטי — $_sv_m MB/s, פחות מחצי מ-$_sv_med MB/s של שאר המגירות"
        else
            continue
        fi
        log "$_sv_d: write check: slow -- $_sv_why"
        printf 'slow' > "$RUN_DIR/targets/$_sv_d/write_check_state"
        printf '%s' "$_sv_why" > "$RUN_DIR/targets/$_sv_d/write_check_reason"
        target_set "$_sv_d" failed "$_sv_why"; _sv_rc=1
    done
    return "$_sv_rc"
}

write_check_round() {
    # $1 = manifest, $2.. = the round's drawers (#1275: only those). Check 2.7
    # first (disk_fits never touches the disk), then the write check, then
    # the speed over all of them -- every verdict before any table is written.
    _wr_m=$1; shift
    for _wr_d in "$@"; do
        target_set "$_wr_d" writing
        disk_fits "$_wr_d" "$_wr_m" && { [ ! -s "$RUN_DIR/targets/$_wr_d/raid_wipe" ] || raid_wipe "$_wr_d"; } && write_check "$_wr_d"   # #1808: the old RAID metadata the operator let go, before the first test write
    done
    write_speed_verdict "$@" || :
}
