# opsay.sh -- the operator's sentence for a drawer's failure (#2987).
# POSIX sh (busybox ash).
#
# `targets/<dev>/error` keeps the full diagnosis: the kernel's words, the
# partition, the tool's rc, /dev names, the SATA power policy. It goes on the
# wire as `error` (the console's hover, the journal, server/disks/ata_cause.py,
# which classifies by its words) and to the log. What the operator reads -- the
# cloner screen, the console's card -- is a short sentence made from it here,
# once, so the three screens never say it three ways (interfaces.md section 4,
# `error_short`; the screen record drawer= field 6 of a failed drawer).
#
# Nadav 12/11 (#2987, mockup 06-bay-states): no /dev, host, RAID, policy or
# algorithm names; what happened, how far it got, and what to do.

OP_CHECK="בדקו דיסק, כבל או מגירה"

op_pct() {
    # $1 = dev. " (74%)": how far its write got; nothing when it cannot be read --
    # a percent that was not measured is not said (principle 5).
    _op_t=$(cat "$RUN_DIR/targets/$1/total" 2>/dev/null); _op_b=$(target_bytes "$1" 2>/dev/null)
    case "$_op_t:$_op_b" in *[!0-9:]*|:*|*:) return 0 ;; esac
    [ "$_op_t" -gt 0 ] && [ "$_op_b" -le "$_op_t" ] || return 0
    printf ' (%s%%)' $((_op_b * 100 / _op_t))
}

op_say() {
    # $1 = dev, $2 = the full reason, $3 = `lc` on the local clone (no next wave).
    # Prints the operator's sentence. The families first; anything else is the
    # reason with its diagnostics cut (parentheses with Latin, /dev names, "--").
    _os_retry="כתבו אותו שוב בגל הבא."; [ "${3:-}" != lc ] || _os_retry="כתבו אותו שוב."
    _os_again="ו$_os_retry"
    case "$2" in
        *"הכונן נעלם מהמערכת"*)
            echo "הדיסק נעלם באמצע הכתיבה$(op_pct "$1"). בדקו שהמגירה סגורה והכבל מחובר, $_os_again" ;;
        *"הקרנל סימן את הכונן offline"*)
            echo "הדיסק הפסיק להגיב באמצע הכתיבה$(op_pct "$1"). $OP_CHECK, $_os_again" ;;
        *"הקרנל דחה כתיבה לכונן"*)
            echo "הדיסק דחה כתיבה באמצע הכתיבה$(op_pct "$1"). $OP_CHECK, $_os_again" ;;
        *"הוצאה מהזרם"*|*"fanout: stalled"*)
            echo "הדיסק לא עמד בקצב והוצא מהסבב$(op_pct "$1"). $OP_CHECK, $_os_again" ;;
        *"פג הזמן — המגירה לא סיימה לכתוב"*|*"פג הזמן — היעד הפסיק לכתוב"*)
            echo "הדיסק הפסיק לכתוב$(op_pct "$1"). $OP_CHECK, $_os_again" ;;
        *"הזרם הגיע ריק או חלקי"*)
            echo "הנתונים מהשרת לא הגיעו שלמים$(op_pct "$1"). $_os_again" ;;
        *"לא הצלחנו לבדוק אם"*"RAID"*"קריאת סוף הדיסק נכשלה"*)
            echo "לא ניתן לקרוא את סוף הדיסק — לא נכתב. $OP_CHECK." ;;
        *"לא הצלחנו לבדוק אם"*"RAID"*)
            echo "לא ניתן לבדוק את הדיסק לפני הכתיבה — לא נכתב. $OP_CHECK." ;;
        *"RAID של Intel"*)
            echo "הדיסק מוגדר כחלק ממערך דיסקים של Intel — לא נכתב. העבירו את ה-BIOS ל-AHCI." ;;
        *"חבר במערך RAID"*)
            echo "הדיסק מוגדר כחלק ממערך דיסקים — לא נכתב." ;;
        *"xxh64"*"שונה מהמקור"*)
            echo "מה שנכתב אינו זהה למקור — $_os_retry" ;;
        *"xxh64"*"לא חושב"*)
            echo "לא נבדק אם מה שנכתב זהה למקור — $_os_retry" ;;
        *"מערכת הקבצים לא:"*"אינו עקבי"*)
            echo "מערכת הקבצים לא הורחבה לכל הדיסק: ה-NTFS במקור אינו עקבי. ב-Windows: chkdsk /f, ואז הרחבת הכונן בניהול הדיסקים." ;;
        *"מערכת הקבצים לא:"*)
            echo "מערכת הקבצים לא הורחבה לכל הדיסק. אפשר להרחיב את הכונן ב-Windows בניהול הדיסקים." ;;
        *) _op_generic "$2" ;;
    esac
}

_op_generic() {
    # $1 = a reason no family above names. Its diagnostics cut (parentheses with
    # Latin, /dev names, "--"); a tool's own English that is still there is not
    # shown -- the Hebrew head before it, if there is one, and "the details are in
    # the log". Busybox sed reads bytes: the Hebrew passes through as it is.
    _og=$(printf '%s\n' "$1" | sed -e 's/ *([^()]*[A-Za-z=][^()]*)//g' -e 's/ -- / — /g' \
        -e 's/partition \([0-9][0-9]*\)/מחיצה \1/g' -e 's#/dev/[A-Za-z0-9]*/*#הדיסק#g' \
        -e 's/ה-RAID/מערך הדיסקים/g' -e 's/ RAID//g' -e 's/פרטי ntfsresize/הפרטים/g' -e 's/ntfsresize/הכלי/g' \
        -e 's/ה-xxh64/הבדיקה/g' -e 's/xxh64/בדיקה/g' -e 's/  */ /g' -e 's/^ //' -e 's/ $//')
    _op_latin "$_og" || { printf '%s\n' "$_og"; return 0; }
    _og_h=${_og%%: *}
    if [ "$_og_h" != "$_og" ] && ! _op_latin "$_og_h"; then printf '%s: הכתיבה נכשלה — הפרטים ביומן\n' "$_og_h"
    else echo "הכתיבה נכשלה — הפרטים ביומן"; fi
}

_op_latin() {
    # 0 = $1 still has a Latin word that is not a name the operator knows.
    printf '%s' "$1" | sed -E 's/(Windows|Linux|BIOS|UEFI|AHCI|NTFS|SMART|TRIM|Intel|SATA|NVMe|USB|chkdsk|GPT|GB|MB|TB|Secure Boot)//g' \
        | grep -q '[A-Za-z][A-Za-z]'
}

target_say_json() {
    # $1 = dev. ,"error_short":"…" beside `error` (build_progress); nothing without an error.
    [ -s "$RUN_DIR/targets/$1/error" ] || return 0
    _ts_s=$(op_say "$1" "$(cat "$RUN_DIR/targets/$1/error")")
    [ -n "$_ts_s" ] || return 0
    printf ',"error_short":"%s"' "$(json_escape "$_ts_s")"
}
