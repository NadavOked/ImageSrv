# directchunk.sh -- the build machine as the SOURCE of a chunk-engine wave (#3161).
# POSIX sh (busybox ash). No bashisms.
#
# directsend.sh is the task; this file sends the disk through the chunk engine
# (#2533), the only engine (#3173): the server says so (task.direct.engine = "chunk").
# The server stays the only orchestrator -- its ChunkEngine, the receivers'
# chunk-pass, the repair plan, the ledger -- and THIS machine is its sender:
#
#   read 1               capture_disk with the sink below: the manifest, the chunk
#                        index of every partition (chunkcut: XXH64 a 4MiB chunk) and
#                        the beginning of its stream, where the head frame is.
#   direct_chunk_offer   before the manifest: every index and every head frame go to
#                        the server. It refuses a manifest whose index_xxh64 is not
#                        the index it was given, and the receivers check both again.
#   direct_chunk_run     the wave. The server hands out one ORDER a pass (partition,
#                        pass, rates, a repair's chunk list) in the hello this machine
#                        sends anyway. An order is one more read of the partition:
#                          partclone | chunkcut | fanout (sha256, counter) | chunkcast send --stdin
#                        and its verdict is told to the server (chunk-sent).
#
# A pass is "sent" only when every stage exited 0 AND the sha256 of what was read
# is the manifest's: a disk that changed between the reads fails HERE, by name, and
# not as five repair rounds on every drawer in the room (principle 5). chunkcast
# itself refuses a stream whose frames are not where read 1's index put them.
# Nobody waits for a receiver: what one of them lacks comes back as the next
# order's chunk list. There is no HTTP fetch in a direct send -- the server holds
# no file -- so every repair is another read of this disk.

DIRECT_HEAD_KEEP="${DIRECT_HEAD_KEEP:-67108864}"   # bytes of every partition's stream that read 1 keeps in RAM: the head frame is inside them
DIRECT_CHUNK_BEAT_S="${DIRECT_CHUNK_BEAT_S:-15}"   # "still sending", told to the server this often while a pass runs

direct_head_keep() {
    # $1 = fifo, $2 = the partition's file name. capture_sink's whole body when
    # nothing is uploaded (CAPTURE_SINK=discard): no byte leaves the machine, and
    # the first bytes are kept for direct_chunk_offer. A kept file that came out
    # short (no RAM) is found there, by its length -- never sent as a head.
    { head -c "$DIRECT_HEAD_KEEP" > "$RUN_DIR/dhead.$2"; cat > /dev/null; } < "$1"
}

_direct_put() {   # $1 = url, $2 = file, $3 = content type. The task's token, as the manifest's PUT
    curl -sfS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" -X PUT -H "Content-Type: $3" \
        -H "X-Imagesrv-Task-Token: ${TASK_TOKEN:-}" --data-binary "@$2" "$1" >> "$LOG_FILE" 2>&1
}

direct_chunk_offer() {
    # $1 = task, $2 = disk, $3 = the live manifest. 0 = the server took every index
    # (or the hello failed: nothing is offered, and the server refuses the wave by
    # name). 1 = said: a task the server did not hand to the chunk engine is
    # refused here by name (#3173) -- there is no other engine to send with.
    _co_rc=0; _co_ours=0
    send_hello && [ "$(json_get "$RESP" ".task.id")" = "$1" ] && _co_ours=1
    if [ "$_co_ours" = 1 ] && [ "$(json_get "$RESP" ".task.direct.engine")" != chunk ]; then
        _direct_failed "$2" "השרת לא הציע את מנוע החתיכות להפצה הישירה -- לא נשלח דבר"; _co_rc=1
    elif [ "$_co_ours" = 1 ]; then
        manifest_plan "$3" > "$RUN_DIR/dchunk.plan" || : > "$RUN_DIR/dchunk.plan"
        while IFS='|' read -r _co_i _co_g _co_r _co_fs _co_st _co_sz _co_f _co_rest <&3; do
            case "$_co_f" in ''|null) continue ;; esac
            _co_x="$RUN_DIR/idx.$_co_i"
            _direct_put "$SERVER/api/v1/direct/$1/index/$_co_i" "$_co_x" text/plain \
                || { _direct_failed "$2" "מחיצה $_co_i: השרת לא קיבל את אינדקס החתיכות"; _co_rc=1; break; }
            _direct_put "$SERVER/api/v1/direct/$1/b3/$_co_i" "$_co_x.b3" application/octet-stream \
                || { _direct_failed "$2" "מחיצה $_co_i: השרת לא קיבל את רשימת ה-BLAKE3"; _co_rc=1; break; }   # #3332: what every receiver checks against
            [ "$(awk 'NR == 1 { print $3 }' "$_co_x")" = partclone ] || continue
            _co_hz=$(awk 'NR == 1 { print $6 }' "$_co_x")
            case "$_co_hz" in ''|0|*[!0-9]*) _co_hz=0 ;; esac
            head -c "$_co_hz" "$RUN_DIR/dhead.$_co_f" > "$RUN_DIR/dhead.put" 2>> "$LOG_FILE"
            if [ "$_co_hz" -eq 0 ] || [ "$(($(wc -c < "$RUN_DIR/dhead.put")))" -ne "$_co_hz" ]; then
                _direct_failed "$2" "מחיצה $_co_i: מפת המחיצה ($_co_hz בתים) לא נשמרה כולה בקריאה הראשונה — אי אפשר להפיץ אותה במנוע החתיכות"
                _co_rc=1; break
            fi
            _direct_put "$SERVER/api/v1/direct/$1/head/$_co_i" "$RUN_DIR/dhead.put" application/octet-stream \
                || { _direct_failed "$2" "מחיצה $_co_i: השרת לא קיבל את מפת המחיצה"; _co_rc=1; break; }
            cp "$RUN_DIR/dhead.put" "$_co_x.head" 2>> "$LOG_FILE" || :   # #3209: the map, for single chunks (directserve.sh); without it a repair reads the whole partition
        done 3< "$RUN_DIR/dchunk.plan"
    fi
    rm -f "$RUN_DIR"/dhead.*
    return "$_co_rc"
}

_direct_chunk_tell() {
    # $1 = task, $2 = order, $3 = sending|done|failed, $4 = rc, $5 = the reason.
    # The answer is in $RUN_DIR/dchunk.$DCHUNK_TAG.told; 1 = no answer.
    _ct_f="$RUN_DIR/dchunk.${DCHUNK_TAG:-main}"
    printf '{"seq":%s,"state":"%s","rc":%s,"error":"%s"}\n' "$2" "$3" "${4:-0}" "$(json_escape "${5:-}")" > "$_ct_f.tell"
    http_post_json "$SERVER/api/v1/direct/$1/chunk-sent" "$_ct_f.tell" \
        "X-Imagesrv-Task-Token: ${TASK_TOKEN:-}" > "$_ct_f.told" 2>> "$LOG_FILE"
}

_direct_chunk_beat() {
    # $1 = task, $2 = order, $3 = the file that says the pass ended. A subshell of
    # its own: while the pass runs the server hears that it does, and a server that
    # no longer waits for it ("stale": the round closed) stops the read at once.
    DCHUNK_TAG=beat
    while [ ! -e "$3" ]; do
        _direct_chunk_tell "$1" "$2" sending 0 ""; _cb_rc=$?
        # "stale", or curl's 22 (the server answered and refused: the task is closed, or no longer ours). A server
        # that did not answer at all is neither -- the pass goes on, and the server's own silence clock decides.
        if [ "$_cb_rc" -eq 22 ] || [ "$(json_get "$RUN_DIR/dchunk.beat.told" ".stale")" = true ]; then
            : > "$RUN_DIR/dchunk.abort"; return 0
        fi
        _cb_n=0
        while [ "$_cb_n" -lt "$DIRECT_CHUNK_BEAT_S" ] && [ ! -e "$3" ]; do sleep 1; _cb_n=$((_cb_n + 1)); done
    done
}

direct_chunk_pass() {
    # $1 task, $2 disk, $3 partition index, $4 fs, $5 the manifest's sha256 of the
    # partition file, $6 = the order (a JSON file). 0 = the pass went out whole, and
    # what was read is what read 1 promised. Otherwise DCHUNK_WHY and DCHUNK_RC.
    DCHUNK_WHY=""; DCHUNK_RC=1
    _dp_seq=$(json_get "$6" ".seq"); _dp_pass=$(json_get "$6" ".pass"); _dp_key=$(json_get "$6" ".key")
    _dp_to="$(json_get "$6" ".group"):$(json_get "$6" ".port")"
    _dp_kbps=$(json_get "$6" ".kbps"); _dp_wkbps=$(json_get "$6" ".write_kbps")
    for _dp_v in "$_dp_seq" "$_dp_pass" "$_dp_kbps" "$_dp_wkbps" "$(json_get "$6" ".port")"; do
        case "$_dp_v" in ''|0|*[!0-9]*) DCHUNK_WHY="ההזמנה מהשרת אינה קריאה"; return 1 ;; esac
    done
    _dp_list=""
    if [ "$(json_get "$6" ".chunks")" = true ]; then
        # a repair pass: only these chunks. A file, as on the server -- the list may be hundreds of KB
        _dp_list="$RUN_DIR/dchunk.list"
        curl -sfS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" -H "X-Imagesrv-Task-Token: ${TASK_TOKEN:-}" \
            "$SERVER/api/v1/direct/$1/chunks/$_dp_seq" > "$_dp_list" 2>> "$LOG_FILE" && grep -q '^[0-9]' "$_dp_list" \
            || { DCHUNK_WHY="רשימת החתיכות של מעבר ההשלמה לא הורדה מהשרת"; return 1; }
    fi
    _dp_node=$(lc_src_node "$2" "$3"); _dp_pcl=$(partclone_for_fs "$4")   # #1171: the view read 1 read, if it made one
    _dp_ignore=$(capture_pcl_flags "$_dp_pcl" "$4")   # read 1's own flags (#1574)
    _dp_w="$RUN_DIR/dchunk.$3"; _dp_fifo="$_dp_w.fifo"
    if [ -n "$_dp_list" ]; then   # #3209: a repair reads its chunks from their own places (directserve.sh); the whole partition only when that did not go out
        direct_chunk_targeted "$1" "$3" "$_dp_node" && { DCHUNK_RC=0; return 0; }
        [ ! -e "$RUN_DIR/dchunk.abort" ] || { rm -f "$RUN_DIR/dchunk.abort"; DCHUNK_WHY="השרת כבר אינו מחכה למעבר הזה — הגל נסגר"; return 1; }
    fi
    # the first pass of a partition is the task's progress; a repair is not counted over it
    _dp_count="$RUN_DIR/targets/$2/bytes.raw"; [ "$_dp_pass" -eq 1 ] || { _dp_count="$_dp_w.bytes"; : > "$_dp_count"; }
    rm -f "$_dp_fifo" "$_dp_w.out" "$_dp_w.sha" "$_dp_w.send.rc" "$_dp_w.pcl.rc" "$_dp_w.cut.rc" "$_dp_w.fanout.rc" \
        "$_dp_w.ended" "$_dp_w.send.pid" "$RUN_DIR/dchunk.abort"
    mkfifo "$_dp_fifo"
    log "partition $3 ($4): chunk pass $_dp_pass (order $_dp_seq)${_dp_list:+, a repair}"
    # Each stage's rc by name (no pipefail in busybox ash). fanout between the cut and
    # the sender, as in directsend.sh (#1444): the counter and the sha256 of the stream.
    # One reader and no siblings to shield, so its evictions go past the shell's ceiling.
    _dp_evict=$(( (WAIT_STREAM_START_S + WAIT_STREAM_STALL_S + 60) * 1000 ))
    # shellcheck disable=SC2046,SC2086 # partclone_mode's flags and the optional pairs are word lists
    (
        # the sender itself in the background, not a group around it: its pid is the one a closed round stops
        chunkcast send --stdin --to "$_dp_to" --session "$_dp_key" --part "$3" --pass "$_dp_pass" \
            --index "$RUN_DIR/idx.$3" --kbps "$_dp_kbps" --write-kbps "$_dp_wkbps" \
            ${IFACE:+--iface "$IFACE"} ${_dp_list:+--chunks-file "$_dp_list"} \
            < "$_dp_fifo" > "$_dp_w.out" 2>> "$LOG_FILE" &
        _dp_spid=$!; echo "$_dp_spid" > "$_dp_w.send.pid"
        { "$_dp_pcl" $(partclone_mode "$_dp_pcl" -c) $_dp_ignore -s "$_dp_node" \
              -L "$RUN_DIR/targets/$2/partclone.log" 2>> "$LOG_FILE"
          echo "$?" > "$_dp_w.pcl.rc"; } \
            | { capture_cut "$_dp_pcl" "$RUN_DIR/didx.$3" 2>> "$LOG_FILE"   # read 1's own cut, byte for byte
                echo "$?" > "$_dp_w.cut.rc"; } \
            | { FANOUT_STALL_EVICT_MS="$_dp_evict" FANOUT_BLOCK_EVICT_MS="$_dp_evict" fanout -S "$_dp_w.sha" \
                    -P "$_dp_count" 16777216 "$_dp_fifo" > /dev/null 2>> "$LOG_FILE"
                echo "$?" > "$_dp_w.fanout.rc"; }
        # fanout that died before open() leaves the sender blocked in it (#49).
        grep -qx 0 "$_dp_w.fanout.rc" || unblock_fifo "$_dp_fifo"
        # the stream is over: the sender ends by itself (its tail), or is ended at the ceiling -- and says how
        _dp_n=0
        while kill -0 "$_dp_spid" 2>/dev/null && [ "$_dp_n" -lt "$WAIT_DRAWER_S" ]; do sleep 1; _dp_n=$((_dp_n + 1)); done
        if kill -0 "$_dp_spid" 2>/dev/null; then
            log "פג הזמן: chunkcast send of partition $3 לא הסתיים תוך $WAIT_DRAWER_S שניות אחרי סוף הזרם -- נכשל"
            kill -9 "$_dp_spid" 2>/dev/null; echo "$WAIT_TIMED_OUT" > "$_dp_w.send.rc"
        else
            wait "$_dp_spid" 2>/dev/null   # wait-bounded: after-kill-0-gone -- the loop above left once it was gone; this collects its exit code
            echo "$?" > "$_dp_w.send.rc"
        fi
        : > "$_dp_w.ended"
    ) &
    _dp_pid=$!
    ( _direct_chunk_beat "$1" "$_dp_seq" "$_dp_w.ended" ) < /dev/null > /dev/null 2>&1 &
    _dp_bpid=$!; WAIT_ABORT_FILE="$RUN_DIR/dchunk.abort"   # one-shot, read by wait_progress (waits.sh)
    if wait_progress "$_dp_pid" "$_dp_count" "$WAIT_STREAM_START_S" "$WAIT_STREAM_STALL_S" \
            "שידור מחיצה $3 מ-$2 (מעבר $_dp_pass)" "$_dp_fifo"; then
        _dp_stage=none
        for _dp_s in pcl cut fanout send; do   # the first stage that failed is the reason; the ones after it only followed
            DCHUNK_RC=$(cat "$_dp_w.$_dp_s.rc" 2>/dev/null || echo 1)
            [ "$DCHUNK_RC" -eq 0 ] || { _dp_stage="$_dp_s"; break; }
        done
    else
        DCHUNK_RC="$WAIT_TIMED_OUT"; _dp_stage=timeout
        [ ! -e "$RUN_DIR/dchunk.abort" ] || _dp_stage=abort
        # The subshell is gone and its children are not (no job control in POSIX sh). The SENDER is stopped by the
        # pid it was started with -- never looked for by name (#3055) -- so nothing goes on being multicast for a
        # round that is over; the readers before it end on its closed pipe.
        _dp_sp=$(cat "$_dp_w.send.pid" 2>/dev/null)
        case "$_dp_sp" in
            ''|*[!0-9]*) log "chunk pass $_dp_pass: the sender's pid was not recorded -- it is not stopped here" ;;
            *) kill "$_dp_sp" 2>/dev/null || : ;;
        esac
    fi
    : > "$_dp_w.ended"; { kill "$_dp_bpid"; wait "$_dp_bpid"; } 2>/dev/null
    rm -f "$_dp_fifo" "$_dp_fifo.bytes" "$RUN_DIR/dchunk.abort"
    case "$_dp_stage" in
        none) ;;
        abort) DCHUNK_WHY="השרת כבר אינו מחכה למעבר הזה — הגל נסגר"; return 1 ;;
        send) DCHUNK_WHY="השליחה נכשלה (chunkcast rc=$DCHUNK_RC)"
              [ "$DCHUNK_RC" -ne 9 ] || DCHUNK_WHY="מה שנקרא מהדיסק אינו מה שהאינדקס של הקריאה הראשונה מתאר — הדיסק השתנה בין הקריאות"
              return 1 ;;
        *) DCHUNK_WHY="הקריאה מהדיסק נכשלה (שלב=$_dp_stage rc=$DCHUNK_RC)"; return 1 ;;
    esac
    DCHUNK_RC=1
    _dp_got=$(awk '{print $1}' "$_dp_w.sha" 2>/dev/null)
    if ! is_sha256 "$_dp_got"; then
        DCHUNK_WHY="ה-sha256 של מה שנקרא לא חושב — אין ראיה שהזרם תאם למניפסט"; return 1
    elif [ "$_dp_got" != "$5" ]; then
        DCHUNK_WHY="הבייטים שנקראו שונים מהמניפסט (הדיסק השתנה בין הקריאות, או שהצינור אינו דטרמיניסטי)"; return 1
    fi
    grep -q '^done ' "$_dp_w.out" 2>/dev/null \
        || { DCHUNK_WHY="chunkcast לא אמר שהמעבר יצא עד סופו — אין ראיה שנשלח"; return 1; }
    [ "$_dp_pass" -ne 1 ] || target_partition_done "$2"
    DCHUNK_RC=0
}

_direct_chunk_order() {
    # $1 = task, $2 = disk; the order is $RUN_DIR/dchunk.order. 0 = sent, and the
    # server took the verdict. 1 = failed -- told to the server, and said here.
    _do_o="$RUN_DIR/dchunk.order"; _do_seq=$(json_get "$_do_o" ".seq"); _do_part=$(json_get "$_do_o" ".part")
    case "$_do_part" in ''|*[!0-9]*) _do_part=x ;; esac
    _do_line=$(awk -F'|' -v i="$_do_part" '$1 == i && $7 != "" && $7 != "null"' "$RUN_DIR/direct.plan")
    DCHUNK_WHY="ההזמנה מהשרת נוקבת במחיצה שאינה במניפסט"; DCHUNK_RC=1
    if [ -n "$_do_line" ]; then
        _do_fs=$(printf '%s' "$_do_line" | cut -d'|' -f4); _do_sha=$(printf '%s' "$_do_line" | cut -d'|' -f8)
        if direct_chunk_pass "$1" "$2" "$_do_part" "$_do_fs" "$_do_sha" "$_do_o"; then
            _direct_chunk_tell "$1" "$_do_seq" "done" 0 "" && return 0
            DCHUNK_WHY="השרת לא קיבל את הדיווח על סיום המעבר"
        fi
    fi
    _direct_chunk_tell "$1" "$_do_seq" failed "$DCHUNK_RC" "$DCHUNK_WHY" \
        || log "direct chunk: the server did not take the failure of order $_do_seq"
    _direct_failed "$2" "מחיצה $_do_part: $DCHUNK_WHY"
    return 1
}

direct_chunk_run() {
    # $1 = task, $2 = disk, $3 = the live manifest. Called once the wave runs and
    # the hello said engine = chunk. 0 only when the SERVER said every partition is
    # whole on the drawers still alive (chunk.done) -- "this machine sent" is not it --
    # AND the round has closed: until then a drawer pushed back in, or a machine that
    # reported late, completes from this disk (#3209, directserve.sh).
    manifest_plan "$3" > "$RUN_DIR/direct.plan" \
        || { _direct_failed "$2" "לא הצלחנו לקרוא את תוכנית המחיצות מהמניפסט החי"; return 1; }
    _cr_total=$(json_get "$3" ".total_compressed_bytes")
    case "$_cr_total" in ''|*[!0-9]*) _cr_total=0 ;; esac
    target_init "$2" "$_cr_total"; capture_unhib_note "$2" || { _direct_failed "$2" "לא הצלחנו לרשום שההשהיה בוטלה בעותק — המקור לא נכתב"; return 1; }   # target_init clears read 1's warning
    echo "sending" > "$RUN_DIR/state"
    direct_serve_start "$2"
    _direct_chunk_loop "$1" "$2"; _cr_rc=$?
    direct_serve_stop
    return "$_cr_rc"
}

_direct_chunk_loop() {   # $1 = task, $2 = disk: the orders, until the round is over. direct_chunk_run's verdict
    _cr_last=0; _cr_miss=0; _cr_done=0
    while :; do
        if send_hello; then
            _cr_miss=0
            if [ "$(json_get "$RESP" ".task.id")" != "$1" ]; then
                [ "$_cr_done" = 0 ] || { log "direct chunk send complete: the task was closed with its round"; return 0; }
                _direct_failed "$2" "השרת סגר את משימת ההפצה הישירה באמצע"; return 1
            fi
            if [ "$_cr_done" = 0 ] && [ "$(json_get "$RESP" ".task.direct.chunk.done")" = true ]; then
                _cr_done=1; log "direct chunk: the server says every partition is whole -- single chunks are served until the round closes"
            fi
            if [ "$(json_get "$RESP" ".task.direct.session_state")" != running ]; then
                [ "$_cr_done" = 0 ] || { log "direct chunk send complete: the round closed"; return 0; }
                _cr_why=$(json_get "$RESP" ".task.direct.chunk.error"); [ "$_cr_why" != null ] || _cr_why="הגל נסגר"
                _direct_failed "$2" "השרת עצר את ההפצה הישירה: $_cr_why"; return 1
            fi
            _cr_seq=$(json_get "$RESP" ".task.direct.chunk.order.seq")
            case "$_cr_seq" in
                ''|null|*[!0-9]*) ;;   # no open order: the server waits for the receivers, not for us
                *) if [ "$_cr_seq" -gt "$_cr_last" ]; then   # an order is carried out once
                       _cr_last="$_cr_seq"
                       jq -c '.task.direct.chunk.order' "$RESP" > "$RUN_DIR/dchunk.order" 2>> "$LOG_FILE"
                       _direct_chunk_order "$1" "$2" || return 1
                       continue   # the next order may be there already
                   fi ;;
            esac
        else
            _cr_miss=$((_cr_miss + 1))
            [ "$_cr_miss" -lt 30 ] || { _direct_failed "$2" "השרת לא ענה באמצע ההפצה הישירה"; return 1; }
        fi
        sleep "${DIRECT_POLL_S:-2}"
    done
}
