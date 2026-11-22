# chunkrecv.sh -- one partition through the chunk engine (#2533, stage 3).
# POSIX sh (busybox ash).
#
# The drawers' side of server/rounds/chunksend.py, the only multicast engine (#3173):
# drawers.sh prepares the drawers, and this file runs
# one `chunkcast recv` for all the machine's drawers and then does what the
# server answers, until the partition is whole on every live drawer:
#
#   receiver listening        -> POST chunk-pass {state: listening}
#   receiver ended (or stall) -> POST chunk-pass {state: ended, durable ranges}
#   answer: wait              -> ask again (a ceiling; "whole": true resets it --
#                                the server is waiting for OTHER machines, not this one)
#           receive, pass N   -> another receiver, for the repair pass
#           fetch, ranges     -> one curl of all the ranges | one chunkcast apply (chunkfetch.sh)
#           done              -> every live drawer must hold every chunk HERE too
#           closed            -> the round is over; what is not whole fails
#   answer.fail {serial: why} -> that drawer fails, by the server's reason
#
# What is durable is chunkcast's state file and nothing else: a chunk whose
# XXH64 matched the index, written, and covered by a flush that succeeded
# (format-spec 9.1). This file adds no verdict of its own to a chunk -- it only
# refuses to call a drawer done on the server's word alone (principle 5).
#
# Not here, and still true for a chunk round: the read-back after the write
# (readback.sh, run by run_restore_drawers for every engine alike).
# Before EVERY pass and every fetch each drawer is asserted again (#2434: serial,
# WWN, size -- assert_target), and only the ones that are still the disks chosen
# are handed to chunkcast: a drawer pulled and another disk pushed in between two
# passes is not written. "Cancel this disk" (#1386) is carried out at the same
# points, and waits for the pass to end. Inside a pass and a fetch the drawers are
# watched as drawer_watch watches a fanout stream (chunkwatch.sh, metal 13/11).
#
# What is durable is also published for the progress report, in the files stage 2
# reads (chunkledger.sh, format-spec 9.1): targets/<dev>/ledger/durable.<part>.

CHUNK_DIR="${CHUNK_DIR:-$RUN_DIR/chunk}"
CHUNK_ASK_POLL_S="${CHUNK_ASK_POLL_S:-2}"
CHUNK_ASK_FAST_S="${CHUNK_ASK_FAST_S:-0.3}"; CHUNK_ASK_FAST_N="${CHUNK_ASK_FAST_N:-5}"   # #3260: the first questions after "ended" come this fast; then CHUNK_ASK_POLL_S

_chunk_live() {   # $@ = disks -> the ones still being written
    for _cl in "$@"; do
        [ -n "${CHUNK_BG:-}" ] || [ ! -e "$RUN_DIR/targets/$_cl/catchup" ] || continue; case "$(cat "$RUN_DIR/targets/$_cl/state" 2>/dev/null)" in failed|cancelled) ;; *) printf '%s ' "$_cl" ;; esac   # catchup: completing in the background, not the round's (drawerback.sh)
    done
}

_chunk_line() {   # $1 = node -> its line of the state file: "<node> ok|failed <count> <ranges> [reason]"
    awk -v n="$1" '$1 == n { print; found = 1 } END { exit !found }' "$CHUNK_STATE" 2>/dev/null
}

_chunk_body() {   # $1 = listening|ended, $2 = pass, $3 = partition index, $4.. = disks -> the control message
    # `durable` is the delta of chunkledger.sh: what the server has not acknowledged yet, one piece of it a message
    _cb_state="$1"; _cb_pass="$2"; _cb_idx="$3"; shift 3; _cb_sep=""
    printf '{"mac":"%s","session_id":"%s","part":%s,"pass":%s,"state":"%s","durable_seq":%s%s,"targets":[' \
        "$MAC" "$CHUNK_SESSION" "$_cb_idx" "$_cb_pass" "$_cb_state" "$(ledger_seq pass "$CHUNK_SESSION")" "${CHUNK_FETCHED:+,\"fetched\":$CHUNK_FETCHED}${CHUNK_RECEIVED:+,\"received\":$CHUNK_RECEIVED}$(chunk_stray_json "$_cb_state" 2>/dev/null)"   # fetched, received: _chunk_fetch_tell (chunkfetch.sh)
    for _cb_d in "$@"; do
        [ ! -e "$RUN_DIR/targets/$_cb_d/catchup" ] || [ -e "$RUN_DIR/targets/$_cb_d/untold" ] || continue; _cb_serial=$(disk_serial "$_cb_d"); [ -n "$_cb_serial" ] || _cb_serial=$(target_bound_serial "$_cb_d")   # gone from sysfs: the serial it was chosen by -- unreported, the server counts it alive
        [ -n "$_cb_serial" ] || continue   # no serial, no ledger (the design, 2.3)
        _cb_mbps=$(_wc_mbps "$_cb_d"); case "$_cb_mbps" in ''|*[!0-9]*) _cb_mbps=null ;; esac
        case "$(cat "$RUN_DIR/targets/$_cb_d/state" 2>/dev/null)" in failed|cancelled) _cb_failed=true ;; *) _cb_failed=false ;; esac; [ ! -e "$RUN_DIR/targets/$_cb_d/catchup" ] || _cb_failed=true; _cb_pl=""; if [ "$_cb_failed" = true ] && command -v drawer_waits > /dev/null 2>&1 && drawer_waits "$_cb_d"; then _cb_pl=',"pulled":true'; fi   # #3181: `untold` = it came back inside a pass, before the server heard it had left -- said once, as failed: out of the wave's plan. pulled = still waited for (drawerplug.sh)
        # skipped (#3141/#3170): what chunkcast recv said this drive was too slow to take in the pass just ended. Not read = not said, never 0
        _cb_sk=""; [ "$_cb_state" != ended ] || [ ! -s "$CHUNK_DIR/skipped" ] || _cb_sk=$(awk -v n="$(partition_node "$_cb_d" "$_cb_idx")" '$1 == n && $2 ~ /^[0-9]+$/ { printf ",\"skipped\":%d", $2 }' "$CHUNK_DIR/skipped" 2>/dev/null)
        printf '%s{"serial":"%s","write_mbps":%s,"failed":%s%s%s}' "$_cb_sep" \
            "$(json_escape "$_cb_serial")" "$_cb_mbps" "$_cb_failed" "$_cb_pl$_cb_sk" "$(LEDGER_SESSION="$CHUNK_SESSION" ledger_json "$_cb_d" pass "$_cb_idx")"
        _cb_sep=","
    done
    if [ -e "$RUN_DIR/ledger.pass.more" ]; then printf '],"durable_more":true}'; else printf ']}'; fi
}

_chunk_tell() {   # $1 = listening|ended, $2 = pass, $3 = index, $4.. = disks. The answer is in $CHUNK_DIR/answer.json; 1 = no answer
    # A long ledger goes in pieces, and a server that lost its place asks for all of it again: the answer is the last piece's.
    _ct_n=0
    while [ "$_ct_n" -lt 200 ]; do
        _ct_n=$((_ct_n + 1)); _chunk_body "$@" > "$CHUNK_DIR/body.json"
        http_post_json "$SERVER/api/v1/agent/chunk-pass" "$CHUNK_DIR/body.json" > "$CHUNK_DIR/answer.json" 2>> "$LOG_FILE"; _ct_rc=$?
        ledger_ack pass "$CHUNK_DIR/answer.json" "$CHUNK_SESSION" "$_ct_rc"; _ct_ack=$?
        [ "$_ct_rc" != 0 ] || _chunk_beat   # an answer is the evidence that this loop and its server are alive
        case "$_ct_rc/$_ct_ack" in 0/2|0/3) ;; 0/*) [ "$1" = fetching ] || rm -f "$RUN_DIR"/targets/*/untold; return 0 ;; *) return 1 ;; esac   # 2 = everything again, 3 = the next piece
    done
    return 1
}

_chunk_why() {   # $1 = chunkcast's reason for a failed target -> the operator's sentence
    case "$1" in
        *"write error"*) echo "שגיאת כתיבה לדיסק (write_error)" ;;   # #1841: the word the server classifies by (ata_cause.py), as the flush's
        *"too long"*)    echo "הדיסק הפסיק לקבל כתיבה ולא חזר -- הוצא מהמעבר" ;;   # hung, not a failed flush: its reason names both
        *"flush"*)       echo "ה-fsync לדיסק נכשל (write_error)" ;;
        *"too small"*)   echo "המחיצה לא נפתחה לכתיבה, או קטנה מהאימג'" ;;
        *)               echo "המקלט הפסיק לכתוב לדיסק" ;;
    esac
}

_chunk_verdicts() {   # $1 = index, $2.. = disks: a target chunkcast failed fails here, by name
    _cv_idx="$1"; shift
    for _cv_d in $(_chunk_live "$@"); do
        _cv_line=$(_chunk_line "$(partition_node "$_cv_d" "$_cv_idx")") || continue
        case "$_cv_line" in
            *" failed "*) log "partition $_cv_idx on $_cv_d: chunkcast: ${_cv_line#* failed }"; event_beep failure
                          "${CHUNK_FAIL:-fail_written_target}" "$_cv_d" "מחיצה $_cv_idx: $(_chunk_why "$_cv_line")" ;;   # CHUNK_FAIL: the watch does not wait for it (chunkwatch.sh)
        esac
    done
}

_chunk_counters() {   # $1 = index, $2.. = disks: each drawer's counter = the compressed bytes of what is durable on it
    _cc_idx="$1"; shift
    for _cc_d in $(_chunk_live "$@"); do
        _chunk_line "$(partition_node "$_cc_d" "$_cc_idx")" | awk -v idx="$CHUNK_IDX" '
            { n = split($4, r, ","); for (i = 1; i <= n && $4 != "-"; i++) { split(r[i], p, "-"); lo[i] = p[1]; hi[i] = p[2] } m = ($4 == "-") ? 0 : n }
            END { while ((getline line < idx) > 0) { k++; if (k == 1) { split(line, h, " "); sum = h[6]; continue }
                      while (j < m && hi[j + 1] + 0 < k - 2) j++
                      if (j < m && lo[j + 1] + 0 <= k - 2) { split(line, f, " "); sum += f[2] } }
                  print sum + 0 }' >> "$RUN_DIR/targets/$_cc_d/feed.bytes"
        if command -v chunk_stream_write >/dev/null 2>&1; then chunk_stream_write "$_cc_d" "$_cc_idx" || :; fi   # #3070: the durable chunks in the data stream's bytes -- the screen's bar and rate (chunkprog.sh)
        # the ledger of the progress report (chunkledger.sh's durable.<part>): the same ranges, a line a run, by a rename.
        # No serial, no ledger: the server refuses such a disk by name (chunkledger.py), so its ranges are not sent at all.
        [ -n "$(disk_serial "$_cc_d")" ] || continue
        _cc_l="$RUN_DIR/targets/$_cc_d/ledger"; mkdir -p "$_cc_l" 2>/dev/null || continue
        _chunk_line "$(partition_node "$_cc_d" "$_cc_idx")" \
            | awk '$2 == "ok" && $4 != "-" { n = split($4, r, ","); for (i = 1; i <= n; i++) print r[i] }' > "$_cc_l/durable.$_cc_idx.new" \
            && mv "$_cc_l/durable.$_cc_idx.new" "$_cc_l/durable.$_cc_idx"
    done
}

_chunk_nodes() {   # $1 = index, $2.. = disks -> the partition nodes of the drawers still written AND still the disks chosen
    _cn_idx="$1"; shift
    for _cn_d in $(_chunk_live "$@"); do
        assert_target "$_cn_d" || continue   # #2434: before every pass and every fetch, not once a partition
        _cn_node=$(partition_node "$_cn_d" "$_cn_idx")
        node_is_block "$_cn_node" || { fail_written_target "$_cn_d" "מחיצה $_cn_idx: $_cn_node אינו התקן בלוקים"; continue; }   # #51
        printf '%s ' "$_cn_node"
    done
}

_chunk_beat() { if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi; }
_chunk_feed() { _cfw="$1"; shift; if command -v "chunk_feed_$_cfw" > /dev/null 2>&1; then "chunk_feed_$_cfw" "$@"; fi; }   # B5 (chunkfeed.sh): HTTP beside the stream; off as shipped

_chunk_run() {   # $1 = what is running (for the log), $2 = pid, $3 = index, $4.. = disks. Its exit code; 99 = killed here
    # $CHUNK_OUT = where it writes its verdict. $CHUNK_TELL = the pass of a fetch: the server is told as it advances.
    _cr_what="$1"; _cr_pid="$2"; _cr_idx="$3"; shift 3; _cr_quiet=0; _cr_was=""; _cr_said=0; _cr_told=0; _cr_tk=""; _cr_sig=""
    # a fetch's first word of proof is told as soon as there is one, not after the pause between two reports (#3149); what is already here at the start is not proof
    if [ -n "${CHUNK_TELL:-}" ]; then _cr_told=$CHUNK_FETCH_TELL_S; _cr_sig="$(cat "$CHUNK_STATE" 2>/dev/null)|"; fi
    chunk_watch_start "$_cr_pid" "$_cr_idx" "$@"   # a drawer that dies in here is out in seconds, by name (chunkwatch.sh)
    while kill -0 "$_cr_pid" 2>/dev/null; do
        _chunk_beat; if command -v drawer_plug_tick > /dev/null 2>&1 && drawer_plug_tick "$_cr_pid" "$_cr_idx" "$@"; then set -- $_disks; fi   # #3181: a drawer pushed back in is found when the kernel says so, inside the pass (drawerplug.sh)
        _nap "$_cr_pid"; _cr_told=$((_cr_told + 1)); [ -n "${CHUNK_TELL:-}${CHUNK_BG:-}" ] || chunk_loss_tell "$CHUNK_OUT"   # #3260: _nap = a second at most, and back the moment the receiver ends (waits.sh; was sleep 1) · #3064 G6/G7: a receiver's losses, while it runs (chunkloss.sh)
        # chunkcast said its verdict ("pass <word>") and wrote its state: a drive that never returns can
        # hold the process (D state) for ever, and its neighbours are not made to wait for that.
        if grep -q '^pass ' "$CHUNK_OUT" 2>/dev/null; then
            _cr_said=$((_cr_said + 1))
            [ "$_cr_said" -lt 15 ] || { log "$_cr_what: verdict written, the process is held by a disk -- going on without it"; chunk_watch_end; _chunk_word_rc; return "$?"; }
        fi
        _cr_now=$(cat "$CHUNK_STATE" 2>/dev/null); _cr_took=$(cat "$CHUNK_STATE.taken" 2>/dev/null)   # apply's own count of chunks taken (chunkfetch.sh)
        if [ "$_cr_now" != "$_cr_was" ]; then _cr_was="$_cr_now"; _cr_quiet=0; _chunk_counters "$_cr_idx" "$@"
        elif [ "$_cr_took" != "$_cr_tk" ]; then _cr_quiet=0   # chunks taken, no flush back yet: a slow drive is not a quiet one (#3149)
        else _cr_quiet=$((_cr_quiet + 1)); fi
        _cr_tk="$_cr_took"
        # a fetch that advances says so (chunkfetch.sh): a long download is not silence, and what is here is not planned again.
        # Anything new since the last report -- durable or taken -- and at most once in CHUNK_FETCH_TELL_S
        if [ -n "${CHUNK_TELL:-}" ] && [ "$_cr_told" -ge "$CHUNK_FETCH_TELL_S" ] && [ "$_cr_now|$_cr_took" != "$_cr_sig" ]; then
            _cr_told=0; _cr_sig="$_cr_now|$_cr_took"; _chunk_fetch_tell "$CHUNK_TELL" "$_cr_idx" "$@" || :
        fi
        if round_closed; then kill "$_cr_pid" 2>/dev/null; fi   # SIGTERM: chunkcast flushes, writes its state and says "stopped"
        # chunkcast has its own start and stall ceilings and ends by itself; past both plus a drawer's
        # write ceiling with nothing new durable, it is a writer held by a disk -- and that has no end.
        if [ "$_cr_quiet" -gt $((STREAM_START_CEILING + WAIT_STREAM_STALL_S + WAIT_DRAWER_S)) ]; then
            log "פג הזמן: $_cr_what לא הסתיים ולא התקדם -- נכשל"
            kill -9 "$_cr_pid" 2>/dev/null; chunk_watch_end; return 99
        fi
    done
    chunk_watch_end; wait "$_cr_pid" 2>/dev/null   # wait-bounded: after-kill-0-gone -- the loop above left only once the process was gone; this collects its exit code, which wait_pid does not hand back
}

chunk_image_args() { CHUNK_IMAGE_ARGS="--index $1 --index-xxh64 $_cp_xxh ${2:---raw}"; }   # the ONE place the image arguments are built (#3064): $1 = index file, $2 = "--head FILE" or empty; $_cp_xxh = the manifest's index_xxh64
chunk_partition_drawers() {
    # $1=server $2=image_id $3=index $4=fs $5=file $6=manifest $7..=disks. 0 = some drawer holds the partition whole.
    _cp_server="$1"; _cp_image="$2"; _cp_idx="$3"; _cp_fs="$4"; _cp_file="$5"; _cp_manifest="$6"; shift 6
    mkdir -p "$CHUNK_DIR"; CHUNK_STATE="$CHUNK_DIR/p$_cp_idx.state"; CHUNK_IDX="$CHUNK_DIR/p$_cp_idx.idx"
    rm -f "$CHUNK_STATE" "$CHUNK_IDX" "$CHUNK_DIR/p$_cp_idx.head" "$CHUNK_DIR/p$_cp_idx.b3" "$CHUNK_DIR/p$_cp_idx.base" "$CHUNK_DIR/p$_cp_idx.kept" "$CHUNK_DIR/skipped"   # kept: drawerback.sh's, of this round only
    _cp_nodes=$(_chunk_nodes "$_cp_idx" "$@")
    [ -n "$_cp_nodes" ] || { log "no drawers left"; return 1; }
    for _cp_d in $(_chunk_live "$@"); do
        target_counter "$_cp_d" "$RUN_DIR/targets/$_cp_d/feed.bytes"; if command -v chunk_stream_init >/dev/null 2>&1; then chunk_stream_init "$_cp_manifest" "$_cp_d" || :; fi   # #3070: the screen's own pair (chunkprog.sh); write_started is expand.sh's and holds the serial: emptied here, a failed drawer kept its GPT
    done

    # The index is believed only when its XXH64 is the manifest's (chunkcast checks it again, itself).
    _cp_xxh=$(jq -r --argjson i "$_cp_idx" '.partitions[] | select(.index == $i) | .index_xxh64' "$_cp_manifest" 2>> "$LOG_FILE")
    if ! http_get "$_cp_server/api/v1/images/$_cp_image/index/$_cp_idx" > "$CHUNK_IDX" 2>> "$LOG_FILE" \
            || [ "$(fanout --xxh64 < "$CHUNK_IDX")" != "$_cp_xxh" ]; then
        _fail_alive "$*" "מחיצה $_cp_idx: האינדקס של המחיצה לא הורד מהשרת, או שאינו תואם למניפסט"
        return 1
    fi
    chunk_image_args "$CHUNK_IDX"
    _cp_headz=$(awk 'NR == 1 { print $6 }' "$CHUNK_IDX")
    if [ "$(awk 'NR == 1 { print $3 }' "$CHUNK_IDX")" = partclone ]; then
        case "$_cp_headz" in ''|*[!0-9]*|0) _cp_headz=1 ;; esac
        if ! curl -sfS --max-time "$HTTP_TIMEOUT" -r "0-$((_cp_headz - 1))" \
                "$_cp_server/api/v1/images/$_cp_image/files/$_cp_file" > "$CHUNK_DIR/p$_cp_idx.head" 2>> "$LOG_FILE"; then
            _fail_alive "$*" "מחיצה $_cp_idx: מפת המחיצה לא הורדה מהשרת"
            return 1
        fi
        chunk_image_args "$CHUNK_IDX" "--head $CHUNK_DIR/p$_cp_idx.head"
    fi
    # #3332: the partition's BLAKE3 list is required (chunkb3.sh). Not there, or not whole = the drawers fail by name
    CHUNK_B3_WHY=""; chunk_b3_fetch "$_cp_server" "$_cp_image" "$_cp_idx" "$_cp_manifest" \
        || { _fail_alive "$*" "מחיצה $_cp_idx: ${CHUNK_B3_WHY:-רשימת ה-BLAKE3 של המחיצה לא נבדקה (chunk_b3_fetch לא רץ)}"; return 1; }
    echo "$CHUNK_IMAGE_ARGS" > "$CHUNK_DIR/p$_cp_idx.args"   # a drawer that comes back completes this partition with them (drawerback.sh)
    if command -v resume_seed > /dev/null 2>&1; then resume_seed "$_cp_idx" "$_cp_xxh" "$@"; fi   # #2528: what a drawer that resumes already holds, read back (resume.sh)
    if command -v rcvbuf_check >/dev/null 2>&1; then rcvbuf_check; fi   # #3072: rmem_max, raised by its one place (rcvbuf.sh); what the socket got is chunkcast's own line
    next_stream_ceiling; _cp_pass=1; _cp_action="receive"; _cp_asked=0; _cp_silent=0; _cp_why=""; _cp_again=0
    while :; do
        cancel_pending "$@"   # #1386: a cancel asked for is carried out between passes
        _cp_nodes=$(_chunk_nodes "$_cp_idx" "$@"); [ -n "$_cp_nodes" ] || return 1   # #2434: again, for this pass
        if [ "$_cp_action" = receive ]; then chunk_loss_mark; CHUNK_FEED_ARGS=""; _chunk_feed prepare   # #3064 G6/G7: the kernel's drop counters before the pass (chunkloss.sh); B5: the FIFO, when it is on
            # shellcheck disable=SC2086 # the image arguments and the node list are word lists on purpose
            chunkcast recv --listen "$CHUNK_GROUP:$CHUNK_PORT" ${IFACE:+--iface "$IFACE"} ${CHUNK_SOURCE:+--from "$CHUNK_SOURCE"} --session "$CHUNK_KEY" \
                --part "$_cp_idx" --pass "$_cp_pass" $CHUNK_IMAGE_ARGS --state "$CHUNK_STATE" \
                --start-s "$STREAM_START_CEILING" --stall-s "$WAIT_STREAM_STALL_S" ${CHUNK_SYNC_MS:+--sync-ms "$CHUNK_SYNC_MS"} $CHUNK_FEED_ARGS $_cp_nodes \
                > "$CHUNK_DIR/recv.out" 2>> "$LOG_FILE" &
            _cp_pid=$!; _cp_n=0; echo "$_cp_idx $_cp_pass $_cp_pid" > "$CHUNK_DIR/onair"   # #3181: the pass on the air -- a drawer that comes back listens beside it (drawerfill.sh)
            # "listening" is said once the socket is bound and the group joined: only then is the server told
            until grep -qx listening "$CHUNK_DIR/recv.out" 2>/dev/null || ! kill -0 "$_cp_pid" 2>/dev/null || [ "$_cp_n" -ge 300 ]; do sleep 0.1 2>/dev/null || sleep 1; _cp_n=$((_cp_n + 1)); done
            # only a receiver that SAID it listens is reported as listening (principle 5): one that died or
            # is not up yet says nothing, and the server does not start a pass for it
            _cp_hold=no; if grep -qx listening "$CHUNK_DIR/recv.out" 2>/dev/null; then
                if _chunk_tell listening "$_cp_pass" "$_cp_idx" "$@"; then _cp_hold=$(json_get "$CHUNK_DIR/answer.json" ".held"); _chunk_feed start "$_cp_server" "$_cp_image" "$_cp_file" "$_cp_idx" "$CHUNK_DIR/answer.json"; else log "chunk-pass: the server did not take 'listening' (pass $_cp_pass)"; fi
            else
                log "partition $_cp_idx: chunkcast recv never said it listens (pass $_cp_pass) -- not reported"
            fi
            # #2388: a first pass that never came is listened to again only while the server, told so, says it holds the wave for another machine's question
            if [ "$_cp_again" = 1 ] && [ "$_cp_hold" != true ]; then kill "$_cp_pid" 2>/dev/null; _chunk_feed end "$CHUNK_DIR/recv.out"; _fail_alive "$*" "מחיצה $_cp_idx: פג הזמן -- הזרם לא הגיע"; return 1; fi
            _cp_again=0
            CHUNK_OUT="$CHUNK_DIR/recv.out" CHUNK_TELL="" _chunk_run "המקלט של מחיצה $_cp_idx (מעבר $_cp_pass)" "$_cp_pid" "$_cp_idx" "$@"; _cp_rc=$?; [ -z "${CHUNK_RELIST:-}" ] || { set -- $_disks; CHUNK_RELIST=""; }   # #3181: a drawer came back under another name inside the pass
            log "partition $_cp_idx: chunkcast recv pass $_cp_pass rc=$_cp_rc: $(tail -n 1 "$CHUNK_DIR/recv.out" 2>/dev/null)"; _chunk_feed end "$CHUNK_DIR/recv.out"
            _chunk_verdicts "$_cp_idx" "$@"; _chunk_counters "$_cp_idx" "$@"; chunk_loss_end "$CHUNK_DIR/recv.out"   # the pass's last word: the counters, the ledger files, and what it lost (chunkloss.sh)
            awk '$1 == "skipped" { print $2, $3 } $1 == "stray" || $1 == "loss_shape" { print $1, $2 }' "$CHUNK_DIR/recv.out" > "$CHUNK_DIR/skipped" 2>/dev/null || rm -f "$CHUNK_DIR/skipped"   # this pass's, for "ended" (+ `stray`, #1079: chunkgate.sh)
            case "$_cp_rc" in
                0|4|5) ;;   # the pass ended, or stalled with its state written, or no target is left: the ledger says what is there
                3) # the first pass never came: the machine has nothing -- it listens again, and fails there unless the server holds the wave (#2388).
                   # A REPAIR pass that never came costs nothing that is already durable -- it is reported as it stands, and the server counts the attempt.
                   [ "$_cp_pass" -gt 1 ] || { _cp_again=1; log "partition $_cp_idx: no stream in $STREAM_START_CEILING s -- is the wave held?"; continue; } ;;
                6) _fail_alive "$*" "מחיצה $_cp_idx: $(round_closed_why)"; return 1 ;;
                7) _fail_alive "$*" "מחיצה $_cp_idx: הזרם שהגיע אינו של האימג' של הסבב"; return 1 ;;
                *) _fail_alive "$*" "מחיצה $_cp_idx: $(_chunk_rc_why "$_cp_rc")"; return 1 ;;   # 8 and 2 by name (chunkrc.sh, #3259)
            esac
        elif [ "$_cp_action" = fetch ]; then
            rm -f "$CHUNK_DIR/skipped"   # a fetch skips nothing: the receive pass's count is not this one's
            # shellcheck disable=SC2086 # the node list is a word list on purpose
            _chunk_fetch "$_cp_server" "$_cp_image" "$_cp_file" "$_cp_idx" "$_cp_pass" "$*" $_cp_nodes; [ -z "${CHUNK_RELIST:-}" ] || { set -- $_disks; CHUNK_RELIST=""; }
            _chunk_verdicts "$_cp_idx" "$@"; _chunk_counters "$_cp_idx" "$@"
        fi
        # shellcheck disable=SC2086 # the last drawer was pulled: wait for it (drawerhold.sh, #3140); back = the round's list is in _disks
        _any_alive "$*" || { drawer_hold "$_cp_idx" "$_cp_pass" "$_cp_server" "$_cp_image" "$@" || return 1; set -- $_disks; }

        # What now? The answer is the server's; "wait" has a ceiling, and no answer is not "done".
        _cp_fast=0
        while :; do
            # An answer feeds the watchdog (_chunk_tell); no answer does not, and is counted in real seconds: the
            # ceiling of a silent server, plus one more question, stays under the watchdog's lease (test_timeouts).
            _cp_t0=$(date +%s)
            if _chunk_tell ended "$_cp_pass" "$_cp_idx" "$@"; then _cp_action=$(json_get "$CHUNK_DIR/answer.json" ".action"); _cp_silent=0
            else _cp_action="wait"; _cp_silent=$((_cp_silent + $(date +%s) - _cp_t0 + CHUNK_ASK_POLL_S)); fi
            jq -r '.fail // {} | to_entries[] | "\(.key)|\(.value)"' "$CHUNK_DIR/answer.json" 2>/dev/null > "$CHUNK_DIR/fail.list"
            [ "$(json_get "$CHUNK_DIR/answer.json" ".whole or .others")" != true ] || _cp_asked=0   # whole, or its part of this round done: the wait is for other machines
            while IFS='|' read -r _cp_s _cp_why <&4; do
                for _cp_d in $(_chunk_live "$@"); do
                    [ "$(disk_serial "$_cp_d")" != "$_cp_s" ] || fail_written_target "$_cp_d" "מחיצה $_cp_idx: $_cp_why" stream
                done
            done 4< "$CHUNK_DIR/fail.list"
            [ "$_cp_action" = wait ] || break
            _cp_fast=$((_cp_fast + 1)); _cp_nap=$CHUNK_ASK_POLL_S; [ "$_cp_fast" -gt "$CHUNK_ASK_FAST_N" ] || _cp_nap=0   # a quick question costs the ceilings nothing: they were set for the slow ones
            _cp_asked=$((_cp_asked + _cp_nap))
            [ "$_cp_silent" -le "$WAIT_STREAM_STALL_S" ] || _cp_why="השרת לא ענה במשך $_cp_silent שניות"
            if round_closed || [ -n "$_cp_why" ] || [ "$_cp_asked" -gt $((WAIT_STREAM_STALL_S * 2 + WAIT_DRAWER_S)) ]; then _cp_action="closed"; break; fi
            # #3260: right after "ended" a "wait" can last only until the server has the next plan (the others' reports in): the first questions come every CHUNK_ASK_FAST_S, then every CHUNK_ASK_POLL_S. A refused fractional sleep (busybox without it) is the full interval, never a hot loop
            if [ "$_cp_nap" -eq 0 ] && sleep "$CHUNK_ASK_FAST_S" 2>/dev/null; then :; else sleep "$CHUNK_ASK_POLL_S"; fi
            if command -v drawer_plug_tick > /dev/null 2>&1 && drawer_plug_tick "" "$_cp_idx" "$@"; then set -- $_disks; CHUNK_RELIST=""; fi   # #3260: kept. The condition is another MACHINE's report and nothing here can wait on it -- this is the question's own interval (the server's load, N drawers, and the ceilings above count in it)
        done
        # shellcheck disable=SC2086 # a drawer pulled and pushed back in joins again, under its new name (drawerback.sh); a word list
        if [ "$_cp_action" != closed ] && drawer_back in "$_cp_idx" "$_cp_server" "$_cp_image" "$@"; then set -- $_disks; fi; _any_alive "$*" || return 1
        case "$_cp_action" in
            receive|fetch) _cp_pass=$(json_get "$CHUNK_DIR/answer.json" ".pass"); _cp_asked=0
                           case "$_cp_pass" in ''|null|*[!0-9]*) _fail_alive "$*" "מחיצה $_cp_idx: תשובת השרת לשלב ההשלמה אינה קריאה"; return 1 ;; esac ;;
            done) break ;;
            *) _fail_alive "$*" "מחיצה $_cp_idx: ${_cp_why:-$(round_closed_why)}"; return 1 ;;
        esac
    done

    # "done" is the server's word; the chunk count on each drawer is the evidence (principle 5).
    _cp_total=$(awk 'NR == 1 { print $9 }' "$CHUNK_IDX")
    for _cp_d in $(_chunk_live "$@"); do
        _cp_have=$(_chunk_line "$(partition_node "$_cp_d" "$_cp_idx")" | awk '$2 == "ok" { print $3 }')
        if [ -n "$_cp_have" ] && [ "$_cp_have" = "$_cp_total" ]; then
            # #1487: as partclone -r, this writer leaves the NTFS backup boot sector unwritten (verdict.sh)
            if ntfs_boot_target "$_cp_d" "$_cp_idx" "$_cp_fs"; then target_partition_done "$_cp_d"
            else fail_written_target "$_cp_d" "$NTFS_BOOT_ERROR"; fi
        else
            fail_written_target "$_cp_d" "מחיצה $_cp_idx: השרת סגר את המחיצה לפני שהדיסק הושלם (${_cp_have:-0} מתוך $_cp_total חלקים)" stream
        fi
    done
    _any_alive "$*"
}
