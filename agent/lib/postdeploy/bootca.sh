# bootca.sh -- מי חתם על מטעני האתחול שעל ה-ESP של האימג' (‏#85).
# POSIX sh (busybox ash). No bashisms.
#
# ‏Secure Boot אינו בודק את האימג'; הוא בודק את החתימה על קובץ ה-EFI שהוא
# עומד להריץ, מול ה-`db` של הקושחה. השאלה "האם המכונה הזאת תוכל לעלות
# מהאימג' הזה" ידועה **משני הצדדים לפני שנוגעים בדיסק** — וכל עוד לא
# שאלנו אותה, התשובה מגיעה אחרי שבע דקות שחזור וכונן שנמחק, כמסך
# ‏`Secure Boot Violation` (הלנובו, ‏#61).
#
# כאן נגזר רק **הצד של האימג'**: אילו רשויות אישורים חתומות על מטעני
# האתחול שלו. הצד השני (מה ה-`db` של המכונה מכיל) והשער עצמו אינם כאן —
# הם החלטה של הבעלים, לא של הקליטה.
#
# ‏`ESPROOT` הוא תפר בדיקה, כמו `SYSROOT`/`DEVROOT` ב-sysinfo.sh: כשהוא
# מוגדר הוא **עץ ESP שכבר עגון**, ואיש אינו מעגן ואינו מנתק.
ESPROOT="${ESPROOT:-}"

# אותם נתיבים שהתפריט מנסה לשרשר אליהם (`LOCAL_BOOT_PATHS` ב-boot/grub_menu.py).
# רשימה אחת ולא `if` ל-Windows: מה שהמכונה תריץ הוא מה שצריך להיות חתום,
# ואין סיבה שקובץ החתימה של shim יהיה פחות מעניין מזה של bootmgfw.
BOOTCA_PATHS="/EFI/Microsoft/Boot/bootmgfw.efi
/EFI/ubuntu/shimx64.efi
/EFI/ubuntu/grubx64.efi
/EFI/debian/shimx64.efi
/EFI/debian/grubx64.efi
/EFI/fedora/shimx64.efi
/EFI/fedora/grubx64.efi
/EFI/centos/shimx64.efi
/EFI/rocky/shimx64.efi
/EFI/almalinux/shimx64.efi
/EFI/opensuse/shim.efi
/EFI/BOOT/bootx64.efi"

#: הסיבה שבגללה לא הצלחנו לגזור. ריק = הצלחנו. נקרא רק אחרי boot_ca_probe.
BOOTCA_ERROR=""

_pe_num() {
    # $1 = קובץ, $2 = היסט, $3 = 2 או 4 בייטים. מספר little-endian אחד.
    dd if="$1" bs=1 skip="$2" count="$3" 2>/dev/null \
        | od -An -tu"$3" | tr -d ' \n'
}

_pe_is_num() {
    case "$1" in "" | *[!0-9]*) return 1 ;; esac
    return 0
}

_pe_cert_table() {
    # $1 = קובץ PE. מדפיס "היסט אורך" של טבלת התעודות (‏Authenticode), או
    # יוצא 1 כשזה אינו PE שאנחנו יודעים לקרוא. **ההיסט הזה הוא היסט בקובץ
    # ולא RVA** — ערך 4 בטבלת הספריות הוא היחיד שכך, וזו הסיבה שאפשר
    # לקרוא אותו בלי למפות סקציות.
    _f="$1"
    _lfa=$(_pe_num "$_f" 60 4)
    _pe_is_num "$_lfa" || return 1
    # חסם שפיות: קובץ שאינו PE נותן כאן מספר אקראי, ו-dd עם skip ענק
    # קורא בייט-בייט עד סוף הדיסק במקום לומר "זה לא PE".
    [ "$_lfa" -gt 0 ] && [ "$_lfa" -lt 1048576 ] || return 1
    _sig=$(dd if="$_f" bs=1 skip="$_lfa" count=4 2>/dev/null | od -An -tx1 | tr -d ' \n')
    [ "$_sig" = "50450000" ] || return 1
    _magic=$(_pe_num "$_f" $((_lfa + 24)) 2)
    case "$_magic" in
        267) _dirs=$((_lfa + 24 + 96)) ;;   # 0x10b — PE32
        523) _dirs=$((_lfa + 24 + 112)) ;;  # 0x20b — PE32+
        *) return 1 ;;
    esac
    # ‏NumberOfRvaAndSizes יושב מיד לפני הטבלה. ערך 4 (טבלת התעודות) הוא
    # החמישי, ולכן קובץ שמצהיר על פחות מחמש ספריות פשוט אינו נושא אותה.
    _n=$(_pe_num "$_f" $((_dirs - 4)) 4)
    _pe_is_num "$_n" || return 1
    [ "$_n" -ge 5 ] || return 1
    _off=$(_pe_num "$_f" $((_dirs + 32)) 4)
    _len=$(_pe_num "$_f" $((_dirs + 36)) 4)
    _pe_is_num "$_off" || return 1
    _pe_is_num "$_len" || return 1
    echo "$_off $_len"
}

_leaf_issuer() {
    # פלט `openssl pkcs7 -print_certs` ב-stdin, ושם ה-CA ב-stdout.
    #
    # ה-CA שצריך לשבת ב-`db` הוא **המנפיק של תעודת העלה**, לא של הראשונה
    # בשקית ולא של השורש: הקושחה מחזיקה את תעודת הביניים עצמה (‏"Microsoft
    # Corporation UEFI CA 2011"), והעלה הוא זה שנחתם בה. העלה מזוהה בלי
    # להסתמך על סדר: הוא התעודה שה-subject שלה אינו מנפיק של אף תעודה
    # אחרת בשקית. שקית שאין בה עלה יחיד אינה מודפסת — "לא הצלחנו לקרוא"
    # ולא ניחוש (עיקרון 5).
    awk '
        function cn(s,   i) {
            i = index(s, "CN=")
            if (i == 0) return ""
            s = substr(s, i + 3)
            sub(/,.*$/, "", s)
            sub(/\/.*$/, "", s)
            return s
        }
        /^subject=/ { subj[++n] = cn($0); next }
        /^issuer=/  { if (n) { iss[n] = cn($0); isca[cn($0)] = 1 } next }
        END {
            for (i = 1; i <= n; i++)
                if (subj[i] != "" && !(subj[i] in isca)) leaf[++L] = iss[i]
            if (L == 1 && leaf[1] != "") print leaf[1]
        }'
}

_pe_signers() {
    # $1 = קובץ PE. שורה לכל חתימה: שם ה-CA שחתם עליה.
    #
    # **קובץ PE נושא יותר מחתימה אחת.** נמדד על shimx64.efi של דביאן 13:
    # טבלה של 19360 בייט ובתוכה שתי רשומות WIN_CERTIFICATE — האחת מתחברת
    # ל-`Microsoft Corporation UEFI CA 2011` והשנייה ל-`Microsoft UEFI CA
    # 2023`. הקושחה מריצה את הקובץ אם **אחת** מהן מתחברת ל-`db`, ולכן
    # רשומה אחת אינה תשובה: היא הייתה מסתירה קובץ שעולה מצוין.
    #
    # יציאה 0 בלי שורות = הקובץ נקרא ואין עליו חתימה כלל. יציאה 1 = לא
    # הצלחנו לקרוא, וזה מצב שלישי.
    _f="$1"
    _tbl=$(_pe_cert_table "$_f") || return 1
    _at=${_tbl% *}
    _end=$((_at + ${_tbl#* }))
    [ "$_end" -gt "$_at" ] || return 0   # אין טבלה: הקובץ אינו חתום
    # שני חסמים, ושניהם על **זמן** ולא על נכונות. הערכים כאן הם uint32
    # מקובץ שלא אנחנו כתבנו: היסט מופרך שולח את `dd bs=1` לקרוא ג'יגה-בייט
    # בייט-בייט, ואורך רשומה מופרך מסובב את הלולאה מיליוני פעמים. מטען
    # אתחול חתום הוא מגה-בייטים בודדים ונושא חתימה אחת או שתיים, ולכן
    # חריגה כאן היא קובץ פגום — ותשובתה "לא הצלחנו לקרוא", לא תלייה של
    # הקליטה עד שמישהו יבחין.
    [ "$_end" -le 268435456 ] || return 1
    _found=0
    _left=8
    while [ "$_at" -lt "$_end" ]; do
        [ "$_left" -gt 0 ] || return 1
        _left=$((_left - 1))
        _wlen=$(_pe_num "$_f" "$_at" 4)
        _pe_is_num "$_wlen" || return 1
        [ "$_wlen" -gt 8 ] || return 1
        # ‏wCertificateType 2 = PKCS_SIGNED_DATA. כל השאר אינו Authenticode.
        if [ "$(_pe_num "$_f" $((_at + 6)) 2)" = "2" ]; then
            rm -f "$RUN_DIR/bootca.p7b"
            dd if="$_f" bs=1 skip=$((_at + 8)) count=$((_wlen - 8)) \
                of="$RUN_DIR/bootca.p7b" 2>/dev/null
            _ca=$(openssl pkcs7 -inform DER -in "$RUN_DIR/bootca.p7b" \
                    -print_certs -noout 2>>"$LOG_FILE" | _leaf_issuer)
            [ -n "$_ca" ] || return 1
            echo "$_ca"
            _found=1
        fi
        # כל רשומה מיושרת ל-8 בייט.
        _at=$(( (_at + _wlen + 7) / 8 * 8 ))
    done
    # טבלה שיש בה תוכן ולא הוצאנו ממנה שום חותם אינה "לא חתום".
    [ "$_found" -eq 1 ] || return 1
}

boot_ca_probe() {
    # $1 = ה-node של מחיצת ה-ESP (ריק = אין כזו), $2 = קובץ פלט לשמות.
    # יציאה 0 = נגזר (הקובץ יכול להיות ריק: נקרא, ואין חתימה). יציאה 1 =
    # לא ניתן היה, והסיבה ב-BOOTCA_ERROR.
    BOOTCA_ERROR=""
    : > "$2"
    if [ -z "$1" ]; then
        BOOTCA_ERROR="אין מחיצת ESP באימג' הזה"
        return 1
    fi
    if ! command -v openssl > /dev/null 2>&1; then
        BOOTCA_ERROR="openssl אינו זמין בסוכן"
        return 1
    fi
    _mnt="$ESPROOT"
    if [ -z "$_mnt" ]; then
        # ‏node_is_block ולא `[ -b ]` ישיר, מאותה סיבה שב-restore.sh —
        # ובלעדיו זה גם מסוכן: ‏`mount` על קובץ רגיל מקים לו loop device
        # מאחורי הגב, וקליטה שאיבדה את הצומת שלה היתה מעגנת משהו אחר.
        if ! node_is_block "$1"; then
            BOOTCA_ERROR="מחיצת ה-ESP אינה התקן בלוקים: $1"
            return 1
        fi
        _mnt="$RUN_DIR/espprobe"
        mkdir -p "$_mnt"
        # ‏-t vfat מפורש קודם, מאותה סיבה שב-_used_bytes: בלי סוג, mount
        # בוחר רק מבין מערכות הקבצים שכבר טעונות (#84). הכתיב חיובי
        # ו-`! cmd` אינו עומד כאן כפקודה עצמאית (‏#231).
        mount -t vfat -o ro "$1" "$_mnt" 2>>"$LOG_FILE" \
            || mount -o ro "$1" "$_mnt" 2>>"$LOG_FILE" \
            || { BOOTCA_ERROR="לא הצלחנו לעגן את ה-ESP לקריאה"; return 1; }
    fi
    _seen=0
    _bad=""
    _raw="$2.raw"
    : > "$_raw"
    for _p in $BOOTCA_PATHS; do
        [ -f "$_mnt$_p" ] || continue
        _seen=$((_seen + 1))
        if _pe_signers "$_mnt$_p" >> "$_raw"; then
            log "boot_ca: $_p נקרא" >&2
        else
            _bad="$_bad $_p"
            log "boot_ca: $_p -- לא הצלחנו לקרוא את החתימה" >&2
        fi
    done
    [ -n "$ESPROOT" ] || umount "$_mnt" 2>/dev/null
    if [ "$_seen" -eq 0 ]; then
        BOOTCA_ERROR="לא נמצא מטען אתחול על ה-ESP"
        return 1
    fi
    if [ -n "$_bad" ]; then
        # רשימה חלקית שמוצגת כשלמה היא בדיוק המצב שהשדה הזה בא למנוע:
        # ‏CA שלא נקרא הוא CA שהמכונה אולי דווקא מכירה.
        BOOTCA_ERROR="לא הצלחנו לקרוא את החתימה של:$_bad"
        return 1
    fi
    # אותו CA חוזר על פני שתי חתימות ועל פני כמה מטענים — פעם אחת ברשימה.
    # כישלון כתיבה כאן (tmpfs מלא ב-initramfs) מקצר את "$2" לריק. בלי בדיקת
    # היציאה `rm -f` שאחריו מצליח והפונקציה יוצאת 0 — וקובץ ריק נקרא כ-`[]`
    # ("נקרא, אינו חתום") במקום "לא הצלחנו לקרוא". זה שני מצבים שקופלו לאחד.
    if ! awk '!seen[$0]++' "$_raw" > "$2"; then
        BOOTCA_ERROR="כתיבת רשימת החותמים נכשלה"
        rm -f "$_raw"
        return 1
    fi
    rm -f "$_raw"
}

boot_ca_json() {
    # $1 = ה-node של ה-ESP. מדפיס את **שני** שדות המניפסט, תמיד שניהם:
    # רשימה כשנגזר, `[]` כשהמטענים נקראו ואינם חתומים כלל, ו-`null` עם
    # סיבה כשלא ניתן היה. שדה חסר ושדה שנכשל הם שני מצבים שונים (#298),
    # ובשום מקרה זה אינו מפיל קליטה: התעודה היא מידע, לא שער.
    _out="$RUN_DIR/bootca.txt"
    if boot_ca_probe "$1" "$_out"; then
        _list=""
        while read -r _nm; do
            [ -n "$_nm" ] || continue
            _list="$_list\"$(json_escape "$_nm")\","
        done < "$_out"
        printf '"boot_ca":[%s],"boot_ca_error":null' "${_list%,}"
    else
        log "boot_ca: $BOOTCA_ERROR" >&2
        printf '"boot_ca":null,"boot_ca_error":"%s"' "$(json_escape "$BOOTCA_ERROR")"
    fi
}
