# diskfit.sh -- check 2.7: does the image fit this disk. Asked by name and
# before the partition table is touched (#12). POSIX sh (busybox ash).
# ‏required_bytes הוא התאום של server/images.py:required_bytes (#82). פוצל
# מ-restore.sh ב-#1217 לפני קיר 300 השורות, בלי שינוי התנהגות; הקוראים הם
# run_restore (restore.sh) ו-run_restore_drawers (drawers.sh).

required_bytes() {
    # $1 = manifest file. כמה בייטים הכונן חייב להחזיק כדי שהפריסה
    # תיכתב עליו: סוף המחיצה האחרונה, ועוד מגה-בייט לעותק הגיבוי של
    # ה-GPT וליישור. **לא** ‏min_target_bytes שבמניפסט — בכל אימג'
    # שנקלט לפני #82 הוא גודל דיסק המקור, וזה חסם כל אימג' שנבנה
    # במכונה וירטואלית מלהיכנס לכונן פיזי מאותה מחלקה.
    #
    # אותו כלל בדיוק בשרת (server/images.py:required_bytes), ויש בדיקה
    # שמריצה את שניהם על אותם מניפסטים ומשווה מספר למספר. מניפסט בלי
    # גיאומטריה מלאה נופל אחורה לשדה המוצהר, שהוא השמרני מהשניים; בלי
    # שניהם הפלט ריק, והקורא נכשל במקום לנחש.
    jq -r '
      (.sector_size // 512) as $ss
      | [ .partitions[]
          | select((.start_sector != null) and (.size_bytes != null))
          | .start_sector * $ss + .size_bytes ] as $ends
      | if ($ends | length) == 0 or ($ends | length) != (.partitions | length)
        then (.min_target_bytes // empty)
        else ($ends | max) as $end
             | ($end + 262144) as $need         # ‏#1171: GPT backup, no MiB round-up
             | if (.source_disk_bytes != null) and (.source_disk_bytes >= $end)
                  and (.source_disk_bytes < $need)
               then .source_disk_bytes else $need end
        end' "$1" 2>/dev/null
}

disk_fits() {
    # $1 = disk name, $2 = manifest file. בדיקה 2.7: כונן קטן מדי נתפס
    # **בשמו ולפני שנגענו בו**, ולא כ"כשל טבלת מחיצות" עמום אחרי
    # ש-apply_gpt כבר הריץ --zap-all (מעבדה, #12). שני המספרים נכנסים
    # להודעה, כי "קטן מדי" בלי כמה וכמה אינו אבחנה.
    # ‏#1808: חבר במערך RAID — לפני כל כתיבה (שחזור לחבר אחד שובר את המערך).
    # מגירה שהמפעיל אישר בה כתיבה (drawergate.sh, נדב 22/10) — raid_wipe מוחק לפני הכתיבה.
    _rm=$(raid_member_reason "$1")
    [ -z "$_rm" ] || [ -s "$RUN_DIR/targets/$1/raid_wipe" ] || { target_set "$1" "failed" "$_rm"; return 1; }
    _n=$(required_bytes "$2")
    # בלי `|| echo 0`: ‏blockdev שנכשל אינו כונן בגודל אפס. שניהם היו
    # נחסמים, אבל ההודעה הייתה אומרת "הכונן קטן מדי, יש בו 0 בייט" על
    # כונן שלא הצלחנו למדוד — וזה בדיוק קיפול שני מצבים לאחד. ה-stderr
    # ליומן ולא ל-/dev/null, כי הסיבה היא מה שמבדיל ביניהם.
    _h=$(blockdev --getsize64 "$DEVROOT/$1" 2>> "$LOG_FILE")
    # שני הערכים חייבים להיות מספרים לפני שמשווים אותם. קודם לכן ההשוואה
    # הייתה `[ -n "$_need" ] && [ ... ] 2>/dev/null` — כלומר מניפסט שלא
    # ידענו לקרוא ממנו את הדרישה **דילג על הבדיקה כולה** והמשיך לכתוב.
    # זה בדיוק עיקרון 5: "לא הצלחנו לבדוק" נראה כמו "בדקנו, הכל תקין".
    case "$_n" in ''|*[!0-9]*) _n="" ;; esac
    case "$_h" in ''|*[!0-9]*) _h="" ;; esac
    if [ -z "$_n" ] || [ -z "$_h" ]; then
        target_set "$1" "failed" \
            "לא ניתן לדעת אם האימג' נכנס: נדרש ${_n:-לא ידוע} בייט, בדיסק ${_h:-לא ידוע}"
        return 1
    fi
    if [ "$_h" -lt "$_n" ]; then    # #1171: the gap itself in MiB, before the two 12-digit numbers
        target_set "$1" "failed" "הדיסק קטן מדי ב-$(( (_n - _h + 1048575) / 1048576 )) MiB\
 (נדרשים $_n בייט, בדיסק $_h; חסרים $((_n - _h)) בייט)"
        return 1
    fi
    # #1354: נכנס = גם נשאר מקום לעבוד. אותו יעד, אותו רגע, לפני שנגענו בו.
    _mf=$(SHARE_HEAD=$([ ! -e "$RUN_DIR/targets/$1/share.ok" ] || echo 1) min_free_check "$2" "$_h" "$_n")   # #1898: the split the operator approved
    [ "$_mf" = ok ] || { target_set "$1" "failed" "${_mf#*|}"; return 1; }
}

# --- #1354: מינימום מקום פנוי במחיצת המערכת אחרי השחזור ----------------------
# הערך הוא הגדרה בקונסולה (min_free_gb, server/minfree.py) ומגיע בכל תשובת
# hello. ברירת המחדל כאן היא התאום של MIN_FREE_GB_DEFAULT שם -- יש טסט
# שמשווה. ערך שלא הגיע או אינו תקין = ברירת המחדל, **לא 0** (עיקרון 5):
# 0 הוא "הכלל כבוי", והוא נבחר רק במפורש בקונסולה.
MIN_FREE_GB_DEFAULT=20
MIN_FREE_GB_MAX=500

min_free_take() {
    # $1 = hello answer. The last value the server sent, kept for this boot --
    # the local clone (no server) uses it. Missing (an older server) = no file.
    _mt=$(json_get "$1" ".min_free_gb")
    case "$_mt" in null|'') return 0 ;; esac
    if ! _min_free_valid "$_mt"; then
        log "min_free_gb from the server is not 0-$MIN_FREE_GB_MAX ($_mt) -- the default ${MIN_FREE_GB_DEFAULT}GB" >&2
        rm -f "$RUN_DIR/min_free_gb"; return 0
    fi
    echo "$_mt" > "$RUN_DIR/min_free_gb"
}

_min_free_valid() {
    # 1-3 digits, no leading zero (ash reads 020 as octal), at most the max.
    case "$1" in 0) return 0 ;; ''|0*|*[!0-9]*|????*) return 1 ;; esac
    [ "$1" -le "$MIN_FREE_GB_MAX" ]
}

min_free_gb() {
    _mg=$(cat "$RUN_DIR/min_free_gb" 2>/dev/null)
    _min_free_valid "$_mg" && { echo "$_mg"; return 0; }
    log "no min_free_gb from the server -- using the default ${MIN_FREE_GB_DEFAULT}GB" >&2
    echo "$MIN_FREE_GB_DEFAULT"
}

# #1875: which partition is the system partition -- ONE rule, read by
# system_free_after (min-free), hostname.sh (_mount_windows) and, as a twin,
# server/minfree.py:system_partition. Evidence first: capture records
# "system": true where Windows/System32/config/SYSTEM sits (manifest.sh).
# A manifest without the mark (captured before #1875): the LARGEST windows
# partition -- an OEM/tools partition is small, C: holds the system. Two at the
# same top size, or one without a size, is real doubt: {ambiguous}, which
# min-free reports as unchecked, never as a wrong "short" (principle 5). No
# windows partition: the one linux partition; #1899: more than one without a
# mark (a separate /boot) is {ambiguous} too -- the first was /boot.
SYSTEM_PART_JQ='def system_part:
  [.partitions[] | select((.start_sector | type) == "number")] as $p
  | [$p[] | select(.system == true)] as $m
  | (if ($m | length) > 0 then $m else [$p[] | select(.role == "windows")] end) as $w
  | if ($w | length) == 1 then $w[0]
    elif ($w | length) > 1 then
      if ([$w[] | .size_bytes | type == "number"] | all) | not then {ambiguous: true, role: $w[0].role}
      else ($w | map(.size_bytes) | max) as $top | [$w[] | select(.size_bytes == $top)]
           | if length == 1 then .[0] else {ambiguous: true, role: $w[0].role} end end
    else [$p[] | select(.role == "linux")]
         | if length == 1 then .[0] elif length > 1 then {ambiguous: true, role: "linux"} else null end end;'

system_partition() {
    # $1 manifest. stdout: the index | ambiguous | nothing (no windows/linux).
    jq -r "$SYSTEM_PART_JQ"' system_part | if . == null then empty
        elif .ambiguous then "ambiguous" else .index end' "$1" 2>> "$LOG_FILE"
}

system_free_after() {
    # $1 manifest, $2 disk bytes, $3 required bytes, $4 the index expand_last
    # stretches ("" = none). stdout: free|role -- the bytes left free in the
    # system partition (system_part above) after the restore; unknown|role when
    # its used_bytes was not measured; ambiguous|windows when it cannot be told;
    # none| when there is no windows/linux partition at all. It grows only when
    # it is the one expand_last stretches or one capture shrank (#1898: C:
    # before the linux root), only past expand_last's own 1 GiB threshold --
    # and then by its share of the room (#1897, expandshare.sh). Twin:
    # server/minfree.py:free_after.
    jq -r --argjson d "$2" --argjson n "$3" --arg c "$4" --arg head "${SHARE_HEAD:-}" "$SYSTEM_PART_JQ"'
      system_part as $s
      | if $s == null then "none|"
        elif $s.ambiguous then "ambiguous|" + $s.role
        elif ($s.used_bytes | type) != "number" or $s.used_bytes < 0
             or ($s.size_bytes | type) != "number" then "unknown|" + $s.role
        else (($d - $n) as $g | ([.partitions[] | select((.index | tostring) == $c)] | .[0]) as $cp
              | if $cp != null and $g > 1073741824 and (($s.index | tostring) == $c
                     or (($s.shrunk_from_bytes | type) == "number"   # #1898: a shrunk C: before it, when approved
                         and ($head == "1" or $s.start_sector > $cp.start_sector)))
                then ($s.shrunk_from_bytes // $s.size_bytes) as $own   # #1897: its share only (expandshare.sh)
                     | (($cp.shrunk_from_bytes // $cp.size_bytes) + ([.partitions[] | select((.index | tostring) != $c
                                 and ($head == "1" or (.start_sector // 0) > $cp.start_sector)) | .shrunk_from_bytes | numbers] | add // 0)) as $all
                     | if $all > 0 then ($g * ($own / $all) | floor) else $g end
                else 0 end) as $grow
             | "\($s.size_bytes - $s.used_bytes + $grow | floor)|" + $s.role end' "$1" 2>> "$LOG_FILE"
}

min_free_check() {
    # $1 manifest, $2 disk bytes, $3 required bytes. stdout: ok |
    # short|<words> | unchecked|<words>. "Not measured" is not "fits"
    # (principle 5), and the words carry the number. The partition that grows
    # is expand_last's own choice (_expand_candidate, EXPAND_OVERRIDE included).
    _mr_gb=$(min_free_gb); [ "$_mr_gb" -gt 0 ] || { echo ok; return 0; }
    _mr=$(system_free_after "$1" "$2" "$3" "$(_expand_candidate "$1" | cut -d'|' -f1)")
    _mr_free=${_mr%%|*}; _mr_role=${_mr#*|}
    _mr_where="ב-C:"; [ "$_mr_role" = linux ] && _mr_where="במחיצת הלינוקס הראשית"
    case "$_mr_free" in
        none) echo ok; return 0 ;;
        ambiguous)   # #1875/#1899: the twins of server/minfree.py's AMBIGUOUS_WHY
            if [ "$_mr_role" = linux ]; then
                echo "unchecked|לא נבדק אם יישארו ${_mr_gb}GB פנויים $_mr_where — לא ידוע איזו מחיצת לינוקס היא השורש (אין סימון מהקליטה, ויש יותר מאחת)"
            else
                echo "unchecked|לא נבדק אם יישארו ${_mr_gb}GB פנויים $_mr_where — לא ידוע איזו מחיצת windows היא C: (אין סימון מהקליטה, ואין אחת גדולה מכולן)"
            fi
            return 0 ;;
        ''|unknown|*[!0-9]*)
            echo "unchecked|לא נבדק אם יישארו ${_mr_gb}GB פנויים $_mr_where — לא נמדד כמה תפוס בה (used_bytes חסר)"
            return 0 ;;
    esac
    if [ "$_mr_free" -lt $((_mr_gb * 1073741824)) ]; then
        echo "short|לא נכנס — יישארו רק $(awk -v b="$_mr_free" 'BEGIN { printf "%.1f", b / 1073741824 }') GB פנויים $_mr_where, המינימום ${_mr_gb}GB"
    else
        echo ok
    fi
}

# --- #1808: a member of a RAID set (Intel RST / DDF / md) is not a plain disk.
# Asked before a capture reads (capturegate.sh) and before a restore writes
# (disk_fits). Never assembles or writes through the array.

raid_member_probe() {
    # $1 = disk. Prints `none`, the member TYPE (isw_raid_member, ...), or
    # `?<why>` when the check itself failed (principle 5: never `none`).
    # blkid rc 2 is also its answer to "could not open/read" (measured,
    # util-linux 2.41.5) -- so the tail of the disk, where IMSM and DDF keep
    # their anchors, is read here first: that read is the positive evidence.
    _rp_n=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null)
    case "$_rp_n" in ''|*[!0-9]*) echo "?גודל הדיסק לא זמין"; return ;; esac
    [ "$_rp_n" -ge 16 ] || { echo "?הדיסק קטן מ-16 סקטורים"; return; }
    _rp_raw="$RUN_DIR/raidtail.$$"; _rp_txt="$RUN_DIR/raidtail.txt.$$"
    dd if="$DEVROOT/$1" bs=512 skip=$((_rp_n - 16)) count=16 > "$_rp_raw" 2>> "$LOG_FILE" \
        && [ "$(wc -c < "$_rp_raw")" -eq 8192 ] \
        || { rm -f "$_rp_raw"; echo "?קריאת סוף הדיסק נכשלה"; return; }
    tr -d '\0' < "$_rp_raw" > "$_rp_txt" || { rm -f "$_rp_raw" "$_rp_txt"; echo "?פענוח סוף הדיסק נכשל"; return; }
    LC_ALL=C grep -qF 'Intel Raid ISM Cfg Sig. ' "$_rp_txt"; _rp_rc=$?
    rm -f "$_rp_raw" "$_rp_txt"
    case "$_rp_rc" in 0) echo isw_raid_member; return ;; 1) ;; *) echo "?חיפוש החתימה נכשל (grep rc=$_rp_rc)"; return ;; esac
    _rp_t=$(blkid -p -u raid -o value -s TYPE "$DEVROOT/$1" 2>> "$LOG_FILE"); _rp_rc=$?
    case "$_rp_rc" in
        0) case "$_rp_t" in *_raid_member) echo "$_rp_t" ;; *) echo none ;; esac ;;
        2) echo none ;;
        *) echo "?blkid rc=$_rp_rc" ;;
    esac
}

raid_member_reason() {
    # $1 = disk. The refusal before any read for a capture or write for a
    # restore, or nothing. Never assembles or writes through the array (#1808).
    _rr=$(raid_member_probe "$1")
    case "$_rr" in
        none) ;;
        \?*) echo "לא הצלחנו לבדוק אם /dev/$1 חבר במערך RAID: ${_rr#?} — לא נגענו בדיסק" ;;
        isw_raid_member) echo "/dev/$1 חבר במערך RAID של Intel ($_rr) — העבירו את ה-BIOS ל-AHCI; לא נגענו בדיסק" ;;
        *) echo "/dev/$1 חבר במערך RAID ($_rr) — לא נגענו בדיסק" ;;
    esac
}

raid_member_json() {
    # $1 = disk. The hello field (build machine only, like `scheme`): the
    # member TYPE, false = checked and none, null = could not check.
    [ "${D_ROLE:-}" = build ] || return 0
    _rj=$(raid_member_probe "$1")
    case "$_rj" in none) printf ',"raid_member":false' ;; \?*) printf ',"raid_member":null' ;;
        *) printf ',"raid_member":"%s"' "$(json_escape "$_rj")" ;; esac
}
