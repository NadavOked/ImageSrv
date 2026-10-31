# shrinkoffer.sh -- ‏#926: ההצעה באתחול להחזיר דיסק שנשאר מכווץ.
# POSIX sh (busybox ash).
#
# פוצל מ-shrinkmem.sh ב-#929: שם נשאר ה-HTTP -- הרשומה בשרת -- וכאן מה
# שקורה במחשב הבנייה כשה-hello הביא רשומה פתוחה (דיאלוג בגואי, 11/10). הרשומה
# היא **לדיסק, עם רשימת מחיצות** (‏#929): כל כניסה נקראת **מהדיסק**
# (‏sgdisk -i) ומושווית לרשומה לפני שמוצע דבר, ואחת שאינה תואמת עוצרת את
# כל ההצעה -- שום דבר לא נכתב ולא נסגר. שאלה **אחת** לכל מה שצריך
# להחזיר; ההחזרה בסדר הפוך לכיווץ; והרשומה נסגרת רק כשכל המחיצות חזרו --
# הטבלה **ומערכת הקבצים** (סקירת Fable על #946: טבלה שנכתבה ומתיחה שנפלה
# נראית בדיוק כמו "בגודלה"). **בלי תשובה -- השאר** (עיקרון 1: שום דבר לא
# נכתב על דיסק בלי אדם שאמר כן).

shrink_restore_ask() {
    # $1 disk $2 = summary file: idx|kind|partition_bytes|now_bytes. ‏kind =
    # table (המחיצה עצמה קטנה) / fs (הטבלה בגודלה, רק מערכת הקבצים קטנה).
    # ההכרעה לבדה ל-stdout: restore / leave.
    # ‏11/10 (נדב: "הכול גואי"): השאלה היא דיאלוג במסך הגרפי, בסגנון שאלת
    # הכיווץ (askgui.sh, ‏screens_ask.c) -- שאלת הטקסט נמחקה. בלי מסך, גשר
    # שנכשל או בלי תשובה עד WAIT_SHRINK_GUI_S -- leave: שום דבר לא נכתב על
    # דיסק בלי אדם שאמר כן.
    awk -F'|' 'NF == 4 { print $1 "|" $4 "|" $3 "|" $2 }' "$2" > "$RUN_DIR/targets/$1/shrunk.rows" || { echo leave; return; }
    if _ra_gui=$(gui_ask_choice growback "$1" "$RUN_DIR/targets/$1/shrunk.rows" "$WAIT_SHRINK_GUI_S"); then
        case "$_ra_gui" in restore|leave) printf '%s\n' "$_ra_gui"; return ;; esac
    fi
    printf 'native GUI: the grow-back question was not answered -- left as it is\n' >&2
    echo leave
}

shrink_fs_bytes() {
    # $1 = partition node. מדפיס "<גודל הווליום>|<גודל קלאסטר>" מ-ntfsresize --info
    # (קריאה בלבד). בלי שורת גודל -- 1: "לא הצלחנו לבדוק" אינו "מתאים".
    ntfsresize --info --no-progress-bar "$1" > "$RUN_DIR/ntfsresize.fit" 2>&1
    _fb_v=$(sed -n 's/^Current volume size: *\([0-9][0-9]*\) bytes.*/\1/p' "$RUN_DIR/ntfsresize.fit" | head -n 1)
    _fb_c=$(sed -n 's/^Cluster size *: *\([0-9][0-9]*\) bytes.*/\1/p' "$RUN_DIR/ntfsresize.fit" | head -n 1)
    case "$_fb_v" in ''|*[!0-9]*) return 1 ;; esac
    case "$_fb_c" in ''|*[!0-9]*) _fb_c=4096 ;; esac
    printf '%s|%s' "$_fb_v" "$_fb_c"
}

shrink_grow_fs() {
    # $1 = disk, $2 = idx. מותח את מערכת הקבצים למחיצה **בלי לגעת בטבלה**
    # (היא כבר בגודלה): ntfsresize -f -f → ntfsfix -d. כשל = הסיבה ב-error
    # ובשרת (shrink-note), הרשומה נשארת פתוחה.
    _gf_node=$(partition_node "$1" "$2")
    if ntfsresize -f -f --no-progress-bar "$_gf_node" >> "$LOG_FILE" 2>&1; then
        if ntfsfix -d "$_gf_node" >> "$LOG_FILE" 2>&1; then log "$1: partition $2: filesystem grown to the partition"; return 0; fi
        _gf_why="דגל ה-dirty לא נוקה אחרי המתיחה (ntfsfix -d)"
    else _gf_why="ntfsresize -f -f rc=$?"; fi
    _gf_msg="מערכת הקבצים במחיצה $2 לא נמתחה לגודל המחיצה אחרי שהטבלה הוחזרה ($_gf_why) — ווינדוס לא יכול להרחיב אותה בעצמו"
    log "WARNING: $1: $_gf_msg"; echo "$_gf_msg" > "$RUN_DIR/targets/$1/error"
    shrink_note_record "$1" "$_gf_msg"
    return 1
}

shrink_offer_disk() {
    # $1 = disk, $2 = שורות הרשומה (שורה למחיצה). כל כניסה נקראת **מהדיסק**
    # ומושווית לרשומה לפני שמוצע דבר: תחילה או GUID ייחודי שונים = לא אותה
    # מחיצה, כלום לא נכתב. מחיצה שכבר בגודלה נבדקת גם **מבפנים**: גודל
    # הווליום מול המחיצה (סובלנות של קלאסטר) -- "טבלה נכתבה, המתיחה נפלה"
    # נראה בדיוק כמו בגודלה, וסגירה שקטה הייתה מוחקת את הראיה היחידה.
    _od_t="$RUN_DIR/targets/$1"; mkdir -p "$_od_t"
    printf '%s\n' "$2" > "$_od_t/shrunk_pending"
    : > "$_od_t/shrunk.todo"; : > "$_od_t/shrunk.ask"; _od_fs=""; _od_id=${2%%|*}
    _od_ss=$(cat "$SYSROOT/sys/block/$1/queue/logical_block_size" 2>/dev/null); case "$_od_ss" in ''|*[!0-9]*) _od_ss=512 ;; esac
    while IFS='|' read -r _od_rid _od_ser _od_idx _od_start _od_size _od_type _od_uguid _od_name _od_attr _od_ntfs _od_when <&3; do
        [ -n "$_od_idx" ] || continue
        _od_info=$(sgdisk -i "$_od_idx" "$DEVROOT/$1" 2>> "$LOG_FILE")
        _od_got=$(printf '%s\n' "$_od_info" | awk -F': ' '/^First sector/ { split($2, f, " ") } /^Partition size/ { split($2, z, " ") }
                                                         /^Partition unique GUID/ { u = $2 } END { print f[1] "|" z[1] "|" u }')
        _od_first=${_od_got%%|*}; _od_rest=${_od_got#*|}; _od_now=${_od_rest%%|*}; _od_u=${_od_rest#*|}
        case "$_od_now" in ''|*[!0-9]*) _od_now="" ;; esac
        if [ "$_od_first" != "$_od_start" ] || [ "$_od_u" != "$_od_uguid" ] || [ -z "$_od_now" ]; then
            log "WARNING: $1: shrink record #$_od_rid (partition $_od_idx at $_od_start, $_od_uguid) does not match the disk (read: $_od_got) -- nothing changed; clear it in the console if the layout was changed on purpose"
            return 1
        fi
        if [ "$_od_now" -lt "$_od_size" ]; then
            printf '%s|%s|%s|%s|%s|%s|%s|%s\n' "$_od_idx" "$_od_start" "$_od_size" "$_od_now" "$_od_type" "$_od_uguid" "$_od_name" "$_od_attr" >> "$_od_t/shrunk.todo"
            printf '%s|table|%s|%s\n' "$_od_idx" $((_od_size * _od_ss)) $((_od_now * _od_ss)) >> "$_od_t/shrunk.ask"
            continue
        fi
        if ! _od_fsb=$(shrink_fs_bytes "$(partition_node "$1" "$_od_idx")"); then
            log "WARNING: $1: partition $_od_idx is at its size but could not read the NTFS volume size (ntfsresize --info) -- record #$_od_rid stays open, nothing changed"
            return 1
        fi
        _od_vol=${_od_fsb%%|*}
        if [ $((_od_vol + ${_od_fsb#*|})) -ge $((_od_now * _od_ss)) ]; then
            log "$1: partition $_od_idx is already $_od_now sectors and the filesystem fills it"; continue
        fi
        _od_fs="$_od_fs $_od_idx"
        printf '%s|fs|%s|%s\n' "$_od_idx" $((_od_now * _od_ss)) "$_od_vol" >> "$_od_t/shrunk.ask"
    done 3< "$_od_t/shrunk_pending"
    if [ ! -s "$_od_t/shrunk.ask" ]; then
        log "$1: every partition of shrink record #$_od_id is at its size and full -- closing the record"
        echo "$_od_id" > "$_od_t/shrink_id"; shrink_close_record "$1" || :
        return 0
    fi
    _od_list=$(awk -F'|' '{ s = s (s ? ", " : "") $1 } END { print s }' "$_od_t/shrunk.ask")
    _od_q="מחיצה $_od_list בדיסק $1 כווצה לקליטה ולא הוחזרה לגודלה — החזר/השאר"
    case "$(attended "$_od_q" shrink_restore_ask "$1" "$_od_t/shrunk.ask")" in
        restore) ;;
        *) log "$1: partition $_od_list left at its current size by the operator's choice (record #$_od_id stays open)"; return 0 ;;
    esac
    echo "$_od_id" > "$_od_t/shrink_id"
    _od_rc=0; _od_bad=""
    # מערכת קבצים שנשארה קטנה במחיצה שכבר בגודלה -- מתיחה בלי טבלה.
    for _od_i in $_od_fs; do shrink_grow_fs "$1" "$_od_i" || _od_rc=1; done
    # הטבלה: בסדר הפוך לכיווץ -- המחיצה האחרונה על הדיסק ראשונה.
    awk '{ a[NR] = $0 } END { for (i = NR; i >= 1; i--) print a[i] }' "$_od_t/shrunk.todo" > "$_od_t/shrunk.undo"
    while IFS='|' read -r _od_i _od_s _od_o _od_n _od_tp _od_ug _od_nm _od_at <&3; do
        [ -n "$_od_i" ] || continue
        # shellcheck disable=SC2154 # ‏_sr1_why נכתב ב-_shrink_restore_one (shrink.sh)
        _shrink_restore_one "$1" "$_od_i" "$_od_s" "$_od_o" "$_od_n" "$_od_tp" "$_od_ug" "$_od_nm" "$_od_at" \
            || { _od_rc=1; _od_bad="$_od_bad${_od_bad:+; }$_sr1_why"; }
    done 3< "$_od_t/shrunk.undo"
    if [ "$_od_rc" -eq 0 ]; then shrink_close_record "$1" || :
    elif [ -n "$_od_bad" ]; then
        _od_msg="המקור לא הוחזר לגודלו: $_od_bad — ווינדוס יעלה"
        log "WARNING: $1: $_od_msg"; echo "$_od_msg" > "$_od_t/error"; shrink_note_record "$1" "$_od_msg"
    fi
    _shrink_offer_screen "$1" "$_od_rc" "back to its original size"; return "$_od_rc"
}

_shrink_offer_screen() {
    # $1 disk $2 rc $3 what succeeded. התוצאה -- ליומן; הכשל עצמו כבר בשרת
    # (shrink_note_record) ובקונסולה. ‏11/10: לא מסך טקסט מאחורי הגואי.
    if [ "$2" -eq 0 ]; then log "$1: $3"
    else log "$1: grow-back FAILED: $(cat "$RUN_DIR/targets/$1/error" 2>/dev/null). The record stays in the console."; fi
}

shrink_offer_pending() {
    # מלולאת מחשב הבנייה, **אחרי** שהמסך הגרפי עלה (11/10: השאלה היא דיאלוג
    # שלו). שואל פעם אחת לאתחול; 0 תמיד -- ההצעה אינה עוצרת את המכונה.
    [ "${_shrink_offered:-0}" = 0 ] || return 0
    shrink_records_refresh || return 0
    _op_all=$(list_disks)
    for _op_d in $_op_all; do
        _op_line=$(shrink_pending_for "$_op_d") || continue
        _shrink_offered=1
        # שני דיסקים כאן עם אותו סידורי (‏"0000000000000000" של גשר USB, ‏VM בלי
        # סידורי): הרשומה יכולה להיות של כל אחד, והשער השני אינו מבחין -- שחזור
        # מאותו אימג' שומר תחילה ו-GUID ייחודי (#26), ואח שכבר "בגודלו" היה
        # סוגר בשקט את הרשומה של המכווץ. לא נוגעים באף אחד, ולא סוגרים.
        _op_s=$(disk_serial "$_op_d"); _op_n=0
        for _op_x in $_op_all; do [ "$(disk_serial "$_op_x")" = "$_op_s" ] && _op_n=$((_op_n + 1)); done
        if [ "$_op_n" -gt 1 ]; then
            log "WARNING: $_op_d: $_op_n disks here report the same serial '$_op_s' -- shrink record #${_op_line%%|*} cannot be tied to one of them; nothing changed, nothing closed"
            continue
        fi
        shrink_offer_disk "$_op_d" "$_op_line" || :
    done
    return 0
}
