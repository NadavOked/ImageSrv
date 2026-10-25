# restore.sh -- consumes the stream exactly as interfaces.md section 7
# defines it: GPT from the manifest, one transmission per partition file,
# receiver-side decompression, sha256 on the compressed bytes.
# POSIX sh (busybox ash).
#
# טבלת המחיצות כולה יושבת ב-expand.sh — ‏apply_gpt, ההרחבה והראיה
# (מתיחת מערכת הקבצים שאחרי הזרם עברה ל-grow.sh, #478)
# שהטבלה הגיעה לדיסק (verify_table); שני הקבצים נטענים יחד. כאן נשאר
# מה שצורך את הזרם עצמו. התקרות של כל ההמתנות כאן יושבות ב-waits.sh.
# בדיקה 2.7 — האם האימג' נכנס לכונן (required_bytes, disk_fits) — ב-diskfit.sh (#1217).

UDPCAST_PORTBASE="${UDPCAST_PORTBASE:-9000}"

# ‏udp-receiver ממתין לשדר לנצח כברירת מחדל — התקרה של הכלי עצמו, הדרך
# היחידה שיצא מרצונו במקום שנהרוג אותו מבחוץ. הערכים נגזרים מ-waits.sh
# (מקום אחד); ההשמה כאן היא רק נפילה אחורה לקובץ שנטען לבדו (בדיקות).
# ‏STALL=300 (11/09/2026): עד כאן 120, והיא נורתה כשהשולח האט למקבל איטי
# בזנב אימג' גדול וקטע את הזרם ב-~90% (FOG/Clonezilla: לא להפיל את האיטי).
# תקרת ההתחלה מגיעה לכל זרם בנפרד מ-next_stream_ceiling (waits.sh, #957).
UDPCAST_STALL_TIMEOUT="${UDPCAST_STALL_TIMEOUT:-${WAIT_STREAM_STALL_S:-300}}"

manifest_plan() {
    # $1 = manifest file. One line per partition:
    # index|type_guid|role|fs|start_sector|size_bytes|file|sha256|expandable|unique_guid|uuid|attrs
    # השדות האופציונליים בסוף בכוונה — מניפסט ישן בלעדיהם עדיין נקרא.
    # ל-read חייב להיות משתנה לכל שדה: האחרון בולע את הזנב עם הקווים.
    #
    # ‏jq פולט שורות תוך כדי ואז נופל על האלמנט הראשון שאינו ניתן
    # לרינדור, וב-pipeline של POSIX sh (אין pipefail) קוד היציאה הזה
    # בלתי נראה: תוכנית **חלקית** נראית בדיוק כמו תוכנית שלמה, והלולאה
    # שנגמרת בשלום אחריה מכריזה done על מחיצות שאיש לא כתב (#51). לכן
    # התוכנית מחומרנת לקובץ, ה-rc נבדק, ומספר השורות מושווה למה שהמניפסט
    # מצהיר עליו — ורק אז היא נקראת. גם tmpfs שנגמר נתפס כאן.
    _pf="$RUN_DIR/plan.$$${PREP_TAG:-}.txt"   # per process (the kiosk, once a second); per drawer in a parallel table write (#1704)
    jq -r '.partitions[] | [.index, .type_guid, .role, .fs, .start_sector, .size_bytes,
        .file, .sha256, .expandable, (.unique_guid // ""), (.uuid // ""), (.attrs // "")] | join("|")' \
        "$1" > "$_pf" 2>> "$LOG_FILE" || { log "plan: jq failed on $1" >&2; return 1; }
    _pw=$(jq -r '.partitions | length' "$1" 2>> "$LOG_FILE")
    case "$_pw" in ''|*[!0-9]*) log "plan: no partition count in $1" >&2; return 1 ;; esac
    _ph=$(awk 'END { print NR }' "$_pf")
    if [ "$_ph" -ne "$_pw" ]; then
        log "plan: $_ph of $_pw partitions rendered from $1" >&2   # #876: נלכד ב-$( )
        return 1
    fi
    cat "$_pf"
}

validate_restore_plan() {
    # $1=disk, $2=manifest. Validate every row and ask sgdisk to parse the
    # complete table without writing it; this must run before --zap-all.
    _vp="$RUN_DIR/preflight${PREP_TAG:-}.plan"; _vimg="$RUN_DIR/preflight${PREP_TAG:-}.disk"   # PREP_TAG: #1704; ‏#1545: ההדמיה על קובץ דליל — על הדיסק החי טבלה ישנה פגומה = sgdisk מתעלם מ--n ויוצא 2
    _vman=$2   # ‏#1418: `set --` למטה דורס את $1/$2
    manifest_plan "$2" > "$_vp" || { PLAN_ERROR="תוכנית השחזור פגומה — הדיסק לא שונה"; return 1; }
    _vsectors=$(blockdev --getsz "$DEVROOT/$1" 2>> "$LOG_FILE")
    case "$_vsectors" in ''|*[!0-9]*) PLAN_ERROR="לא ניתן לקרוא את גודל דיסק היעד — הדיסק לא שונה"; return 1 ;; esac
    _vwhy=$(awk -F'|' -v cap="$_vsectors" '   # #1435: הכלל שנכשל, בשם
        function bad(r) { printf "%s: %s", ($1 ~ /^[0-9]+$/ ? "מחיצה " $1 : "שורה " NR), r; exit 1 } BEGIN { n = 0 }
        NF < 11 || NF > 12 { bad("מספר שדות שגוי (" NF ")") }
        $1 !~ /^[0-9]+$/ || $1 < 1 || $1 > 128 || seen[$1]++ { bad("מספר המחיצה אינו חוקי או כפול") }
        $2 !~ /^[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}$/ { bad("סוג ה-GPT אינו GUID חוקי") }
        $3 !~ /^(esp|msr|windows|recovery|linux|swap|data)$/ { bad("תפקיד לא מוכר") }
        $4 !~ /^(ntfs|vfat|ext4|btrfs|swap|unknown)?$/ { bad("מערכת קבצים לא מוכרת") }
        $5 !~ /^[0-9]+$/ || $6 !~ /^[0-9]+$/ || $5 < 1 || $6 < 512 || $6 % 512 { bad("התחלה או גודל לא חוקיים") }
        $5 + $6 / 512 > cap { bad("חורגת מסוף הדיסק") }
        { for (i=0; i<n; i++) if ($5 < e[i] && $5 + $6/512 > s[i]) bad("חופפת למחיצה אחרת"); s[n]=$5; e[n++]=$5+$6/512 }
        { bare = ($4 == "swap" && $7 == "" && $8 == "") || ($7 ~ /^local:p[0-9]+$/ && $8 == "") }   # ‏#1433: swap בלי קובץ; שיכפול מקומי בלי sha
        (!bare && ($7 == "" || index($7, "/") || index($7, "\\") || $7 ~ /[[:cntrl:]]/ || length($8) != 64 || $8 !~ /^[0-9a-f]+$/)) { bad("קובץ או sha256 חסרים או לא חוקיים") }; $9 !~ /^(true|false)$/ { bad("ערך expandable אינו true/false") }
        $10 != "" && $10 !~ /^[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}$/ { bad("ה-GUID הייחודי אינו חוקי") }
        $11 ~ /[[:cntrl:]|]/ || ($11 != "" && $11 !~ /^([0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9A-F]{4}-[0-9A-F]{4}|[0-9A-F]{16})$/) { bad("ה-UUID של מערכת הקבצים אינו חוקי") }
        $12 !~ /^([0-9A-F]{16})?$/ { bad("מאפייני ה-GPT אינם חוקיים") }
        END { if (NR < 1) { printf "אין מחיצות בתוכנית"; exit 1 } }' "$_vp") || { PLAN_ERROR="תוכנית השחזור פגומה (${_vwhy:-סיבה לא ידועה}) — הדיסק לא שונה"; return 1; }
    set -- sgdisk -a 1 --pretend --clear   # ‏#1434: --zap-all בהדמיה משאיר את הטבלה הקיימת בזיכרון
    while IFS='|' read -r _i _g _r _fs _s _z _f _h _e _u _uuid _a; do
        _last=$((_s + _z / 512 - 1)); set -- "$@" -n "$_i:$_s:$_last" -t "$_i:$_g"
        [ -z "$_u" ] || set -- "$@" -u "$_i:$_u"
        [ -z "$_a" ] || set -- "$@" -A "$_i:=:0x$_a"
    done < "$_vp"
    _dg=$(json_get "$_vman" ".disk_guid"); [ -z "$_dg" ] || [ "$_dg" = null ] || set -- "$@" -U "$_dg"
    rm -f "$_vimg"; dd if=/dev/zero of="$_vimg" bs=512 count=0 seek="$_vsectors" 2>> "$LOG_FILE" || { PLAN_ERROR="לא ניתן להכין את בדיקת התוכנית — הדיסק לא שונה"; return 1; }
    "$@" "$_vimg" >> "$LOG_FILE" 2>&1; _vrc=$?; rm -f "$_vimg"; [ "$_vrc" -eq 0 ] || { PLAN_ERROR="sgdisk דחה את תוכנית השחזור — הדיסק לא שונה"; return 1; }
}

partclone_for_fs() {
    case "$1" in
        ntfs)                 echo "partclone.ntfs" ;;
        vfat|fat|fat32|fat16) echo "partclone.fat" ;;
        ext4|ext3|ext2)       echo "partclone.ext4" ;;
        btrfs)                echo "partclone.btrfs" ;;
        *)                    echo "partclone.dd" ;;
    esac
}

partclone_mode() {
    # $1=הכלי $2=הדגל המבוקש (-c לקליטה, -r לשחזור). partclone.dd לא
    # מכיר אף אחד מהם — הוא תמיד מעתיק בייט-בייט, לשני הכיוונים.
    # קריאה עם הדגל נכשלת מיד (מחיצת bios-grub, מעבדת ה-VM, issue #12).
    if [ "$1" = "partclone.dd" ]; then echo ""; else echo "$2"; fi
}

partition_node() {
    # sda 3 -> /dev/sda3, nvme0n1 3 -> /dev/nvme0n1p3.
    case "$1" in
        *[0-9]) echo "$DEVROOT/${1}p$2" ;;
        *)      echo "$DEVROOT/$1$2" ;;
    esac
}

node_is_block() {
    # $1 = נתיב לצומת מחיצה. פונקציה נפרדת ולא `[ -b ]` ישיר רק כדי
    # שהבדיקות יוכלו להחליף אותה: אין דרך ליצור התקן בלוקים בלי root,
    # ובדיקה שמדולגת בחצי מהסביבות היא ירוק בלי ראיה (הלקח מ-#52).
    [ -b "$1" ]
}

stream_source() {
    # $1 = mode, $2 = server URL, $3 = image id, $4 = file, $5 = תקרה עד הבייט הראשון.
    if [ "$1" = "multicast" ]; then   # #2039: the NIC DHCP answered on (net.conf), as directsend's sender; >&2: stdout is the stream
        [ -n "${IFACE:-}" ] || log "udp-receiver: IFACE is empty -- no --interface, udpcast picks its default NIC (#2039)" >&2
        udp-receiver --nokbd ${IFACE:+--interface "$IFACE"} --portbase "$UDPCAST_PORTBASE" \
            --start-timeout "${5:-${WAIT_STREAM_START_S:-600}}" --receive-timeout "$UDPCAST_STALL_TIMEOUT" 2>> "$LOG_FILE"
    else
        http_get_stream "$2/api/v1/images/$3/files/$4"
    fi
}

is_swap_partition() {     # $1 = fs. ‏swap נקבעת מ-fs בלבד (#424): "בלי קובץ"
    [ "$1" = "swap" ]     # אינו swap אלא מניפסט פגום, ונתפס לפני מחיקת הטבלה.
}

make_swap() {
    # $1 = node, $2 = ה-UUID מהמניפסט. ‏swap אינה משודרת (אפיון סעיף 14) אלא
    # נבראת כאן — ה-mkswap היחיד בסוכן, כתיבה אחת על המיקום הסופי (‏swap נגררת
    # כבר הועברה לזנב, #46). ‏-U כי fstab של דביאן מפנה ל-UUID=, ובלעדיו כל
    # שחזור ממציא UUID חדש וה-swap לא עולה (#48); מניפסט ישן בלי השדה נופל
    # אחורה ל-mkswap רגיל עם UUID אקראי, בדיוק כמו קודם (עיקרון 1).
    _u=""
    [ -n "$2" ] && [ "$2" != "null" ] && _u="-U $2"
    # shellcheck disable=SC2086 # הפיצול של $_u מכוון
    mkswap $_u "$1" >> "$LOG_FILE" 2>&1
}

is_sha256() {
    # ‏64 ספרות hex ותו לא: "ה-sha לא חושב" הוא מצב נפרד מ"לא תאם" (#73) —
    # קובץ ריק שמושווה למניפסט מייצר האשמה ספציפית על בדיקה שלא נעשתה.
    case "$1" in ''|*[!0-9a-f]*) return 1 ;; esac
    [ "${#1}" -eq 64 ]
}

restore_partition() {
    # $1=mode $2=server $3=image_id $4=disk $5=index $6=fs $7=file $8=sha256 $9=uuid
    # Pipeline: source | fanout (-P counter, -S sha) > feed; zstd -d < feed | partclone
    _tdir="$RUN_DIR/targets/$4"
    _node=$(partition_node "$4" "$5")
    _pcl=$(partclone_for_fs "$6")

    # יעד שאינו התקן בלוקים הוא קובץ רגיל ב-devtmpfs: ‏partclone היה כותב
    # אליו את המחיצה כולה, יוצא 0, וה-sha256 היה עובר — כי הוא נלקח על
    # הבייטים שהתקבלו. ‏apply_gpt כבר אימת את כל הצמתים; זו הבדיקה
    # האחרונה, מיד לפני הכתיבה עצמה (#51).
    node_is_block "$_node" \
        || { log "partition $5: $_node is not a block device"; return 1; }

    if is_swap_partition "$6"; then
        make_swap "$_node" "$9" || { log "partition $5: mkswap failed"; return 1; }
        target_partition_done "$4"
        return 0
    fi

    next_stream_ceiling   # #957: אחרי ה-swap — נספרים רק זרמים
    rm -f "$_tdir/feed" "$_tdir/sha.out" "$_tdir/pipe.rc" "$_tdir/zstd.rc" "$_tdir/fanout.rc" "$_tdir/fanout.out"
    mkfifo "$_tdir/feed"
    # הצינור רץ ברקע כדי שתהיה עין שמסתכלת עליו: המונה (‏-P, פורמט pv -n)
    # הוא ההוכחה היחידה שמשהו זז, ו-wait_progress מודד בדיוק אותו. ‏#1444:
    # מונה וגיבוב בתוך fanout ולא pv|tee|sha256sum של busybox (‏#1438).
    (
        restore_pipe "$_tdir" "$_pcl" "$_node" pipe.rc &   # #1906: zstd's rc too (drawers.sh), as the drawers'
        _wpid=$!
        stream_source "$1" "$2" "$3" "$7" "$STREAM_START_CEILING" \
            | fanout -S "$_tdir/sha.out" -P "$_tdir/bytes.raw" 16777216 "$_tdir/feed" \
                > "$_tdir/fanout.out" 2>> "$LOG_FILE"   # #1601: restore_blame reads it
        echo "$?" > "$_tdir/fanout.rc"
        grep -qx 0 "$_tdir/fanout.rc" || unblock_fifo "$_tdir/feed"   # מת לפני open (#49)
        wait_pid "$_wpid" "$WAIT_DRAWER_S" "כתיבת מחיצה $5 על $4"
    ) &
    _pipepid=$!
    if wait_progress "$_pipepid" "$_tdir/bytes.raw" \
            "$STREAM_START_CEILING" "$WAIT_STREAM_STALL_S" \
            "מחיצה $5 ($7) על $4" "$_tdir/feed"; then
        _rc=$(cat "$_tdir/pipe.rc" 2>/dev/null || echo 1)
        [ "$_rc" -ne 0 ] || _rc=$(cat "$_tdir/zstd.rc" 2>/dev/null || echo 1)   # #1906: not recorded = not 0
        [ "$_rc" -ne 0 ] || _rc=$(cat "$_tdir/fanout.rc" 2>/dev/null || echo 1)
    else
        _rc="$WAIT_TIMED_OUT"
    fi
    rm -f "$_tdir/feed" "$_tdir/feed.bytes"

    if [ "$_rc" -ne 0 ]; then
        if [ -f "$_tdir/writeback.failed" ]; then RESTORE_BLAME=""; log "partition $5 ($7): periodic fsync failed (write_error)"
        else
            restore_blame "$_tdir" "$8"; log "partition $5 ($7): write failed (rc=$_rc${RESTORE_BLAME:+, blamed on the stream})"   # #1601
        fi
        return 1
    fi

    # Verification is on the compressed bytes as received; in multicast there
    # is no asking again. ‏-S נכתב רק ב-EOF נקי: fanout שנהרג (או שפג זמנו)
    # לא משאיר ערך, וההשוואה הישירה הייתה הופכת היעדר ראיה לטענה על בייטים (#73).
    _got=$(awk '{print $1}' "$_tdir/sha.out" 2>/dev/null)
    if ! is_sha256 "$_got"; then
        log "partition $5 ($7): ה-sha256 לא חושב -- אין ראיה שהזרם הגיע שלם"
        RESTORE_BLAME=stream; return 1   # #1601: of the stream, not the disk
    elif [ "$_got" != "$8" ]; then
        log "partition $5 ($7): sha256 mismatch"
        RESTORE_BLAME=stream; return 1
    fi
    ntfs_boot_target "$4" "$5" "$6" && target_partition_done "$4"   # #1487
}

run_restore() {
    # $1 = mode (multicast/unicast), $2 = disk name, $3 = server URL,
    # $4 = image id, $5 = manifest file.
    echo "writing" > "$RUN_DIR/state"
    target_set "$2" "writing"
    # A classroom station records the failed target and its ATA evidence.  It
    # does not perform the drawer-only post-failure GPT invalidation (#1388).
    : > "$RUN_DIR/targets/$2/evidence_only"

    # A too-small disk is rejected by name before its table is touched (#12).
    if ! disk_fits "$2" "$5"; then
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi

    # Firmware trust is checked before apply_gpt touches the target. Unchecked
    # follows the existing SMART rule: report and continue; mismatch refuses.
    if ! boot_ca_preflight "$5" "$2"; then
        target_set "$2" "failed" "$BOOT_CA_PREFLIGHT_REASON"
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi
    # #1366: a disk that does not take a write, or writes below the floor, fails here by name.
    { write_check "$2" && write_speed_verdict "$2"; } || { echo "failed" > "$RUN_DIR/state"; return 1; }
    discard_before_restore "$2"; [ $? != 2 ] || { target_set "$2" failed "ה-TRIM לא הסתיים תוך $DISCARD_MAX_S שניות"; echo "failed" > "$RUN_DIR/state"; return 1; }   # #1969

    # הטבלה נקבעת עד תומה לפני שהנתונים מגיעים — כולל ההרחבה, כי היא זו
    # שמעבירה swap נגררת לזנב, וה-mkswap של הלולאה חייב לנחות על המקום
    # הסופי. הרחבה שנכשלה באמצע חוזרת לטבלת המניפסט ומשחזרת בגודל המקורי:
    # עוד לא נכתב בייט, ולכן הנסיגה הזו שלמה.
    _table=0
    if apply_gpt "$2" "$5"; then
        if expand_last "$2" "$5"; then
            _table=1
        else
            log "WARNING: expansion failed -- restoring at the image's own size"
            apply_gpt "$2" "$5" && _table=1
        fi
    fi
    if [ "$_table" -ne 1 ]; then
        case "$PLAN_ERROR" in
            *"הדיסק לא שונה")
                # The manifest gate is before --zap-all: report the named refusal
                # without turning a deliberately untouched disk into a failed write.
                target_set "$2" "failed" "$PLAN_ERROR" ;;
            *)  fail_written_target "$2" "${PLAN_ERROR:-לא הצלחנו לכתוב את טבלת המחיצות}" ;;
        esac
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi

    # Materialize once; fd 3 keeps tools in the loop from consuming the plan.
    _plan="$RUN_DIR/restore.plan"
    if ! manifest_plan "$5" > "$_plan"; then
        fail_written_target "$2" "לא הצלחנו לקרוא את תוכנית המחיצות מהמניפסט"
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi
    _expected=$(awk 'END { print NR }' "$_plan")
    _expected_rc=$?
    case "$_expected" in ''|*[!0-9]*) _expected_rc=1 ;; esac
    if [ "$_expected_rc" -ne 0 ]; then
        fail_written_target "$2" "לא הצלחנו לספור את המחיצות בתוכנית השחזור"
        echo "failed" > "$RUN_DIR/state"; return 1
    fi
    _written=0; STREAMED_PARTITIONS=0; RESTORE_BLAME=""   # #957: הזרם הראשון של השחזור הזה; #1601
    while IFS='|' read -r _idx _guid _role _fs _start _size _f _sha _exp _uguid _uuid _attrs <&3; do
        log "partition $_idx ($_role, $_fs): receiving $_f"; echo "$_idx" > "$RUN_DIR/targets/$2/partition"   # the GUI's "מחיצה N" (srgui.sh)
        restore_partition "$1" "$3" "$4" "$2" "$_idx" "$_fs" "$_f" "$_sha" "$_uuid" \
            || break
        _written=$((_written + 1))
    done 3< "$_plan"

    if [ "$_expected" -lt 1 ] || [ "$_written" -ne "$_expected" ]; then
        fail_written_target "$2" "נכתבו $_written מתוך $_expected מחיצות" "$RESTORE_BLAME"
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi

    echo "verifying" > "$RUN_DIR/state"   # #1805: what the disk holds, before the filesystem grows
    { RB_SERVER=$3 RB_IMAGE=$4 readback_drawers "$5" "$2" && finish_grow "$2" "" "$5"; } || { echo "failed" > "$RUN_DIR/state"; return 1; }
}   # #1935: still 'verifying' -- done is the caller's, after the name, the drivers and the mark
