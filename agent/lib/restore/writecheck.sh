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
# 17/09 (500->146GB round); 50 is under half of it. #2338: the console's
# setting (writecheck_min_mbps, 10-500) comes in the manifest; this is the
# floor when it does not (writecheck_floor below).
WRITECHECK_MIN_MBPS="${WRITECHECK_MIN_MBPS:-50}"

_wc_place() { case "$1" in 1) printf 'התחלה' ;; 2) printf 'אמצע' ;; *) printf 'סוף' ;; esac; }

_wc_why() { # $1 = stderr file, $2 = fallback: dd's own line, not a guess
    _wy=$(grep -m 1 'dd:' "$1" 2>/dev/null | tr -d '\r')
    printf '%s' "${_wy:-$2}"
}

_wc_pats() {
    # $@ = the offsets. A different random pattern per place (_wb_w/pat.N); a disk that folds addresses is caught.
    _wb_i=0
    for _wb_o in "$@"; do
        _wb_i=$((_wb_i + 1))
        head -c "$_wb_b" /dev/urandom > "$_wb_w/pat.$_wb_i" 2>/dev/null
        [ "$(wc -c < "$_wb_w/pat.$_wb_i" 2>/dev/null | tr -d ' ')" = "$_wb_b" ]             || { echo "unchecked|לא נוצר דפוס לבדיקה (urandom)"; return 1; }
    done
}

_wc_body() {
    # $1 = disk, $2 = node, $3 = work dir, remaining = offsets in MiB. Prints
    # one line, state|reason. Runs as a background job: write_check bounds it.
    _wb_d=$1; _wb_n=$2; _wb_w=$3; shift 3; _wb_b=$((WRITECHECK_MIB * 1048576))
    _wc_pats "$@" || return
    assert_target "$_wb_d" || { echo "failed|$(cat "$RUN_DIR/targets/$_wb_d/error" 2>/dev/null)"; return; }   # #2434: not the disk chosen = failed, never "unchecked"
    target_write_started "$_wb_d" || { echo "unchecked|לא ניתן לשמור את זהות היעד לפני הכתיבה"; return; }
    _wc_io "$@"
}

_wc_io() {
    # The writes, the clock and the read-back (#3268: idlecheck.sh runs it with its own guards and a copy). Prints the line.
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
        _wa_i=$((_wa_i + 1)); [ -s "$2/mk.$_wa_i" ] || head -c 4096 /dev/urandom > "$2/mk.$_wa_i" 2>/dev/null   # #3268: idlecheck made them before the copy left
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
    # $1 = disk name (sdb), **one of the round's disks**, $2 = the manifest. 0 = write it
    # (ok/unchecked), 1 = failed: the target is failed by name, before the stream.
    hdd_target_gate "${2:-}" "$1" || return 1   # X16: before the first test write
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

_wc_kind() { # $1 = disk: its kind in Hebrew by /sys rotational (0 = SSD, 1 = HDD), or ? -- not read, no kind to compare to
    case "$(cat "$SYSROOT/sys/block/$1/queue/rotational" 2>/dev/null)" in
        0) printf 'מוצק' ;; 1) printf 'מכני' ;; *) printf '?' ;; esac
}

writecheck_floor() {
    # $1 = the manifest the server served. #2338: sets WC_FLOOR to its
    # writecheck_min_mbps (the console's setting, 10-500). Missing or bad (an old
    # server, a manifest that did not read) = WRITECHECK_MIN_MBPS and a log line:
    # never 0 and never no floor.
    _wf_v=$(jq -c '.writecheck_min_mbps' "$1" 2>/dev/null)
    case "$_wf_v" in [1-9][0-9]|[1-9][0-9][0-9]) if [ "$_wf_v" -le 500 ]; then
        WC_FLOOR=$_wf_v; log "write check floor: $WC_FLOOR MB/s (writecheck_min_mbps)"; return 0; fi ;; esac
    WC_FLOOR=$WRITECHECK_MIN_MBPS
    log "write check floor: writecheck_min_mbps ${_wf_v:-unread} in the manifest is not 10-500 -- $WC_FLOOR MB/s"
}

_wc_slow_why() {
    # $1 = disk, $2 = every measured disk ("dev mbps kind" per line), $3 = the floor, $4 = the floor of an HDD with the switch on (X16, empty = none), $5 = its note. Prints why it is slow, or nothing (#3268: the waiting screen asks too).
    _sw_l=$(printf '%s' "$2" | awk -v d="$1" '$1 == d'); [ -n "$_sw_l" ] || return 0
    _sw_m=$(echo "$_sw_l" | cut -d' ' -f2); _sw_k=$(echo "$_sw_l" | cut -d' ' -f3)
    _sw_med=$(printf '%s' "$2" | awk -v d="$1" -v k="$_sw_k" 'k != "?" && $1 != d && $3 == k { print $2 }'         | sort -n | awk '/^[0-9]+$/ { v[++n] = $1 } END { if (n >= 2) print (n % 2) ? v[(n + 1) / 2] : int((v[n / 2] + v[n / 2 + 1]) / 2) }')
    _sw_fl=$3
    case "${WC_HDD:-}:$_sw_k" in   # #3268 (נדב 14/11): "תמיכה ב-HDD". off = a mechanical disk is a source only: a target it is slow, measured or not
        off:מכני) printf 'איטי — דיסק מכני (HDD), נמדד %s MB/s: נתמך כמקור בלבד (תמיכה ב-HDD כבויה)' "$_sw_m"; return ;;
        on:מכני) _sw_fl=${4:-0} ;;   # not the floor an SSD meets: X16's learned expectation ($4), or none; then against the other mechanical disks (below)
    esac
    if [ "$_sw_m" -lt "$_sw_fl" ]; then printf 'איטי — %s MB/s, מתחת לסף %s MB/s%s' "$_sw_m" "$_sw_fl" "${5:-}"
    elif [ -n "$_sw_med" ] && [ $((_sw_m * 2)) -lt "$_sw_med" ]; then
        printf 'איטי — %s MB/s, פחות מחצי מ-%s MB/s של שאר המגירות מאותו סוג (%s)' "$_sw_m" "$_sw_med" "$_sw_k"
    fi
}

writecheck_hdd_policy() {
    # $1 = the hello answer (.hdd_support, #3268). WC_HDD = on | off, or empty = an older server that does not say: as before.
    case "$(jq -c '.hdd_support' "$1" 2>/dev/null)" in true) WC_HDD=on ;; false) WC_HDD=off ;; *) WC_HDD="" ;; esac
}

# X16 (Nadav 14/11): the HDD **target** policy; the switch is #3268's (hdd_support in the hello answer, WC_HDD).
# Off (the default, also when unread -- logged): an HDD target is refused before any write, red, by name. On: it meets
# the expectation the server learned for its serial and model (hdd_floors in the manifest), not WC_FLOOR; without one,
# hdd_floor_mbps -- an estimate, logged. rotational 0 -- as before; not read = not measured, logged, as a solid disk.
HDD_ESTIMATE_MBPS="${HDD_ESTIMATE_MBPS:-20}"   # an estimate, NOT measured: a manifest without one
_hp_num() { case "$1" in ''|*[!0-9]*|0*) return 1 ;; esac; }   # positive, no leading 0 (ash: octal)

hdd_target_policy() { # $1 = manifest, $2 = disk. Sets HDD_FLOOR, HDD_NOTE, HDD_REASON; 1 = refused
    HDD_FLOOR=${WC_FLOOR:-$WRITECHECK_MIN_MBPS}; HDD_NOTE=''; HDD_REASON='HDD — תמיכה ב-HDD כבויה בהגדרות'
    _hp_v=$(cat "$SYSROOT/sys/block/$2/queue/rotational" 2>/dev/null)
    case "$_hp_v" in 0) return 0 ;; 1) ;;
        *) log "$2: rotational not read (${_hp_v:-no file}) -- not measured; the non-rotational policy"; return 0 ;; esac
    writecheck_hdd_policy "$RUN_DIR/response.json"
    case "$WC_HDD" in on) ;; off) return 1 ;;
        *) log "$2: hdd_support unread in the hello answer -- off, the default"; return 1 ;; esac
    _hp_s=$(disk_serial "$2"); _hp_m=$(trim "$(cat "$SYSROOT/sys/block/$2/device/model" 2>/dev/null)")
    _hp_v=$(jq -r --arg s "$_hp_s" --arg m "$_hp_m" '.hdd_floors[$s][$m] // empty' "$1" 2>/dev/null)
    if _hp_num "$_hp_v"; then HDD_FLOOR=$_hp_v; HDD_NOTE=' (הציפייה שנלמדה ל-HDD הזה)'; return 0; fi
    _hp_v=$(jq -r '.hdd_floor_mbps // empty' "$1" 2>/dev/null); _hp_num "$_hp_v" || _hp_v=$HDD_ESTIMATE_MBPS
    HDD_FLOOR=$_hp_v; HDD_NOTE=' (הערכת פתיחה ל-HDD, לא נמדדה)'
    log "$2: no measured waves for HDD ${_hp_s:-?}/${_hp_m:-?} -- the starting estimate $HDD_FLOOR MB/s"
}

hdd_target_gate() { # $1 = manifest, $2 = disk. A refused target fails here, red, by its reason
    hdd_target_policy "$1" "$2" && return 0
    mkdir -p "$RUN_DIR/targets/$2"; printf 'slow' > "$RUN_DIR/targets/$2/write_check_state"
    printf '%s' "$HDD_REASON" > "$RUN_DIR/targets/$2/write_check_reason"
    log "$2: write check: slow -- $HDD_REASON"; target_set "$2" failed "$HDD_REASON"; return 1
}

write_speed_verdict() {
    # $1 = the manifest (its floor: writecheck_floor), $2.. = the round's disks
    # on this machine. A disk that wrote below the floor, or below half the
    # median of the **other** measured
    # disks here **of its own kind** (two at least: a median of one is one
    # disk's mood), is **red** (Nadav 13/10): it holds the whole wave back, and
    # red skips when nobody answers -- failed by name, before its table. Not
    # measured = no verdict at all (5b). 1 = a disk was failed here.
    # #2037 (decided 03/11): SSD against SSD, HDD against HDD -- an SSD beside
    # two HDDs pulled their "median" up and failed a 57 MB/s HDD. A disk alone
    # of its kind (or whose kind was not read) meets the fixed floor only. And
    # every number is taken **before** any disk is failed: failing one here
    # used to drop it from the next one's comparison, so the order of the
    # drawers decided (57 failed and 54 passed; the next round, the reverse).
    _sv_manifest=$1; writecheck_floor "$1"; writecheck_hdd_policy "$RUN_DIR/response.json"; shift
    _sv_rc=0; _sv_all=""
    for _sv_d in "$@"; do
        _sv_m=$(_wc_mbps "$_sv_d"); [ -n "$_sv_m" ] || continue
        _sv_all="$_sv_all$_sv_d $_sv_m $(_wc_kind "$_sv_d")
"
    done
    for _sv_d in "$@"; do
        [ "$(cat "$RUN_DIR/targets/$_sv_d/state" 2>/dev/null)" = failed ] && continue   # X16: failed before, by its own reason
        hdd_target_gate "$_sv_manifest" "$_sv_d" || { _sv_rc=1; continue; }   # X16: sets HDD_FLOOR and HDD_NOTE
        _sv_why=$(_wc_slow_why "$_sv_d" "$_sv_all" "$WC_FLOOR" "${HDD_NOTE:+$HDD_FLOOR}" "$HDD_NOTE"); [ -n "$_sv_why" ] || continue
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
        hdd_target_gate "$_wr_m" "$_wr_d" || continue   # X16: before the idle check is taken and before raid_wipe, the first write here
        # #1808: the old RAID metadata the operator let go, before the first test write; #2412: the whole plan before both
        _wr_a=1; command -v idle_wc_adopt > /dev/null 2>&1 && { idle_wc_adopt "$_wr_d"; _wr_a=$?; }   # #3268: the waiting-screen check of this disk; 0 = taken, 1 = none (write it now), 2 = failed by name
        disk_fits "$_wr_d" "$_wr_m" && plan_gate "$_wr_d" "$_wr_m" && { [ ! -s "$RUN_DIR/targets/$_wr_d/raid_wipe" ] || raid_wipe "$_wr_d"; } && { [ "$_wr_a" = 0 ] || { [ "$_wr_a" = 1 ] && write_check "$_wr_d" "$_wr_m"; }; }
    done
    write_speed_verdict "$_wr_m" "$@" || :
}
