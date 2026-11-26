# tools_rescue.sh -- #2804: copy a failing disk to an image file at the
# destination (tooldest.sh): ddrescue-image, safecopy. POSIX sh.
#
# The image, and what the tool keeps between runs (ddrescue's mapfile,
# safecopy's stage files), live in one folder per disk serial --
# rescue/<serial>/ -- so running it again on the same disk continues, it does
# not start over. source.txt there holds the disk's size and model: a folder
# whose size differs is never continued (that would be another disk's map).
# A disk without a serial gets a fresh time-stamped folder and no resume.
#
# Success is read from what the tool left at the destination: ddrescue's
# mapfile must cover the whole disk, with the rescued bytes counted from it;
# safecopy's image must be the source's size. The tool's own exit code alone
# is never the verdict (principle 5).

tools_rescue_list() {
    cat <<'EOF'
ddrescue-image|disk|שחזור דיסק גוסס|rw|disk:הדיסק הגוסס|מעתיק דיסק עם שגיאות לקובץ-מפה וניסיונות חוזרים, לפני שהוא מת לגמרי.
safecopy|disk|שחזור ממדיה פגומה|rw|disk:הדיסק הפגום|חלופה ל-ddrescue כשדיסק או USB נחתכים באמצע קריאה.
EOF
}

_trs_means() { printf '\nמה זה אומר: %s\n' "$1"; }

_trs_size() { wc -c 2>/dev/null < "$1" | tr -d ' '; }

_trs_prepare() {
    # $1 = image file name. -> TD_DIR and TRS_IMG; rc 2 on any refusal. The
    # need is the source size less what an earlier run already wrote.
    _trsm=$(tr -d '\r\n|' 2>/dev/null < "${SYSROOT:-}/sys/block/$TD_SRC_DISK/device/model")
    if _trsr=$(tooldest_serial "$TD_SRC_DISK"); then _trsd="rescue/$_trsr"
    else _trsd="rescue/no-serial-$(tooldest_stamp)"; _trsr=""; fi
    tooldest_root || { printf 'סירוב: %s. הכלי לא הורץ ושום דבר לא נשמר.\n' "$TD_WHY"; return 2; }
    _trsi="$TD_ROOT/$_trsd/$1"; _trsh=0
    if [ -f "$_trsi" ]; then
        _trsh=$(du -k "$_trsi" 2>/dev/null | awk '{ print $1 }'); case "$_trsh" in ''|*[!0-9]*) _trsh=0 ;; esac
        [ "$(cat "$TD_ROOT/$_trsd/source.txt" 2>/dev/null)" = "$TD_SRC_BYTES|$_trsm" ] ||
            { printf 'סירוב: בתיקייה %s יש ריצה קודמת של דיסק בגודל או דגם אחר. היא לא תימשך ולא תידרס.\n' "$TD_ROOT/$_trsd"; return 2; }
    fi
    _trsn=$(( TD_SRC_BYTES / 1024 - _trsh + 1024 )); [ "$_trsn" -gt 1024 ] || _trsn=1024
    tooldest_prepare "$_trsd" "$_trsn" || return 2
    # FAT (the stick's data partition) holds no file of 4 GiB or more.
    if [ "$TD_MFS" = vfat ] && [ "$TD_SRC_BYTES" -ge 4294967296 ]; then
        printf 'סירוב: היעד הוא FAT, וקובץ אימג׳ של %s בתים לא נכנס בו (התקרה 4GB לקובץ).\n' "$TD_SRC_BYTES"; return 2
    fi
    printf '%s|%s\n' "$TD_SRC_BYTES" "$_trsm" > "$TD_DIR/source.txt" || { printf 'סירוב: לא ניתן לכתוב ליעד.\n'; return 2; }
    TRS_IMG="$TD_DIR/$1"
    [ -n "$_trsr" ] || printf 'לדיסק אין מספר סידורי: ריצה חוזרת לא תמשיך את זו.\n'
    [ "$_trsh" = 0 ] || printf 'ממשיך ריצה קודמת של הדיסק הזה (%s KB כבר ביעד).\n' "$_trsh"
}

_trs_map_read() {
    # $1 = ddrescue mapfile -> TRS_OK TRS_BAD TRS_LEFT TRS_ALL (bytes). The
    # first line that is not a comment is the status line, the rest blocks of
    # "pos size status": + rescued, - bad, anything else not finished yet.
    TRS_OK=0; TRS_BAD=0; TRS_LEFT=0; TRS_ALL=0; _trsl=0
    [ -s "$1" ] || return 1
    while read -r _trsa _trsb _trsc _; do
        case "$_trsa" in ''|'#'*) continue ;; esac
        [ "$_trsl" = 1 ] || { _trsl=1; continue; }
        case "$_trsb" in 0x*) ;; *) return 1 ;; esac
        _trsz=$((_trsb)); TRS_ALL=$((TRS_ALL + _trsz))
        case "$_trsc" in +) TRS_OK=$((TRS_OK + _trsz)) ;; -) TRS_BAD=$((TRS_BAD + _trsz)) ;; *) TRS_LEFT=$((TRS_LEFT + _trsz)) ;; esac
    done < "$1"
    [ "$_trsl" = 1 ]
}

_trs_ddrescue() {
    _trsp="$TD_DIR/image.map"
    printf '\n--- ddrescue ---\n'
    ddrescue -r3 "$TD_SRC" "$TRS_IMG" "$_trsp" </dev/null 2>&1; _trsrc=$?
    _trs_map_read "$_trsp" || { _trs_means "המפה של ddrescue (rc $_trsrc) חסרה או לא נקראה — אין ראיה למה שהועתק."; return 2; }
    printf '\nלפי המפה ביעד: הועתקו %s בתים, %s בתים פגומים, %s בתים עוד לא נוסו — מתוך %s.\n' \
        "$TRS_OK" "$TRS_BAD" "$TRS_LEFT" "$TD_SRC_BYTES"
    if [ "$TRS_ALL" != "$TD_SRC_BYTES" ]; then
        _trs_means "המפה מכסה $TRS_ALL בתים ולא את כל הדיסק ($TD_SRC_BYTES). אין לסמוך על האימג׳."; return 2
    elif [ "$_trsrc" -ne 0 ] || [ "$TRS_LEFT" -gt 0 ]; then
        _trs_means "ההעתקה לא הסתיימה (rc $_trsrc). הרצה חוזרת על אותו דיסק תמשיך מאותה נקודה."; return 2
    fi
    _trsz=$(_trs_size "$TRS_IMG"); case "$_trsz" in ''|*[!0-9]*) _trsz=0 ;; esac   # unreadable = empty, never "there"
    if [ "$TRS_OK" -gt 0 ] && [ "$_trsz" -le 0 ]; then
        _trs_means 'המפה אומרת שהועתק מידע, אבל קובץ האימג׳ ביעד ריק. אין לסמוך עליו.'; return 2
    elif [ "$TRS_BAD" -gt 0 ]; then
        _trs_means "הועתק כל מה שאפשר; $TRS_BAD בתים לא נקראו גם אחרי שלושה ניסיונות. הדיסק פגום — לא לכתוב אליו, להחליף."; return 1
    fi
    _trs_means 'כל הדיסק הועתק לאימג׳ שביעד, בלי בלוק פגום. המקור לא נכתב.'
}

_trs_safecopy() {
    # The three presets of safecopy(1), each continuing from the one before
    # by its stage file; a stage counts as done only by its .done marker
    # (exit 0 or 1 -- 1 = some blocks were not recovered, which the next
    # stage retries). 2 = aborted: stop, a rerun picks it up.
    _trsrc=0
    for _trss in 1 2 3; do
        [ ! -f "$TD_DIR/stage$_trss.done" ] || continue
        printf '\n--- safecopy --stage%s ---\n' "$_trss"
        ( cd "$TD_DIR" && safecopy "--stage$_trss" "$TD_SRC" "$TRS_IMG" </dev/null ) 2>&1; _trsrc=$?
        case "$_trsrc" in 0|1) : > "$TD_DIR/stage$_trss.done" ;; *) break ;; esac
    done
    _trsz=$(_trs_size "$TRS_IMG"); case "$_trsz" in ''|*[!0-9]*) _trsz=-1 ;; esac
    _trsb=$(grep -c . "$TD_DIR/stage3.badblocks" 2>/dev/null); case "$_trsb" in ''|*[!0-9]*) _trsb=-1 ;; esac
    printf '\nביעד: אימג׳ של %s בתים מתוך %s; בלוקים שלא נקראו בשלב האחרון: %s.\n' "$_trsz" "$TD_SRC_BYTES" "$_trsb"
    if [ ! -f "$TD_DIR/stage3.done" ]; then
        _trs_means "safecopy נעצר באמצע (rc $_trsrc). הרצה חוזרת על אותו דיסק תמשיך מהשלב שנעצר."; return 2
    elif [ "$_trsz" != "$TD_SRC_BYTES" ] || [ "$_trsb" -lt 0 ]; then
        _trs_means 'האימג׳ ביעד אינו בגודל הדיסק, או שרשימת הבלוקים לא נקראה. אין לסמוך עליו.'; return 2
    elif [ "$_trsb" -gt 0 ]; then
        _trs_means "הועתק כל מה שאפשר; $_trsb בלוקים לא נקראו גם בשלב השלישי ומסומנים BaDbLoCk באימג׳. המדיה פגומה — להחליף."; return 1
    fi
    _trs_means 'כל המדיה הועתקה לאימג׳ שביעד, בלי בלוק פגום. המקור לא נכתב.'
}

tools_rescue_run() {
    _id=${1:-}; _arg=${2:-}
    case "$_id" in
        ddrescue-image) _trsbin=ddrescue; _trsf=image.img ;;
        safecopy) _trsbin=safecopy; _trsf=safecopy.img ;;
        *) return 4 ;;
    esac
    {
        command -v "$_trsbin" >/dev/null 2>&1 || { printf 'לא נבדק: הכלי %s חסר.\n' "$_trsbin"; _trs_means 'הכלי לא הורץ; אין כאן תוצאה.'; return 2; }
        tooldest_source "$_arg" disk || { _trs_means 'הכלי לא הורץ; לא נקרא ולא נשמר דבר.'; return 2; }
        _trs_prepare "$_trsf" || { _trs_means 'הכלי לא הורץ; לא נקרא ולא נשמר דבר.'; return 2; }
        printf 'מקור: %s · %s בתים. הכלי רק קורא ממנו.\n' "$TD_SRC" "$TD_SRC_BYTES"
        case "$_id" in ddrescue-image) _trs_ddrescue ;; safecopy) _trs_safecopy ;; esac
    } > "${RUN_DIR:?}/tool-$_id.out" 2>&1
}
