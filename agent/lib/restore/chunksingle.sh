# chunksingle.sh -- the GUI's single restore (#706) and a station's pull, through the
# chunk engine over HTTP (#3171). POSIX sh (busybox ash).
#
# The server names the engine in the pull's answer (`engine`, pull.sh); run_restore
# hands every partition here when it is "chunk". With a format-2 image a partition
# is fetched as ONE HTTP Range -- from its first frame to its last: they lie back
# to back in the file (format-spec 8) -- into one `chunkcast apply`, the writer of
# the drawers' chunk path (chunkfetch.sh). Each frame is checked against the index
# (whose own XXH64 is the manifest's) before it is written, and nothing is durable
# before a flush. Done is positive evidence only: chunkcast's state line for the
# node holds every chunk. The read-back after the write is run_restore's, as for
# every engine. A chunk failure never falls back to the legacy writer.
#
# The legacy writer stays for swap (mkswap, nothing to fetch) and for an image that
# is not format 2 -- said in the log, never silently.

chunk_single_enabled() {   # $1 = mode, $2 = manifest
    [ "$1" = unicast ] && [ "${PULL_ENGINE:-http}" = chunk ] &&
        [ "$(json_get "$2" '.image_format')" = 2 ]
}

single_partition() {
    # restore_partition's nine arguments, then the manifest. Sets run_restore's RESTORE_ERROR / RESTORE_BLAME.
    RESTORE_ERROR=""
    if is_swap_partition "$6" || ! chunk_single_enabled "$1" "${10}"; then
        [ "$1" != unicast ] || is_swap_partition "$6" ||
            log "partition $5: the server chose the chunk engine and this image is not format 2 -- the legacy writer (#3171)"
        restore_partition "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9"; return
    fi
    next_stream_ceiling   # out here: the count of streams is run_restore's, and the subshell below would lose it (#957)
    rm -f "$RUN_DIR/targets/$4/chunk.error" "$RUN_DIR/targets/$4/chunk.blame"
    chunk_single_partition "$3" "$2" "$4" "$5" "$6" "$7" "${10}" && return 0
    RESTORE_ERROR=$(cat "$RUN_DIR/targets/$4/chunk.error" 2>/dev/null)
    [ -n "$RESTORE_ERROR" ] || RESTORE_ERROR="מחיצה $5 על $4: הכתיבה בחלקים נכשלה בלי ראיה להשלמה"
    RESTORE_BLAME=$(cat "$RUN_DIR/targets/$4/chunk.blame" 2>/dev/null)   # stream = no ATA evidence against the disk (#1598)
    log "$RESTORE_ERROR"; return 1
}

chunk_single_partition() (
    # $1=image $2=server $3=disk $4=partition $5=fs $6=file $7=manifest. A subshell: the
    # chunk helpers' globals (CHUNK_DIR / CHUNK_STATE / CHUNK_IDX) never reach a round.
    _cs_disk=$3; _cs_part=$4; _cs_fs=$5; _cs_t="$RUN_DIR/targets/$3"; _cs_url="$2/api/v1/images/$1/files/$6"
    CHUNK_DIR="$RUN_DIR/chunk/single.$3.p$4"; CHUNK_IDX="$CHUNK_DIR/index"; CHUNK_STATE="$CHUNK_DIR/state"
    chunk_loss_tell() { :; }   # a unicast HTTP restore has no multicast drops to report (chunkloss.sh is the drawers' pass)
    _cs_fail() { printf 'מחיצה %s על %s: %s\n' "$_cs_part" "$_cs_disk" "$*" > "$_cs_t/chunk.error"; exit 1; }
    _cs_stream() { echo stream > "$_cs_t/chunk.blame"; _cs_fail "$@"; }   # the stream's fault, not the disk's
    # nothing of an earlier job is evidence of this one: its state, its base, its ledger
    { rm -rf "$CHUNK_DIR" && mkdir -p "$CHUNK_DIR" "$_cs_t/ledger" && rm -f "$_cs_t/ledger/durable.$4"; } ||
        _cs_fail 'לא ניתן לנקות את המצב של כתיבה קודמת'
    command -v chunkcast >/dev/null 2>&1 || _cs_fail 'אין chunkcast ב-initramfs הזה'
    assert_target "$3" || _cs_fail 'הדיסק אינו זה שנבחר (#2434)'
    _cs_node=$(partition_node "$3" "$4")
    node_is_block "$_cs_node" || _cs_fail "$_cs_node אינו התקן בלוקים"
    _cp_xxh=$(jq -er --argjson i "$4" '.partitions[] | select(.index == $i) | .index_xxh64' "$7" 2>> "$LOG_FILE") ||
        _cs_fail 'למחיצה אין index_xxh64 במניפסט'
    http_get "$2/api/v1/images/$1/index/$4" > "$CHUNK_IDX" 2>> "$LOG_FILE" || _cs_stream 'האינדקס של המחיצה לא הורד מהשרת'
    _cs_got=$(fanout --xxh64 < "$CHUNK_IDX") || _cs_fail 'ה-XXH64 של האינדקס לא חושב'
    [ "$_cs_got" = "$_cp_xxh" ] || _cs_stream 'האינדקס שהגיע אינו תואם למניפסט'
    _cs_total=$(awk 'NR == 1 { print $9 }' "$CHUNK_IDX")
    case "$_cs_total" in ''|*[!0-9]*|0) _cs_stream 'מספר החלקים באינדקס אינו חוקי' ;; esac
    # one range: every frame where the one before it ended, and as many as the header says
    _cs_range=$(awk -v total="$_cs_total" 'NR == 1 { next } NR == 2 { lo = $1 } NR > 2 && $1 != at { bad = 1; exit }
        { at = $1 + $2; n++ } END { if (bad || n < 1 || n != total) exit 1; printf "%.0f-%.0f", lo, at - 1 }' "$CHUNK_IDX") ||
        _cs_stream 'החלקים באינדקס אינם רצופים או חסרים'
    _cs_kind=$(awk 'NR == 1 { print $3 }' "$CHUNK_IDX")
    # the arguments first (chunkrecv.sh: the one place they are built, from $_cp_xxh -- #3064); the head file is named here, fetched below
    if [ "$_cs_kind" = partclone ]; then chunk_image_args "$CHUNK_IDX" "--head $CHUNK_DIR/head"; else chunk_image_args "$CHUNK_IDX"; fi
    # #3332: no chunk is written, and no range is asked for, without its BLAKE3 -- the list is required, never XXH64 alone (chunkb3.sh adds it to the arguments)
    chunk_b3_fetch "$2" "$1" "$4" "$7" || _cs_stream "$CHUNK_B3_WHY"
    if [ "$_cs_kind" = partclone ]; then
        _cs_head=$(awk 'NR == 1 { print $6 }' "$CHUNK_IDX")
        case "$_cs_head" in ''|*[!0-9]*|0) _cs_stream 'אורך מפת המחיצה באינדקס אינו חוקי' ;; esac
        curl -sfS --max-time "$HTTP_TIMEOUT" -r "0-$((_cs_head - 1))" "$_cs_url" > "$CHUNK_DIR/head" 2>> "$LOG_FILE" ||
            _cs_stream 'מפת המחיצה לא הורדה מהשרת'
    fi
    printf '0-%s\n' "$((_cs_total - 1))" > "$CHUNK_DIR/ranges" || _cs_fail 'רשימת החלקים לא נכתבה'
    { assert_target "$_cs_disk" && node_is_block "$_cs_node"; } || _cs_fail 'הדיסק השתנה לפני הכתיבה (#2434)'
    target_counter "$_cs_disk" "$_cs_t/feed.bytes" || _cs_fail 'מונה ההתקדמות לא נפתח'
    # The URL on curl's own command line: "ביטול השחזור" finds the stream by it (sr_stop_stream, srgui.sh).
    # shellcheck disable=SC2086 # the image arguments are a word list on purpose
    { curl -sSf -r "$_cs_range" --connect-timeout "$HTTP_TIMEOUT" --speed-limit 1 --speed-time "$WAIT_STREAM_STALL_S" \
          "$_cs_url" 2>> "$LOG_FILE"; echo "$?" > "$CHUNK_DIR/fetch.rc"; } |
        chunkcast apply --part "$_cs_part" $CHUNK_IMAGE_ARGS --state "$CHUNK_STATE" --chunks-file "$CHUNK_DIR/ranges" \
            "$_cs_node" > "$CHUNK_DIR/apply.out" 2> "$CHUNK_DIR/apply.err" &
    _cs_pid=$!
    CHUNK_OUT="$CHUNK_DIR/apply.out" CHUNK_TELL="" _chunk_run "ההורדה של מחיצה $_cs_part על $_cs_disk" "$_cs_pid" "$_cs_part" "$_cs_disk"
    _cs_rc=$?
    _chunk_counters "$_cs_part" "$_cs_disk"   # the last word: the progress counter and the report's ledger
    cat "$CHUNK_DIR/apply.err" >> "$LOG_FILE"
    _cs_line=$(_chunk_line "$_cs_node")   # chunkcast's own state: the one evidence of what is durable
    if [ "$_cs_rc" != 0 ] || [ "$_cs_line" != "$_cs_node ok $_cs_total 0-$((_cs_total - 1))" ]; then
        # apply's word first: a disk that failed makes curl fail too (its pipe closed), and is not the network
        _cs_bad=$(grep 'does not match the index' "$CHUNK_DIR/apply.err" | tail -n 1)
        _cs_http=$(cat "$CHUNK_DIR/fetch.rc" 2>/dev/null)
        case "$_cs_rc" in
            5) _cs_fail "$(_chunk_why "$_cs_line")" ;;
            6) _cs_stream 'הכתיבה נעצרה לפני שהושלמה' ;;
            8) [ -z "$_cs_bad" ] || _cs_stream "חלק שהגיע מהשרת אינו תואם לאינדקס ולא נכתב: ${_cs_bad#chunkcast: }"
               _cs_stream "ההורדה מהשרת נקטעה (curl rc=${_cs_http:-?})" ;;
            99) _cs_fail 'פג הזמן: הכתיבה לא הסתיימה ולא התקדמה' ;;
            0) _cs_fail "היומן של הדיסק אינו שלם: ${_cs_line:-אין שורה במצב של chunkcast}" ;;
            *) _cs_fail "הכתיבה בחלקים נכשלה (rc=$_cs_rc, curl rc=${_cs_http:-?})" ;;
        esac
    fi
    case "$(cat "$_cs_t/state" 2>/dev/null)" in writing) ;; *) _cs_fail "הדיסק נכשל בזמן הכתיבה: $(head -n 1 "$_cs_t/error" 2>/dev/null)" ;; esac
    ntfs_boot_target "$_cs_disk" "$_cs_part" "$_cs_fs" || _cs_fail "${NTFS_BOOT_ERROR:-תיקון אתחול ה-NTFS נכשל}"
    target_partition_done "$_cs_disk"
)
