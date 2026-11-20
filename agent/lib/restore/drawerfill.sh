# drawerfill.sh -- what a drawer that came back lacks of ONE partition: the pass on the air, a neighbour, HTTP (#3181).
# POSIX sh (busybox ash).
#
# Until #3181 a drawer pushed back in fetched over HTTP, in the background, everything it lacked of the partition
# being written -- the chunks the round had not even sent yet among them -- and met the stream again only at the
# next partition's start (drawerback.sh, #3112). A pull of two seconds cost the rest of a 70GiB partition from the
# server's library disk, while the same chunks went by on the multicast VLAN.
#
# _db_part is the background completion's step for one partition (_db_catch, drawerback.sh). In order:
#
#   1. what chunkcast had made durable on the drawer stays, under the node's new name (`p<n>.state` of its own);
#   2. LIVE: while the round's receiver of this partition runs (`onair`, chunkrecv.sh: index, pass, pid), the
#      drawer listens beside it -- a `chunkcast recv` of its own on the same group, for its node alone. Two
#      sockets on one multicast group both get every packet (SO_REUSEADDR, chunkcast.c). It takes what is sent
#      from now on, every chunk checked against the index as in any pass, and ends with the pass. Should it
#      outlive the round's receiver (it began after the pass's END), it is stopped, its state written;
#   3. MEASURED: what it still lacks is the index minus its own ledger -- counted, never guessed from how long
#      it was out -- said in the log, and told to the server in the progress report (`rejoin`, drawerplug.sh)
#      until the partition is whole. Counted AFTER the pass it joined: before it, the count holds every chunk
#      the round has not sent yet, and those were never missed;
#   4. A NEIGHBOUR first: neighborfill_run (neighborfill.sh: over SATA, XXH64 before a byte is written, shared
#      between the neighbours, under one ceiling). Its evidence is the list it leaves -- what is no longer in
#      it was written and re-read whole. A list that is not a part of the one it was given proves nothing:
#      nothing of it is believed. No neighborfill_run in this agent = said by name, and all of it goes to HTTP;
#   5. HTTP for the rest: the repair fetch's own way (_chunk_fetch), ranges of the agent's own;
#   6. whole = the drawer's line in its state file says every chunk (principle 5) -- not an exit code.
#
# The pass that is joined is a partition's FIRST pass, on the air on THIS machine. A repair pass carries the few
# chunks two machines lack, and waiting for its end would only hold the fill. A cloner whose only drawer was
# pulled has no receiver left (chunkcast ends with "no-target"): its drawer completes by a neighbour and HTTP.
# A drawer still behind when the round begins the next partition takes that partition's first pass the same way,
# when its completion gets there (_db_catch goes on to a partition that is on the air).

DRAWER_LIVE_TAIL_S="${DRAWER_LIVE_TAIL_S:-5}"   # how long a drawer's own receiver may outlive the round's

_fl_count() { awk -F- '{ s += $2 - $1 + 1 } END { print s + 0 }' "$1" 2> /dev/null; }   # $1 = a list, "A-B" a line -> its chunks
_fl_on() { [ "$(cat "$RUN_DIR/targets/$1/state" 2> /dev/null)" = writing ]; }           # still written: not failed or cancelled meanwhile

_fl_missing() {   # $1 = state file, $2 = node, $3 = chunks in the index -> what the node lacks, "A-B" a line. Not 0 = not computed
    { _db_line "$1" "$2" || :; } | awk -v t="$3" '{ r = $4 } END {
        n = (r == "" || r == "-") ? 0 : split(r, a, ","); k = 0
        for (i = 1; i <= n; i++) { split(a[i], p, "-"); if (p[1] + 0 > k) print k "-" p[1] - 1; k = p[2] + 1 }
        if (k < t + 0) print k "-" t - 1 }'
}

_fl_note() {   # $1 = target, $2 = partition, $3 = chunks it lacks now, $4 = chunks in the index, $5 = neighbor|http: where they come from now
    # the progress report's `rejoin` (drawer_back_json, drawerplug.sh; interfaces.md section 4). away_s: not measured = null, never 0
    _fq_t="$RUN_DIR/targets/$1"; _fq_a=$(cat "$_fq_t/away" 2> /dev/null); case "$_fq_a" in ''|*[!0-9]*) _fq_a=null ;; esac
    printf '{"part":%s,"missing":%s,"chunks":%s,"away_s":%s,"step":"%s"}\n' "$2" "$3" "$4" "$_fq_a" "$5" \
        > "$_fq_t/rejoin.new" && mv "$_fq_t/rejoin.new" "$_fq_t/rejoin"
}

_fl_onair() {   # $1 = partition index. 0 = its FIRST pass is on the air on this machine now (_fv_pass, _fv_pid: chunkrecv.sh's `onair`)
    read -r _fv_i _fv_pass _fv_pid < "$_db_main/onair" 2> /dev/null || return 1
    [ "$_fv_i" = "$1" ] && [ "$_fv_pass" = 1 ] && kill -0 "$_fv_pid" 2> /dev/null
}

_fl_live() {   # $1 = the drawer, $2 = partition index: the round's receiver of this partition runs now -- the drawer listens beside it
    _fl_onair "$2" || return 0   # no first pass of it on the air here
    assert_target "$1" || return 0   # #2434: no longer the disk chosen -- failed by name there, and nothing is written
    log "המגירה $1 מצטרפת לשידור החי של מחיצה $2 (מעבר $_fv_pass) -- כותבת את מה שמשודר מעכשיו"
    # shellcheck disable=SC2086 # the image arguments are a word list on purpose
    chunkcast recv --listen "$CHUNK_GROUP:$CHUNK_PORT" ${IFACE:+--iface "$IFACE"} --session "$CHUNK_KEY" \
        --part "$2" --pass "$_fv_pass" $CHUNK_IMAGE_ARGS --state "$CHUNK_STATE" \
        --start-s "$STREAM_START_CEILING" --stall-s "$WAIT_STREAM_STALL_S" "$(partition_node "$1" "$2")" \
        > "$CHUNK_DIR/recv.out" 2>> "$LOG_FILE" &
    _fv_r=$!; echo "$_fv_r" > "$CHUNK_DIR/apply.pid"   # "cancel this disk" stops it by this file, as it stops a fetch (drawer_catchup_stop)
    # the round's receiver ended and this one did not (it began after the pass's END): SIGTERM -- it flushes and writes its state
    ( while kill -0 "$_fv_pid" 2> /dev/null; do sleep 1; done; sleep "$DRAWER_LIVE_TAIL_S"; kill "$_fv_r" 2> /dev/null ) > /dev/null 2>&1 &
    _fv_g=$!
    CHUNK_OUT="$CHUNK_DIR/recv.out" CHUNK_TELL="" _chunk_run "ההצטרפות של $1 לשידור של מחיצה $2" "$_fv_r" "$2" "$1"; _fv_rc=$?
    kill "$_fv_g" 2> /dev/null; rm -f "$CHUNK_DIR/apply.pid"   # a pid kept past the process is another process's
    log "המגירה $1: ההצטרפות לשידור של מחיצה $2 הסתיימה (rc=$_fv_rc: $(tail -n 1 "$CHUNK_DIR/recv.out" 2> /dev/null))"
}

_fl_neighbour() {   # $1 = the drawer, $2 = partition index, $3 = its node, $4 = chunks in the index, $_dp_ls = what it lacks.
    # What a neighbour brought enters the drawer's state file, and the caller counts what is missing again from there
    if ! command -v neighborfill_run > /dev/null 2>&1; then
        log "מחיצה $2: neighborfill_run אינה מוגדרת בסוכן הזה -- אין השלמה ממגירה שכנה, כל החסר של $1 מושלם ב-HTTP"; return 0
    fi
    # #3181: the brain's word on this measure, when it gives one (enforce): HTTP only, or which neighbours and under what ceiling (fillword.sh)
    FILL_ONLY=""; if command -v fill_word > /dev/null 2>&1; then fill_word "$1" "$2" "$(_fl_count "$_dp_ls")" || return 0; fi
    cp "$_dp_ls" "$_dp_ls.was" || return 0
    NF_PART="$2"; NF_ONLY="$FILL_ONLY"; neighborfill_run "$1" "$_dp_ls"; _fh_rc=$?
    # the list it left, checked: each run inside a run of the list it was given -> "<durable> <ranges>": all but what is left
    _fh_l=$(awk -F- -v t="$4" 'BEGIN { k = 0; j = 0; n = 0; c = 0; have = 0 }
        $0 !~ /^[0-9]+-[0-9]+$/ || $1 + 0 > $2 + 0 { bad = 1; exit }
        NR == FNR { lo[++n] = $1 + 0; hi[n] = $2 + 0; next }
        { while (j < n && hi[j + 1] < $1 + 0) j++
          if (j >= n || lo[j + 1] > $1 + 0 || hi[j + 1] < $2 + 0 || $1 + 0 < k) { bad = 1; exit }
          if ($1 + 0 > k) { out = out (c++ ? "," : "") k "-" $1 - 1; have += $1 - k }
          k = $2 + 1 }
        END { if (bad) exit 2
              if (k < t + 0) { out = out (c++ ? "," : "") k "-" t - 1; have += t - k }
              print have, (c ? out : "-") }' "$_dp_ls.was" "$_dp_ls" 2>> "$LOG_FILE") || _fh_l=""
    _fh_h=$(head -n 1 "$CHUNK_STATE" 2> /dev/null) || _fh_h=""   # the header chunkcast will ask for: the drawer's own, or the round's
    case "$_fh_h" in "chunkcast-state "*) ;; *) _fh_h=$(head -n 1 "$_dp_m.state" 2> /dev/null) || _fh_h="" ;; esac
    case "$_fh_h" in "chunkcast-state "*) ;; *) _fh_h="" ;; esac
    _fh_was=$(_fl_count "$_dp_ls.was"); _fh_now=$(_fl_count "$_dp_ls")
    if [ -z "$_fh_l" ]; then
        log "מחיצה $2: הרשימה שההשלמה מהשכנות החזירה ל-$1 אינה חלק מהרשימה שקיבלה (rc=$_fh_rc) -- לא מאמינים לה, כל החסר מושלם ב-HTTP"
    elif [ "$_fh_now" = "$_fh_was" ]; then
        log "מחיצה $2: אף חתיכה לא הושלמה ל-$1 מהשכנות (rc=$_fh_rc) -- $_fh_was חתיכות מושלמות ב-HTTP"
    elif [ -z "$_fh_h" ]; then
        log "מחיצה $2: אין כותרת לקובץ המצב של $1 -- מה שהושלם מהשכנות אינו נרשם, כל החסר מושלם ב-HTTP"
    elif printf '%s\n%s ok %s\n' "$_fh_h" "$3" "$_fh_l" > "$CHUNK_STATE.new" && mv "$CHUNK_STATE.new" "$CHUNK_STATE"; then
        _chunk_counters "$2" "$1"   # its counter and its ledger follow, as after any pass
        log "מחיצה $2: $((_fh_was - _fh_now)) מתוך $_fh_was חתיכות הושלמו ל-$1 מהמגירות השכנות; $_fh_now נשארו ל-HTTP"
    else
        log "מחיצה $2: קובץ המצב של $1 לא נכתב אחרי ההשלמה מהשכנות -- כל החסר מושלם ב-HTTP"
    fi
}

_db_part() {   # $1 = old name, $2 = new name, $3 = index, $4 = file. In the background. 0 = the partition is whole on it; DB_WHY = why not
    _dp_m="$_db_main/p$3"; _dp_bs="$CHUNK_DIR/p$3.state"; _dp_ls="$CHUNK_DIR/missing.list"; rm -f "$_dp_bs"
    [ -s "$_dp_m.idx" ] && [ -s "$_dp_m.args" ] || { DB_WHY="מחיצה $3: האינדקס שלה לא נמצא אצל הסוכן"; return 1; }
    _dp_old=$(partition_node "$1" "$3"); _dp_new=$(partition_node "$2" "$3")
    _dp_l=$(_db_line "$_dp_m.state" "$_dp_old") || _dp_l=$(_db_line "$_dp_m.kept" "$_dp_old") || _dp_l=""
    # #3181: a NEW disk (prodline.sh) holds nothing of the partitions the round began before it came in -- under a name the
    # round already used, the line of that node is ANOTHER disk's. `newdisk` = where the round stood then
    _dp_nw=$(cat "$RUN_DIR/targets/$2/newdisk" 2> /dev/null) || _dp_nw=""
    case "$_dp_nw" in ''|*[!0-9-]*) ;; *) [ "$(_db_pos "$3" || echo 0)" -gt "$_dp_nw" ] || _dp_l="" ;; esac
    # what chunkcast had made durable on it stays: under the node's new name, and "ok" -- its reason was the pull.
    # Nothing (it was out all along): no state at all, and chunkcast begins from nothing.
    if [ -n "$_dp_l" ] && head -n 1 "$_dp_m.state" > "$_dp_bs" 2> /dev/null; then
        echo "$_dp_l" | awk -v n="$_dp_new" '{ print n, "ok", $3, $4 }' >> "$_dp_bs"
    else rm -f "$_dp_bs"; fi
    _dp_total=$(awk 'NR == 1 { print $9 }' "$_dp_m.idx")
    CHUNK_STATE="$_dp_bs"; CHUNK_IDX="$_dp_m.idx"; CHUNK_IMAGE_ARGS=$(cat "$_dp_m.args")
    _fl_missing "$_dp_bs" "$_dp_new" "$_dp_total" > "$_dp_ls" || { DB_WHY="מחיצה $3: החלקים החסרים לא חושבו"; return 1; }
    _dp_at=$(_fl_count "$_dp_ls")   # the ledger against the index when it came back: what the round has not SENT yet is in it too
    if [ -s "$_dp_ls" ]; then
        _fl_live "$2" "$3"
        # its own receiver failed the node (a write error, a flush): that is the drawer's verdict, and nothing more is written over it
        case "$(_db_line "$_dp_bs" "$_dp_new")" in *" failed "*) DB_WHY="מחיצה $3: הכתיבה למגירה נכשלה בזמן ההצטרפות לשידור"; return 1 ;; esac
        _fl_missing "$_dp_bs" "$_dp_new" "$_dp_total" > "$_dp_ls" || { DB_WHY="מחיצה $3: החלקים החסרים לא חושבו"; return 1; }
    fi
    if [ -s "$_dp_ls" ] && _fl_on "$2"; then
        _dp_k=$(_fl_count "$_dp_ls"); DB_K=$((DB_K + $(grep -c . "$_dp_ls")))
        log "מדידה: למגירה $2 חסרות $_dp_k מתוך $_dp_total חתיכות במחיצה $3 (ה-ledger שלה מול האינדקס; כשחזרה חסרו $_dp_at)"
        _fl_note "$2" "$3" "$_dp_k" "$_dp_total" neighbor
        _fl_neighbour "$2" "$3" "$_dp_new" "$_dp_total"
        _fl_missing "$_dp_bs" "$_dp_new" "$_dp_total" > "$_dp_ls" || { DB_WHY="מחיצה $3: החלקים החסרים לא חושבו"; return 1; }
    fi
    if [ -s "$_dp_ls" ] && _fl_on "$2"; then
        _fl_note "$2" "$3" "$(_fl_count "$_dp_ls")" "$_dp_total" http
        _db_note drawer_back_part "$2/p$3" "מחיצה $3: השלמה ב-HTTP על $2 (טווחים: $(grep -c . "$_dp_ls"), חתיכות במחיצה: $_dp_total)"   # #3125 (U5): the journal learns the decision too
        awk -F- 'BEGIN { printf "{\"ranges\":[" } { printf "%s[%d,%d]", (NR > 1 ? "," : ""), $1, $2 } END { print "]}" }' \
            "$_dp_ls" > "$CHUNK_DIR/back.answer.json"
        CHUNK_ANSWER="$CHUNK_DIR/back.answer.json"
        # B29 (peerwire.sh): a neighbour cloner the server paired it with brings what it can first; what it did not stays in the answer.
        # No instruction (shadow, the default), a refusal, a failure: the answer is untouched and all of it is fetched, as before
        _dp_skip=0; if command -v peerfill_try > /dev/null 2>&1; then peerfill_try "$2" "$3" "$CHUNK_ANSWER" || _dp_skip=1; fi
        [ "$_dp_skip" = 1 ] || _chunk_fetch "$_db_srv" "$_db_img" "$4" "$3" "" "$2" "$_dp_new"   # no pass: the server is not asked, nor told
    fi
    _dp_have=$(_db_line "$_dp_bs" "$_dp_new" | awk '$2 == "ok" { print $3 }')
    [ -n "$_dp_have" ] && [ "$_dp_have" = "$_dp_total" ] || { DB_WHY="מחיצה $3: ${_dp_have:-0} מתוך $_dp_total חלקים אחרי ההשלמה"; return 1; }
    rm -f "$RUN_DIR/targets/$2/rejoin"   # whole: nothing is missing, and the report says nothing more of it
}
