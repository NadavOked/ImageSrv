# resume.sh -- #2528 stage 4: a cloner that went down mid-round goes on from what is already on its drawers.
# POSIX sh (busybox ash).
#
# Until here a cloner that lost power mid-round and was answered "כתוב מחדש" wrote every drawer from chunk 0
# (metal 14/11, cloner 2: down at 9,172 of 18,254 chunks of the Windows partition; the rewrite took 35 minutes).
# After a reboot nothing of the write is left here -- $RUN_DIR is tmpfs -- but the disk is: its table, the #1798
# mark "the write did not finish", and the chunks that reached the platter. Three steps:
#
#   1. resume_round, before any disk I/O of the round: for every drawer that carries the mark, the server is told
#      its serial and size and the rate at which it reads (a read of RESUME_PROBE_MIB, uncached, through
#      XXH64 -- the read-back's own work -- measured here, never assumed). The server's brain answers per disk,
#      by its ledger: resume or restart (server/brain/resume.py), and with it the ceiling of the read-back (#3670:
#      the brain's number, not one kept here). Only "resume" AND "enforced" AND a ceiling is acted on --
#      shadow, no answer, no ceiling, no mark, no serial: the drawer is written from the start, as before.
#   2. a drawer that resumes skips the two steps that destroy what is on it: the TRIM (discard.sh) and the write
#      check (writecheck.sh writes 4MiB at the start, the middle and the end). Its table is written again as
#      always: the same manifest and the same disk give the same table, and sgdisk writes only the table.
#   3. resume_seed, per partition, after its index arrived: every chunk is read back from the partition and its
#      XXH64 compared with the index (`fanout --readback -x`, the read-back's own reader). What matches is
#      chunkcast's state before the first pass -- chunkcast writes only the rest, and tells the server with
#      `listening` (chunkledger.sh), so the server sends only what is missing (chunksend.py `_first_pass`).
#      The ledger is NOT believed: it only says how much the server expected; the read-back is the evidence.
#
# Principle 5: a read-back that failed, ended early, or was not finished within its ceiling = that partition
# is written from the start. Never "all written".
#
# #3670: what is decided is the brain's -- resume or restart, how long the read-back may take, and "not measured" =
# restart (the measurement goes up as null). What stays here is the SHAPE of the one measurement, which comes before the
# question and so cannot come from its answer: how much is read, and how long it may take. It decides nothing.

RESUME_PROBE_MIB="${RESUME_PROBE_MIB:-64}"
RESUME_PROBE_S="${RESUME_PROBE_S:-30}"     # a disk that does not give RESUME_PROBE_MIB in this is not measured: the rate goes up as null

resume_held() { [ -e "$RUN_DIR/targets/$1/resume" ]; }   # this drawer goes on from what is on it

_rs_ceiling() {   # $1 = disk, $2 = bytes of the partition -> the seconds its read-back may take, by the BRAIN's numbers (answer.readback)
    _rc_a=$(jq -r --arg s "$(disk_serial "$1")" '.targets[]? | select(.serial == $s) | .readback | select(type == "object")
        | [.min_mbps, .slack_s] | select(all(.[]; type == "number" and . > 0 and . == floor)) | "\(.[0]) \(.[1])"' \
        "$RUN_DIR/resume.answer.json" 2> /dev/null)
    case "${_rc_a% *}${_rc_a#* }" in ''|*[!0-9]*) return 0 ;; esac   # no numbers, or not whole ones = the brain gave no ceiling: nothing
    echo "$(($2 / (${_rc_a% *} * 1000000) + ${_rc_a#* }))"
}

_rs_read_mbps() {   # $1 = disk -> MB/s of an uncached read through XXH64; nothing = not measured (or not in RESUME_PROBE_S)
    _rp_f="$RUN_DIR/resume.probe.$1"; rm -f "$_rp_f" "$_rp_f.dd" "$_rp_f.done"
    ( _rp_t0=$(cut -d' ' -f1 "$SYSROOT/proc/uptime" 2> /dev/null)
      { dd if="$DEVROOT/$1" iflag=direct bs=4194304 count=$((RESUME_PROBE_MIB / 4)) 2>> "$LOG_FILE"
        echo "$?" > "$_rp_f.dd"; } | fanout --xxh64 > /dev/null 2>> "$LOG_FILE" || { : > "$_rp_f.done"; exit 0; }
      [ "$(cat "$_rp_f.dd" 2> /dev/null)" = 0 ] || { : > "$_rp_f.done"; exit 0; }
      awk -v a="$_rp_t0" -v b="$(cut -d' ' -f1 "$SYSROOT/proc/uptime" 2> /dev/null)" -v n=$((RESUME_PROBE_MIB * 1048576)) \
          'BEGIN { if (a ~ /^[0-9]+\.[0-9]+$/ && b ~ /^[0-9]+\.[0-9]+$/ && b + 0 > a + 0) printf "%d\n", n / (b - a) / 1000000 }' > "$_rp_f"; : > "$_rp_f.done" ) &
    _rp_p=$!; _rp_n=0
    while [ ! -e "$_rp_f.done" ] && kill -0 "$_rp_p" 2> /dev/null && [ "$_rp_n" -lt "$RESUME_PROBE_S" ]; do sleep 1; _rp_n=$((_rp_n + 1)); done
    if [ ! -e "$_rp_f.done" ]; then kill "$_rp_p" 2> /dev/null; log "המגירה $1: מדידת הקריאה לא הסתיימה תוך $RESUME_PROBE_S שניות -- לא נמדדה" >&2; return 0; fi   # >&2: the caller reads stdout
    cat "$_rp_f" 2> /dev/null
}

resume_round() {   # $1 = mode, $2 = server, $3 = image, $4 = manifest, $5.. = the round's drawers. RESUME_REST = the others
    _ru_mode="$1"; _ru_srv="$2"; _ru_m="$4"; shift 4; RESUME_REST="$*"
    for _ru_d in "$@"; do rm -f "$RUN_DIR/targets/$_ru_d/resume"; done
    [ "$_ru_mode" = chunk ] || return 0   # only the chunk engine reads a partition back chunk by chunk
    _ru_bytes=$(jq '[.partitions[] | select((.readback_chunks | type) == "number") | .readback_chunks] | add // 0' "$_ru_m" 2>> "$LOG_FILE")
    case "$_ru_bytes" in ''|*[!0-9]*|0) return 0 ;; esac
    _ru_bytes=$((_ru_bytes * 4194304)); _ru_t=""; _ru_c=""
    for _ru_d in "$@"; do
        [ "$(writemark_state "$_ru_d")" = incomplete ] || continue   # no mark: nothing of ours was cut on it
        _ru_s=$(disk_serial "$_ru_d"); _ru_z=$(blockdev --getsize64 "$DEVROOT/$_ru_d" 2>> "$LOG_FILE")
        [ -n "$_ru_s" ] || continue; case "$_ru_z" in ''|*[!0-9]*) continue ;; esac
        _ru_r=$(_rs_read_mbps "$_ru_d")   # what is read goes up as it is: how long that takes is the brain's to compute (#3670)
        case "$_ru_r" in ''|*[!0-9]*|0) _ru_r=null ;; esac
        log "המגירה $_ru_d נושאת את סימן הכתיבה שלא הושלמה: קריאה ${_ru_r}MB/s -- שואלים את השרת אם להמשיך"
        _ru_t="$_ru_t${_ru_t:+,}{\"dev\":\"$_ru_d\",\"serial\":\"$(json_escape "$_ru_s")\",\"size_bytes\":$_ru_z,\"read_mbps\":$_ru_r,\"read_bytes\":$_ru_bytes}"
        _ru_c="$_ru_c $_ru_d"
    done
    [ -n "$_ru_c" ] || return 0
    printf '{"mac":"%s","session_id":"%s","targets":[%s]}' "$MAC" "$CHUNK_SESSION" "$_ru_t" > "$RUN_DIR/resume.body.json"
    if ! http_post_json "$_ru_srv/api/v1/agent/resume" "$RUN_DIR/resume.body.json" > "$RUN_DIR/resume.answer.json" 2>> "$LOG_FILE" \
            || [ "$(jq -r '.ok' "$RUN_DIR/resume.answer.json" 2> /dev/null)" != true ]; then
        log "השרת לא ענה על ההמשך -- כל המגירות נכתבות מההתחלה"; rm -f "$RUN_DIR/resume.answer.json"; return 0
    fi
    for _ru_d in $_ru_c; do
        _ru_s=$(disk_serial "$_ru_d")
        _ru_a=$(jq -r --arg s "$_ru_s" '.targets[]? | select(.serial == $s) | "\(.verdict) \(.enforced) \(.reason)"' "$RUN_DIR/resume.answer.json" 2> /dev/null)
        case "$_ru_a" in
            "resume true "*)
                [ -n "$(_rs_ceiling "$_ru_d" 1)" ] || { log "המגירה $_ru_d נכתבת מההתחלה: המוח לא נתן תקרה לקריאה החוזרת"; continue; }
                # the gates of write_check_round that write nothing (writecheck.sh): a disk too small or a plan refused fails here, by name
                target_set "$_ru_d" writing
                if disk_fits "$_ru_d" "$_ru_m" && plan_gate "$_ru_d" "$_ru_m"; then :; else continue; fi
                : > "$RUN_DIR/targets/$_ru_d/resume"
                RESUME_REST=$(for _ru_x in $RESUME_REST; do [ "$_ru_x" = "$_ru_d" ] || printf '%s ' "$_ru_x"; done)
                log "המגירה $_ru_d ממשיכה ממה שכבר עליה: ${_ru_a#resume true }" ;;
            "resume false "*) log "המגירה $_ru_d נכתבת מההתחלה (המוח במצב צל היה ממשיך: ${_ru_a#resume false })" ;;
            "restart "*) log "המגירה $_ru_d נכתבת מההתחלה: ${_ru_a#restart * }" ;;
            *) log "המגירה $_ru_d נכתבת מההתחלה: השרת לא אמר עליה דבר" ;;
        esac
    done
}

_rs_verified() {   # $1 = index, $2 = the read-back's list -> "<count> <ranges|->" of the chunks whose XXH64 is the index's
    awk 'NR == FNR { if (FNR > 1) want[FNR - 2] = $3; next }
         $1 == "c" && ($2 in want) && $3 == want[$2] { ok[$2 + 0] = 1 }
         END { n = 0; out = ""; k = -1
               for (i = 0; i in want; i++) {
                   if (i in ok) { n++; if (k < 0) k = i }
                   else if (k >= 0) { out = out (out == "" ? "" : ",") k "-" (i - 1); k = -1 } }
               if (k >= 0) out = out (out == "" ? "" : ",") k "-" (i - 1)
               print n, (out == "" ? "-" : out) }' "$1" "$2"
}

resume_seed() {   # $1 = partition index, $2 = its index XXH64, $3.. = disks. chunk_partition_drawers' CHUNK_* are set
    _rd_i="$1"; _rd_x="$2"; shift 2
    _rd_kind=$(awk 'NR == 1 { print $3 }' "$CHUNK_IDX"); _rd_bytes=$(awk 'NR == 1 { print $8 }' "$CHUNK_IDX")
    _rd_n=$(awk 'NR == 1 { print $9 }' "$CHUNK_IDX"); _rd_jobs=""; _rd_ceil=0
    for _rd_d in "$@"; do
        resume_held "$_rd_d" || continue
        case "$(cat "$RUN_DIR/targets/$_rd_d/state" 2> /dev/null)" in failed|cancelled) continue ;; esac
        _rd_c=$(_rs_ceiling "$_rd_d" "$_rd_bytes")
        [ -n "$_rd_c" ] || { log "מחיצה $_rd_i: המוח לא נתן ל-$_rd_d תקרה לקריאה החוזרת -- המחיצה נכתבת בה מההתחלה"; continue; }
        [ "$_rd_c" -le "$_rd_ceil" ] || _rd_ceil=$_rd_c   # one wait for the partition: the longest ceiling of its drawers
        _rd_o="$CHUNK_DIR/resume.$_rd_d.$_rd_i"; rm -f "$_rd_o" "$_rd_o.rc"
        _rd_node=$(partition_node "$_rd_d" "$_rd_i")
        if [ "$_rd_kind" = partclone ]; then   # the image's own map: the one on the disk may be half written
            ( zstd -dc < "$CHUNK_DIR/p$_rd_i.head" | fanout --readback -x "$_rd_node" > "$_rd_o" 2>> "$LOG_FILE"; echo "$?" > "$_rd_o.rc" ) &
        else
            ( fanout --readback -x "$_rd_node" "$_rd_bytes" > "$_rd_o" 2>> "$LOG_FILE"; echo "$?" > "$_rd_o.rc" ) &
        fi
        _rd_jobs="$_rd_jobs $_rd_d:$!"
    done
    [ -n "$_rd_jobs" ] || return 0
    log "מחיצה $_rd_i: קריאה חוזרת של מה שכבר נכתב, מול האינדקס (XXH64), במגירות:$(for _rd_j in $_rd_jobs; do printf ' %s' "${_rd_j%%:*}"; done)"
    _rd_t=0
    while [ "$_rd_t" -lt "$_rd_ceil" ]; do   # one second at a time: the watchdog is fed while the disks are read
        _rd_on=0; for _rd_j in $_rd_jobs; do   # its rc file is its end: a pid that answers may be one not yet collected
            if [ ! -e "$CHUNK_DIR/resume.${_rd_j%%:*}.$_rd_i.rc" ] && kill -0 "${_rd_j#*:}" 2> /dev/null; then _rd_on=1; fi; done
        [ "$_rd_on" = 1 ] || break
        if command -v _chunk_beat > /dev/null 2>&1; then _chunk_beat; fi; sleep 1; _rd_t=$((_rd_t + 1))
    done
    _rd_lines=""
    for _rd_j in $_rd_jobs; do
        _rd_d="${_rd_j%%:*}"; _rd_o="$CHUNK_DIR/resume.$_rd_d.$_rd_i"
        [ -e "$_rd_o.rc" ] || { kill "${_rd_j#*:}" 2> /dev/null; log "מחיצה $_rd_i: הקריאה החוזרת של $_rd_d לא הסתיימה תוך $_rd_ceil שניות"; }
        _rd_led=$(jq -r --arg s "$(disk_serial "$_rd_d")" --arg p "$_rd_i" '.targets[]? | select(.serial == $s) | .ledger[$p] // "אין"' "$RUN_DIR/resume.answer.json" 2> /dev/null)
        if [ "$(cat "$_rd_o.rc" 2> /dev/null)" != 0 ] || [ "$(awk '$1 == "n" { print $2 }' "$_rd_o" 2> /dev/null)" != "$_rd_n" ]; then
            log "מחיצה $_rd_i: לא הצלחנו לקרוא בחזרה את $_rd_d (rc=$(cat "$_rd_o.rc" 2> /dev/null || echo לא-נרשם)) -- המחיצה נכתבת בה מההתחלה"; continue
        fi
        _rd_v=$(_rs_verified "$CHUNK_IDX" "$_rd_o") || _rd_v=""
        case "$_rd_v" in [0-9]*" "*) ;; *) log "מחיצה $_rd_i: ההשוואה של $_rd_d מול האינדקס לא רצה -- המחיצה נכתבת בה מההתחלה"; continue ;; esac
        log "מחיצה $_rd_i: במגירה $_rd_d אומתו ${_rd_v%% *} מתוך $_rd_n חתיכות (ה-ledger בשרת אמר ${_rd_led:-אין}); השאר נכתב"
        [ "${_rd_v%% *}" = 0 ] || _rd_lines="$_rd_lines$(partition_node "$_rd_d" "$_rd_i") ok $_rd_v
"
    done
    [ -n "$_rd_lines" ] || return 0
    printf 'chunkcast-state 1 %s %s %s\n%s' "$_rd_x" "$_rd_i" "$_rd_n" "$_rd_lines" > "$CHUNK_STATE.new" && mv "$CHUNK_STATE.new" "$CHUNK_STATE" \
        || { rm -f "$CHUNK_STATE" "$CHUNK_STATE.new"; log "מחיצה $_rd_i: קובץ המצב לא נכתב -- המחיצה נכתבת מההתחלה"; return 0; }
    _chunk_counters "$_rd_i" "$@"   # the counters, and the ledger `listening` carries to the server (chunkledger.sh)
}
