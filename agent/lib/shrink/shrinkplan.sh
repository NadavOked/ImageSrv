# shrinkplan.sh -- ‏#929: מה מכווצים, בכמה, ומה שואלים -- קריאה בלבד.
# POSIX sh (busybox ash).
#
# פוצל מ-shrink.sh כשהכיווץ הורחב מהמחיצה האחרונה לכל מחיצות ה-NTFS
# (‏#929, הכרעת נדב 17/09). כאן יושב כל מה ש**אינו כותב לדיסק**: המועמדות,
# המדידה (ntfsresize --info), היעד, השאלה למפעיל והרמז בסירוב. shrink.sh
# נשאר הנתיב ההרסני -- ntfsresize -s, הטבלה, וההחזרה.
#
# המועמדות הן **כל** מחיצות ה-windows/linux על הדיסק, לפי start_sector עולה,
# ולא רק האחרונה: דיסק בנייה עם D: אחרי C: מכווץ את שתיהן, כל אחת
# **במקומה** -- ntfsresize מקטין מהסוף, והמחיצה נשארת בתחילתה -- בלי להזיז
# נתונים על דיסק הבנייה. המניפסט והיעד מקבלים את הפריסה הצפופה
# (‏source_start_sector זוכר את המקום האמיתי), ו-expand_last מותח על היעד
# רק את האחרונה.
#
# מחיצה שאי-אפשר למדוד (‏--info נכשל: מלוכלכת, מהוברת, לא עקבית) מסרבת את
# **כל** הקליטה, בשם -- לא "מכווצים חלקית" (עיקרון 5). מחיצה שאין מה לכווץ
# בה, או שאינה NTFS (‏ext4 הוא מסלול נפרד עם e2fsck, לא כאן), פשוט אינה
# בתוכנית; כשהאימג' עדיין גדול מהיעד, ‏shrink_unshrinkable_hint אומר זאת.

# ‏#1202 (הכרעת נדב 22/09, "כמו FOG"): "256GB" של יצרן אחד קטן במגה-בייטים
# מזה של אחר (‏1,744,896 בייט, נמדד על ברזל 20/09). כש-C: כמעט מלאה הכיווץ
# הרגיל (מרווח 10%/2GiB) לא מזיז כלום, והפריסה נגמרת בסוף דיסק המקור. לכן
# הפריסה חייבת להיגמר **לפחות** כך וכך לפני סוף דיסק המקור: מה שחסר נלקח
# מהמחיצה האחרונה (לפי start_sector) שנשאר בה אחרי כן לפחות אותו סכום פנוי
# מעבר למינימום של ntfsresize, וכל מה שאחריה (‏recovery, ‏MSR) זז אחורה
# במניפסט בלבד (‏_shrink_compact) -- על המקור אף מחיצה אינה זזה ואינה נכתבת
# מלבד זו שמתכווצת. אין מחיצה עם מקום -- אין זנב, והשחזור נפסל בשם ובכמה
# (‏disk_fits). כפולה של MiB; ‏0 מכבה.
SHRINK_TAIL_BYTES="${SHRINK_TAIL_BYTES:-67108864}"

_shrink_gb() { awk -v b="$1" 'BEGIN { printf "%.1f GB", b / 1e9 }'; }

_shrink_candidates() {
    # $1 = parts file (idx|type|uguid|start|size_sectors). כל מחיצת
    # windows/linux, לפי start_sector עולה -- הסדר שבו הן מכווצות ושבו
    # ההפרשים מצטברים אחורה. המיון ב-awk כמו ב-_move_to_tail; ריק = אין.
    while IFS='|' read -r _cd_idx _cd_guid _cd_rest; do
        [ -n "$_cd_idx" ] || continue
        case "$(_partition_role "$_cd_guid")" in
            windows|linux) printf '%s|%s|%s\n' "$_cd_idx" "$_cd_guid" "$_cd_rest" ;;
        esac
    done < "$1" | awk -F'|' '{ a[NR] = $0; k[NR] = $4 + 0 }
        END { for (i = 1; i <= NR; i++) for (j = i + 1; j <= NR; j++)
                  if (k[j] < k[i]) { t = k[i]; k[i] = k[j]; k[j] = t; u = a[i]; a[i] = a[j]; a[j] = u }
              for (i = 1; i <= NR; i++) print a[i] }'
}

_shrink_target() {
    # $1 = המינימום של ntfsresize, $2 = הגודל הנוכחי. היעד: מינימום ועוד
    # מרווח (10% או 2GiB, הגדול מביניהם), מעוגל למעלה ל-MiB -- וההפרש מהנוכחי
    # מעוגל **למטה** ל-MiB, כדי שהזנב שיזוז במניפסט יישאר מיושר. הפרש קטן
    # ממגה = אין מה לכווץ: מודפס הגודל הנוכחי.
    awk -v m="$1" -v c="$2" 'BEGIN {
        margin = m * 0.10; if (margin < 2147483648) margin = 2147483648
        t = int((m + margin + 1048575) / 1048576) * 1048576
        d = int((c - t) / 1048576) * 1048576; if (d < 1048576) d = 0
        printf "%.0f\n", c - d }'
}

shrink_plan() {
    # $1 disk $2 parts file $3 sector size. כותב $RUN_DIR/shrink.plan -- שורה
    # לכל מחיצה שתכווץ: idx|type|uguid|start|size_sectors|cur_bytes|new_bytes,
    # לפי start_sector. ‏0 = התוכנית נכתבה (גם ריקה = אין מה לכווץ); ‏1 =
    # מחיצת NTFS שלא ניתן למדוד, SHRINK_ERROR אומר איזו. ‏--info קורא בלבד
    # ומריץ 100% מהחישוב ("You might resize at N bytes"); בלי -f: ווליום
    # מלוכלך או עם סקטורים פגומים נעצר כאן, בהודעה של הכלי.
    _sp="$RUN_DIR/shrink.plan"; : > "$_sp"; _sp_m="$RUN_DIR/shrink.meas"; : > "$_sp_m"
    _shrink_candidates "$2" > "$RUN_DIR/shrink.cand"
    while IFS='|' read -r _sp_idx _sp_guid _sp_uguid _sp_start _sp_size <&3; do
        [ -n "$_sp_idx" ] || continue
        _sp_node=$(partition_node "$1" "$_sp_idx")
        case "$(_fs_of "$_sp_node")" in ntfs) ;; *) log "partition $_sp_idx: not NTFS -- not shrunk here"; continue ;; esac
        _sp_cur=$((_sp_size * $3))
        ntfsresize --info --no-progress-bar "$_sp_node" > "$RUN_DIR/ntfsresize.info.$_sp_idx" 2>&1
        _sp_min=$(sed -n 's/.*You might resize at \([0-9][0-9]*\) bytes.*/\1/p' "$RUN_DIR/ntfsresize.info.$_sp_idx" | head -n 1)
        case "$_sp_min" in ''|*[!0-9]*)
            # #1502 (metal 16/10): the tool's own words stay in the log and in
            # ntfsresize.info.N; the screen gets Hebrew first and the fix.
            log "partition $_sp_idx: ntfsresize --info gave no minimum: $(tail -n 1 "$RUN_DIR/ntfsresize.info.$_sp_idx" 2>/dev/null | tr -d '\r')"
            LC_ALL=C grep -qF "NTFS is inconsistent" "$RUN_DIR/ntfsresize.info.$_sp_idx"; _sp_rc=$?
            if [ "$_sp_rc" -eq 0 ]; then
                SHRINK_ERROR="מחיצה $_sp_idx בדיסק $1 לא עקבית (NTFS) — בחלונות: chkdsk /f על הכונן, אתחלו פעמיים, כבו ב-shutdown /s /t 0 וקלטו מחדש. הדיסק לא שונה."
            else   # 1 = another refusal, 2 = grep itself failed: neither is a diagnosis
                SHRINK_ERROR="לא הצלחנו לבדוק כמה אפשר לכווץ את מחיצה $_sp_idx בדיסק $1 — פרטי ntfsresize ביומן הסוכן. הדיסק לא שונה."
            fi
            return 1 ;;
        esac
        _sp_new=$(_shrink_target "$_sp_min" "$_sp_cur")
        [ "${CAPTURE_SINK:-upload}" != discard ] || _sp_new=$_sp_cur   # #1171: a direct send takes the tail only
        [ "$_sp_new" -lt "$_sp_cur" ] || log "partition $_sp_idx: $_sp_cur bytes, minimum $_sp_min -- nothing to shrink"
        printf '%s|%s|%s|%s|%s|%s|%s|%s\n' "$_sp_idx" "$_sp_guid" "$_sp_uguid" "$_sp_start" "$_sp_size" "$_sp_cur" "$_sp_new" "$_sp_min" >> "$_sp_m"
    done 3< "$RUN_DIR/shrink.cand"
    # ‏#1202: הזנב. כמה כבר פנוי אחרי הפריסה בסוף דיסק המקור; כונן שלא נמדד
    # נחשב 0 -- הכיוון הבטוח: כיווץ קטן ששואלים עליו, לא פסילה מול כיתה.
    _sp_disk=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null)
    case "$_sp_disk" in ''|*[!0-9]*) log "$1: disk size unknown -- the tail allowance assumes no room after the layout"; _sp_disk=0 ;; esac
    _sp_slack=$(awk -F'|' -v d="$_sp_disk" -v ss="$3" '{ e = ($4 + $5) * ss; if (e > end) end = e }
        END { s = d * 512 - end; printf "%.0f\n", (d > 0 && s > 0) ? s : 0 }' "$2")
    _shrink_tail "$_sp_m" "${_sp_slack:-0}" > "$_sp" || return 1
    [ ! -s "$RUN_DIR/shrink.tail" ] \
        || log "partition $(cut -d'|' -f1 "$RUN_DIR/shrink.tail"): $(cut -d'|' -f2 "$RUN_DIR/shrink.tail") more bytes, so the layout ends $SHRINK_TAIL_BYTES before the disk end (#1202)"
}

_shrink_tail() {
    # $1 = המדידות (idx|guid|uguid|start|size|cur|new|min, לפי start), $2 = כמה
    # כבר פנוי אחרי הפריסה. ל-stdout: התוכנית -- השורות שבאמת מתכווצות. החסר עד
    # SHRINK_TAIL_BYTES (מעוגל למעלה ל-MiB, כדי שהזנב יישאר מיושר) נוסף למחיצה
    # האחרונה שנשאר בה אחריו לפחות SHRINK_TAIL_BYTES מעל המינימום; אין כזו --
    # התוכנית כמו בלי הזנב. ‏$RUN_DIR/shrink.tail = idx|bytes כשנלקח, ריק כשלא.
    awk -F'|' -v t="$SHRINK_TAIL_BYTES" -v slack="$2" -v rec="$RUN_DIR/shrink.tail" '
        { id[NR] = $1; a[NR] = $1 "|" $2 "|" $3 "|" $4 "|" $5; cur[NR] = $6; nw[NR] = $7; mn[NR] = $8; tot += $6 - $7 }
        END { printf "" > rec
              need = t - slack - tot
              if (need > 0) {
                  need = int((need + 1048575) / 1048576) * 1048576
                  for (i = NR; i >= 1; i--) if (nw[i] - need - mn[i] >= t) {
                      nw[i] -= need; printf "%s|%.0f\n", id[i], need > rec; break }
              }
              for (i = 1; i <= NR; i++) if (nw[i] < cur[i]) printf "%s|%.0f|%.0f\n", a[i], cur[i], nw[i] }' "$1"
}

shrink_question() {
    # $1 = plan file. השאלה במילים -- לקונסולה (‏attended, ‏#906) ולשדה error
    # כשבוטלה: כל מחיצה עם המספרים שלה.
    _sq=""
    while IFS='|' read -r _sq_i _sq_g _sq_u _sq_s _sq_z _sq_cur _sq_new; do
        [ -n "$_sq_i" ] || continue
        _sq="$_sq${_sq:+, }מחיצה $_sq_i מ-$(_shrink_gb "$_sq_cur") ל-$(_shrink_gb "$_sq_new")"
    done < "$1"
    if [ "${CAPTURE_SINK:-upload}" = discard ]; then   # #1171: the direct send's question
        printf 'ההפצה הישירה תכווץ בעותק בזיכרון, והמקור לא ייכתב: %s — כווץ ושלח / שלח בלי כיווץ' "$_sq"; return
    fi
    printf '%sהקליטה תכווץ במחשב הזה: %s — המשך/ביטול' "${CAPTURE_SPACE_WARNING:+$CAPTURE_SPACE_WARNING — }" "$_sq"
}

shrink_ask() {
    # $1 = plan file. ההכרעה בלבד ל-stdout (הקורא לוכד ב-`$( )`): continue /
    # plain / cancel. שאלה **אחת** עם הטבלה של כל המחיצות -- לא שאלה למחיצה.
    # ‏#928: השאלה היא דיאלוג של המסך הגרפי (‏shrinkgui.sh), ורק שם (נדב 11/10:
    # GUI בלבד -- מסך הטקסט שהיה כאן הוסר). אין מסך, גשר שנכשל, מסך שמת או
    # פקיעה -- cancel: שום דבר לא מכווץ בלי אדם שאמר כן.
    if [ -n "${_gui_pid:-}" ] && _sa_gui=$(gui_shrink_choice "$1"); then
        case "$_sa_gui" in continue|plain|cancel) printf '%s\n' "$_sa_gui"; return ;; esac
    fi
    printf 'native GUI: the shrink question was not answered -- capture cancelled\n' >&2
    echo cancel
}

shrink_unshrinkable_hint() {
    # $1 disk $2 parts file $3 sector size. ‏#929: הסירוב על הרצפה
    # (‏capture.sh) אומר גם **למה** הכיווץ לא הספיק -- מחיצת windows/linux
    # שאינה NTFS אינה מכווצת כאן, ומה לעשות. הגדלים של המקור (parts.orig.txt
    # כשכווץ) -- מה שהמפעיל רואה. ריק = אין מה להוסיף.
    _uh_src="$2"; [ -f "$RUN_DIR/parts.orig.txt" ] && _uh_src="$RUN_DIR/parts.orig.txt"
    _shrink_candidates "$_uh_src" | while IFS='|' read -r _uh_idx _uh_guid _uh_uguid _uh_first _uh_size; do
        _uh_fs=$(_fs_of "$(partition_node "$1" "$_uh_idx")")
        [ "$_uh_fs" = ntfs ] && continue
        printf ' — מחיצה %s (%s, %s) אינה NTFS והכיווץ בקליטה פועל על NTFS בלבד: כווצו אותה בעצמכם (resize2fs) או מחקו אותה, וקלטו מחדש' \
            "$_uh_idx" "$(_shrink_gb $((_uh_size * $3)))" "$_uh_fs"
        break
    done
}
