# capturesink.sh -- where capture_disk's results go: each partition to the
# server while it is still being read (capture_sink), the manifest last
# (upload_manifest), and a refusal or a failure to the target's error field
# (_capture_failed). POSIX sh (busybox ash).
# פוצל מ-capture.sh ב-#1217 לפני קיר 300 השורות, בלי שינוי התנהגות; שם נשאר
# capture_disk, שקורא את הדיסק. שני הקבצים נטענים יחד ב-imagesrv-agent.

_capture_failed() {
    # $1 = disk, $2 = reason, $3 = optional recovery code (docs/interfaces.md).
    # Persist the reason in the target's error field, like partition failures (#106).
    log "capture failed on $1: $2"
    # ‏#87: המקור חוזר לגודלו קודם. החזרה שנכשלה מצטרפת לסיבה — היומן
    # לבדו נעלם, והמפעיל חייב לדעת אם דיסק הבנייה נשאר מכווץ.
    _cf_why="$2"
    shrink_restore_source "$1" || _cf_why="$2 | $(cat "$RUN_DIR/targets/$1/error")"
    target_set "$1" "failed" "$_cf_why"
    printf '%s\n' "${3:-source_disk}" > "$RUN_DIR/capture.reason"
    echo "failed" > "$RUN_DIR/state"
}

capture_sink() {
    # $1 = fifo, $2 = task id, $3 = file. Consumes one compressed partition.
    # ‏CAPTURE_SINK=discard (#715): הדיסק נקרא ומגובב בדיוק כמו בקליטה —
    # אותם שערים, אותו מניפסט — אבל אף בייט לא יוצא מהמכונה; מחשב הבנייה
    # משדר אותם בעצמו בקריאה השנייה (directsend.sh).
    [ "${CAPTURE_SINK:-upload}" = discard ] && { cat "$1" > /dev/null; return; }
    # ‏-T ולא --data-binary: ‏--data-binary קורא את כל ה-FIFO לזיכרון כדי
    # לחשב Content-Length — מחיצה גדולה מה-RAM נהרגת ב-OOM (‏#15). ‏-T
    # מזרים ב-chunked. ‏--max-time 0 = בלי תקרת משך (100GB לוקחים זמן),
    # אבל עם תקרת חוסר-התקדמות: חיבור שנפל באמצע יוצא, לא נתלה.
    curl -sfS --max-time 0 --speed-limit 1 --speed-time "$HTTP_STALL_TIMEOUT" \
        -H "Content-Type: application/octet-stream" \
        -H "X-Imagesrv-Task-Token: ${TASK_TOKEN:-}" \
        -T "$1" "$SERVER/api/v1/capture/$2/files/$3"
}

capture_space_preflight() {
    # $1 task, $2 disk, $3 partitions, $4 sector size. Read-only, before shrink.
    # Direct send stores no image on the server and needs no server capacity.
    CAPTURE_SPACE_ERROR="לא נבדק: מקום פנוי בשרת — בדיקת הקליטה לא התקבלה"
    CAPTURE_SPACE_CODE=network_server   # #2052: no answer = the cable/server step; an answer = its own reason (unknown: IT)
    CAPTURE_SPACE_WARNING=""
    [ "${CAPTURE_SINK:-upload}" = discard ] && return 0
    _cs_parts=""
    while IFS='|' read -r _cs_i _cs_guid _cs_u _cs_start _cs_size; do
        _cs_node=$(partition_node "$2" "$_cs_i"); _cs_fs=$(_fs_of "$_cs_node")
        if [ "$_cs_fs" = swap ] || [ "$(_partition_role "$_cs_guid")" = swap ]; then
            _cs_used=0
        elif [ "$(partclone_for_fs "$_cs_fs")" = partclone.dd ]; then
            # Raw capture streams every byte, including MSR/unknown filesystems.
            _cs_used=$((_cs_size * $4))
        else
            _cs_used=$(_used_bytes "$_cs_node" "$_cs_fs")
        fi
        cd_used "$_cs_i" "$_cs_used" 2>/dev/null || :   # #1505
        case "$_cs_used" in ''|*[!0-9]*) _cs_used=null ;; esac
        _cs_parts="$_cs_parts${_cs_parts:+,}{\"index\":$_cs_i,\"used_bytes\":$_cs_used}"
    done < "$3"
    printf '{"partitions":[%s]}\n' "$_cs_parts" > "$RUN_DIR/capture-space.json"
    rm -f "$RUN_DIR/capture-space.head"
    http_post_json "$SERVER/api/v1/capture/$1/preflight" "$RUN_DIR/capture-space.json" \
        "X-Imagesrv-Task-Token: ${TASK_TOKEN:-}" "$RUN_DIR/capture-space.head" > "$RUN_DIR/capture-space.reply" || {
        # ‏#1916: ‏404 הוא "no such open task" של task_for — המשימה נסגרה בשרת (בוטלה,
        # או נסגרה בדיווח), והמקום מעולם לא היה השאלה. רק שורת הסטטוס היא ראיה;
        # כל כשל אחר — כולל כותרות שלא נכתבו — נשאר "לא נבדק" (עיקרון 5).
        case "$(grep '^HTTP/' "$RUN_DIR/capture-space.head" 2>/dev/null | tail -n 1)" in
            "HTTP/"*" 404"*) CAPTURE_SPACE_ERROR="המשימה נסגרה בשרת לפני בדיקת המקום — הקליטה לא התחילה"; CAPTURE_SPACE_CODE=unknown ;;
        esac
        return 1; }
    CAPTURE_SPACE_CODE=unknown   # the server answered: a refusal is not the cable
    _cs_status=$(json_get "$RUN_DIR/capture-space.reply" .status)
    _cs_reason=$(json_get "$RUN_DIR/capture-space.reply" .reason)
    case "$_cs_reason" in ''|null) return 1 ;; esac
    case "$_cs_status" in
        ok) return 0 ;;
        warning)
            CAPTURE_SPACE_WARNING="$_cs_reason"
            log "$_cs_reason"
            target_set "$2" waiting "$_cs_reason"
            return 0 ;;
        refused|unchecked) CAPTURE_SPACE_ERROR="$_cs_reason"; return 1 ;;
        *) return 1 ;;
    esac
}

capture_stage() {
    # #980 section 4: the stage the console shows (ntfs_check, shrink, stream,
    # restore_size, verify); build_progress carries it in the task report.
    printf '%s\n' "$1" > "$RUN_DIR/stage"
}

upload_manifest() {
    # $1 = task id, $2 = manifest path. #1470: the server answers 202 after its
    # cheap checks (every file arrived, the sha256 it computed while receiving
    # matches) and re-reads every file in the background; the image enters the
    # library only when that passes. This machine is released here.
    capture_stage verify
    # #2213: the manifest may close the task immediately. Deliver its warning
    # while the task is still open, before that PUT can win the reporter race.
    if [ -s "$RUN_DIR/src_unhib" ]; then
        report_final "${_ppid:-}" "" "$MAC" "$SERVER" "$1" || {
            printf '%s\n' network_server > "$RUN_DIR/capture.reason"; return 1; }
    fi
    curl -sfS -X PUT -H "Content-Type: application/json" \
        -H "X-Imagesrv-Task-Token: ${TASK_TOKEN:-}" \
        --data-binary "@$2" \
        "$SERVER/api/v1/capture/$1/manifest" >> "$LOG_FILE" 2>&1 || {
        printf '%s\n' network_server > "$RUN_DIR/capture.reason"; return 1; }
}
